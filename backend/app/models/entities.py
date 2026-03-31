from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(50), default="support")
    active_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    revisions: Mapped[list["AgentRevision"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", order_by="AgentRevision.version"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class AgentRevision(Base):
    __tablename__ = "agent_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    knowledge_base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    additional_guidelines: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    parent_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="published")
    sync_status: Mapped[str] = mapped_column(String(30), default="idle")
    source_summary: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[dict] = mapped_column(SQLiteJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="revisions")
    documents: Mapped[list["KnowledgeDocument"]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(ForeignKey("agent_revisions.id", ondelete="CASCADE"))
    source_type: Mapped[str] = mapped_column(String(50), default="kb_article")
    title: Mapped[str] = mapped_column(String(500))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[dict] = mapped_column(SQLiteJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    revision: Mapped["AgentRevision"] = relationship(back_populates="documents")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("agent_revisions.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[dict] = mapped_column(SQLiteJSON, default=dict)

    document: Mapped["KnowledgeDocument"] = relationship(back_populates="chunks")
    revision: Mapped["AgentRevision"] = relationship(back_populates="chunks")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("agent_revisions.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255), default="New conversation")
    pending_action: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pending_payload: Mapped[dict] = mapped_column(SQLiteJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    agent: Mapped["Agent"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[list] = mapped_column(SQLiteJSON, default=list)
    payload_json: Mapped[dict] = mapped_column(SQLiteJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class IssueReport(Base):
    __tablename__ = "issue_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("agent_revisions.id", ondelete="CASCADE"))
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    user_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assistant_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    customer_note: Mapped[str] = mapped_column(Text, default="")
    diagnosis_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    diagnosis_summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FixAttempt(Base):
    __tablename__ = "fix_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    issue_id: Mapped[str] = mapped_column(ForeignKey("issue_reports.id", ondelete="CASCADE"))
    target_revision_id: Mapped[str] = mapped_column(String(36))
    candidate_revision_id: Mapped[str] = mapped_column(String(36))
    patch_type: Mapped[str] = mapped_column(String(60))
    patch_summary: Mapped[str] = mapped_column(Text, default="")
    replay_passed: Mapped[bool] = mapped_column(default=False)
    auto_published: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReplayResult(Base):
    __tablename__ = "replay_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    fix_attempt_id: Mapped[str] = mapped_column(ForeignKey("fix_attempts.id", ondelete="CASCADE"))
    prompt: Mapped[str] = mapped_column(Text)
    expected_behavior: Mapped[str] = mapped_column(Text, default="")
    actual_answer: Mapped[str] = mapped_column(Text, default="")
    passed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentBlueprint(Base):
    __tablename__ = "agent_blueprints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    knowledge_summary: Mapped[str] = mapped_column(Text, default="")
    enabled_tools_json: Mapped[list] = mapped_column(SQLiteJSON, default=list)
    created_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MetaSession(Base):
    __tablename__ = "meta_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    target_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    draft_spec_json: Mapped[dict] = mapped_column(SQLiteJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    messages: Mapped[list["MetaMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="MetaMessage.created_at"
    )
    documents: Mapped[list["MetaDocument"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class MetaMessage(Base):
    __tablename__ = "meta_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("meta_sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(SQLiteJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped["MetaSession"] = relationship(back_populates="messages")


class MetaDocument(Base):
    __tablename__ = "meta_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("meta_sessions.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(50), default="upload")
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[dict] = mapped_column(SQLiteJSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped["MetaSession"] = relationship(back_populates="documents")
