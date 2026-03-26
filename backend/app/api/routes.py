from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Agent, FixAttempt, IssueReport, Message
from app.schemas.api import (
    AgentCreateResponse,
    AgentResponse,
    BlueprintResponse,
    BootstrapResponse,
    ChatRequest,
    ChatResponse,
    ConversationResponse,
    FixAttemptResponse,
    IssueCreateRequest,
    IssueResponse,
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

    new_revision = source_service.clone_revision(
        db,
        current_revision,
        additional_guidelines=payload.additional_guidelines,
        knowledge_base_url=payload.knowledge_base_url,
        system_prompt=source_service.build_system_prompt(payload.name or agent.name),
    )
    new_revision.system_prompt = source_service.build_system_prompt(agent.name)
    source_service.sync_revision_sources(
        db,
        new_revision,
        knowledge_base_url=new_revision.knowledge_base_url,
    )
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


@router.post("/meta/generate-agent", response_model=AgentCreateResponse)
async def generate_agent(
    agent_name: str = Form(...),
    description: str = Form(""),
    instructions: str = Form(""),
    files: list[UploadFile] | None = File(default=None),
    db: Session = Depends(get_db),
) -> AgentCreateResponse:
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
