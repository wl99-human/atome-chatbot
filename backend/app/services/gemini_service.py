from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import get_settings
from app.utils.text import normalize_whitespace

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - import safety
    genai = None
    types = None


JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


class GeminiService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = None
        if self.settings.gemini_api_key and genai is not None:
            self.client = genai.Client(api_key=self.settings.gemini_api_key)

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def _call_model(self, prompt: str, temperature: float = 0.2) -> str | None:
        if not self.client or types is None:
            return None
        try:
            response = self.client.models.generate_content(
                model=self.settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=temperature),
            )
        except Exception:
            return None
        return normalize_whitespace(getattr(response, "text", "") or "")

    def generate_json(self, prompt: str, default: dict[str, Any]) -> dict[str, Any]:
        if not self.client or types is None:
            return default
        try:
            response = self.client.models.generate_content(
                model=self.settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            raw_text = getattr(response, "text", "") or ""
            parsed = json.loads(raw_text)
            return parsed if isinstance(parsed, dict) else default
        except Exception:
            text = self._call_model(prompt, temperature=0.1) or ""
            match = JSON_BLOCK_RE.search(text)
            if not match:
                return default
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return default
            return parsed if isinstance(parsed, dict) else default

    def answer_kb_from_context(
        self,
        *,
        user_message: str,
        history: list[dict[str, str]],
        context_blocks: list[dict[str, str]],
    ) -> str | None:
        if not self.enabled:
            return None

        history_text = "\n".join(
            f"{item['role'].upper()}: {item['content']}" for item in history[-6:]
        )
        context_text = "\n\n".join(
            f"[{item['label']}|{item.get('source_type', 'kb_article')}] {item['title']}\n"
            f"URL: {item.get('source_url') or 'N/A'}\n"
            f"{item['content']}"
            for item in context_blocks
        )
        prompt = f"""
You are a careful customer service assistant answering a general knowledge-base question.

Rules:
- Answer only from the supplied sources.
- If a source is marked as a correction, treat it as authoritative guidance that fixes an earlier incomplete answer.
- Integrate correction facts naturally into the answer instead of repeating instruction phrasing like "please mention that".
- If the answer is not fully supported, say so clearly.
- Use citations inline like [1] or [2].
- Do not invent policies, timelines, or account outcomes.
- Do not ask for an application reference number or transaction ID unless the user is explicitly asking for a personal account lookup.
- Do not turn a general FAQ answer into a tool workflow.

Conversation history:
{history_text or "No prior history."}

Sources:
{context_text}

User message:
{user_message}
""".strip()
        return self._call_model(prompt, temperature=0.2)

    def classify_personal_request(self, message: str) -> dict[str, str]:
        default = {"intent": "unknown", "reason": "No model available."}
        prompt = f"""
Return JSON with keys intent and reason.
Intent must be one of: application_status, failed_transaction, kb_qa, unknown.
Decide whether the user is asking for a case-specific lookup or a general informational help-center question.
User message: {message}
""".strip()
        return self.generate_json(prompt, default)

    def analyze_issue(self, prompt_text: str, answer_text: str, customer_note: str) -> dict[str, str]:
        default = {
            "diagnosis_type": "other",
            "diagnosis_summary": "The issue needs manual review.",
        }
        prompt = f"""
Return JSON with keys diagnosis_type and diagnosis_summary.
diagnosis_type must be one of: retrieval_gap, instruction_gap, tool_routing_gap, unsafe_answer, other.

Customer prompt: {prompt_text}
Assistant answer: {answer_text}
Customer note: {customer_note or "None"}
""".strip()
        return self.generate_json(prompt, default)

    def create_blueprint(
        self,
        *,
        agent_name: str,
        description: str,
        instructions: str,
        document_summaries: list[str],
    ) -> dict[str, Any]:
        default = {
            "name": agent_name,
            "description": description or f"{agent_name} support agent",
            "instructions": instructions.strip(),
            "knowledge_summary": "\n".join(document_summaries[:5]),
            "enabled_tools": ["support_handoff"],
        }
        prompt = f"""
Return JSON with keys name, description, instructions, knowledge_summary, enabled_tools.
enabled_tools must be a list chosen from: application_status, failed_transaction, support_handoff.

Agent name: {agent_name}
Description: {description}
Manager instructions: {instructions}
Uploaded document summaries:
{chr(10).join(document_summaries[:10])}
""".strip()
        return self.generate_json(prompt, default)

    def evaluate_replay(
        self,
        *,
        prompt_text: str,
        expected_behavior: str,
        actual_answer: str,
    ) -> dict[str, Any]:
        default = {
            "passed": False,
            "reason": "Model-based replay evaluation unavailable.",
        }
        prompt = f"""
Return JSON with keys passed and reason.
Judge whether the assistant answer satisfies the expected behavior.

Prompt: {prompt_text}
Expected behavior: {expected_behavior}
Actual answer: {actual_answer}
""".strip()
        return self.generate_json(prompt, default)


gemini_service = GeminiService()
