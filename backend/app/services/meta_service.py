from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Agent, AgentBlueprint, AgentRevision
from app.services.gemini_service import gemini_service
from app.services.source_service import ParsedDocument, source_service
from app.utils.text import normalize_whitespace


ALLOWED_BLUEPRINT_TOOLS = {"application_status", "failed_transaction", "support_handoff"}


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


class MetaService:
    def generate_agent(
        self,
        db: Session,
        *,
        agent_name: str,
        description: str,
        instructions: str,
        knowledge_base_url: str | None,
        documents: list[ParsedDocument],
    ) -> tuple[AgentBlueprint, Agent]:
        document_summaries = [
            f"{document.title}: {document.content[:220]}" for document in documents[:8]
        ]
        blueprint_data = gemini_service.create_blueprint(
            agent_name=agent_name,
            description=description,
            instructions=instructions,
            document_summaries=document_summaries,
        )
        fallback_description = description or f"{agent_name} support agent"
        fallback_summary = "\n".join(document_summaries)
        generated_agent_tools = ["support_handoff"]

        blueprint = AgentBlueprint(
            name=_coerce_blueprint_text(blueprint_data.get("name"), agent_name) or agent_name,
            description=_coerce_blueprint_text(
                blueprint_data.get("description"), fallback_description
            ),
            instructions=_coerce_blueprint_text(
                blueprint_data.get("instructions"), instructions
            )
            or instructions,
            knowledge_summary=_coerce_blueprint_text(
                blueprint_data.get("knowledge_summary"), fallback_summary
            )
            or fallback_summary,
            enabled_tools_json=generated_agent_tools,
        )
        db.add(blueprint)
        db.flush()

        agent = Agent(
            name=blueprint.name,
            description=blueprint.description,
            role="generated",
        )
        db.add(agent)
        db.flush()

        normalized_kb_url = (knowledge_base_url or "").strip() or None

        revision = AgentRevision(
            agent_id=agent.id,
            version=1,
            knowledge_base_url=normalized_kb_url,
            additional_guidelines=blueprint.instructions,
            system_prompt=source_service.build_system_prompt(agent.name),
            status="published",
            sync_status="syncing",
            source_summary=blueprint.knowledge_summary,
            payload_json={"enabled_tools": generated_agent_tools},
        )
        db.add(revision)
        db.flush()
        agent.active_revision_id = revision.id
        blueprint.created_agent_id = agent.id
        db.flush()

        parsed_documents: list[ParsedDocument] = []
        last_sync_warning: str | None = None
        sync_mode = "manager_input"

        if normalized_kb_url:
            try:
                parsed_documents.extend(source_service.parse_public_help_center(normalized_kb_url))
                sync_mode = "live_api"
            except Exception as exc:
                last_sync_warning = (
                    "Live Zendesk KB sync failed during manager creation; "
                    "the agent was created using uploaded files or manager instructions instead. "
                    f"Reason: {exc.__class__.__name__}"
                )

        if documents:
            parsed_documents.extend(documents)
            if sync_mode != "live_api":
                sync_mode = "upload"
        elif not parsed_documents:
            parsed_documents.append(
                ParsedDocument(
                    title="Manager instructions",
                    content=blueprint.instructions
                    or instructions
                    or "Answer only from manager-provided knowledge.",
                    source_type="manager_input",
                    filename="manager-instructions.txt",
                    mime_type="text/plain",
                    payload_json={
                        "sync_mode": "manager_input",
                        "provenance": "manager_instructions",
                    },
                )
            )

        documents_synced = 0
        chunks_synced = 0
        live_documents_synced = 0
        uploaded_documents_synced = 0
        manager_documents_synced = 0
        for document in parsed_documents:
            chunk_count = source_service.add_document(db, revision, document)
            if chunk_count == 0:
                continue
            documents_synced += 1
            chunks_synced += chunk_count
            if document.source_type == "kb_article":
                live_documents_synced += 1
            elif document.source_type == "manager_input":
                manager_documents_synced += 1
            else:
                uploaded_documents_synced += 1

        revision.sync_status = "ready"
        if live_documents_synced and uploaded_documents_synced:
            revision.source_summary = (
                f"Indexed {documents_synced} documents and {chunks_synced} chunks "
                "from the KB URL and manager-provided files."
            )
        elif live_documents_synced and manager_documents_synced:
            revision.source_summary = (
                f"Indexed {documents_synced} documents and {chunks_synced} chunks "
                "from the KB URL and manager instructions."
            )
        elif live_documents_synced:
            revision.source_summary = (
                f"Indexed {documents_synced} documents and {chunks_synced} chunks from the KB URL."
            )
        elif uploaded_documents_synced:
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
                "last_sync_warning": last_sync_warning,
                "last_sync_source_url": normalized_kb_url,
            }
        )
        revision.payload_json = payload
        db.flush()
        db.commit()
        db.refresh(blueprint)
        db.refresh(agent)
        return blueprint, agent


meta_service = MetaService()
    