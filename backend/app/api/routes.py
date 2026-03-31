from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Agent, Conversation, FixAttempt, IssueReport, Message
from app.schemas.api import (
    AgentCreateResponse,
    AgentResponse,
    BlueprintResponse,
    BootstrapResponse,
    ChatRequest,
    ChatResponse,
    ConversationDetailResponse,
    ConversationResponse,
    DeleteAgentResponse,
    FixAttemptResponse,
    IssueCreateRequest,
    IssueResponse,
    MetaGenerateResponse,
    MetaDraftSpecUpdateRequest,
    MetaSessionCreateRequest,
    MetaSessionMessageRequest,
    MetaSessionResponse,
    PublishRevisionRequest,
    SyncResponse,
    ToolRequest,
    ToolResponse,
)
from app.services.issue_service import issue_service
from app.services.meta_service import meta_service
from app.services.runtime_service import runtime_service
from app.services.source_service import ParsedDocument, source_service
from app.services.tool_service import get_application_status, get_card_transaction_status
from app.core.config import get_settings


router = APIRouter()
settings = get_settings()


def _normalized_optional_text(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _revision_sync_metadata(active_revision) -> dict:
    payload = dict(active_revision.payload_json or {}) if active_revision else {}
    return {
        "sync_mode": payload.get("sync_mode", "fallback"),
        "fallback_used": bool(payload.get("fallback_used", False)),
        "documents_synced": int(payload.get("documents_synced", 0) or 0),
        "chunks_synced": int(payload.get("chunks_synced", 0) or 0),
        "last_sync_warning": payload.get("last_sync_warning"),
    }


def _agent_response(agent: Agent) -> AgentResponse:
    active_revision = next(
        (revision for revision in agent.revisions if revision.id == agent.active_revision_id),
        None,
    )
    sync_metadata = _revision_sync_metadata(active_revision)
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        role=agent.role,
        active_revision_id=agent.active_revision_id,
        active_revision_version=active_revision.version if active_revision else None,
        knowledge_base_url=active_revision.knowledge_base_url if active_revision else None,
        additional_guidelines=active_revision.additional_guidelines if active_revision else "",
        source_summary=active_revision.source_summary if active_revision else "",
        sync_status=active_revision.sync_status if active_revision else "idle",
        sync_mode=sync_metadata["sync_mode"],
        fallback_used=sync_metadata["fallback_used"],
        documents_synced=sync_metadata["documents_synced"],
        chunks_synced=sync_metadata["chunks_synced"],
        last_sync_warning=sync_metadata["last_sync_warning"],
    )


def _latest_fix_response(db: Session, issue_id: str) -> FixAttemptResponse | None:
    fix_attempt = db.scalar(
        select(FixAttempt)
        .where(FixAttempt.issue_id == issue_id)
        .order_by(FixAttempt.created_at.desc())
    )
    if not fix_attempt:
        return None
    return FixAttemptResponse(
        id=fix_attempt.id,
        patch_type=fix_attempt.patch_type,
        patch_summary=fix_attempt.patch_summary,
        replay_passed=fix_attempt.replay_passed,
        auto_published=fix_attempt.auto_published,
        candidate_revision_id=fix_attempt.candidate_revision_id,
        created_at=fix_attempt.created_at,
    )


def _issue_response(db: Session, issue: IssueReport) -> IssueResponse:
    prompt = db.get(Message, issue.user_message_id) if issue.user_message_id else None
    answer = db.get(Message, issue.assistant_message_id) if issue.assistant_message_id else None
    return IssueResponse(
        id=issue.id,
        agent_id=issue.agent_id,
        revision_id=issue.revision_id,
        conversation_id=issue.conversation_id,
        assistant_message_id=issue.assistant_message_id,
        customer_note=issue.customer_note,
        diagnosis_type=issue.diagnosis_type,
        diagnosis_summary=issue.diagnosis_summary,
        status=issue.status,
        prompt=prompt.content if prompt else None,
        answer=answer.content if answer else None,
        latest_fix_attempt=_latest_fix_response(db, issue.id),
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )


