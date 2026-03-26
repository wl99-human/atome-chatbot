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
