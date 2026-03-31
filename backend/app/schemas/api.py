from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CitationResponse(BaseModel):
    label: str
    title: str
    source_url: str | None = None
    snippet: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str | None = None


class ConversationResponse(BaseModel):
    id: str
    agent_id: str
    revision_id: str
    pending_action: str | None = None
    updated_at: datetime


class ConversationMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    citations: list[CitationResponse] = Field(default_factory=list)
    created_at: datetime


class ConversationDetailResponse(BaseModel):
    id: str
    agent_id: str
    revision_id: str
    pending_action: str | None = None
    updated_at: datetime
    messages: list[ConversationMessageResponse] = Field(default_factory=list)


class ChatResponse(BaseModel):
    conversation_id: str
    assistant_message_id: str
    user_message_id: str
    intent: str
    needs_followup: bool = False
    followup_field: str | None = None
    message: str
    citations: list[CitationResponse] = Field(default_factory=list)
    conversation: ConversationResponse


class PublishRevisionRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    knowledge_base_url: str | None = None
    additional_guidelines: str = ""


class SyncResponse(BaseModel):
    revision_id: str
    documents_synced: int
    chunks_synced: int
    source_summary: str
    sync_mode: str = "fallback"
    fallback_used: bool = False
    last_sync_warning: str | None = None


class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    role: str
    active_revision_id: str | None = None
    active_revision_version: int | None = None
    knowledge_base_url: str | None = None
    additional_guidelines: str = ""
    source_summary: str = ""
    sync_status: str = "idle"
    sync_mode: str = "fallback"
    fallback_used: bool = False
    documents_synced: int = 0
    chunks_synced: int = 0
    last_sync_warning: str | None = None


class AgentCreateResponse(BaseModel):
    agent: AgentResponse
    blueprint: "BlueprintResponse | None" = None


class IssueCreateRequest(BaseModel):
    agent_id: str
    assistant_message_id: str
    customer_note: str = ""


class FixAttemptResponse(BaseModel):
    id: str
    patch_type: str
    patch_summary: str
    replay_passed: bool
    auto_published: bool
    candidate_revision_id: str
    created_at: datetime


class IssueResponse(BaseModel):
    id: str
    agent_id: str
    revision_id: str
    conversation_id: str | None = None
    assistant_message_id: str | None = None
    customer_note: str
    diagnosis_type: str | None = None
    diagnosis_summary: str
    status: str
    prompt: str | None = None
    answer: str | None = None
    latest_fix_attempt: FixAttemptResponse | None = None
    created_at: datetime
    updated_at: datetime


class ToolRequest(BaseModel):
    reference_id: str = Field(min_length=2)


class ToolResponse(BaseModel):
    reference_id: str
    status: str
    detail: str


class MetaDraftSpecResponse(BaseModel):
    name: str = ""
    description: str = ""
    behavior_instructions: str = ""
    response_style: str = ""
    allowed_scope: str = ""
    fallback_behavior: str = ""
    knowledge_summary: str = ""
    open_questions: list[str] = Field(default_factory=list)
    status: str = "draft"


class MetaSessionCreateRequest(BaseModel):
    target_agent_id: str | None = None


class MetaSessionMessageRequest(BaseModel):
    message: str = Field(min_length=1)


class MetaDraftSpecUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    behavior_instructions: str | None = None
    response_style: str | None = None
    allowed_scope: str | None = None
    fallback_behavior: str | None = None


class MetaMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime


class MetaDocumentResponse(BaseModel):
    id: str
    title: str
    filename: str | None = None
    mime_type: str | None = None
    content_preview: str = ""
    created_at: datetime


class MetaSessionResponse(BaseModel):
    id: str
    status: str
    target_agent_id: str | None = None
    created_agent_id: str | None = None
    draft_spec: MetaDraftSpecResponse
    messages: list[MetaMessageResponse] = Field(default_factory=list)
    documents: list[MetaDocumentResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class BlueprintResponse(BaseModel):
    id: str
    name: str
    description: str
    instructions: str
    knowledge_summary: str
    enabled_tools: list[str] = Field(default_factory=list)
    created_agent_id: str | None = None


class BootstrapResponse(BaseModel):
    agents: list[AgentResponse]
    issues: list[IssueResponse]
    default_agent_id: str | None = None
    model: str


class DeleteAgentResponse(BaseModel):
    deleted: bool
    agent_id: str
    message: str


class MetaGenerateResponse(BaseModel):
    session: MetaSessionResponse
    agent: AgentResponse
    blueprint: BlueprintResponse | None = None


AgentCreateResponse.model_rebuild()
