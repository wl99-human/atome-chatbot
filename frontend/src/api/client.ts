import type {
  Agent,
  AgentCreateResponse,
  BootstrapResponse,
  ChatResponse,
  ConversationDetail,
  Issue,
  MetaGenerateResponse,
  MetaSession,
  SyncResponse,
} from "../types/api";

const DEFAULT_API_BASE =
  typeof window !== "undefined" && !["localhost", "127.0.0.1", "::1"].includes(window.location.hostname)
    ? "/api"
    : "http://localhost:8000/api";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    const contentType = response.headers.get("content-type") ?? "";
    let message = `Request failed: ${response.status}`;

    if (contentType.includes("application/json")) {
      const payload = (await response.json()) as { detail?: string | { message?: string } };
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (payload.detail && typeof payload.detail === "object" && "message" in payload.detail) {
        message = String(payload.detail.message);
      }
    } else {
      const errorText = await response.text();
      if (errorText) {
        message = errorText;
      }
    }

    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export function fetchBootstrap() {
  return request<BootstrapResponse>("/bootstrap");
}

export function sendChat(agentId: string, payload: { message: string; conversation_id?: string | null }) {
  return request<ChatResponse>(`/chat/${agentId}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchConversation(conversationId: string) {
  return request<ConversationDetail>(`/conversations/${conversationId}`);
}

export function publishAgent(
  agentId: string,
  payload: { name?: string; description?: string; knowledge_base_url?: string; additional_guidelines: string },
) {
  return request<Agent>(`/agents/${agentId}/publish`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function syncAgent(agentId: string) {
  return request<SyncResponse>(`/agents/${agentId}/sync-sources`, { method: "POST" });
}

export function reportIssue(payload: { agent_id: string; assistant_message_id: string; customer_note: string }) {
  return request<Issue>("/issues", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchIssues() {
  return request<Issue[]>("/issues");
}

export function autoFixIssue(issueId: string) {
  return request<Issue>(`/issues/${issueId}/auto-fix`, {
    method: "POST",
  });
}

export function approveFix(issueId: string) {
  return request<Issue>(`/issues/${issueId}/approve-fix`, {
    method: "POST",
  });
}

export function rejectFix(issueId: string) {
  return request<Issue>(`/issues/${issueId}/reject-fix`, {
    method: "POST",
  });
}

export function generateAgent(formData: FormData) {
  return request<AgentCreateResponse>("/meta/generate-agent", {
    method: "POST",
    body: formData,
  });
}

export function createMetaSession(payload: { target_agent_id?: string | null }) {
  return request<MetaSession>("/meta/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchMetaSession(sessionId: string) {
  return request<MetaSession>(`/meta/sessions/${sessionId}`);
}

export function sendMetaMessage(sessionId: string, payload: { message: string }) {
  return request<MetaSession>(`/meta/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateMetaSessionDraft(
  sessionId: string,
  payload: {
    name?: string;
    description?: string;
    behavior_instructions?: string;
    response_style?: string;
    allowed_scope?: string;
    fallback_behavior?: string;
  },
) {
  return request<MetaSession>(`/meta/sessions/${sessionId}/draft-spec`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function uploadMetaSessionDocuments(sessionId: string, files: File[]) {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  return request<MetaSession>(`/meta/sessions/${sessionId}/documents`, {
    method: "POST",
    body: formData,
  });
}

export function generateFromMetaSession(sessionId: string) {
  return request<MetaGenerateResponse>(`/meta/sessions/${sessionId}/generate`, {
    method: "POST",
  });
}

export function updateAgentFromMetaSession(sessionId: string, payload: { target_agent_id: string }) {
  return request<MetaGenerateResponse>(`/meta/sessions/${sessionId}/update-agent`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function resetAgent(agentId: string) {
  return request<Agent>(`/agents/${agentId}/reset`, { method: "POST" });
}

export function uploadAgentDocuments(agentId: string, files: File[]) {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  return request<Agent>(`/agents/${agentId}/upload-documents`, {
    method: "POST",
    body: formData,
  });
}

export function deleteAgent(agentId: string) {
  return request<{ deleted: boolean; agent_id: string; message: string }>(`/agents/${agentId}`, {
    method: "DELETE",
  });
}
