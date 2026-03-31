from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Agent, AgentRevision, Conversation, Message
from app.schemas.api import ChatRequest
from app.services.gemini_service import gemini_service
from app.services.retrieval_service import RetrievedChunk, retrieval_service
from app.services.tool_service import get_application_status, get_card_transaction_status
from app.utils.text import normalize_whitespace, tokenize


APPLICATION_REF_RE = re.compile(r"\b(?:APP[- ]?)?[A-Z0-9]{6,}\b", re.IGNORECASE)
TRANSACTION_ID_RE = re.compile(
    r"\b(?:TXN?|TRX|TRANS(?:ACTION)?)[-:# ]*[A-Z0-9]{4,}\b|\b[A-Z0-9]{8,}\b",
    re.IGNORECASE,
)
CORRECTION_RE = re.compile(
    r"Required correction:\s*(.+?)(?:\s+Apply this correction|\s+This correction is authoritative|$)",
    re.IGNORECASE,
)
ANSWER_ADDITION_RE = re.compile(
    r"Answer addition:\s*(.+?)(?:\s+Use this answer addition|\s+Do not repeat the correction|$)",
    re.IGNORECASE,
)
CORRECTION_PREFIX_RE = re.compile(
    r"^(?:(?:please|kindly)\s+)?"
    r"(?:(?:the\s+)?(?:answer|response|reply|bot)\s+should\s+)?"
    r"(?:(?:also\s+)?(?:mention|include|add|state|clarify|note|say|explain))(?:\s+that)?\s+",
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
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\s*\n+\s*")
FALLBACK_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "do",
    "does",
    "for",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "my",
    "of",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "why",
    "you",
}


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
        normalized_content = normalize_whitespace(content)
        match = ANSWER_ADDITION_RE.search(normalized_content)
        if match:
            return self._normalize_correction_sentence(match.group(1))
        legacy_match = CORRECTION_RE.search(normalized_content)
        if legacy_match:
            return self._normalize_correction_sentence(legacy_match.group(1))
        return None

    def _normalize_correction_sentence(self, text: str) -> str:
        normalized = CORRECTION_PREFIX_RE.sub("", normalize_whitespace(text)).strip()
        if normalized.lower().startswith("that "):
            normalized = normalized[5:].strip()
        if normalized and normalized[-1] not in ".!?":
            normalized += "."
        if normalized:
            normalized = normalized[0].upper() + normalized[1:]
        return normalized

    def _summarize_content(self, content: str, max_chars: int = 220) -> str:
        normalized = normalize_whitespace(content)
        if len(normalized) <= max_chars:
            return normalized.rstrip(". ")
        sentence_end = normalized.rfind(". ", 0, max_chars)
        if sentence_end > max_chars // 2:
            return normalized[: sentence_end + 1].rstrip(". ")
        return normalized[:max_chars].rsplit(" ", 1)[0].rstrip(". ")

    def _truncate_for_citation(self, content: str, max_chars: int) -> str:
        normalized = normalize_whitespace(content)
        if len(normalized) <= max_chars:
            return normalized
        truncated = normalized[:max_chars]
        if " " not in truncated:
            return truncated
        return truncated.rsplit(" ", 1)[0]

    def _split_into_sentences(self, content: str) -> list[str]:
        return [
            normalize_whitespace(part)
            for part in SENTENCE_SPLIT_RE.split(content or "")
            if normalize_whitespace(part)
        ]

    def _strip_title_prefix(self, sentence: str, title: str | None) -> str:
        normalized_sentence = normalize_whitespace(sentence)
        normalized_title = normalize_whitespace(title or "").rstrip(":.- ")
        if not normalized_title:
            return normalized_sentence

        lowered_sentence = normalized_sentence.lower()
        lowered_title = normalized_title.lower()
        if lowered_sentence == lowered_title:
            return ""

        for separator in (": ", " - ", " ", "\n"):
            prefix = f"{normalized_title}{separator}"
            if lowered_sentence.startswith(prefix.lower()):
                return normalize_whitespace(normalized_sentence[len(prefix) :])
        return normalized_sentence

    def _is_heading_like_sentence(self, sentence: str) -> bool:
        if any(mark in sentence for mark in ".!?"):
            return False
        tokens = tokenize(sentence)
        return 0 < len(tokens) <= 6

    def _strip_heading_prefix(self, sentence: str) -> str:
        words = sentence.split()
        uppercase_prefix_count = 0
        for word in words:
            alpha = next((char for char in word if char.isalpha()), "")
            if alpha and alpha.isupper():
                uppercase_prefix_count += 1
                continue
            break
        if uppercase_prefix_count >= 3:
            return " ".join(words[uppercase_prefix_count - 1 :])
        return sentence

    def _prepare_sentence_candidate(self, sentence: str, title: str | None) -> str:
        cleaned = self._strip_heading_prefix(self._strip_title_prefix(sentence, title))
        if not cleaned or self._is_heading_like_sentence(cleaned):
            return ""
        return cleaned

    def _meaningful_query_tokens(self, message_text: str) -> list[str]:
        return [
            token
            for token in tokenize(message_text)
            if token not in FALLBACK_STOPWORDS and (len(token) > 2 or token.isdigit())
        ]

    def _query_phrases(self, query_tokens: list[str]) -> set[str]:
        return {
            f"{query_tokens[index]} {query_tokens[index + 1]}"
            for index in range(len(query_tokens) - 1)
        }

    def _sentence_match_score(
        self,
        sentence: str,
        *,
        query_tokens: list[str],
        query_phrases: set[str],
    ) -> float:
        sentence_tokens = tokenize(sentence)
        if not sentence_tokens:
            return 0.0
        sentence_token_set = set(sentence_tokens)
        score = 0.0
        for token in query_tokens:
            if token in sentence_token_set:
                score += 2.0
                score += min(sentence_tokens.count(token) - 1, 1) * 0.35
        lowered_sentence = sentence.lower()
        for phrase in query_phrases:
            if phrase in lowered_sentence:
                score += 2.5
        if len(sentence) <= 180:
            score += 0.25
        return score

    def _should_include_second_sentence(
        self,
        message_text: str,
        *,
        first_score: float,
        second_score: float,
        first_sentence: str,
    ) -> bool:
        lowered = normalize_whitespace(message_text.lower())
        if lowered.startswith(("how ", "what happens", "where ", "when ", "can i ", "do i ", "does ")):
            return False
        if len(first_sentence) < 72 and second_score >= max(2.0, first_score * 0.8):
            return True
        return second_score >= max(3.5, first_score * 0.9)

    def _select_sentence_level_candidates(
        self,
        *,
        message_text: str,
        labeled_items: list[tuple[int, RetrievedChunk]],
    ) -> list[dict]:
        query_tokens = self._meaningful_query_tokens(message_text)
        if not query_tokens:
            return []
        query_phrases = self._query_phrases(query_tokens)
        candidates: list[dict] = []
        for label_index, item in labeled_items:
            for sentence in self._split_into_sentences(item.content):
                cleaned_sentence = self._prepare_sentence_candidate(sentence, item.title)
                if not cleaned_sentence:
                    continue
                score = self._sentence_match_score(
                    cleaned_sentence,
                    query_tokens=query_tokens,
                    query_phrases=query_phrases,
                )
                if score <= 0:
                    continue
                candidates.append(
                    {
                        "label": label_index,
                        "sentence": cleaned_sentence,
                        "score": score,
                    }
                )
        if not candidates:
            return []
        candidates.sort(key=lambda item: (-item["score"], len(item["sentence"]), item["label"]))

        selected = [candidates[0]]
        if len(candidates) > 1 and self._should_include_second_sentence(
            message_text,
            first_score=candidates[0]["score"],
            second_score=candidates[1]["score"],
            first_sentence=candidates[0]["sentence"],
        ):
            second_candidate = candidates[1]
            if second_candidate["sentence"].lower() != candidates[0]["sentence"].lower():
                selected.append(second_candidate)
        return selected

    def _format_sentence_level_answer(
        self,
        selected: list[dict],
        *,
        instruction_bundle: dict[str, str],
    ) -> str | None:
        if not selected:
            return None
        response = " ".join(
            f"{candidate['sentence'].rstrip('. ')}. [{candidate['label']}]"
            for candidate in selected
        )
        if "formal" in instruction_bundle.get("response_style", "").lower():
            return f"Verified guidance: {response}"
        return response

    def _compose_sentence_level_fallback(
        self,
        *,
        message_text: str,
        prioritized: list[RetrievedChunk],
        instruction_bundle: dict[str, str],
    ) -> str | None:
        selected = self._select_sentence_level_candidates(
            message_text=message_text,
            labeled_items=list(enumerate(prioritized[:3], start=1)),
        )
        return self._format_sentence_level_answer(selected, instruction_bundle=instruction_bundle)

    def _render_fallback_message(self, instruction_bundle: dict[str, str]) -> str:
        fallback_behavior = normalize_whitespace(
            instruction_bundle.get("fallback_behavior")
            or "The current documents do not confirm that."
        )
        lowered = fallback_behavior.lower()

        if fallback_behavior and not lowered.startswith(("if ", "when ", "say ", "respond ", "ask ", "tell ")):
            message = fallback_behavior
        else:
            message = "The current documents do not confirm that."

        if "contact support" in lowered and "contact support" not in message.lower():
            message = f"{message.rstrip('. ')}. Please contact support for more help."
        return message

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
            supports_lookup_tools=self._supports_lookup_tools(agent),
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
        agent = db.get(Agent, revision.agent_id)
        return self.generate_reply(
            db,
            revision=revision,
            message_text=normalize_whitespace(prompt_text),
            history=[],
            pending_action=None,
            pending_payload={},
            supports_lookup_tools=self._supports_lookup_tools(agent),
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
        supports_lookup_tools: bool,
    ) -> ReplyResult:
        if supports_lookup_tools and pending_action == "application_status":
            return self._handle_pending_application(message_text)
        if supports_lookup_tools and pending_action == "failed_transaction":
            return self._handle_pending_transaction(message_text)

        intent = self._classify_intent(message_text, supports_lookup_tools)
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
            instruction_bundle = self._get_instruction_bundle(revision)
            return ReplyResult(
                intent="unknown",
                message=self._render_fallback_message(instruction_bundle),
                citations=[],
            )
        answer = self._answer_from_retrieval(revision, message_text, history, retrieved)
        citations = self._build_citations(retrieved)
        return ReplyResult(intent="kb_qa", message=answer, citations=citations)

    def _supports_lookup_tools(self, agent: Agent | None) -> bool:
        return bool(agent and agent.role == "support")

    def _get_instruction_bundle(self, revision: AgentRevision) -> dict[str, str]:
        payload = dict(revision.payload_json or {})
        bundle = dict(payload.get("instruction_bundle") or {})
        if bundle:
            return bundle
        return {
            "behavior_instructions": normalize_whitespace(revision.additional_guidelines),
            "response_style": "Be friendly, concise, and practical.",
            "allowed_scope": "Answer only from the uploaded or synced knowledge sources.",
            "fallback_behavior": "If the answer is not supported by the current knowledge, say that clearly.",
            "citation_policy": "Use inline citations that match the retrieved sources.",
        }

    def _answer_from_retrieval(
        self,
        revision: AgentRevision,
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
        instruction_bundle = self._get_instruction_bundle(revision)
        agent = revision.agent
        model_answer = gemini_service.answer_kb_from_context(
            user_message=message_text,
            history=history,
            context_blocks=context_blocks,
            agent_name=agent.name if agent else "customer service assistant",
            instruction_bundle=instruction_bundle,
        )
        if model_answer:
            return model_answer

        correction_item = next((item for item in prioritized if item.source_type == "correction"), None)
        supporting_item = next((item for item in prioritized if item.source_type != "correction"), None)
        if correction_item:
            correction_text = self._extract_correction_sentence(correction_item.content)
            if correction_text:
                correction_label = prioritized.index(correction_item) + 1
                support_answer = self._format_sentence_level_answer(
                    self._select_sentence_level_candidates(
                        message_text=message_text,
                        labeled_items=[
                            (index + 1, item)
                            for index, item in enumerate(prioritized[:4])
                            if item.source_type != "correction"
                        ],
                    ),
                    instruction_bundle=instruction_bundle,
                )
                if support_answer:
                    if correction_text.lower() in support_answer.lower():
                        return support_answer
                    return f"{support_answer} {correction_text.rstrip('. ')}. [{correction_label}]"
                if supporting_item:
                    support_label = prioritized.index(supporting_item) + 1
                    support_snippet = self._summarize_content(
                        self._strip_title_prefix(supporting_item.content, supporting_item.title)
                    )
                    return (
                        f"{support_snippet}. [{support_label}] "
                        f"{correction_text.rstrip('. ')}. [{correction_label}]"
                    )
                return f"{correction_text.rstrip('. ')}. [{correction_label}]"

        sentence_level_answer = self._compose_sentence_level_fallback(
            message_text=message_text,
            prioritized=prioritized,
            instruction_bundle=instruction_bundle,
        )
        if sentence_level_answer:
            return sentence_level_answer
        return self._render_fallback_message(instruction_bundle)

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
                    "snippet": self._truncate_for_citation(item.content, 220),
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

    def _classify_intent(self, message_text: str, supports_lookup_tools: bool) -> str:
        lowered = normalize_whitespace(message_text.lower())
        if supports_lookup_tools and self._looks_like_personal_application_status(lowered):
            return "application_status"
        if supports_lookup_tools and self._looks_like_personal_failed_transaction(lowered):
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
