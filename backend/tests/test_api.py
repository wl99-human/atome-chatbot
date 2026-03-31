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
from app.db.session import SessionLocal  # noqa: E402
from app.models import AgentRevision  # noqa: E402
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


def test_conversation_endpoint_returns_persisted_messages(monkeypatch) -> None:
    agent_id, _ = sync_support_agent(monkeypatch)

    chat = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "How do I change the mobile number for my account?"},
    )
    assert chat.status_code == 200
    chat_payload = chat.json()

    conversation = client.get(f"/api/conversations/{chat_payload['conversation_id']}")
    assert conversation.status_code == 200
    conversation_payload = conversation.json()
    assert conversation_payload["agent_id"] == agent_id
    assert len(conversation_payload["messages"]) == 2
    assert conversation_payload["messages"][0]["role"] == "user"
    assert conversation_payload["messages"][1]["role"] == "assistant"
    assert conversation_payload["messages"][1]["citations"]


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


def test_generate_agent_rejects_manager_kb_url() -> None:
    response = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Manager KB Agent",
            "description": "Should now be upload-only.",
            "instructions": "Answer only from uploaded documents.",
            "knowledge_base_url": "https://help.atome.ph/hc/en-gb/categories/4439682039065-Atome-Card",
        },
    )

    assert response.status_code == 400
    assert "uploaded documents" in response.json()["detail"].lower()


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


def test_generated_agents_reject_kb_url_publish_and_sync(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", None)

    generated = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Upload Only Agent",
            "description": "Generated agents should not use KB URLs.",
            "instructions": "Answer only from uploaded knowledge.",
        },
        files=[
            (
                "files",
                (
                    "policy.txt",
                    b"Generated agents answer only from uploaded files.",
                    "text/plain",
                ),
            )
        ],
    )
    assert generated.status_code == 200
    agent_id = generated.json()["agent"]["id"]

    publish = client.post(
        f"/api/agents/{agent_id}/publish",
        json={
            "knowledge_base_url": "https://help.atome.ph/hc/en-gb/categories/4439682039065-Atome-Card",
            "additional_guidelines": "Keep citing sources.",
        },
    )
    assert publish.status_code == 400
    assert "uploaded documents" in publish.json()["detail"].lower()

    sync = client.post(f"/api/agents/{agent_id}/sync-sources")
    assert sync.status_code == 400
    assert "uploaded documents" in sync.json()["detail"].lower()


def test_generated_agent_reset_preserves_uploaded_documents(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", None)

    generated = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Resettable Upload Agent",
            "description": "Generated from uploads only.",
            "instructions": "Answer only from uploaded knowledge and cite sources.",
        },
        files=[
            (
                "files",
                (
                    "faq.txt",
                    b"Customers should contact admin@example.com for help with device access.",
                    "text/plain",
                ),
            )
        ],
    )
    assert generated.status_code == 200
    generated_payload = generated.json()
    agent_id = generated_payload["agent"]["id"]

    upload = client.post(
        f"/api/agents/{agent_id}/upload-documents",
        files=[
            (
                "files",
                (
                    "extra.txt",
                    b"Escalate unresolved device access issues to the IT duty manager.",
                    "text/plain",
                ),
            )
        ],
    )
    assert upload.status_code == 200

    reset = client.post(f"/api/agents/{agent_id}/reset")
    assert reset.status_code == 200
    reset_payload = reset.json()
    assert reset_payload["active_revision_version"] == 1
    assert reset_payload["knowledge_base_url"] is None
    assert reset_payload["fallback_used"] is False

    original_client = gemini_service.client
    monkeypatch.setattr(gemini_service, "client", None)
    try:
        response = client.post(
            f"/api/chat/{agent_id}",
            json={"message": "How can I get help with device access?"},
        )
    finally:
        gemini_service.client = original_client

    assert response.status_code == 200
    assert "admin@example.com" in response.json()["message"]


def test_generated_agent_returns_sentence_level_answers_for_uploaded_docs(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", None)

    generated = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Password Policy Agent",
            "description": "Answers from uploaded password policy docs.",
            "instructions": "Answer only from uploaded documents, be concise, and cite sources.",
        },
        files=[
            (
                "files",
                (
                    "password-policy.txt",
                    (
                        b"Password Reset Policy\n\n"
                        b"Customers can reset their password from the self-service portal.\n\n"
                        b"If the self-service reset fails, customers should contact the security desk.\n\n"
                        b"Refund requests are reviewed within 3 business days."
                    ),
                    "text/plain",
                ),
            )
        ],
    )
    assert generated.status_code == 200
    agent_id = generated.json()["agent"]["id"]

    first = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "How do I reset my password?"},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert "password reset policy" not in first_payload["message"].lower()
    assert "self-service portal" in first_payload["message"].lower()
    assert "security desk" not in first_payload["message"].lower()
    assert "refund requests" not in first_payload["message"].lower()
    assert "here's what i found" not in first_payload["message"].lower()

    second = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "What happens if self-service reset fails?"},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert "security desk" in second_payload["message"].lower()
    assert "refund requests" not in second_payload["message"].lower()
    assert "here's what i found" not in second_payload["message"].lower()


