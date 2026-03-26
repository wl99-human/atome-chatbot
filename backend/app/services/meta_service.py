from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Agent, AgentBlueprint, AgentRevision
from app.services.gemini_service import gemini_service
from app.services.source_service import ParsedDocument, source_service
from app.utils.text import normalize_whitespace


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

        blueprint = AgentBlueprint(
            name=blueprint_data.get("name", agent_name),
            description=blueprint_data.get("description", description),
            instructions=normalize_whitespace(
                blueprint_data.get("instructions", instructions) or instructions
            ),
            knowledge_summary=normalize_whitespace(
                blueprint_data.get("knowledge_summary", "\n".join(document_summaries))
            ),
            enabled_tools_json=list(blueprint_data.get("enabled_tools", ["support_handoff"])),
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
