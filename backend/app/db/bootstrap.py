from __future__ import annotations

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import Base, SessionLocal, engine
from app.models import KnowledgeDocument
from app.services.source_service import source_service


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    settings = get_settings()
    with SessionLocal() as db:
        agent = source_service.seed_default_agent(db)
        db.commit()
        active_revision = next(
            (revision for revision in agent.revisions if revision.id == agent.active_revision_id),
            None,
        )
        if not active_revision:
            return
        has_documents = db.scalar(
            select(KnowledgeDocument.id).where(KnowledgeDocument.revision_id == active_revision.id)
        )
        if settings.auto_sync_default_agent and has_documents:
            try:
                source_service.sync_revision_sources(
                    db,
                    active_revision,
                    knowledge_base_url=active_revision.knowledge_base_url,
                )
                db.commit()
            except Exception:
                db.rollback()
