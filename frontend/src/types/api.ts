export type Citation = {
  label: string;
  title: string;
  source_url?: string | null;
  snippet: string;
};

export type Agent = {
  id: string;
  name: string;
  description: string;
  role: string;
  active_revision_id?: string | null;
  active_revision_version?: number | null;
  knowledge_base_url?: string | null;
  additional_guidelines: string;
  source_summary: string;
  sync_status: string;
  sync_mode: string;
  fallback_used: boolean;
  documents_synced: number;
  chunks_synced: number;
  last_sync_warning?: string | null;
};

export type SyncResponse = {
  revision_id: string;
  documents_synced: number;
  chunks_synced: number;
  source_summary: string;
  sync_mode: string;
  fallback_used: boolean;
  last_sync_warning?: string | null;
};

export type ConversationSummary = {
  id: string;
  agent_id: string;
  revision_id: string;
  pending_action?: string | null;
  updated_at: string;
};

export type ConversationMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  created_at: string;
};

export type ConversationDetail = {
  id: string;
  agent_id: string;
  revision_id: string;
  pending_action?: string | null;
  updated_at: string;
  messages: ConversationMessage[];
};

export type ChatResponse = {
  conversation_id: string;
  assistant_message_id: string;
  user_message_id: string;
  intent: string;
  needs_followup: boolean;
  followup_field?: string | null;
  message: string;
  citations: Citation[];
  conversation: ConversationSummary;
};

export type FixAttempt = {
  id: string;
  patch_type: string;
  patch_summary: string;
  replay_passed: boolean;
  auto_published: boolean;
  candidate_revision_id: string;
  created_at: string;
};

export type Issue = {
  id: string;
  agent_id: string;
  revision_id: string;
  conversation_id?: string | null;
  assistant_message_id?: string | null;
  customer_note: string;
  diagnosis_type?: string | null;
  diagnosis_summary: string;
  status: string;
  prompt?: string | null;
  answer?: string | null;
  latest_fix_attempt?: FixAttempt | null;
  created_at: string;
  updated_at: string;
};

export type Blueprint = {
  id: string;
  name: string;
  description: string;
  instructions: string;
  knowledge_summary: string;
  enabled_tools: string[];
  created_agent_id?: string | null;
};

export type AgentCreateResponse = {
  agent: Agent;
  blueprint?: Blueprint | null;
};

export type MetaDraftSpec = {
  name: string;
  description: string;
  behavior_instructions: string;
  response_style: string;
  allowed_scope: string;
  fallback_behavior: string;
  knowledge_summary: string;
  open_questions: string[];
  status: string;
};

export type MetaSessionMessage = {
  id: string;
  role: "manager" | "assistant";
  content: string;
  created_at: string;
};

export type MetaSessionDocument = {
  id: string;
  title: string;
  filename?: string | null;
  mime_type?: string | null;
  content_preview: string;
  created_at: string;
};

export type MetaSession = {
  id: string;
  status: string;
  target_agent_id?: string | null;
  created_agent_id?: string | null;
  draft_spec: MetaDraftSpec;
  messages: MetaSessionMessage[];
  documents: MetaSessionDocument[];
  created_at: string;
  updated_at: string;
};

export type MetaGenerateResponse = {
  session: MetaSession;
  agent: Agent;
  blueprint?: Blueprint | null;
};

export type BootstrapResponse = {
  agents: Agent[];
  issues: Issue[];
  default_agent_id?: string | null;
  model: string;
};

export type UIMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  assistantMessageId?: string;
};
