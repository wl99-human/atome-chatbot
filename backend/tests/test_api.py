from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from fastapi.testclient import TestClient


TEST_DB_PATH = Path(__file__).resolve().parent / "test_atome_chatbot.db"
if TEST_DB_PATH.exists():
    TEST_DB_PATH.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["AUTO_SYNC_DEFAULT_AGENT"] = "false"
os.environ["GEMINI_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app.services.gemini_service import gemini_service  # noqa: E402
from app.services.issue_service import issue_service  # noqa: E402
from app.services.source_service import source_service  # noqa: E402


client = TestClient(app)
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class MockResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self.payload


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def install_fixture_kb(monkeypatch) -> None:
    payload_by_url = {
        "https://help.atome.ph/api/v2/help_center/en-gb/categories/4439682039065/sections.json?per_page=100": load_fixture(
            "zendesk_sections.json"
        ),
        "https://help.atome.ph/api/v2/help_center/en-gb/sections/8712875442713/articles.json?per_page=100": load_fixture(
            "zendesk_articles_application_page1.json"
        ),
        "https://help.atome.ph/api/v2/help_center/en-gb/sections/24883355032089/articles.json?per_page=100": load_fixture(
            "zendesk_articles_accounts_page1.json"
        ),
        "https://help.atome.ph/api/v2/help_center/en-gb/sections/9344964730777/articles.json?per_page=100": load_fixture(
            "zendesk_articles_transaction_page1.json"
        ),
    }

    def fake_get(url: str, timeout: int):
        if url not in payload_by_url:
            raise AssertionError(f"Unexpected URL fetched in test fixture: {url}")
        return MockResponse(payload_by_url[url])

    monkeypatch.setattr(source_service.session, "get", fake_get)


def sync_support_agent(monkeypatch):
    install_fixture_kb(monkeypatch)
    bootstrap = client.get("/api/bootstrap").json()
    agent_id = bootstrap["default_agent_id"]
    response = client.post(f"/api/agents/{agent_id}/sync-sources")
    assert response.status_code == 200
    return agent_id, response.json()


def test_bootstrap_returns_seeded_agent() -> None:
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    payload = response.json()
    assert payload["default_agent_id"]
    assert payload["agents"][0]["name"] == "Atome Card Support"


def test_root_redirects_to_customer_and_preserves_query() -> None:
    response = client.get("/?agent=test-agent-id", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/customer?agent=test-agent-id"


def test_zendesk_ingestion_parses_sections_and_article_html(monkeypatch) -> None:
    install_fixture_kb(monkeypatch)
    documents = source_service.parse_public_help_center(
        "https://help.atome.ph/hc/en-gb/categories/4439682039065-Atome-Card"
    )
    assert len(documents) == 3
    titles = {document.title for document in documents}
    assert "How do I change the mobile number for my account?" in titles
    mobile_doc = next(doc for doc in documents if "mobile number" in doc.title.lower())
    assert "contact atome support through the app" in mobile_doc.content.lower()
    assert mobile_doc.payload_json["section_name"] == "Managing Accounts"
    assert mobile_doc.payload_json["sync_mode"] == "live_api"


def test_sync_endpoint_reports_live_api_metadata(monkeypatch) -> None:
    agent_id, sync_payload = sync_support_agent(monkeypatch)
    assert sync_payload["sync_mode"] == "live_api"
    assert sync_payload["fallback_used"] is False
    assert sync_payload["documents_synced"] == 3
    assert sync_payload["last_sync_warning"] is None

    bootstrap = client.get("/api/bootstrap").json()
    agent = next(agent for agent in bootstrap["agents"] if agent["id"] == agent_id)
    assert agent["sync_mode"] == "live_api"
    assert agent["fallback_used"] is False
    assert agent["documents_synced"] == 3


def test_general_kb_questions_return_grounded_answers(monkeypatch) -> None:
    agent_id, _ = sync_support_agent(monkeypatch)

    coverage_questions = [
        "How do I change the mobile number for my account?",
        "How can I check the status of my application?",
        "Why did my card transaction fail?",
    ]
    forbidden_phrases = [
        "application reference number",
        "transaction id first",
        "transaction id to check",
    ]

    for question in coverage_questions:
        response = client.post(f"/api/chat/{agent_id}", json={"message": question})
        assert response.status_code == 200
        payload = response.json()
        assert payload["intent"] == "kb_qa"
        assert payload["citations"]
        answer_text = payload["message"].lower()
        assert not any(phrase in answer_text for phrase in forbidden_phrases)


def test_application_status_lookup_requests_reference_then_returns_status(monkeypatch) -> None:
    agent_id, _ = sync_support_agent(monkeypatch)
    first = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "Please check my application status"},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["needs_followup"] is True
    assert first_payload["intent"] == "application_status"

    second = client.post(
        f"/api/chat/{agent_id}",
        json={
            "message": "APP123456",
            "conversation_id": first_payload["conversation_id"],
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["intent"] == "application_status"
    assert "APP123456" in second_payload["message"]


def test_transaction_lookup_uses_tool_flow(monkeypatch) -> None:
    agent_id, _ = sync_support_agent(monkeypatch)

    first = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "My card transaction failed"},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["intent"] == "failed_transaction"
    assert first_payload["needs_followup"] is True

    second = client.post(
        f"/api/chat/{agent_id}",
        json={
            "message": "TRX123456",
            "conversation_id": first_payload["conversation_id"],
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["intent"] == "failed_transaction"
    assert "TRX123456" in second_payload["message"]


def test_gemini_classification_is_not_used_for_faq_routing(monkeypatch) -> None:
    agent_id, _ = sync_support_agent(monkeypatch)
    original_client = gemini_service.client
    monkeypatch.setattr(gemini_service, "client", object())
    monkeypatch.setattr(
        gemini_service,
        "classify_personal_request",
        lambda message: (_ for _ in ()).throw(AssertionError("classification should not be used")),
    )
    monkeypatch.setattr(gemini_service, "answer_kb_from_context", lambda **kwargs: None)

    response = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "Why did my card transaction fail?"},
    )
    gemini_service.client = original_client
    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "kb_qa"
    assert payload["citations"]


def test_sync_endpoint_reports_fallback_metadata_when_live_sync_fails(monkeypatch) -> None:
    def failing_get(url: str, timeout: int):
        raise requests.HTTPError("blocked")

    monkeypatch.setattr(source_service.session, "get", failing_get)
    bootstrap = client.get("/api/bootstrap").json()
    agent_id = bootstrap["default_agent_id"]
    response = client.post(f"/api/agents/{agent_id}/sync-sources")
    assert response.status_code == 200
    payload = response.json()
    assert payload["sync_mode"] == "fallback"
    assert payload["fallback_used"] is True
    assert payload["last_sync_warning"] is not None


def test_generate_json_returns_default_when_model_json_is_not_object(monkeypatch) -> None:
    class StubModels:
        @staticmethod
        def generate_content(*args, **kwargs):
            return type("Response", (), {"text": '["not", "an", "object"]'})()

    class StubClient:
        models = StubModels()

    monkeypatch.setattr(gemini_service, "client", StubClient())
    result = gemini_service.generate_json("prompt", {"ok": True})
    assert result == {"ok": True}


def test_generate_agent_coerces_non_string_blueprint_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        gemini_service,
        "create_blueprint",
        lambda **kwargs: {
            "name": ["VIP", "Support"],
            "description": {"summary": "Priority", "channel": "chat"},
            "instructions": ["Use verified KB only.", {"note": "Escalate if unsure."}],
            "knowledge_summary": ["Card FAQ", {"detail": "Escalation flow"}],
            "enabled_tools": "application_status",
        },
    )

    response = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Generated Agent",
            "description": "",
            "instructions": "Fallback instructions",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent"]["name"] == "VIP Support"
    assert payload["blueprint"]["description"] == "Priority chat"
    assert payload["blueprint"]["instructions"] == "Use verified KB only. Escalate if unsure."
    assert payload["blueprint"]["knowledge_summary"] == "Card FAQ Escalation flow"
    assert payload["blueprint"]["enabled_tools"] == ["support_handoff"]


def test_generate_agent_syncs_manager_kb_url(monkeypatch) -> None:
    install_fixture_kb(monkeypatch)

    response = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Manager KB Agent",
            "description": "Uses the public KB URL during creation.",
            "instructions": "Answer only from the configured KB.",
            "knowledge_base_url": "https://help.atome.ph/hc/en-gb/categories/4439682039065-Atome-Card",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent"]["knowledge_base_url"] == "https://help.atome.ph/hc/en-gb/categories/4439682039065-Atome-Card"
    assert payload["agent"]["sync_mode"] == "live_api"
    assert payload["agent"]["fallback_used"] is False
    assert payload["agent"]["documents_synced"] == 3
    assert payload["agent"]["last_sync_warning"] is None


def test_generate_agent_combines_manager_kb_url_and_uploaded_files(monkeypatch) -> None:
    install_fixture_kb(monkeypatch)

    response = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Hybrid Knowledge Agent",
            "description": "Uses a KB URL and a manager upload.",
            "instructions": "Use both the KB and uploaded notes.",
            "knowledge_base_url": "https://help.atome.ph/hc/en-gb/categories/4439682039065-Atome-Card",
        },
        files=[
            (
                "files",
                (
                    "extra-guidance.txt",
                    b"Escalate refund questions when a merchant confirms the charge was duplicated.",
                    "text/plain",
                ),
            )
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent"]["knowledge_base_url"] == "https://help.atome.ph/hc/en-gb/categories/4439682039065-Atome-Card"
    assert payload["agent"]["sync_mode"] == "live_api"
    assert payload["agent"]["fallback_used"] is False
    assert payload["agent"]["documents_synced"] == 4
    assert "kb url and manager-provided files" in payload["agent"]["source_summary"].lower()


def test_generate_agent_keeps_manager_content_when_kb_sync_fails(monkeypatch) -> None:
    def failing_get(url: str, timeout: int):
        raise requests.HTTPError("blocked")

    monkeypatch.setattr(source_service.session, "get", failing_get)

    response = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Resilient Agent",
            "description": "Falls back to manager input if the KB URL fails.",
            "instructions": "Use the manager instructions when the KB is unavailable.",
            "knowledge_base_url": "https://help.atome.ph/hc/en-gb/categories/4439682039065-Atome-Card",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent"]["knowledge_base_url"] == "https://help.atome.ph/hc/en-gb/categories/4439682039065-Atome-Card"
    assert payload["agent"]["sync_mode"] == "manager_input"
    assert payload["agent"]["fallback_used"] is False
    assert payload["agent"]["documents_synced"] == 1
    assert payload["agent"]["last_sync_warning"] is not None
    assert "manager instructions" in payload["agent"]["source_summary"].lower()


def test_generated_agents_do_not_use_lookup_flows(monkeypatch) -> None:
    monkeypatch.setattr(
        gemini_service,
        "create_blueprint",
        lambda **kwargs: {
            "name": "Internal IT Support",
            "description": "IT helpdesk demo agent",
            "instructions": "Answer only from uploaded knowledge.",
            "knowledge_summary": "Reset passwords and access requests.",
            "enabled_tools": ["application_status", "failed_transaction"],
        },
    )

    generated = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Internal IT Support",
            "description": "IT helpdesk demo agent",
            "instructions": "Answer only from uploaded knowledge.",
        },
    )
    assert generated.status_code == 200
    generated_payload = generated.json()
    assert generated_payload["blueprint"]["enabled_tools"] == ["support_handoff"]

    response = client.post(
        f"/api/chat/{generated_payload['agent']['id']}",
        json={"message": "Please check my application status"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] != "application_status"
    assert payload["needs_followup"] is False
    assert payload["conversation"]["pending_action"] is None


def test_uploaded_email_text_is_preserved_in_answer_and_citations(monkeypatch) -> None:
    monkeypatch.setattr(
        gemini_service,
        "create_blueprint",
        lambda **kwargs: {
            "name": "IT Support Agent",
            "description": "Handles internal IT knowledge.",
            "instructions": "Answer only from uploaded knowledge.",
            "knowledge_summary": "Password reset guidance for employees.",
            "enabled_tools": ["support_handoff"],
        },
    )

    generated = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "IT Support Agent",
            "description": "Handles internal IT knowledge.",
            "instructions": "Answer only from uploaded knowledge.",
        },
        files=[
            (
                "files",
                (
                    "password-reset.txt",
                    b"To reset your password, please contact the network admin at admin@example.com",
                    "text/plain",
                ),
            )
        ],
    )
    assert generated.status_code == 200
    generated_payload = generated.json()

    original_client = gemini_service.client
    monkeypatch.setattr(gemini_service, "client", None)
    try:
        response = client.post(
            f"/api/chat/{generated_payload['agent']['id']}",
            json={"message": "How do I reset my password?"},
        )
    finally:
        gemini_service.client = original_client

    assert response.status_code == 200
    payload = response.json()
    assert "admin@example.com" in payload["message"]
    assert payload["citations"]
    assert "admin@example.com" in payload["citations"][0]["snippet"]


