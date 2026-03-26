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
            enabled_tools_json=_coerce_enabled_tools(blueprint_data.get("enabled_tools")),
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

        revision = AgentRevision(
            agent_id=agent.id,
            version=1,
            knowledge_base_url=None,
            additional_guidelines=blueprint.instructions,
            system_prompt=source_service.build_system_prompt(agent.name),
            status="published",
            sync_status="ready",
            source_summary=blueprint.knowledge_summary,
            payload_json={"enabled_tools": blueprint.enabled_tools_json},
        )
        db.add(revision)
        db.flush()
        agent.active_revision_id = revision.id
        blueprint.created_agent_id = agent.id
        db.flush()

        if documents:
            for document in documents:
                source_service.add_document(db, revision, document)
        else:
            source_service.add_document(
                db,
                revision,
                ParsedDocument(
                    title="Manager instructions",
                    content=blueprint.instructions,
                    source_type="upload",
                    filename="manager-instructions.txt",
                    mime_type="text/plain",
                ),
            )
        db.commit()
        db.refresh(blueprint)
        db.refresh(agent)
        return blueprint, agent


meta_service = MetaService()
