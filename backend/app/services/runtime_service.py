from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Agent, AgentRevision, Conversation, Message
from app.schemas.api import ChatRequest
from app.services.gemini_service import gemini_service
from app.services.retrieval_service import RetrievedChunk, retrieval_service
from app.services.tool_service import get_application_status, get_card_transaction_status
from app.utils.text import normalize_whitespace


APPLICATION_REF_RE = re.compile(r"\b(?:APP[- ]?)?[A-Z0-9]{6,}\b", re.IGNORECASE)
TRANSACTION_ID_RE = re.compile(
    r"\b(?:TXN?|TRX|TRANS(?:ACTION)?)[-:# ]*[A-Z0-9]{4,}\b|\b[A-Z0-9]{8,}\b",
    re.IGNORECASE,
)
CORRECTION_RE = re.compile(
    r"Required correction:\s*(.+?)(?:\s+Apply this correction|\s+This correction is authoritative|$)",
    re.IGNORECASE,
)
HOW_TO_PREFIXES = ("how", "why", "what", "when", "where", "can", "do", "does", "is", "are")
GENERAL_FAQ_PREFIXES = (
    "how can i",
    "how do i",
    "what should i do",
    "why did",
    "where can i",
    "can i",
)


@dataclass
class ReplyResult:
    intent: str
    message: str
    citations: list[dict]
    needs_followup: bool = False
    followup_field: str | None = None
    pending_action: str | None = None
    pending_payload: dict | None = None


