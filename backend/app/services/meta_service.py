from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    Agent,
    AgentBlueprint,
    AgentRevision,
    MetaDocument,
    MetaMessage,
    MetaSession,
)
from app.services.gemini_service import gemini_service
from app.services.source_service import ParsedDocument, source_service
from app.utils.text import normalize_whitespace, sha256_text


ALLOWED_BLUEPRINT_TOOLS = {"application_status", "failed_transaction", "support_handoff"}
DEFAULT_RESPONSE_STYLE = "Be friendly, concise, and practical."
DEFAULT_ALLOWED_SCOPE = "Answer only from the uploaded documents in the draft workspace."
DEFAULT_FALLBACK_BEHAVIOR = (
    "If the answer is not supported by the current knowledge, say that clearly."
)


def _coerce_blueprint_text(value: Any, fallback: str = "") -> str:
    if value is None:
        value = fallback
    if isinstance(value, str):
        return normalize_whitespace(value)
    if isinstance(value, dict):
        parts = []
        for item in value.values():
            coerced = _coerce_blueprint_text(item)
            if coerced:
                parts.append(coerced)
        return normalize_whitespace("\n".join(parts))
    if isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            coerced = _coerce_blueprint_text(item)
            if coerced:
                parts.append(coerced)
        return normalize_whitespace("\n".join(parts))
    return normalize_whitespace(str(value))


def _coerce_enabled_tools(value: Any) -> list[str]:
    if value is None:
        raw_items: list[Any] = []
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    tools: list[str] = []
    for item in raw_items:
        tool_name = _coerce_blueprint_text(item)
        if tool_name in ALLOWED_BLUEPRINT_TOOLS and tool_name not in tools:
            tools.append(tool_name)
    return tools or ["support_handoff"]