def _meta_session_response(session) -> MetaSessionResponse:
    return MetaSessionResponse(
        id=session.id,
        status=session.status,
        target_agent_id=session.target_agent_id,
        created_agent_id=session.created_agent_id,
        draft_spec=session.draft_spec_json or {},
        messages=[
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
            }
            for message in session.messages
        ],
        documents=[
            {
                "id": document.id,
                "title": document.title,
                "filename": document.filename,
                "mime_type": document.mime_type,
                "content_preview": (document.content or "")[:220],
                "created_at": document.created_at,
            }
            for document in session.documents
        ],
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _conversation_detail_response(conversation: Conversation) -> ConversationDetailResponse:
    return ConversationDetailResponse(
        id=conversation.id,
        agent_id=conversation.agent_id,
        revision_id=conversation.revision_id,
        pending_action=conversation.pending_action,
        updated_at=conversation.updated_at,
        messages=[
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "citations": message.citations_json or [],
                "created_at": message.created_at,
            }
            for message in conversation.messages
        ],
    )


@router.get("/bootstrap", response_model=BootstrapResponse)
def bootstrap(db: Session = Depends(get_db)) -> BootstrapResponse:
    agents = db.scalars(select(Agent).order_by(Agent.created_at.asc())).all()
    issues = db.scalars(select(IssueReport).order_by(IssueReport.created_at.desc())).all()
    return BootstrapResponse(
        agents=[_agent_response(agent) for agent in agents],
        issues=[_issue_response(db, issue) for issue in issues],
        default_agent_id=agents[0].id if agents else None,
        model=settings.gemini_model,
    )


@router.get("/agents", response_model=list[AgentResponse])
def list_agents(db: Session = Depends(get_db)) -> list[AgentResponse]:
    agents = db.scalars(select(Agent).order_by(Agent.created_at.asc())).all()
    return [_agent_response(agent) for agent in agents]