class RuntimeService:
    def _prioritize_retrieved(self, retrieved: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return sorted(
            retrieved,
            key=lambda item: (item.source_type != "correction", -item.score, item.title.lower()),
        )

    def _extract_correction_sentence(self, content: str) -> str | None:
        match = CORRECTION_RE.search(normalize_whitespace(content))
        if not match:
            return None
        return match.group(1).strip()

    def get_active_agent_and_revision(self, db: Session, agent_id: str) -> tuple[Agent, AgentRevision]:
        agent = db.get(Agent, agent_id)
        if not agent:
            raise ValueError("Agent not found.")
        revision_id = agent.active_revision_id
        if not revision_id:
            raise ValueError("Agent has no active revision.")
        revision = db.get(AgentRevision, revision_id)
        if not revision:
            raise ValueError("Active revision not found.")
        return agent, revision

    def handle_chat(self, db: Session, agent_id: str, request: ChatRequest) -> dict:
        agent, revision = self.get_active_agent_and_revision(db, agent_id)
        conversation = self._get_or_create_conversation(db, agent, revision, request.conversation_id)

        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=normalize_whitespace(request.message),
        )
        db.add(user_message)
        db.flush()

        history = [
            {"role": message.role, "content": message.content}
            for message in conversation.messages[-8:]
            if message.id != user_message.id
        ]
        reply = self.generate_reply(
            db,
            revision=revision,
            message_text=user_message.content,
            history=history,
            pending_action=conversation.pending_action,
            pending_payload=conversation.pending_payload or {},
        )

        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=reply.message,
            citations_json=reply.citations,
            payload_json={"intent": reply.intent},
        )
        db.add(assistant_message)

        conversation.pending_action = reply.pending_action
        conversation.pending_payload = reply.pending_payload or {}
        conversation.revision_id = revision.id
        if conversation.title == "New conversation":
            conversation.title = user_message.content[:80]
        db.flush()
        db.commit()

        return {
            "conversation": conversation,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "reply": reply,
        }

    def run_replay(self, db: Session, revision: AgentRevision, prompt_text: str) -> ReplyResult:
        return self.generate_reply(
            db,
            revision=revision,
            message_text=normalize_whitespace(prompt_text),
            history=[],
            pending_action=None,
            pending_payload={},
        )

    def generate_reply(
        self,
        db: Session,
        *,
        revision: AgentRevision,
        message_text: str,
        history: list[dict[str, str]],
        pending_action: str | None,
        pending_payload: dict,
    ) -> ReplyResult:
        if pending_action == "application_status":
            return self._handle_pending_application(message_text)
        if pending_action == "failed_transaction":
            return self._handle_pending_transaction(message_text)

        intent = self._classify_intent(message_text)
        if intent == "application_status":
            application_ref = self._extract_application_ref(message_text)
            if not application_ref:
                return ReplyResult(
                    intent="application_status",
                    message="I can check your card application status. Please share your application reference number first.",
                    citations=[],
                    needs_followup=True,
                    followup_field="application_reference",
                    pending_action="application_status",
                )
            return self._application_status_reply(application_ref)

        if intent == "failed_transaction":
            transaction_id = self._extract_transaction_id(message_text)
            if not transaction_id:
                return ReplyResult(
                    intent="failed_transaction",
                    message="I can look up the failed card transaction, but I need the transaction ID first.",
                    citations=[],
                    needs_followup=True,
                    followup_field="transaction_id",
                    pending_action="failed_transaction",
                )
            return self._transaction_status_reply(transaction_id)

        retrieved = retrieval_service.search(db, revision.id, message_text, limit=5)
        if not retrieved:
            return ReplyResult(
                intent="unknown",
                message=(
                    "I couldn't verify that from the current knowledge sources. "
                    "Please try rephrasing the question or report the mistake so it can be reviewed."
                ),
                citations=[],
            )
        answer = self._answer_from_retrieval(revision, message_text, history, retrieved)
        citations = self._build_citations(retrieved)
        return ReplyResult(intent="kb_qa", message=answer, citations=citations)

    def _answer_from_retrieval(
        self,
        _revision: AgentRevision,
        message_text: str,
        history: list[dict[str, str]],
        retrieved: list[RetrievedChunk],
    ) -> str:
        prioritized = self._prioritize_retrieved(retrieved)
        context_blocks = [
            {
                "label": str(index + 1),
                "title": item.title,
                "source_url": item.source_url,
                "content": item.content,
                "source_type": item.source_type,
            }
            for index, item in enumerate(prioritized[:4])
        ]
        model_answer = gemini_service.answer_kb_from_context(
            user_message=message_text,
            history=history,
            context_blocks=context_blocks,
        )
        if model_answer:
            return model_answer

        correction_item = next((item for item in prioritized if item.source_type == "correction"), None)
        supporting_item = next((item for item in prioritized if item.source_type != "correction"), None)
        if correction_item:
            correction_text = self._extract_correction_sentence(correction_item.content)
            if correction_text:
                correction_label = prioritized.index(correction_item) + 1
                if supporting_item:
                    support_label = prioritized.index(supporting_item) + 1
                    support_snippet = supporting_item.content[:220].rsplit(" ", 1)[0].rstrip(". ")
                    return (
                        f"{support_snippet}. [{support_label}] "
                        f"{correction_text.rstrip('. ')}. [{correction_label}]"
                    )
                return f"{correction_text.rstrip('. ')}. [{correction_label}]"

        bullets = []
        for index, item in enumerate(prioritized[:2]):
            snippet = item.content[:260].rsplit(" ", 1)[0]
            bullets.append(f"{snippet} [{index + 1}]")
        if not bullets:
            return (
                "I couldn't verify that from the current knowledge sources. "
                "Please try rephrasing the question."
            )
        return "Here's what I found from the available knowledge base:\n- " + "\n- ".join(bullets)

    def _build_citations(self, retrieved: list[RetrievedChunk]) -> list[dict]:
        citations: list[dict] = []
        seen_documents: set[str] = set()
        label_index = 1
        for item in self._prioritize_retrieved(retrieved):
            if item.document_id in seen_documents:
                continue
            seen_documents.add(item.document_id)
            citations.append(
                {
                    "label": f"[{label_index}]",
                    "title": item.title,
                    "source_url": item.source_url,
                    "snippet": item.content[:220].rsplit(" ", 1)[0],
                }
            )
            label_index += 1
            if len(citations) >= 3:
                break
        return citations

    def _get_or_create_conversation(
        self,
        db: Session,
        agent: Agent,
        revision: AgentRevision,
        conversation_id: str | None,
    ) -> Conversation:
        if conversation_id:
            conversation = db.get(Conversation, conversation_id)
            if conversation and conversation.agent_id == agent.id:
                return conversation
        conversation = Conversation(agent_id=agent.id, revision_id=revision.id)
        db.add(conversation)
        db.flush()
        return conversation

    def _classify_intent(self, message_text: str) -> str:
        lowered = normalize_whitespace(message_text.lower())
        if self._looks_like_personal_application_status(lowered):
            return "application_status"
        if self._looks_like_personal_failed_transaction(lowered):
            return "failed_transaction"
        return "kb_qa"

    def _looks_like_personal_application_status(self, lowered: str) -> bool:
        if "application" not in lowered or "status" not in lowered:
            return False
        if self._is_general_application_status_faq(lowered):
            return False
        personal_markers = (
            "please check my",
            "check my application status",
            "tell me my application status",
            "what is my application status",
            "where is my application",
            "application reference",
        )
        return bool(self._extract_application_ref(lowered)) or any(
            marker in lowered for marker in personal_markers
        )

    def _looks_like_personal_failed_transaction(self, lowered: str) -> bool:
        transaction_words = ("transaction", "card charge", "payment")
        if not any(word in lowered for word in transaction_words):
            return False
        if "fail" not in lowered and "declin" not in lowered and "pending" not in lowered:
            return False
        if self._is_general_transaction_faq(lowered):
            return False
        personal_markers = (
            "my card transaction failed",
            "my transaction failed",
            "check my transaction",
            "look up my transaction",
            "transaction id",
            "trx",
            "txn",
        )
        return bool(self._extract_transaction_id(lowered)) or any(
            marker in lowered for marker in personal_markers
        )

    def _is_general_application_status_faq(self, lowered: str) -> bool:
        return (
            any(lowered.startswith(prefix) for prefix in HOW_TO_PREFIXES)
            and ("application" in lowered and "status" in lowered)
            and (
                "how can i check" in lowered
                or "how do i check" in lowered
                or "status of my application" in lowered
                or "check the status of my application" in lowered
            )
        )

    def _is_general_transaction_faq(self, lowered: str) -> bool:
        return (
            any(lowered.startswith(prefix) for prefix in GENERAL_FAQ_PREFIXES)
            and ("transaction" in lowered or "payment" in lowered)
            and ("fail" in lowered or "declin" in lowered or "pending" in lowered)
        )

    def _extract_application_ref(self, message_text: str) -> str | None:
        for match in APPLICATION_REF_RE.finditer(message_text):
            candidate = re.sub(r"[^A-Z0-9-]", "", match.group(0).upper())
            if any(char.isdigit() for char in candidate):
                return candidate
        return None

    def _extract_transaction_id(self, message_text: str) -> str | None:
        blocked_words = {"TRANSACTION", "APPLICATION", "STATUS", "PENDING", "DECLINED"}
        for match in TRANSACTION_ID_RE.finditer(message_text):
            candidate = re.sub(r"[^A-Z0-9-]", "", match.group(0).upper())
            if candidate in blocked_words:
                continue
            if any(char.isdigit() for char in candidate):
                return candidate
        return None

    def _handle_pending_application(self, message_text: str) -> ReplyResult:
        application_ref = self._extract_application_ref(message_text)
        if not application_ref:
            return ReplyResult(
                intent="application_status",
                message="I still need your application reference number before I can look up the status.",
                citations=[],
                needs_followup=True,
                followup_field="application_reference",
                pending_action="application_status",
            )
        return self._application_status_reply(application_ref)

    def _handle_pending_transaction(self, message_text: str) -> ReplyResult:
        transaction_id = self._extract_transaction_id(message_text)
        if not transaction_id:
            return ReplyResult(
                intent="failed_transaction",
                message="I still need the transaction ID to check the failed transaction status.",
                citations=[],
                needs_followup=True,
                followup_field="transaction_id",
                pending_action="failed_transaction",
            )
        return self._transaction_status_reply(transaction_id)

    def _application_status_reply(self, application_ref: str) -> ReplyResult:
        result = get_application_status(application_ref)
        return ReplyResult(
            intent="application_status",
            message=(
                f"Application reference `{result['reference_id']}` is currently `{result['status']}`. "
                f"{result['detail']}"
            ),
            citations=[],
            pending_action=None,
        )

    def _transaction_status_reply(self, transaction_id: str) -> ReplyResult:
        result = get_card_transaction_status(transaction_id)
        return ReplyResult(
            intent="failed_transaction",
            message=(
                f"Transaction `{result['reference_id']}` is currently `{result['status']}`. "
                f"{result['detail']}"
            ),
            citations=[],
            pending_action=None,
        )


runtime_service = RuntimeService()
