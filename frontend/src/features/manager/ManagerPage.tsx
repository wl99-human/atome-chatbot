import { useEffect, useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";

import {
  createMetaSession,
  fetchMetaSession,
  generateFromMetaSession,
  sendMetaMessage,
  updateMetaSessionDraft,
  updateAgentFromMetaSession,
  uploadMetaSessionDocuments,
} from "../../api/client";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { InlineAlert } from "../../components/ui/InlineAlert";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useToast } from "../../components/ui/ToastProvider";
import { useAutosizeTextarea } from "../../hooks/useAutosizeTextarea";
import { useAppShell } from "../../layout/AppShell";
import { classNames, formatFileSize, getDraftStatusTone, mergeFiles } from "../../lib/utils";

const starterPrompts = [
  "Build a grounded FAQ agent that only answers from uploaded policy documents and says clearly when the answer is not supported.",
  "Make the agent warm and concise. It should cite sources and avoid guessing timelines or account outcomes.",
  "This agent should handle refund and dispute questions from uploaded docs. If the docs do not support the answer, it should ask the customer to contact support.",
];

export function ManagerPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const { showToast } = useToast();
  const { agents, selectedAgent, setSelectedAgentId } = useAppShell();
  const sessionId = searchParams.get("meta") ?? "";
  const [composer, setComposer] = useState("");
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [managerError, setManagerError] = useState("");
  const [draftNameInput, setDraftNameInput] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const textareaRef = useAutosizeTextarea<HTMLTextAreaElement>(composer);
  const targetGeneratedAgentId = selectedAgent?.role === "generated" ? selectedAgent.id : null;

  const sessionQuery = useQuery({
    queryKey: ["meta-session", sessionId],
    queryFn: () => fetchMetaSession(sessionId),
    enabled: Boolean(sessionId),
  });

  function setSessionInCache(nextSessionId: string, nextSession: Awaited<ReturnType<typeof fetchMetaSession>>) {
    queryClient.setQueryData(["meta-session", nextSessionId], nextSession);
  }

  const createSessionMutation = useMutation({
    mutationFn: (payload: { target_agent_id?: string | null }) => createMetaSession(payload),
    onSuccess: (session) => {
      const next = new URLSearchParams(searchParams.toString());
      next.set("meta", session.id);
      setSearchParams(next, { replace: true });
      setSessionInCache(session.id, session);
      setManagerError("");
    },
    onError: (error: Error) => {
      setManagerError(error.message || "Could not start the manager workspace.");
    },
  });

  useEffect(() => {
    if (sessionId || createSessionMutation.isPending) {
      return;
    }
    createSessionMutation.mutate({
      target_agent_id: targetGeneratedAgentId,
    });
  }, [createSessionMutation, sessionId, targetGeneratedAgentId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [sessionQuery.data?.messages]);

  const sendMessageMutation = useMutation({
    mutationFn: (message: string) => sendMetaMessage(sessionId, { message }),
    onSuccess: (session) => {
      setSessionInCache(session.id, session);
      setComposer("");
      setManagerError("");
    },
    onError: (error: Error) => {
      setManagerError(error.message || "The meta-agent could not process that message.");
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => uploadMetaSessionDocuments(sessionId, files),
    onSuccess: (session, files) => {
      setSessionInCache(session.id, session);
      setUploadFiles([]);
      setManagerError("");
      showToast({
        title: "Documents uploaded",
        description: `${files.length} document(s) added to the draft workspace.`,
        tone: "success",
      });
    },
    onError: (error: Error) => {
      setManagerError(error.message || "Document upload failed.");
    },
  });

  const generateMutation = useMutation({
    mutationFn: () => generateFromMetaSession(sessionId),
    onSuccess: async (response) => {
      setSessionInCache(response.session.id, response.session);
      setSelectedAgentId(response.agent.id);
      setManagerError("");
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
      showToast({
        title: "Agent generated",
        description: `${response.agent.name} is ready to test in customer view.`,
        tone: "success",
      });
    },
    onError: (error: Error) => {
      setManagerError(error.message || "Agent generation failed.");
    },
  });

  const updateMutation = useMutation({
    mutationFn: (agentId: string) => updateAgentFromMetaSession(sessionId, { target_agent_id: agentId }),
    onSuccess: async (response) => {
      setSessionInCache(response.session.id, response.session);
      setSelectedAgentId(response.agent.id);
      setManagerError("");
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
      showToast({
        title: "Agent updated",
        description: `${response.agent.name} now has a fresh revision based on the draft workspace.`,
        tone: "success",
      });
    },
    onError: (error: Error) => {
      setManagerError(error.message || "Agent update failed.");
    },
  });

  const session = sessionQuery.data;
  const draft = session?.draft_spec;
  const createdAgentId = session?.created_agent_id ?? null;
  const linkedTargetAgent = agents.find((agent) => agent.id === session?.target_agent_id) ?? null;
  const linkedGeneratedAgentId = linkedTargetAgent?.role === "generated" ? linkedTargetAgent.id : null;
  const canGenerate = Boolean(draft && draft.status === "ready_to_generate");

  useEffect(() => {
    setDraftNameInput(draft?.name ?? "");
  }, [draft?.name, sessionId]);

  const updateDraftMutation = useMutation({
    mutationFn: (payload: { name?: string }) => updateMetaSessionDraft(sessionId, payload),
    onSuccess: (nextSession) => {
      setSessionInCache(nextSession.id, nextSession);
      setDraftNameInput(nextSession.draft_spec.name);
      setManagerError("");
    },
    onError: (error: Error) => {
      setManagerError(error.message || "Could not update the draft.");
      setDraftNameInput(draft?.name ?? "");
    },
  });

  function submitPrompt(rawPrompt?: string) {
    const nextMessage = (rawPrompt ?? composer).trim();
    if (!nextMessage || !sessionId || sendMessageMutation.isPending) {
      return;
    }
    setManagerError("");
    sendMessageMutation.mutate(nextMessage);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submitPrompt();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitPrompt();
    }
  }

  function handleFileSelection(nextFiles: FileList | null) {
    if (!nextFiles) {
      return;
    }
    setUploadFiles((current) => mergeFiles(current, Array.from(nextFiles)));
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    handleFileSelection(event.dataTransfer.files);
  }

  function handleDraftNameSave() {
    const nextName = draftNameInput.trim();
    const currentName = draft?.name ?? "";
    if (!sessionId) {
      return;
    }
    if (!nextName) {
      setDraftNameInput(currentName);
      return;
    }
    if (nextName === currentName) {
      return;
    }
    setManagerError("");
    updateDraftMutation.mutate({ name: nextName });
  }

  function startFreshDraft() {
    if (createSessionMutation.isPending) {
      return;
    }
    setManagerError("");
    createSessionMutation.mutate({ target_agent_id: null });
  }

  if (!sessionId && createSessionMutation.isPending) {
    return (
      <div className="grid h-full gap-3 xl:grid-cols-[minmax(0,1.55fr)_332px]">
        <div className="h-full animate-pulse rounded-[28px] bg-white/80" />
        <div className="h-full animate-pulse rounded-[28px] bg-white/80" />
      </div>
    );
  }

  if (sessionQuery.isError) {
    return (
      <EmptyState
        title="Manager workspace unavailable"
        description={sessionQuery.error instanceof Error ? sessionQuery.error.message : "Could not load the meta-agent workspace."}
      />
    );
  }

  if (!session || !draft) {
    return (
      <EmptyState
        title="Starting manager workspace"
        description="Preparing a fresh draft session for the meta-agent."
      />
    );
  }

  return (
    <div className="h-full min-h-0">
      <Card className="flex h-full min-h-0 flex-col space-y-3 overflow-hidden p-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate-500">Manager Workspace</p>
            <h1 className="mt-0.5 text-lg font-semibold tracking-tight text-slate-950">Conversational meta-agent</h1>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              Upload docs, refine the draft in chat, and explicitly generate or update a testable customer-facing agent.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge tone={getDraftStatusTone(draft.status)}>{draft.status}</StatusBadge>
            {linkedGeneratedAgentId ? <StatusBadge tone="accent">Linked to {linkedTargetAgent?.name}</StatusBadge> : null}
            <button
              type="button"
              onClick={startFreshDraft}
              disabled={createSessionMutation.isPending}
              className="rounded-full border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
            >
              {createSessionMutation.isPending ? "Starting..." : "Start fresh draft"}
            </button>
          </div>
        </div>

        {managerError ? (
          <InlineAlert tone="danger" title="Manager flow error" className="py-2.5">
            {managerError}
          </InlineAlert>
        ) : null}

        {createdAgentId ? (
          <InlineAlert tone="success" title="Draft has a generated agent" className="py-2.5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="space-y-1">
                <p className="text-sm leading-5">The draft is already connected to a generated agent. You can test it now or keep refining the workspace.</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => navigate({ pathname: "/customer", search: `?agent=${createdAgentId}` })}
                  className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800"
                >
                  Open in customer view
                </button>
                <button
                  type="button"
                  onClick={() => navigate({ pathname: "/admin", search: `?agent=${createdAgentId}&panel=issues` })}
                  className="rounded-full border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                >
                  Review in admin
                </button>
              </div>
            </div>
          </InlineAlert>
        ) : null}

        <div className="grid min-h-0 flex-1 gap-3 overflow-hidden xl:grid-cols-[minmax(0,1.55fr)_332px]">
          <div className="flex min-h-0 flex-col gap-2.5 overflow-hidden">
            <Card className="shrink-0 rounded-[22px] bg-slate-50 p-2.5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-slate-950">Draft workspace sources</h2>
                  <p className="mt-0.5 text-[11px] leading-5 text-slate-500">Keep this compact: upload here, then spend most of the time in chat.</p>
                </div>
                <StatusBadge tone="neutral">{session.documents.length} docs</StatusBadge>
              </div>

              <div className="mt-2 grid gap-2.5 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
                <div
                  onDragOver={(event) => {
                    event.preventDefault();
                    setDragActive(true);
                  }}
                  onDragLeave={() => setDragActive(false)}
                  onDrop={handleDrop}
                  className={classNames(
                    "rounded-2xl border-2 border-dashed px-3 py-2.5 text-center transition",
                    dragActive ? "border-orange-400 bg-orange-50" : "border-slate-300 bg-white",
                  )}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    onChange={(event) => handleFileSelection(event.target.files)}
                    className="hidden"
                  />
                  <p className="text-xs font-semibold text-slate-900">Drag files here or browse</p>
                  <p className="mt-0.5 text-[11px] text-slate-500">PDF, DOCX, or TXT only.</p>
                  <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      className="rounded-full border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                    >
                      Choose files
                    </button>
                    {uploadFiles.length ? (
                      <button
                        type="button"
                        onClick={() => uploadMutation.mutate(uploadFiles)}
                        disabled={uploadMutation.isPending}
                        className="rounded-full bg-slate-950 px-3.5 py-1.5 text-xs font-semibold text-white transition hover:bg-slate-800"
                      >
                        {uploadMutation.isPending ? "Uploading..." : `Upload (${uploadFiles.length})`}
                      </button>
                    ) : null}
                  </div>
                </div>

                <div className="rounded-2xl bg-white px-3 py-2.5">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Attached docs</p>
                  {session.documents.length ? (
                    <div className="mt-2 max-h-24 space-y-1.5 overflow-y-auto pr-1">
                      {session.documents.map((document) => (
                        <div key={document.id} className="rounded-2xl bg-slate-50 px-3 py-2">
                          <p className="truncate text-xs font-semibold text-slate-900">{document.title}</p>
                          <p className="mt-1 text-[11px] leading-5 text-slate-500">{document.content_preview}</p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-2 text-xs leading-5 text-slate-500">No source documents uploaded yet.</p>
                  )}
                </div>
              </div>

              {uploadFiles.length ? (
                <div className="mt-2 max-h-20 space-y-1.5 overflow-y-auto pr-1">
                  {uploadFiles.map((file, index) => (
                    <div key={`${file.name}-${file.size}-${file.lastModified}`} className="flex items-center justify-between gap-3 rounded-2xl bg-white px-3 py-2">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-semibold text-slate-900">{file.name}</p>
                        <p className="text-[11px] text-slate-500">{formatFileSize(file.size)}</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => setUploadFiles((current) => current.filter((_, fileIndex) => fileIndex !== index))}
                        className="rounded-full border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-slate-700 transition hover:bg-slate-50"
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
            </Card>

            <Card className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[24px] border border-slate-300 p-0 shadow-[0_20px_48px_-34px_rgba(15,23,42,0.32)]">
              <div className="border-b border-slate-100 px-4 py-3 sm:px-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-950">Meta-agent chat</h2>
                    <p className="mt-1 text-[11px] leading-[18px] text-slate-500">Use chat to refine instructions, tone, scope, and fallback behavior.</p>
                  </div>
                </div>
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {starterPrompts.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => submitPrompt(prompt)}
                      title={prompt}
                      disabled={sendMessageMutation.isPending}
                      className="max-w-full rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium leading-4 text-slate-700 transition hover:border-slate-300 hover:bg-white"
                    >
                      <span className="block max-w-[230px] truncate">{prompt}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4 sm:px-5">
                {session.messages.map((message) => (
                  <article
                    key={message.id}
                    className={classNames(
                      "max-w-3xl rounded-[24px] border px-4 py-3 shadow-sm",
                      message.role === "assistant"
                        ? "border-slate-200 bg-white text-slate-900"
                        : "ml-auto border-slate-950 bg-slate-950 text-white",
                    )}
                  >
                    <p className="whitespace-pre-wrap text-[13px] leading-[22px]">{message.content}</p>
                  </article>
                ))}
                {(sendMessageMutation.isPending || uploadMutation.isPending) ? (
                  <article className="max-w-3xl rounded-[24px] border border-slate-200 bg-white px-4 py-3 shadow-sm">
                    <div className="flex items-center gap-2 text-[13px] text-slate-500">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-orange-500" />
                      Updating the draft workspace...
                    </div>
                  </article>
                ) : null}
                <div ref={bottomRef} />
              </div>

              <form onSubmit={handleSubmit} className="border-t border-slate-200 bg-white/95 px-4 py-3 backdrop-blur sm:px-5">
                <div className="flex gap-3 sm:items-center">
                  <div className="flex-1 rounded-[22px] border border-slate-200 bg-[#fcfaf6] px-4 py-2.5 focus-within:border-slate-400">
                    <textarea
                      ref={textareaRef}
                      value={composer}
                      onChange={(event) => setComposer(event.target.value)}
                      onKeyDown={handleKeyDown}
                      rows={1}
                      placeholder="Describe how the customer-facing agent should behave."
                      className="min-h-[24px] w-full resize-none border-0 bg-transparent p-0 text-[13px] leading-[22px] text-slate-900 outline-none placeholder:text-slate-400"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={sendMessageMutation.isPending || !sessionId}
                    className="rounded-[20px] bg-orange-500 px-4 py-2.5 text-[13px] font-semibold text-white shadow-[0_18px_32px_-20px_rgba(249,115,22,0.9)] transition hover:bg-orange-600"
                  >
                    {sendMessageMutation.isPending ? "Sending..." : "Send"}
                  </button>
                </div>
              </form>
            </Card>
          </div>

          <Card className="flex min-h-0 flex-col overflow-hidden p-3">
            <div className="flex items-start justify-between gap-3 pb-3">
              <div className="min-w-0">
                <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate-500">Draft spec</p>
                <h2 className="mt-1 text-lg font-semibold text-slate-950">Live agent definition</h2>
              </div>
              <StatusBadge tone={getDraftStatusTone(draft.status)}>{draft.status}</StatusBadge>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto pr-1">
              <div className="space-y-2 pb-3">
                <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Name</p>
                  <input
                    value={draftNameInput}
                    onChange={(event) => setDraftNameInput(event.target.value)}
                    onBlur={handleDraftNameSave}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        event.currentTarget.blur();
                      }
                    }}
                    placeholder="Enter agent name"
                    className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-950 outline-none transition focus:border-slate-400"
                  />
                  <p className="mt-2 text-[11px] leading-4 text-slate-500">
                    Rename the draft here. `Generate new agent` always creates a separate agent, even if this draft started from an existing one.
                  </p>
                </div>
                <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Description</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-5 text-slate-700">{draft.description}</p>
                </div>
                <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Behavior instructions</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-5 text-slate-700">{draft.behavior_instructions}</p>
                </div>
                <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Response style</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-5 text-slate-700">{draft.response_style}</p>
                </div>
                <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Allowed scope</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-5 text-slate-700">{draft.allowed_scope}</p>
                </div>
                <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Fallback behavior</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-5 text-slate-700">{draft.fallback_behavior}</p>
                </div>
                <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Knowledge summary</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-5 text-slate-700">{draft.knowledge_summary || "No knowledge summary yet."}</p>
                </div>
                <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Open questions</p>
                  {draft.open_questions.length ? (
                    <div className="mt-2 space-y-2">
                      {draft.open_questions.map((question) => (
                        <p key={question} className="text-sm leading-5 text-slate-700">
                          {question}
                        </p>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-2 text-sm leading-5 text-emerald-700">No blocking questions. The draft is ready to generate.</p>
                  )}
                </div>
              </div>
            </div>

            <div className="sticky bottom-0 mt-auto space-y-2 border-t border-slate-100 bg-white pt-3">
              <button
                type="button"
                onClick={() => generateMutation.mutate()}
                disabled={!canGenerate || generateMutation.isPending}
                className="w-full rounded-full bg-orange-500 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-orange-600 disabled:cursor-not-allowed disabled:bg-orange-300"
              >
                {generateMutation.isPending ? "Generating..." : "Generate new agent"}
              </button>
              {linkedGeneratedAgentId ? (
                <button
                  type="button"
                  onClick={() => updateMutation.mutate(linkedGeneratedAgentId)}
                  disabled={!canGenerate || updateMutation.isPending}
                  className="w-full rounded-full border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-400"
                >
                  {updateMutation.isPending ? "Updating..." : `Update ${linkedTargetAgent?.name ?? "linked agent"}`}
                </button>
              ) : null}
            </div>
          </Card>
        </div>
      </Card>
    </div>
  );
}