@router.post("/chat/{agent_id}", response_model=ChatResponse)
def chat(agent_id: str, payload: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    try:
        result = runtime_service.handle_chat(db, agent_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    conversation = result["conversation"]
    reply = result["reply"]
    return ChatResponse(
        conversation_id=conversation.id,
        assistant_message_id=result["assistant_message"].id,
        user_message_id=result["user_message"].id,
        intent=reply.intent,
        needs_followup=reply.needs_followup,
        followup_field=reply.followup_field,
        message=reply.message,
        citations=reply.citations,
        conversation=ConversationResponse(
            id=conversation.id,
            agent_id=conversation.agent_id,
            revision_id=conversation.revision_id,
            pending_action=conversation.pending_action,
            updated_at=conversation.updated_at,
        ),
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)) -> ConversationDetailResponse:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return _conversation_detail_response(conversation)


@router.post("/agents/{agent_id}/publish", response_model=AgentResponse)
def publish_revision(
    agent_id: str,
    payload: PublishRevisionRequest,
    db: Session = Depends(get_db),
) -> AgentResponse:
    try:
        agent, current_revision = runtime_service.get_active_agent_and_revision(db, agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if payload.name:
        agent.name = payload.name
    if payload.description is not None:
        agent.description = payload.description

    requested_kb_url = _normalized_optional_text(payload.knowledge_base_url)
    if agent.role == "generated" and requested_kb_url:
        raise HTTPException(
            status_code=400,
            detail="Generated agents only support uploaded documents and cannot use a knowledge base URL.",
        )

    new_revision = source_service.clone_revision(
        db,
        current_revision,
        additional_guidelines=payload.additional_guidelines,
        knowledge_base_url=None if agent.role == "generated" else payload.knowledge_base_url,
        system_prompt=source_service.build_system_prompt(payload.name or agent.name),
    )
    new_revision.system_prompt = source_service.build_system_prompt(agent.name)
    revision_payload = dict(new_revision.payload_json or {})
    revision_payload["instruction_bundle"] = source_service.build_instruction_bundle(
        behavior_instructions=new_revision.additional_guidelines,
        response_style=((current_revision.payload_json or {}).get("instruction_bundle") or {}).get(
            "response_style", "Be friendly, concise, and practical."
        ),
        allowed_scope=((current_revision.payload_json or {}).get("instruction_bundle") or {}).get(
            "allowed_scope", "Answer only from the uploaded or synced knowledge sources."
        ),
        fallback_behavior=((current_revision.payload_json or {}).get("instruction_bundle") or {}).get(
            "fallback_behavior", "If the answer is not supported by the current knowledge, say that clearly."
        ),
    )
    new_revision.payload_json = revision_payload
    if agent.role != "generated":
        source_service.sync_revision_sources(
            db,
            new_revision,
            knowledge_base_url=new_revision.knowledge_base_url,
        )
    else:
        new_revision.knowledge_base_url = None
        new_revision.sync_status = "ready"
    agent.active_revision_id = new_revision.id
    db.commit()
    db.refresh(agent)
    return _agent_response(agent)


@router.post("/agents/{agent_id}/sync-sources", response_model=SyncResponse)
def sync_sources(agent_id: str, db: Session = Depends(get_db)) -> SyncResponse:
    try:
        agent, current_revision = runtime_service.get_active_agent_and_revision(db, agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if agent.role == "generated":
        raise HTTPException(
            status_code=400,
            detail="Generated agents only support uploaded documents and cannot sync from a knowledge base URL.",
        )

    new_revision = source_service.clone_revision(db, current_revision)
    sync_result = source_service.sync_revision_sources(
        db,
        new_revision,
        knowledge_base_url=new_revision.knowledge_base_url,
    )
    agent.active_revision_id = new_revision.id
    db.commit()
    return SyncResponse(
        revision_id=new_revision.id,
        documents_synced=sync_result.documents_synced,
        chunks_synced=sync_result.chunks_synced,
        source_summary=new_revision.source_summary,
        sync_mode=sync_result.sync_mode,
        fallback_used=sync_result.fallback_used,
        last_sync_warning=sync_result.last_sync_warning,
    )


@router.post("/agents/{agent_id}/reset", response_model=AgentResponse)
def reset_agent(agent_id: str, db: Session = Depends(get_db)) -> AgentResponse:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found.")

    # Keep only the very first revision (version 1) and delete the rest
    revisions = sorted(agent.revisions, key=lambda r: r.version)
    if not revisions:
        raise HTTPException(status_code=400, detail="Agent has no revisions to reset.")

    first_revision = revisions[0]
    for revision in revisions[1:]:
        db.delete(revision)
    db.flush()

    if agent.role != "generated":
        # Re-sync the first revision so knowledge is fresh
        source_service.sync_revision_sources(
            db,
            first_revision,
            knowledge_base_url=first_revision.knowledge_base_url,
            preserve_corrections=False,
        )
    else:
        first_revision.knowledge_base_url = None
        first_revision.sync_status = "ready"
    agent.active_revision_id = first_revision.id
    db.commit()
    db.refresh(agent)
    return _agent_response(agent)


@router.post("/agents/{agent_id}/upload-documents", response_model=AgentResponse)
async def upload_agent_documents(
    agent_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> AgentResponse:
    try:
        agent, revision = runtime_service.get_active_agent_and_revision(db, agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if not files:
        raise HTTPException(status_code=400, detail="No files were provided.")

    total_chunks = 0
    for upload in files:
        raw_bytes = await upload.read()
        parsed = source_service.parse_uploaded_document(
            upload.filename or "untitled", upload.content_type, raw_bytes
        )
        total_chunks += source_service.add_document(db, revision, parsed)

    revision.source_summary = (
        f"{revision.source_summary or ''} "
        f"Added {len(files)} uploaded document(s) with {total_chunks} new chunk(s)."
    ).strip()
    db.commit()
    db.refresh(agent)
    return _agent_response(agent)


@router.delete("/agents/{agent_id}", response_model=DeleteAgentResponse)
def delete_agent(agent_id: str, db: Session = Depends(get_db)) -> DeleteAgentResponse:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found.")
    if agent.role == "support":
        raise HTTPException(
            status_code=403,
            detail="The default Atome support agent cannot be deleted.",
        )
    db.delete(agent)
    db.commit()
    return DeleteAgentResponse(
        deleted=True,
        agent_id=agent_id,
        message=f"Agent '{agent.name}' has been permanently deleted.",
    )


@router.get("/issues", response_model=list[IssueResponse])
def list_issues(db: Session = Depends(get_db)) -> list[IssueResponse]:
    issues = db.scalars(select(IssueReport).order_by(IssueReport.created_at.desc())).all()
    return [_issue_response(db, issue) for issue in issues]


@router.post("/issues", response_model=IssueResponse)
def report_issue(payload: IssueCreateRequest, db: Session = Depends(get_db)) -> IssueResponse:
    try:
        issue = issue_service.create_issue(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _issue_response(db, issue)


@router.post("/issues/{issue_id}/auto-fix", response_model=IssueResponse)
def auto_fix_issue(issue_id: str, db: Session = Depends(get_db)) -> IssueResponse:
    try:
        result = issue_service.auto_fix_issue(db, issue_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _issue_response(db, result["issue"])


@router.post("/issues/{issue_id}/approve-fix", response_model=IssueResponse)
def approve_fix(issue_id: str, db: Session = Depends(get_db)) -> IssueResponse:
    try:
        issue = issue_service.approve_fix(db, issue_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _issue_response(db, issue)


@router.post("/issues/{issue_id}/reject-fix", response_model=IssueResponse)
def reject_fix(issue_id: str, db: Session = Depends(get_db)) -> IssueResponse:
    try:
        issue = issue_service.reject_fix(db, issue_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _issue_response(db, issue)


@router.post("/meta/sessions", response_model=MetaSessionResponse)
def create_meta_session(
    payload: MetaSessionCreateRequest,
    db: Session = Depends(get_db),
) -> MetaSessionResponse:
    session = meta_service.create_session(db, target_agent_id=payload.target_agent_id)
    return _meta_session_response(session)


@router.get("/meta/sessions/{session_id}", response_model=MetaSessionResponse)
def get_meta_session(session_id: str, db: Session = Depends(get_db)) -> MetaSessionResponse:
    try:
        session = meta_service.get_session(db, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _meta_session_response(session)


@router.post("/meta/sessions/{session_id}/messages", response_model=MetaSessionResponse)
def send_meta_message(
    session_id: str,
    payload: MetaSessionMessageRequest,
    db: Session = Depends(get_db),
) -> MetaSessionResponse:
    try:
        session = meta_service.add_session_message(
            db,
            session_id=session_id,
            message=payload.message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _meta_session_response(session)


@router.patch("/meta/sessions/{session_id}/draft-spec", response_model=MetaSessionResponse)
def update_meta_session_draft(
    session_id: str,
    payload: MetaDraftSpecUpdateRequest,
    db: Session = Depends(get_db),
) -> MetaSessionResponse:
    try:
        session = meta_service.update_session_draft(
            db,
            session_id=session_id,
            draft_patch=payload.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _meta_session_response(session)


@router.post("/meta/sessions/{session_id}/documents", response_model=MetaSessionResponse)
async def upload_meta_session_documents(
    session_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> MetaSessionResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files were provided.")
    parsed_documents: list[ParsedDocument] = []
    for upload in files:
        raw_bytes = await upload.read()
        parsed_documents.append(
            source_service.parse_uploaded_document(
                upload.filename or "untitled",
                upload.content_type,
                raw_bytes,
            )
        )
    try:
        session = meta_service.upload_session_documents(
            db,
            session_id=session_id,
            documents=parsed_documents,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _meta_session_response(session)


@router.post("/meta/sessions/{session_id}/generate", response_model=MetaGenerateResponse)
def generate_agent_from_meta_session(
    session_id: str,
    db: Session = Depends(get_db),
) -> MetaGenerateResponse:
    try:
        session, blueprint, agent = meta_service.generate_session_agent(db, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MetaGenerateResponse(
        session=_meta_session_response(session),
        agent=_agent_response(agent),
        blueprint=BlueprintResponse(
            id=blueprint.id,
            name=blueprint.name,
            description=blueprint.description,
            instructions=blueprint.instructions,
            knowledge_summary=blueprint.knowledge_summary,
            enabled_tools=blueprint.enabled_tools_json,
            created_agent_id=blueprint.created_agent_id,
        ),
    )


@router.post("/meta/sessions/{session_id}/update-agent", response_model=MetaGenerateResponse)
def update_agent_from_meta_session(
    session_id: str,
    payload: MetaSessionCreateRequest,
    db: Session = Depends(get_db),
) -> MetaGenerateResponse:
    if not payload.target_agent_id:
        raise HTTPException(status_code=400, detail="target_agent_id is required.")
    try:
        session, blueprint, agent = meta_service.update_session_agent(
            db,
            session_id=session_id,
            agent_id=payload.target_agent_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MetaGenerateResponse(
        session=_meta_session_response(session),
        agent=_agent_response(agent),
        blueprint=BlueprintResponse(
            id=blueprint.id,
            name=blueprint.name,
            description=blueprint.description,
            instructions=blueprint.instructions,
            knowledge_summary=blueprint.knowledge_summary,
            enabled_tools=blueprint.enabled_tools_json,
            created_agent_id=blueprint.created_agent_id,
        ),
    )


@router.post("/meta/generate-agent", response_model=AgentCreateResponse)
async def generate_agent(
    agent_name: str = Form(...),
    description: str = Form(""),
    instructions: str = Form(""),
    knowledge_base_url: str | None = Form(default=None),
    files: list[UploadFile] | None = File(default=None),
    db: Session = Depends(get_db),
) -> AgentCreateResponse:
    if _normalized_optional_text(knowledge_base_url):
        raise HTTPException(
            status_code=400,
            detail="Generated agents only support uploaded documents and cannot use a knowledge base URL.",
        )
    parsed_documents: list[ParsedDocument] = []
    for upload in files or []:
        raw_bytes = await upload.read()
        parsed_documents.append(
            source_service.parse_uploaded_document(upload.filename, upload.content_type, raw_bytes)
        )
    blueprint, agent = meta_service.generate_agent(
        db,
        agent_name=agent_name,
        description=description,
        instructions=instructions,
        documents=parsed_documents,
    )
    return AgentCreateResponse(
        agent=_agent_response(agent),
        blueprint=BlueprintResponse(
            id=blueprint.id,
            name=blueprint.name,
            description=blueprint.description,
            instructions=blueprint.instructions,
            knowledge_summary=blueprint.knowledge_summary,
            enabled_tools=blueprint.enabled_tools_json,
            created_agent_id=blueprint.created_agent_id,
        ),
    )


@router.post("/tools/application-status", response_model=ToolResponse)
def application_status(payload: ToolRequest) -> ToolResponse:
    result = get_application_status(payload.reference_id)
    return ToolResponse(**result)


@router.post("/tools/transaction-status", response_model=ToolResponse)
def transaction_status(payload: ToolRequest) -> ToolResponse:
    result = get_card_transaction_status(payload.reference_id)
    return ToolResponse(**result)