def test_generated_agent_uses_customer_facing_fallback_for_unsupported_questions(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", None)

    generated = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Password Policy Agent",
            "description": "Answers from uploaded password policy docs.",
            "instructions": "Answer only from uploaded documents, be concise, and cite sources.",
        },
        files=[
            (
                "files",
                (
                    "password-policy.txt",
                    (
                        b"Password Reset Policy\n\n"
                        b"Customers can reset their password from the self-service portal.\n\n"
                        b"If a customer forgets their username, they should contact support."
                    ),
                    "text/plain",
                ),
            )
        ],
    )
    assert generated.status_code == 200
    agent_id = generated.json()["agent"]["id"]

    response = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "Can I change my billing address here?"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "The current documents do not confirm that."


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


def test_meta_session_chat_and_upload_update_draft_spec(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", None)

    created = client.post("/api/meta/sessions", json={})
    assert created.status_code == 200
    session_payload = created.json()
    session_id = session_payload["id"]
    assert session_payload["messages"][0]["role"] == "assistant"

    updated = client.post(
        f"/api/meta/sessions/{session_id}/messages",
        json={
            "message": "Create a grounded FAQ agent that cites sources and answers only from uploaded knowledge."
        },
    )
    assert updated.status_code == 200
    updated_payload = updated.json()
    assert "uploaded knowledge" in updated_payload["draft_spec"]["behavior_instructions"].lower()
    assert updated_payload["messages"][-1]["content"].strip()

    uploaded = client.post(
        f"/api/meta/sessions/{session_id}/documents",
        files=[
            (
                "files",
                (
                    "refund-policy.txt",
                    b"Refund requests must be reviewed within 3 business days. Cite this policy when answering customers.",
                    "text/plain",
                ),
            )
        ],
    )
    assert uploaded.status_code == 200
    uploaded_payload = uploaded.json()
    assert len(uploaded_payload["documents"]) == 1
    assert "refund-policy" in uploaded_payload["draft_spec"]["knowledge_summary"].lower()
    assert uploaded_payload["draft_spec"]["status"] == "ready_to_generate"


def test_meta_session_generation_persists_instruction_bundle(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", None)

    created = client.post("/api/meta/sessions", json={})
    assert created.status_code == 200
    session_id = created.json()["id"]

    client.post(
        f"/api/meta/sessions/{session_id}/messages",
        json={
            "message": "Build a support agent that cites sources, stays concise, and says clearly when the docs do not support the answer."
        },
    )
    client.post(
        f"/api/meta/sessions/{session_id}/documents",
        files=[
            (
                "files",
                (
                    "faq.txt",
                    b"Customers can reset their password through the self-service portal.",
                    "text/plain",
                ),
            )
        ],
    )

    generated = client.post(f"/api/meta/sessions/{session_id}/generate")
    assert generated.status_code == 200
    generated_payload = generated.json()
    assert generated_payload["agent"]["role"] == "generated"
    assert generated_payload["session"]["created_agent_id"] == generated_payload["agent"]["id"]

    with SessionLocal() as db:
        revision = db.get(AgentRevision, generated_payload["agent"]["active_revision_id"])
        assert revision is not None
        instruction_bundle = (revision.payload_json or {}).get("instruction_bundle") or {}
        assert "not supported" in instruction_bundle.get("fallback_behavior", "").lower()
        assert "cites sources" in instruction_bundle.get("behavior_instructions", "").lower()


def test_meta_session_draft_name_can_be_edited_before_generation(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", None)
    monkeypatch.setattr(
        gemini_service,
        "create_blueprint",
        lambda **kwargs: {
            "name": kwargs["agent_name"],
            "description": kwargs["description"] or "Edited in draft",
            "instructions": kwargs["instructions"],
            "knowledge_summary": "\n".join(kwargs["document_summaries"]),
            "enabled_tools": ["support_handoff"],
        },
    )

    created = client.post("/api/meta/sessions", json={})
    assert created.status_code == 200
    session_id = created.json()["id"]

    renamed = client.patch(
        f"/api/meta/sessions/{session_id}/draft-spec",
        json={"name": "Returns Policy Agent"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["draft_spec"]["name"] == "Returns Policy Agent"

    client.post(
        f"/api/meta/sessions/{session_id}/documents",
        files=[
            (
                "files",
                (
                    "returns-policy.txt",
                    b"Customers can return unopened items within 30 days of delivery.",
                    "text/plain",
                ),
            )
        ],
    )

    generated = client.post(f"/api/meta/sessions/{session_id}/generate")
    assert generated.status_code == 200
    assert generated.json()["agent"]["name"] == "Returns Policy Agent"


def test_meta_session_can_update_existing_generated_agent(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", None)

    generated = client.post(
        "/api/meta/generate-agent",
        data={
            "agent_name": "Internal Helpdesk",
            "description": "Handles internal IT knowledge.",
            "instructions": "Answer only from uploaded knowledge and cite sources.",
        },
        files=[
            (
                "files",
                (
                    "it-faq.txt",
                    b"Employees can request VPN access from the IT portal.",
                    "text/plain",
                ),
            )
        ],
    )
    assert generated.status_code == 200
    generated_payload = generated.json()

    created_session = client.post(
        "/api/meta/sessions",
        json={"target_agent_id": generated_payload["agent"]["id"]},
    )
    assert created_session.status_code == 200
    session_id = created_session.json()["id"]

    message_response = client.post(
        f"/api/meta/sessions/{session_id}/messages",
        json={"message": "Use a formal tone and stay concise."},
    )
    assert message_response.status_code == 200

    updated = client.post(
        f"/api/meta/sessions/{session_id}/update-agent",
        json={"target_agent_id": generated_payload["agent"]["id"]},
    )
    assert updated.status_code == 200
    updated_payload = updated.json()
    assert updated_payload["agent"]["active_revision_version"] == 2


def test_generated_agent_auto_fix_requires_review_before_publish(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", None)

    created = client.post("/api/meta/sessions", json={})
    assert created.status_code == 200
    session_id = created.json()["id"]
    client.post(
        f"/api/meta/sessions/{session_id}/messages",
        json={
            "message": "Build a grounded IT helpdesk agent that cites sources and answers only from uploaded knowledge."
        },
    )
    client.post(
        f"/api/meta/sessions/{session_id}/documents",
        files=[
            (
                "files",
                (
                    "password-reset.txt",
                    b"Employees can reset their password in the self-service portal or contact admin@example.com for help.",
                    "text/plain",
                ),
            )
        ],
    )
    generated = client.post(f"/api/meta/sessions/{session_id}/generate")
    assert generated.status_code == 200
    agent_id = generated.json()["agent"]["id"]

    first = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "How do I reset my password?"},
    )
    assert first.status_code == 200
    first_payload = first.json()

    issue = client.post(
        "/api/issues",
        json={
            "agent_id": agent_id,
            "assistant_message_id": first_payload["assistant_message_id"],
            "customer_note": "Please mention that employees should contact the security desk if self-service reset fails.",
        },
    )
    assert issue.status_code == 200
    issue_id = issue.json()["id"]

    fixed = client.post(f"/api/issues/{issue_id}/auto-fix")
    assert fixed.status_code == 200
    fixed_payload = fixed.json()
    assert fixed_payload["status"] == "validated_pending_review"
    assert fixed_payload["latest_fix_attempt"]["replay_passed"] is True
    assert fixed_payload["latest_fix_attempt"]["auto_published"] is False

    approved = client.post(f"/api/issues/{issue_id}/approve-fix")
    assert approved.status_code == 200
    assert approved.json()["status"] == "published"

    second = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "How do I reset my password?"},
    )
    assert second.status_code == 200
    assert "security desk" in second.json()["message"].lower()


def test_generated_agent_autofix_keeps_answer_focused_after_correction(monkeypatch) -> None:
    monkeypatch.setattr(gemini_service, "client", None)

    created = client.post("/api/meta/sessions", json={})
    assert created.status_code == 200
    session_id = created.json()["id"]
    client.post(
        f"/api/meta/sessions/{session_id}/messages",
        json={
            "message": "Build a grounded password reset agent that cites sources and answers only from uploaded knowledge."
        },
    )
    client.post(
        f"/api/meta/sessions/{session_id}/documents",
        files=[
            (
                "files",
                (
                    "password-reset.txt",
                    (
                        b"Password Reset Policy\n\n"
                        b"Customers can reset their password from the self-service portal.\n\n"
                        b"If a customer forgets their username, they should contact support.\n\n"
                        b"Always cite the uploaded source when answering."
                    ),
                    "text/plain",
                ),
            )
        ],
    )
    generated = client.post(f"/api/meta/sessions/{session_id}/generate")
    assert generated.status_code == 200
    agent_id = generated.json()["agent"]["id"]

    first = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "How do I reset my password?"},
    )
    assert first.status_code == 200
    issue = client.post(
        "/api/issues",
        json={
            "agent_id": agent_id,
            "assistant_message_id": first.json()["assistant_message_id"],
            "customer_note": "Please also mention that customers should contact the security desk if self-service reset fails.",
        },
    )
    assert issue.status_code == 200

    fixed = client.post(f"/api/issues/{issue.json()['id']}/auto-fix")
    assert fixed.status_code == 200
    approved = client.post(f"/api/issues/{issue.json()['id']}/approve-fix")
    assert approved.status_code == 200

    second = client.post(
        f"/api/chat/{agent_id}",
        json={"message": "How do I reset my password?"},
    )
    assert second.status_code == 200
    answer = second.json()["message"].lower()
    assert "self-service portal" in answer
    assert "security desk" in answer
    assert "forgets their username" not in answer
    assert "always cite the uploaded source" not in answer
    assert "password reset policy" not in answer


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