def _truncate_preview(text: str, max_chars: int = 220) -> str:
    normalized = normalize_whitespace(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rsplit(" ", 1)[0]


class MetaService:
    def _default_draft_spec(
        self,
        *,
        target_agent: Agent | None = None,
        target_revision: AgentRevision | None = None,
    ) -> dict[str, Any]:
        instruction_bundle = {}
        if target_revision:
            instruction_bundle = dict((target_revision.payload_json or {}).get("instruction_bundle") or {})
        behavior_instructions = _coerce_blueprint_text(
            instruction_bundle.get("behavior_instructions"), target_revision.additional_guidelines if target_revision else ""
        )
        response_style = _coerce_blueprint_text(
            instruction_bundle.get("response_style"), DEFAULT_RESPONSE_STYLE
        )
        allowed_scope = _coerce_blueprint_text(
            instruction_bundle.get("allowed_scope"), DEFAULT_ALLOWED_SCOPE
        )
        fallback_behavior = _coerce_blueprint_text(
            instruction_bundle.get("fallback_behavior"), DEFAULT_FALLBACK_BEHAVIOR
        )
        knowledge_summary = _coerce_blueprint_text(
            target_revision.source_summary if target_revision else "", ""
        )
        name = normalize_whitespace(target_agent.name if target_agent else "") or "Manager-Built Support Agent"
        description = normalize_whitespace(target_agent.description if target_agent else "")
        draft = {
            "name": name,
            "description": description
            or "A grounded customer support agent created through the manager workspace.",
            "behavior_instructions": behavior_instructions
            or "Answer only from verified knowledge and cite the supporting sources.",
            "response_style": response_style,
            "allowed_scope": allowed_scope,
            "fallback_behavior": fallback_behavior,
            "knowledge_summary": knowledge_summary,
            "open_questions": [],
            "status": "draft",
        }
        return self._finalize_draft(draft, [])

    def _summarize_documents(self, documents: list[ParsedDocument]) -> list[str]:
        return [f"{document.title}: {_truncate_preview(document.content)}" for document in documents[:8]]

    def _meta_documents_as_parsed(self, meta_documents: list[MetaDocument]) -> list[ParsedDocument]:
        return [
            ParsedDocument(
                title=document.title,
                content=document.content,
                source_type=document.source_type,
                filename=document.filename,
                mime_type=document.mime_type,
                payload_json=dict(document.payload_json or {}),
            )
            for document in meta_documents
        ]

    def _infer_response_style(self, message_text: str, current_value: str) -> str:
        lowered = message_text.lower()
        detected: list[str] = []
        if "formal" in lowered:
            detected.append("formal")
        if "friendly" in lowered or "warm" in lowered:
            detected.append("friendly")
        if "concise" in lowered or "brief" in lowered or "short" in lowered:
            detected.append("concise")
        if "step by step" in lowered:
            detected.append("step-by-step")
        if not detected:
            return current_value
        return normalize_whitespace(
            f"Be {' and '.join(detected)} when you answer customers."
        )

    def _finalize_draft(
        self,
        draft_spec: dict[str, Any],
        document_summaries: list[str],
    ) -> dict[str, Any]:
        knowledge_summary = _coerce_blueprint_text(
            draft_spec.get("knowledge_summary"), ""
        ) or "\n".join(document_summaries[:5])
        draft = {
            "name": _coerce_blueprint_text(
                draft_spec.get("name"), "Manager-Built Support Agent"
            )
            or "Manager-Built Support Agent",
            "description": _coerce_blueprint_text(
                draft_spec.get("description"),
                "A grounded customer support agent created through the manager workspace.",
            )
            or "A grounded customer support agent created through the manager workspace.",
            "behavior_instructions": _coerce_blueprint_text(
                draft_spec.get("behavior_instructions"),
                "Answer only from verified knowledge and cite the supporting sources.",
            )
            or "Answer only from verified knowledge and cite the supporting sources.",
            "response_style": _coerce_blueprint_text(
                draft_spec.get("response_style"), DEFAULT_RESPONSE_STYLE
            )
            or DEFAULT_RESPONSE_STYLE,
            "allowed_scope": _coerce_blueprint_text(
                draft_spec.get("allowed_scope"), DEFAULT_ALLOWED_SCOPE
            )
            or DEFAULT_ALLOWED_SCOPE,
            "fallback_behavior": _coerce_blueprint_text(
                draft_spec.get("fallback_behavior"), DEFAULT_FALLBACK_BEHAVIOR
            )
            or DEFAULT_FALLBACK_BEHAVIOR,
            "knowledge_summary": knowledge_summary,
        }

        open_questions: list[str] = []
        if not draft["behavior_instructions"]:
            open_questions.append("What core instructions should the customer-facing agent follow?")
        if not draft["knowledge_summary"] and not document_summaries:
            open_questions.append("Upload at least one knowledge document or describe the knowledge scope.")
        if "cite" not in draft["behavior_instructions"].lower():
            open_questions.append("Should the agent always include citations when it answers from knowledge?")
        if "unsupported" not in draft["fallback_behavior"].lower() and "not supported" not in draft["fallback_behavior"].lower():
            open_questions.append("How should the agent respond when the docs do not support an answer?")

        draft["open_questions"] = open_questions[:3]
        draft["status"] = "ready_to_generate" if not open_questions[:2] else "draft"
        return draft

    def _build_assistant_reply(
        self,
        draft_spec: dict[str, Any],
        *,
        manager_message: str | None = None,
        documents_added: int = 0,
    ) -> str:
        highlights = []
        if manager_message:
            highlights.append("I updated the draft based on your latest instructions.")
        if documents_added:
            highlights.append(f"I added {documents_added} document(s) to the draft workspace.")
        highlights.append(f"Draft status: {draft_spec.get('status', 'draft')}.")
        if draft_spec.get("knowledge_summary"):
            highlights.append(f"Knowledge summary: {draft_spec['knowledge_summary']}")
        if draft_spec.get("open_questions"):
            return normalize_whitespace(
                " ".join(highlights)
                + " Open questions: "
                + " ".join(draft_spec["open_questions"])
            )
        return normalize_whitespace(
            " ".join(highlights)
            + " The draft looks complete enough to generate or update an agent."
        )

    def _merge_draft_with_message(
        self,
        current_draft: dict[str, Any],
        manager_message: str,
        document_summaries: list[str],
        model_patch: dict[str, Any] | None,
    ) -> dict[str, Any]:
        message_text = normalize_whitespace(manager_message)
        merged = dict(current_draft or {})
        model_draft = dict((model_patch or {}).get("draft_spec") or {})

        merged["name"] = _coerce_blueprint_text(
            model_draft.get("name"), merged.get("name", "")
        ) or merged.get("name", "")
        merged["description"] = _coerce_blueprint_text(
            model_draft.get("description"), merged.get("description", "")
        ) or merged.get("description", "")

        existing_behavior = _coerce_blueprint_text(merged.get("behavior_instructions"), "")
        model_behavior = _coerce_blueprint_text(model_draft.get("behavior_instructions"), "")
        if model_behavior:
            merged["behavior_instructions"] = model_behavior
        elif message_text:
            merged["behavior_instructions"] = normalize_whitespace(
                f"{existing_behavior}\n{message_text}".strip()
            )

        merged["response_style"] = _coerce_blueprint_text(
            model_draft.get("response_style"),
            self._infer_response_style(message_text, merged.get("response_style", DEFAULT_RESPONSE_STYLE)),
        )
        merged["allowed_scope"] = _coerce_blueprint_text(
            model_draft.get("allowed_scope"), merged.get("allowed_scope", DEFAULT_ALLOWED_SCOPE)
        )
        merged["fallback_behavior"] = _coerce_blueprint_text(
            model_draft.get("fallback_behavior"),
            merged.get("fallback_behavior", DEFAULT_FALLBACK_BEHAVIOR),
        )
        merged["knowledge_summary"] = _coerce_blueprint_text(
            model_draft.get("knowledge_summary"),
            merged.get("knowledge_summary") or "\n".join(document_summaries[:5]),
        )
        merged["open_questions"] = model_draft.get("open_questions") or merged.get("open_questions", [])
        merged["status"] = model_draft.get("status") or merged.get("status", "draft")
        return self._finalize_draft(merged, document_summaries)

    def _build_instruction_bundle(self, draft_spec: dict[str, Any]) -> dict[str, str]:
        return source_service.build_instruction_bundle(
            behavior_instructions=_coerce_blueprint_text(draft_spec.get("behavior_instructions"), ""),
            response_style=_coerce_blueprint_text(draft_spec.get("response_style"), DEFAULT_RESPONSE_STYLE),
            allowed_scope=_coerce_blueprint_text(draft_spec.get("allowed_scope"), DEFAULT_ALLOWED_SCOPE),
            fallback_behavior=_coerce_blueprint_text(
                draft_spec.get("fallback_behavior"), DEFAULT_FALLBACK_BEHAVIOR
            ),
        )

    def _build_blueprint_from_draft(
        self,
        draft_spec: dict[str, Any],
        document_summaries: list[str],
    ) -> AgentBlueprint:
        blueprint_data = gemini_service.create_blueprint(
            agent_name=_coerce_blueprint_text(
                draft_spec.get("name"), "Manager-Built Support Agent"
            ),
            description=_coerce_blueprint_text(draft_spec.get("description"), ""),
            instructions=_coerce_blueprint_text(
                draft_spec.get("behavior_instructions"), ""
            ),
            document_summaries=document_summaries,
        )
        generated_agent_tools = ["support_handoff"]
        return AgentBlueprint(
            name=_coerce_blueprint_text(
                blueprint_data.get("name"), draft_spec.get("name", "Manager-Built Support Agent")
            )
            or "Manager-Built Support Agent",
            description=_coerce_blueprint_text(
                blueprint_data.get("description"), draft_spec.get("description", "")
            )
            or draft_spec.get("description", ""),
            instructions=_coerce_blueprint_text(
                blueprint_data.get("instructions"), draft_spec.get("behavior_instructions", "")
            )
            or draft_spec.get("behavior_instructions", ""),
            knowledge_summary=_coerce_blueprint_text(
                blueprint_data.get("knowledge_summary"),
                draft_spec.get("knowledge_summary", "\n".join(document_summaries[:5])),
            )
            or draft_spec.get("knowledge_summary", "\n".join(document_summaries[:5])),
            enabled_tools_json=generated_agent_tools,
        )

    def _sync_generated_revision(
        self,
        db: Session,
        revision: AgentRevision,
        *,
        draft_spec: dict[str, Any],
        documents: list[ParsedDocument],
    ) -> None:
        parsed_documents: list[ParsedDocument] = []
        sync_mode = "manager_input"

        if documents:
            parsed_documents.extend(documents)
            sync_mode = "upload"
        elif not parsed_documents:
            parsed_documents.append(
                ParsedDocument(
                    title="Manager brief",
                    content=_coerce_blueprint_text(
                        draft_spec.get("knowledge_summary"),
                        draft_spec.get("behavior_instructions", ""),
                    )
                    or "Answer only from manager-provided knowledge.",
                    source_type="manager_input",
                    filename="manager-brief.txt",
                    mime_type="text/plain",
                    payload_json={
                        "sync_mode": "manager_input",
                        "provenance": "manager_draft",
                    },
                )
            )

        documents_synced = 0
        chunks_synced = 0
        uploaded_documents_synced = 0
        for document in parsed_documents:
            chunk_count = source_service.add_document(db, revision, document)
            if chunk_count == 0:
                continue
            documents_synced += 1
            chunks_synced += chunk_count
            if document.source_type != "manager_input":
                uploaded_documents_synced += 1

        revision.sync_status = "ready"
        revision.knowledge_base_url = None
        if uploaded_documents_synced:
            revision.source_summary = (
                f"Indexed {documents_synced} documents and {chunks_synced} chunks from manager-provided files."
            )
        else:
            revision.source_summary = (
                f"Indexed {documents_synced} documents and {chunks_synced} chunks from manager instructions."
            )

        payload = dict(revision.payload_json or {})
        payload.update(
            {
                "sync_mode": sync_mode,
                "fallback_used": False,
                "documents_synced": documents_synced,
                "chunks_synced": chunks_synced,
                "last_sync_warning": None,
                "last_sync_source_url": None,
            }
        )
        revision.payload_json = payload

    def _create_generated_agent(
        self,
        db: Session,
        *,
        draft_spec: dict[str, Any],
        documents: list[ParsedDocument],
    ) -> tuple[AgentBlueprint, Agent]:
        document_summaries = self._summarize_documents(documents)
        finalized_draft = self._finalize_draft(draft_spec, document_summaries)
        blueprint = self._build_blueprint_from_draft(finalized_draft, document_summaries)
        db.add(blueprint)
        db.flush()

        agent = Agent(
            name=blueprint.name,
            description=blueprint.description,
            role="generated",
        )
        db.add(agent)
        db.flush()

        instruction_bundle = self._build_instruction_bundle(finalized_draft)
        revision = AgentRevision(
            agent_id=agent.id,
            version=1,
            knowledge_base_url=None,
            additional_guidelines=instruction_bundle["behavior_instructions"],
            system_prompt=source_service.build_system_prompt(agent.name),
            status="published",
            sync_status="syncing",
            source_summary=blueprint.knowledge_summary,
            payload_json={
                "enabled_tools": ["support_handoff"],
                "instruction_bundle": instruction_bundle,
            },
        )
        db.add(revision)
        db.flush()
        agent.active_revision_id = revision.id
        blueprint.created_agent_id = agent.id
        db.flush()

        self._sync_generated_revision(
            db,
            revision,
            draft_spec=finalized_draft,
            documents=documents,
        )
        db.commit()
        db.refresh(blueprint)
        db.refresh(agent)
        return blueprint, agent

    def create_session(self, db: Session, *, target_agent_id: str | None = None) -> MetaSession:
        target_agent = db.get(Agent, target_agent_id) if target_agent_id else None
        target_revision = None
        if target_agent and target_agent.active_revision_id:
            target_revision = db.get(AgentRevision, target_agent.active_revision_id)
        session = MetaSession(
            status="draft",
            target_agent_id=target_agent.id if target_agent else None,
            draft_spec_json=self._default_draft_spec(
                target_agent=target_agent, target_revision=target_revision
            ),
        )
        db.add(session)
        db.flush()
        db.add(
            MetaMessage(
                session_id=session.id,
                role="assistant",
                content=(
                    "Upload source documents or tell me how the customer-facing agent should behave. "
                    "I will keep the draft spec updated as we go."
                ),
            )
        )
        db.commit()
        db.refresh(session)
        return session

    def get_session(self, db: Session, session_id: str) -> MetaSession:
        session = db.get(MetaSession, session_id)
        if not session:
            raise ValueError("Meta session not found.")
        return session

    def add_session_message(
        self,
        db: Session,
        *,
        session_id: str,
        message: str,
    ) -> MetaSession:
        session = self.get_session(db, session_id)
        manager_message = normalize_whitespace(message)
        db.add(MetaMessage(session_id=session.id, role="manager", content=manager_message))
        db.flush()
        db.refresh(session)

        parsed_documents = self._meta_documents_as_parsed(session.documents)
        document_summaries = self._summarize_documents(parsed_documents)
        model_patch = gemini_service.plan_meta_agent_turn(
            current_draft=dict(session.draft_spec_json or {}),
            document_summaries=document_summaries,
            history=[
                {"role": meta_message.role, "content": meta_message.content}
                for meta_message in session.messages[-8:]
            ],
            manager_message=manager_message,
        )
        updated_draft = self._merge_draft_with_message(
            dict(session.draft_spec_json or {}),
            manager_message,
            document_summaries,
            model_patch,
        )
        session.draft_spec_json = updated_draft
        session.status = updated_draft["status"]
        assistant_reply = _coerce_blueprint_text(
            model_patch.get("assistant_reply") if model_patch else "",
            "",
        )
        if not assistant_reply:
            assistant_reply = self._build_assistant_reply(updated_draft, manager_message=manager_message)
        db.add(
            MetaMessage(
                session_id=session.id,
                role="assistant",
                content=assistant_reply,
                payload_json={"draft_status": updated_draft["status"]},
            )
        )
        db.commit()
        db.refresh(session)
        return session

    def upload_session_documents(
        self,
        db: Session,
        *,
        session_id: str,
        documents: list[ParsedDocument],
    ) -> MetaSession:
        session = self.get_session(db, session_id)
        added_count = 0
        for document in documents:
            checksum = sha256_text(f"{document.title}\n{document.content}\n{document.filename or ''}")
            existing = next(
                (
                    session_document
                    for session_document in session.documents
                    if session_document.checksum == checksum
                ),
                None,
            )
            if existing:
                continue
            db.add(
                MetaDocument(
                    session_id=session.id,
                    title=document.title[:500],
                    source_type=document.source_type,
                    filename=document.filename,
                    mime_type=document.mime_type,
                    content=document.content,
                    checksum=checksum,
                    payload_json=dict(document.payload_json or {}),
                )
            )
            added_count += 1
        db.flush()
        db.refresh(session)

        parsed_documents = self._meta_documents_as_parsed(session.documents)
        updated_draft = self._finalize_draft(
            dict(session.draft_spec_json or {}),
            self._summarize_documents(parsed_documents),
        )
        session.draft_spec_json = updated_draft
        session.status = updated_draft["status"]
        db.add(
            MetaMessage(
                session_id=session.id,
                role="assistant",
                content=self._build_assistant_reply(updated_draft, documents_added=added_count),
                payload_json={"draft_status": updated_draft["status"]},
            )
        )
        db.commit()
        db.refresh(session)
        return session

    def update_session_draft(
        self,
        db: Session,
        *,
        session_id: str,
        draft_patch: dict[str, Any],
    ) -> MetaSession:
        session = self.get_session(db, session_id)
        merged_draft = dict(session.draft_spec_json or {})

        for key in (
            "name",
            "description",
            "behavior_instructions",
            "response_style",
            "allowed_scope",
            "fallback_behavior",
        ):
            if key not in draft_patch or draft_patch[key] is None:
                continue
            merged_draft[key] = _coerce_blueprint_text(
                draft_patch[key],
                merged_draft.get(key, ""),
            )

        parsed_documents = self._meta_documents_as_parsed(session.documents)
        updated_draft = self._finalize_draft(
            merged_draft,
            self._summarize_documents(parsed_documents),
        )
        session.draft_spec_json = updated_draft
        session.status = updated_draft["status"]
        db.commit()
        db.refresh(session)
        return session

    def generate_session_agent(
        self,
        db: Session,
        *,
        session_id: str,
    ) -> tuple[MetaSession, AgentBlueprint, Agent]:
        session = self.get_session(db, session_id)
        parsed_documents = self._meta_documents_as_parsed(session.documents)
        blueprint, agent = self._create_generated_agent(
            db,
            draft_spec=dict(session.draft_spec_json or {}),
            documents=parsed_documents,
        )
        session.target_agent_id = agent.id
        session.created_agent_id = agent.id
        session.status = "generated"
        session.draft_spec_json = self._finalize_draft(
            dict(session.draft_spec_json or {}),
            self._summarize_documents(parsed_documents),
        )
        db.add(
            MetaMessage(
                session_id=session.id,
                role="assistant",
                content=f"I generated `{agent.name}`. You can now test it in customer view or review fixes in admin.",
                payload_json={"created_agent_id": agent.id},
            )
        )
        db.commit()
        db.refresh(session)
        db.refresh(agent)
        db.refresh(blueprint)
        return session, blueprint, agent

    def update_session_agent(
        self,
        db: Session,
        *,
        session_id: str,
        agent_id: str,
    ) -> tuple[MetaSession, AgentBlueprint, Agent]:
        session = self.get_session(db, session_id)
        agent = db.get(Agent, agent_id)
        if not agent:
            raise ValueError("Target agent not found.")
        if not agent.active_revision_id:
            raise ValueError("Target agent has no active revision.")
        current_revision = db.get(AgentRevision, agent.active_revision_id)
        if not current_revision:
            raise ValueError("Active revision not found.")

        parsed_documents = self._meta_documents_as_parsed(session.documents)
        draft_spec = self._finalize_draft(
            dict(session.draft_spec_json or {}),
            self._summarize_documents(parsed_documents),
        )
        blueprint = self._build_blueprint_from_draft(
            draft_spec, self._summarize_documents(parsed_documents)
        )
        db.add(blueprint)
        db.flush()

        agent.name = blueprint.name
        agent.description = blueprint.description
        instruction_bundle = self._build_instruction_bundle(draft_spec)
        new_revision = source_service.clone_revision(
            db,
            current_revision,
            additional_guidelines=instruction_bundle["behavior_instructions"],
            knowledge_base_url=None,
            system_prompt=source_service.build_system_prompt(agent.name),
        )
        payload = dict(new_revision.payload_json or {})
        payload.update(
            {
                "enabled_tools": ["support_handoff"],
                "instruction_bundle": instruction_bundle,
            }
        )
        new_revision.payload_json = payload
        new_revision.source_summary = blueprint.knowledge_summary
        for document in parsed_documents:
            source_service.add_document(db, new_revision, document)
        new_revision.sync_status = "ready"
        agent.active_revision_id = new_revision.id
        blueprint.created_agent_id = agent.id
        session.target_agent_id = agent.id
        session.created_agent_id = agent.id
        session.status = "generated"
        session.draft_spec_json = draft_spec
        db.add(
            MetaMessage(
                session_id=session.id,
                role="assistant",
                content=f"I updated `{agent.name}` and published a new revision for testing.",
                payload_json={"created_agent_id": agent.id, "revision_id": new_revision.id},
            )
        )
        db.commit()
        db.refresh(session)
        db.refresh(agent)
        db.refresh(blueprint)
        return session, blueprint, agent

    def generate_agent(
        self,
        db: Session,
        *,
        agent_name: str,
        description: str,
        instructions: str,
        documents: list[ParsedDocument],
    ) -> tuple[AgentBlueprint, Agent]:
        draft_spec = self._finalize_draft(
            {
                "name": agent_name,
                "description": description,
                "behavior_instructions": instructions,
                "response_style": DEFAULT_RESPONSE_STYLE,
                "allowed_scope": DEFAULT_ALLOWED_SCOPE,
                "fallback_behavior": DEFAULT_FALLBACK_BEHAVIOR,
                "knowledge_summary": "\n".join(self._summarize_documents(documents)[:5]),
            },
            self._summarize_documents(documents),
        )
        return self._create_generated_agent(
            db,
            draft_spec=draft_spec,
            documents=documents,
        )


meta_service = MetaService()
