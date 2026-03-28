import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { reportIssue, sendChat } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { InlineAlert } from "../../components/ui/InlineAlert";
import { Modal } from "../../components/ui/Modal";
import { useToast } from "../../components/ui/ToastProvider";
import { useAutosizeTextarea } from "../../hooks/useAutosizeTextarea";
import { useAppShell } from "../../layout/AppShell";
import { classNames, describePendingAction, getSyncTone } from "../../lib/utils";
import type { ChatResponse, UIMessage } from "../../types/api";

function buildQuickPrompts(agentRole?: string) {
  if (agentRole === "support") {
    return [
      {
        label: "KB question",
        prompt: "How do I change the mobile number for my account?",
      },
      {
        label: "Application status",
        prompt: "Please check my application status",
      },
      {
        label: "Failed transaction",
        prompt: "My card transaction failed and I need help checking it",
      },
    ];
  }

  return [
    {
      label: "Knowledge question",
      prompt: "What can you help me with from your knowledge base?",
    },
  ];
}

function buildWelcomeMessage(agentName?: string, agentRole?: string) {
  return [
    {
      id: `welcome-${agentName ?? "default"}`,
      role: "assistant" as const,
      content: agentName
        ? agentRole === "support"
          ? `You are chatting with ${agentName}. Ask a knowledge-base question, or try a status lookup flow.`
          : `You are chatting with ${agentName}. Ask a question grounded in this agent's knowledge base.`
        : "Choose an agent to start a conversation.",
    },
  ];
}