def test_auto_fix_improves_future_faq_answers(monkeypatch) -> None:
    agent_id, _ = sync_support_agent(monkeypatch)

    first = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "Why can an Atome Card transaction fail?"},
    )
    assert first.status_code == 200
    first_payload = first.json()

    issue = client.post(
        "/api/issues",
        json={
            "agent_id": agent_id,
            "assistant_message_id": first_payload["assistant_message_id"],
            "customer_note": "Please mention that unsuccessful pending charges are usually reversed or refunded within 14 days.",
        },
    )
    assert issue.status_code == 200
    issue_payload = issue.json()

    fixed = client.post(f"/api/issues/{issue_payload['id']}/auto-fix")
    assert fixed.status_code == 200
    fixed_payload = fixed.json()
    assert fixed_payload["status"] == "archived"
    assert fixed_payload["latest_fix_attempt"]["replay_passed"] is True

    second = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "Why can an Atome Card transaction fail?"},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert "14 days" in second_payload["message"].lower()
    assert "please mention" not in second_payload["message"].lower()
    assert any("correction:" in citation["title"].lower() for citation in second_payload["citations"])


def test_replay_keeps_deterministic_pass_when_model_judge_is_negative(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", object())
    monkeypatch.setattr(
        gemini_service,
        "evaluate_replay",
        lambda **kwargs: {"passed": False, "reason": "model judge was stricter than the deterministic check"},
    )

    passed, reason = issue_service._evaluate_replay(
        prompt_text="Why can an Atome Card transaction fail?",
        expected_behavior="Please mention that unsuccessful pending charges are usually reversed or refunded within 14 days.",
        actual_answer=(
            "An unsuccessful pending charge is usually reversed or refunded within 14 days."
        ),
        citations=[{"label": "[1]"}],
        diagnosis_type="retrieval_gap",
    )

    assert passed is True
    assert "corrected answer" in reason.lower()


def test_answer_kb_from_context_falls_back_when_model_call_raises(monkeypatch) -> None:
    class FailingModels:
        @staticmethod
        def generate_content(*args, **kwargs):
            raise RuntimeError("quota exceeded")

    class StubClient:
        models = FailingModels()

    monkeypatch.setattr(gemini_service, "client", StubClient())

    answer = gemini_service.answer_kb_from_context(
        user_message="Why can an Atome Card transaction fail?",
        history=[],
        context_blocks=[
            {
                "label": "1",
                "title": "Correction: Why can an Atome Card transaction fail?",
                "source_url": None,
                "content": "Required correction: mention that unsuccessful pending charges are usually reversed or refunded within 14 days.",
                "source_type": "correction",
            }
        ],
    )

    assert answer is None
