from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, AgentRevision, FixAttempt, IssueReport, Message, ReplayResult
from app.schemas.api import IssueCreateRequest
from app.services.gemini_service import gemini_service
from app.services.runtime_service import runtime_service
from app.services.source_service import ParsedDocument, source_service
from app.utils.text import normalize_whitespace, tokenize


REPLAY_STOPWORDS = {
    "a",
    "an",
    "and",
    "answer",
    "be",
    "details",
    "for",
    "i",
    "if",
    "in",
    "include",
    "it",
    "mention",
    "of",
    "or",
    "please",
    "respond",
    "response",
    "should",
    "that",
    "the",
    "this",
    "to",
    "use",
    "with",
    "you",
}
CORRECTION_PREFIX_RE = re.compile(
    r"^(?:(?:please|kindly)\s+)?"
    r"(?:(?:the\s+)?(?:answer|response|reply|bot)\s+should\s+)?"
    r"(?:(?:also\s+)?(?:mention|include|add|state|clarify|note|say|explain))(?:\s+that)?\s+",
    re.IGNORECASE,
)


class IssueService:
    def create_issue(self, db: Session, payload: IssueCreateRequest) -> IssueReport:
        assistant_message = db.get(Message, payload.assistant_message_id)
        if not assistant_message or assistant_message.role != "assistant":
            raise ValueError("Assistant message not found.")

        conversation = assistant_message.conversation
        agent = db.get(Agent, payload.agent_id)
        if not agent or conversation.agent_id != agent.id:
            raise ValueError("Agent mismatch for issue report.")

        user_message = next(
            (
                message
                for message in reversed(conversation.messages)
                if message.role == "user" and message.created_at <= assistant_message.created_at
            ),
            None,
        )
        prompt_text = user_message.content if user_message else ""
        diagnosis = self._diagnose(
            prompt_text,
            assistant_message.content,
            payload.customer_note,
            assistant_message.citations_json or [],
        )

        issue = IssueReport(
            agent_id=agent.id,
            revision_id=conversation.revision_id,
            conversation_id=conversation.id,
            user_message_id=user_message.id if user_message else None,
            assistant_message_id=assistant_message.id,
            customer_note=payload.customer_note.strip(),
            diagnosis_type=diagnosis["diagnosis_type"],
            diagnosis_summary=diagnosis["diagnosis_summary"],
        )
        db.add(issue)
        db.commit()
        db.refresh(issue)
        return issue

    def auto_fix_issue(self, db: Session, issue_id: str) -> dict[str, Any]:
        issue = db.get(IssueReport, issue_id)
        if not issue:
            raise ValueError("Issue not found.")
        target_revision = db.get(AgentRevision, issue.revision_id)
        if not target_revision:
            raise ValueError("Target revision not found.")
        agent = db.get(Agent, issue.agent_id)
        requires_review = bool(agent and agent.role == "generated")

        prompt_text, answer_text = self._issue_prompt_and_answer(db, issue)
        expected_behavior = issue.customer_note or issue.diagnosis_summary
        diagnosis_type = issue.diagnosis_type or "other"

        if diagnosis_type == "tool_routing_gap":
            patch_type = "instruction_patch"
            candidate_revision = source_service.clone_revision(
                db,
                target_revision,
                additional_guidelines=normalize_whitespace(
                    f"{target_revision.additional_guidelines}\nCorrection for issue {issue.id}: {expected_behavior}"
                ),
            )
        else:
            patch_type = "correction_doc"
            additional_guidelines = None
            if diagnosis_type == "instruction_gap":
                additional_guidelines = normalize_whitespace(
                    f"{target_revision.additional_guidelines}\nCorrection for issue {issue.id}: {expected_behavior}"
                )
            candidate_revision = source_service.clone_revision(
                db,
                target_revision,
                additional_guidelines=additional_guidelines,
            )
            correction_text = self._build_correction_text(
                prompt_text=prompt_text,
                answer_text=answer_text,
                customer_note=issue.customer_note,
            )
            source_service.add_document(
                db,
                candidate_revision,
                ParsedDocument(
                    title=self._build_correction_title(prompt_text, issue.id),
                    content=correction_text,
                    source_type="correction",
                    source_url=None,
                    filename=f"issue-{issue.id}.txt",
                    mime_type="text/plain",
                    payload_json={
                        "section_name": normalize_whitespace(prompt_text)[:180] or "Issue correction",
                        "issue_id": issue.id,
                        "provenance": "issue_autofix",
                    },
                ),
            )

        replay = runtime_service.run_replay(db, candidate_revision, prompt_text)
        replay_passed, replay_reason = self._evaluate_replay(
            prompt_text=prompt_text,
            expected_behavior=expected_behavior,
            actual_answer=replay.message,
            citations=replay.citations,
            diagnosis_type=diagnosis_type,
        )

        fix_attempt = FixAttempt(
            issue_id=issue.id,
            target_revision_id=target_revision.id,
            candidate_revision_id=candidate_revision.id,
            patch_type=patch_type,
            patch_summary=replay_reason,
            replay_passed=replay_passed,
            auto_published=replay_passed and not requires_review,
        )
        db.add(fix_attempt)
        db.flush()
        db.add(
            ReplayResult(
                fix_attempt_id=fix_attempt.id,
                prompt=prompt_text,
                expected_behavior=expected_behavior,
                actual_answer=replay.message,
                passed=replay_passed,
            )
        )

        if replay_passed:
            if agent and not requires_review:
                agent.active_revision_id = candidate_revision.id
                issue.status = "archived"
            else:
                issue.status = "validated_pending_review"
        else:
            issue.status = "proposed_fix"
        db.commit()
        db.refresh(fix_attempt)
        db.refresh(issue)
        return {
            "issue": issue,
            "fix_attempt": fix_attempt,
            "replay_answer": replay.message,
        }

    def approve_fix(self, db: Session, issue_id: str) -> IssueReport:
        issue = db.get(IssueReport, issue_id)
        if not issue:
            raise ValueError("Issue not found.")
        fix_attempt = db.scalar(
            select(FixAttempt)
            .where(FixAttempt.issue_id == issue_id)
            .order_by(FixAttempt.created_at.desc())
        )
        if not fix_attempt:
            raise ValueError("No fix attempt is available for review.")
        if not fix_attempt.replay_passed:
            raise ValueError("The latest fix attempt did not pass replay validation.")
        agent = db.get(Agent, issue.agent_id)
        if not agent:
            raise ValueError("Agent not found.")
        agent.active_revision_id = fix_attempt.candidate_revision_id
        issue.status = "published"
        db.commit()
        db.refresh(issue)
        return issue

    def reject_fix(self, db: Session, issue_id: str) -> IssueReport:
        issue = db.get(IssueReport, issue_id)
        if not issue:
            raise ValueError("Issue not found.")
        fix_attempt = db.scalar(
            select(FixAttempt)
            .where(FixAttempt.issue_id == issue_id)
            .order_by(FixAttempt.created_at.desc())
        )
        if not fix_attempt:
            raise ValueError("No fix attempt is available for rejection.")
        issue.status = "rejected"
        db.commit()
        db.refresh(issue)
        return issue

    def _diagnose(
        self,
        prompt_text: str,
        answer_text: str,
        customer_note: str,
        citations: list[dict],
    ) -> dict[str, str]:
        lowered_prompt = prompt_text.lower()
        lowered_note = customer_note.lower()
        personal_lookup_markers = ("my ", "check my", "tell me", "look up", "status of my")
        asks_for_personal_lookup = any(marker in lowered_prompt for marker in personal_lookup_markers)
        if asks_for_personal_lookup and (
            ("application" in lowered_prompt and "status" in lowered_prompt)
            or "transaction" in lowered_prompt
        ):
            if "reference" not in answer_text.lower() and "transaction id" not in answer_text.lower():
                return {
                    "diagnosis_type": "tool_routing_gap",
                    "diagnosis_summary": "The assistant should have routed this question through a lookup workflow.",
                }
        if not citations:
            return {
                "diagnosis_type": "retrieval_gap",
                "diagnosis_summary": "The answer was not grounded in a retrieved source.",
            }
        if any(term in lowered_note for term in ["unsafe", "wrong person", "sensitive"]):
            return {
                "diagnosis_type": "unsafe_answer",
                "diagnosis_summary": "The assistant may have answered in an unsafe or privacy-sensitive way.",
            }
        model_diagnosis = gemini_service.analyze_issue(prompt_text, answer_text, customer_note)
        if model_diagnosis.get("diagnosis_type"):
            return {
                "diagnosis_type": model_diagnosis["diagnosis_type"],
                "diagnosis_summary": model_diagnosis.get(
                    "diagnosis_summary", "Model diagnosis generated."
                ),
            }
        return {
            "diagnosis_type": "instruction_gap",
            "diagnosis_summary": "The answer should be corrected with additional instructions.",
        }

    def _build_correction_text(self, *, prompt_text: str, answer_text: str, customer_note: str) -> str:
        preferred = customer_note.strip() or "Provide a more accurate answer grounded in the verified knowledge base."
        normalized_prompt = normalize_whitespace(prompt_text)
        normalized_answer = normalize_whitespace(answer_text)
        answer_addition = self._build_answer_addition(preferred)
        return normalize_whitespace(
            f"""
Question: {normalized_prompt}
Answer addition: {answer_addition}
Use this answer addition naturally when the same or a very similar question is asked again.
Do not repeat the correction as an instruction or mention that it came from a report.
Customer report: {preferred}
This correction is authoritative and should be prioritized over older incomplete answers.
Previous incomplete answer: {normalized_answer}
"""
        )

    def _build_correction_title(self, prompt_text: str, issue_id: str) -> str:
        normalized_prompt = normalize_whitespace(prompt_text)
        if not normalized_prompt:
            return f"Correction for issue {issue_id}"
        return f"Correction: {normalized_prompt}"[:500]

    def _meaningful_replay_tokens(self, text: str) -> set[str]:
        return {
            token
            for token in tokenize(text)
            if token not in REPLAY_STOPWORDS and (len(token) > 2 or token.isdigit())
        }

    def _build_answer_addition(self, customer_note: str) -> str:
        normalized = normalize_whitespace(customer_note)
        answer_addition = CORRECTION_PREFIX_RE.sub("", normalized).strip()
        if answer_addition.lower().startswith("that "):
            answer_addition = answer_addition[5:].strip()
        if not answer_addition:
            answer_addition = normalized
        if answer_addition and answer_addition[-1] not in ".!?":
            answer_addition += "."
        if answer_addition:
            answer_addition = answer_addition[0].upper() + answer_addition[1:]
        return answer_addition

    def _evaluate_replay(
        self,
        *,
        prompt_text: str,
        expected_behavior: str,
        actual_answer: str,
        citations: list[dict],
        diagnosis_type: str,
    ) -> tuple[bool, str]:
        if diagnosis_type == "tool_routing_gap":
            passed = "reference" in actual_answer.lower() or "transaction" in actual_answer.lower()
            reason = "Replay expects the assistant to ask for or use the required lookup identifier."
        else:
            expected_tokens = self._meaningful_replay_tokens(expected_behavior)
            actual_tokens = self._meaningful_replay_tokens(actual_answer)
            overlap = len(expected_tokens & actual_tokens)
            minimum_overlap = 1 if len(expected_tokens) <= 2 else max(2, len(expected_tokens) // 3)
            passed = overlap >= minimum_overlap
            if passed:
                reason = "Replay confirmed that the corrected answer now includes the reported behavior."
            else:
                reason = (
                    "The replay answer is still missing the key correction from the reported mistake."
                )

        if gemini_service.enabled:
            evaluation = gemini_service.evaluate_replay(
                prompt_text=prompt_text,
                expected_behavior=expected_behavior,
                actual_answer=actual_answer,
            )
            passed = passed or bool(evaluation.get("passed", False))
            if not passed:
                reason = evaluation.get("reason", reason)
        return passed, reason

    def _issue_prompt_and_answer(self, db: Session, issue: IssueReport) -> tuple[str, str]:
        user_message = db.get(Message, issue.user_message_id) if issue.user_message_id else None
        assistant_message = db.get(Message, issue.assistant_message_id) if issue.assistant_message_id else None
        return (
            user_message.content if user_message else "",
            assistant_message.content if assistant_message else "",
        )


issue_service = IssueService()