export function CustomerPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { selectedAgent, selectedAgentId } = useAppShell();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UIMessage[]>(
    buildWelcomeMessage(selectedAgent?.name, selectedAgent?.role),
  );
  const [chatInput, setChatInput] = useState("");
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [reportTargetId, setReportTargetId] = useState<string | null>(null);
  const [reportReason, setReportReason] = useState("");
  const [reportDetails, setReportDetails] = useState("");
  const [reportError, setReportError] = useState("");
  const [reportSuccess, setReportSuccess] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useAutosizeTextarea<HTMLTextAreaElement>(chatInput);

  useEffect(() => {
    setConversationId(null);
    setPendingAction(null);
    setChatInput("");
    setMessages(buildWelcomeMessage(selectedAgent?.name, selectedAgent?.role));
  }, [selectedAgent?.id, selectedAgent?.name, selectedAgent?.role]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pendingAction, selectedAgentId]);

  const chatMutation = useMutation({
    mutationFn: ({
      agentId,
      message,
      existingConversationId,
    }: {
      agentId: string;
      message: string;
      existingConversationId: string | null;
    }) => sendChat(agentId, { message, conversation_id: existingConversationId }),
    onSuccess: (response: ChatResponse) => {
      setConversationId(response.conversation_id);
      setPendingAction(response.conversation.pending_action ?? null);
      setMessages((current) => [
        ...current,
        {
          id: response.assistant_message_id,
          role: "assistant",
          content: response.message,
          citations: response.citations,
          assistantMessageId: response.assistant_message_id,
        },
      ]);
    },
    onError: (error: Error) => {
      setMessages((current) => [
        ...current,
        {
          id: `assistant-error-${Date.now()}`,
          role: "assistant",
          content: error.message || "The chat request failed.",
        },
      ]);
      showToast({
        title: "Chat request failed",
        description: error.message,
        tone: "danger",
      });
    },
  });

  const reportMutation = useMutation({
    mutationFn: ({
      agentId,
      assistantMessageId,
      customerNote,
    }: {
      agentId: string;
      assistantMessageId: string;
      customerNote: string;
    }) =>
      reportIssue({
        agent_id: agentId,
        assistant_message_id: assistantMessageId,
        customer_note: customerNote,
      }),
    onSuccess: async () => {
      setReportSuccess(true);
      await queryClient.invalidateQueries({ queryKey: ["issues"] });
      showToast({
        title: "Mistake reported",
        description: "The admin queue now includes this diagnosis for review.",
        tone: "success",
      });
    },
    onError: (error: Error) => {
      setReportError(error.message || "Failed to report the issue.");
    },
  });

  async function submitMessage(rawMessage?: string) {
    if (!selectedAgentId || chatMutation.isPending) {
      return;
    }
    const message = (rawMessage ?? chatInput).trim();
    if (!message) {
      return;
    }

    setMessages((current) => [...current, { id: `user-${Date.now()}`, role: "user", content: message }]);
    setChatInput("");

    await chatMutation.mutateAsync({
      agentId: selectedAgentId,
      message,
      existingConversationId: conversationId,
    });
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void submitMessage();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitMessage();
    }
  }

  function resetConversation() {
    setConversationId(null);
    setPendingAction(null);
    setChatInput("");
    setMessages(buildWelcomeMessage(selectedAgent?.name, selectedAgent?.role));
  }

  function openReportModal(messageId: string) {
    setReportTargetId(messageId);
    setReportReason("");
    setReportDetails("");
    setReportError("");
    setReportSuccess(false);
  }

  function closeReportModal() {
    setReportTargetId(null);
    setReportReason("");
    setReportDetails("");
    setReportError("");
    setReportSuccess(false);
  }

  async function submitIssueReport() {
    if (!selectedAgentId || !reportTargetId) {
      return;
    }
    if (!reportReason.trim()) {
      setReportError("Please add a short reason so the admin review has useful context.");
      return;
    }
    setReportError("");
    const customerNote = reportDetails.trim()
      ? `${reportReason.trim()}\n\nDetails: ${reportDetails.trim()}`
      : reportReason.trim();
    await reportMutation.mutateAsync({
      agentId: selectedAgentId,
      assistantMessageId: reportTargetId,
      customerNote,
    });
  }

  if (!selectedAgent) {
    return (
      <EmptyState
        title="No active agent selected"
        description="Choose or generate an agent before starting the customer chat flow."
      />
    );
  }

  const quickPrompts = buildQuickPrompts(selectedAgent.role);
  const chatPlaceholder =
    selectedAgent.role === "support"
      ? "Ask about Atome Card, or request an application / transaction lookup."
      : "Ask a question grounded in this agent's knowledge base.";

  return (
    <>
      <div className="h-full min-h-0">
        <Card className="flex h-full min-h-0 flex-col space-y-3 overflow-hidden p-4">
          <div className="flex flex-col gap-3 border-b border-slate-100 pb-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h1 className="text-xl font-semibold tracking-tight text-slate-950">Chat playground</h1>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700">
                  {selectedAgent.name}
                </span>
                <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600">
                  Rev {selectedAgent.active_revision_version ?? "n/a"}
                </span>
                <span
                  className={classNames(
                    "rounded-full px-2.5 py-1 font-medium",
                    getSyncTone(selectedAgent.sync_mode, selectedAgent.fallback_used) === "success"
                      ? "bg-emerald-50 text-emerald-700"
                      : getSyncTone(selectedAgent.sync_mode, selectedAgent.fallback_used) === "warning"
                        ? "bg-amber-50 text-amber-800"
                        : "bg-slate-100 text-slate-600",
                  )}
                >
                  {selectedAgent.sync_mode}
                </span>
              </div>
            </div>
            <button
              type="button"
              onClick={resetConversation}
              className="rounded-full border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
            >
              New chat
            </button>
          </div>

          <div className="flex flex-wrap gap-2">
            {quickPrompts.map((item) => (
              <button
                key={item.label}
                type="button"
                onClick={() => void submitMessage(item.prompt)}
                disabled={chatMutation.isPending}
                className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-slate-300 hover:bg-white"
              >
                {item.label}
              </button>
            ))}
          </div>

          {pendingAction ? (
            <InlineAlert tone="info" title="Follow-up needed" className="py-2.5">
              {describePendingAction(pendingAction)}
            </InlineAlert>
          ) : null}

          <div className="flex min-h-0 flex-1 flex-col rounded-[30px] border border-slate-200 bg-[#fcfaf6]">
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-5">
              {messages.map((message) => (
                <article
                  key={message.id}
                  className={classNames(
                    "max-w-3xl rounded-[26px] border px-4 py-4 shadow-sm",
                    message.role === "assistant"
                      ? "border-slate-200 bg-white text-slate-900"
                      : "ml-auto border-slate-950 bg-slate-950 text-white",
                  )}
                >
                  <p className="whitespace-pre-wrap text-sm leading-5">{message.content}</p>
                  {message.citations?.length ? (
                    <details className="mt-4 rounded-[22px] border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
                      <summary className="cursor-pointer list-none font-semibold text-slate-900">
                        Sources ({message.citations.length})
                      </summary>
                      <div className="mt-3 space-y-3">
                        {message.citations.map((citation) => (
                          <div key={`${message.id}-${citation.label}`} className="rounded-[18px] bg-white px-3 py-3">
                            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
                              {citation.label}
                            </p>
                            <p className="mt-1 text-sm font-semibold text-slate-900">{citation.title}</p>
                            <p className="mt-2 text-sm leading-5 text-slate-600">{citation.snippet}</p>
                            {citation.source_url ? (
                              <a
                                href={citation.source_url}
                                target="_blank"
                                rel="noreferrer"
                                className="mt-2 inline-flex text-sm font-medium text-orange-700 transition hover:text-orange-800"
                              >
                                Open source
                              </a>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </details>
                  ) : null}
                  {message.role === "assistant" && message.assistantMessageId ? (
                    <div className="mt-4">
                      <button
                        type="button"
                        onClick={() => openReportModal(message.assistantMessageId!)}
                        className="rounded-full border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                      >
                        Report mistake
                      </button>
                    </div>
                  ) : null}
                </article>
              ))}

              {chatMutation.isPending ? (
                <article className="max-w-3xl rounded-[26px] border border-slate-200 bg-white px-4 py-4 shadow-sm">
                  <div className="flex items-center gap-2 text-sm text-slate-500">
                    <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-orange-500" />
                    The assistant is preparing a reply...
                  </div>
                </article>
              ) : null}
              <div ref={bottomRef} />
            </div>

            <form onSubmit={handleSubmit} className="border-t border-slate-200 bg-white/95 px-4 py-3 backdrop-blur sm:px-5">
              <div className="flex gap-3 sm:items-center">
                <div className="flex-1 rounded-[24px] border border-slate-200 bg-[#fcfaf6] px-4 py-2.5 focus-within:border-slate-400">
                  <textarea
                    ref={textareaRef}
                    value={chatInput}
                    onChange={(event) => setChatInput(event.target.value)}
                    onKeyDown={handleKeyDown}
                    rows={1}
                    placeholder={chatPlaceholder}
                    className="min-h-[24px] w-full resize-none border-0 bg-transparent p-0 text-sm leading-6 text-slate-900 outline-none placeholder:text-slate-400"
                  />
                </div>
                <button
                  type="submit"
                  disabled={chatMutation.isPending || !selectedAgentId}
                  className="rounded-[22px] bg-orange-500 px-4 py-2.5 text-sm font-semibold text-white shadow-[0_18px_32px_-20px_rgba(249,115,22,0.9)] transition hover:bg-orange-600"
                >
                  {chatMutation.isPending ? "Sending..." : "Send"}
                </button>
              </div>
            </form>
          </div>
        </Card>
      </div>

      <Modal
        open={Boolean(reportTargetId)}
        title={reportSuccess ? "Report submitted" : "Report a mistake"}
        description={
          reportSuccess
            ? "The issue is now available in the admin review queue."
            : "Give the admin reviewer a short description of what felt wrong so the bot can be corrected."
        }
        onClose={closeReportModal}
      >
        {reportSuccess ? (
          <div className="space-y-4">
            <InlineAlert tone="success" title="Admin review updated">
              The report has been stored with the affected answer and will show up in the issue queue.
            </InlineAlert>
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => navigate({ pathname: "/admin", search: `?agent=${selectedAgentId}&panel=issues` })}
                className="rounded-full bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
              >
                Open admin queue
              </button>
              <button
                type="button"
                onClick={closeReportModal}
                className="rounded-full border border-slate-200 px-5 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
              >
                Close
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {reportError ? (
              <InlineAlert tone="danger" title="Could not submit">
                {reportError}
              </InlineAlert>
            ) : null}
            <label className="block text-sm font-medium text-slate-700">
              Short reason
              <input
                value={reportReason}
                onChange={(event) => setReportReason(event.target.value)}
                placeholder="Example: Asked for a transaction ID on a general FAQ question"
                className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
              />
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Optional details
              <textarea
                value={reportDetails}
                onChange={(event) => setReportDetails(event.target.value)}
                rows={4}
                placeholder="Add any extra context that would help the reviewer or autofix flow."
                className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-3 text-sm text-slate-900 outline-none transition focus:border-slate-400"
              />
            </label>
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => void submitIssueReport()}
                disabled={reportMutation.isPending}
                className="rounded-full bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
              >
                {reportMutation.isPending ? "Submitting..." : "Submit report"}
              </button>
              <button
                type="button"
                onClick={closeReportModal}
                className="rounded-full border border-slate-200 px-5 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
