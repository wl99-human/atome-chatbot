import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { autoFixIssue, fetchIssues, publishAgent, syncAgent } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { InlineAlert } from "../../components/ui/InlineAlert";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useToast } from "../../components/ui/ToastProvider";
import { useAppShell } from "../../layout/AppShell";
import { classNames, formatTimestamp, getIssueStatusTone, getSyncTone } from "../../lib/utils";

type AdminTab = "overview" | "issues";
type StatusFilter = "open" | "archived" | "all";

export function AdminPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { selectedAgent, selectedAgentId } = useAppShell();
  const [searchParams, setSearchParams] = useSearchParams();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [knowledgeBaseUrl, setKnowledgeBaseUrl] = useState("");
  const [guidelines, setGuidelines] = useState("");
  const [overviewNotice, setOverviewNotice] = useState<{
    tone: "success" | "warning" | "danger";
    title: string;
    message: string;
  } | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [expandedIssueId, setExpandedIssueId] = useState<string | null>(null);

  const activeTab: AdminTab = searchParams.get("panel") === "issues" ? "issues" : "overview";

  const issuesQuery = useQuery({
    queryKey: ["issues"],
    queryFn: fetchIssues,
  });

  useEffect(() => {
    if (!selectedAgent) {
      return;
    }
    setName(selectedAgent.name);
    setDescription(selectedAgent.description);
    setKnowledgeBaseUrl(selectedAgent.knowledge_base_url ?? "");
    setGuidelines(selectedAgent.additional_guidelines ?? "");
    setOverviewNotice(null);
  }, [selectedAgent]);

  const publishMutation = useMutation({
    mutationFn: () =>
      publishAgent(selectedAgentId, {
        name,
        description,
        knowledge_base_url: knowledgeBaseUrl,
        additional_guidelines: guidelines,
      }),
    onSuccess: async (agent) => {
      setOverviewNotice({
        tone: "success",
        title: "Revision published",
        message: `${agent.name} is now on revision ${agent.active_revision_version ?? "n/a"} with a refreshed source snapshot.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
      showToast({
        title: "Revision published",
        description: "The latest agent configuration is now active.",
        tone: "success",
      });
    },
    onError: (error: Error) => {
      setOverviewNotice({
        tone: "danger",
        title: "Publish failed",
        message: error.message,
      });
    },
  });

  const syncMutation = useMutation({
    mutationFn: () => syncAgent(selectedAgentId),
    onSuccess: async (result) => {
      setOverviewNotice({
        tone: result.fallback_used ? "warning" : "success",
        title: result.fallback_used ? "Sync completed with fallback content" : "Sources synced",
        message: `${result.documents_synced} documents and ${result.chunks_synced} chunks were indexed via ${result.sync_mode}.`,
      });
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
      showToast({
        title: "Source sync completed",
        description: `Indexed ${result.documents_synced} documents.`,
        tone: result.fallback_used ? "warning" : "success",
      });
    },
    onError: (error: Error) => {
      setOverviewNotice({
        tone: "danger",
        title: "Sync failed",
        message: error.message,
      });
    },
  });

  const autoFixMutation = useMutation({
    mutationFn: (issueId: string) => autoFixIssue(issueId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["issues"] }),
        queryClient.invalidateQueries({ queryKey: ["bootstrap"] }),
      ]);
      showToast({
        title: "Auto-fix completed",
        description: "The issue queue and agent metadata have been refreshed.",
        tone: "success",
      });
    },
    onError: (error: Error) => {
      showToast({
        title: "Auto-fix failed",
        description: error.message,
        tone: "danger",
      });
    },
  });

  function setTab(tab: AdminTab) {
    const next = new URLSearchParams(searchParams.toString());
    next.set("panel", tab);
    setSearchParams(next, { replace: true });
  }

  const isDirty =
    !!selectedAgent &&
    (name !== selectedAgent.name ||
      description !== selectedAgent.description ||
      knowledgeBaseUrl !== (selectedAgent.knowledge_base_url ?? "") ||
      guidelines !== selectedAgent.additional_guidelines);

  const issues =
    issuesQuery.data?.filter((issue) => issue.agent_id === selectedAgentId).filter((issue) => {
      if (statusFilter === "all") {
        return true;
      }
      if (statusFilter === "archived") {
        return issue.status === "archived";
      }
      return issue.status !== "archived";
    }) ?? [];

  if (!selectedAgent) {
    return (
      <EmptyState
        title="No agent selected"
        description="Choose an active agent from the shell to manage revisions or review its issue queue."
      />
    );
  }

  return (
    <div className="h-full min-h-0">
      <Card className="flex h-full min-h-0 flex-col space-y-2.5 overflow-hidden p-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate-500">Admin Studio</p>
            <h1 className="mt-0.5 text-lg font-semibold tracking-tight text-slate-950">Revision controls</h1>
          </div>
          <div className="flex flex-wrap gap-2">
            {(["overview", "issues"] as AdminTab[]).map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setTab(tab)}
                className={classNames(
                  "rounded-full px-3 py-1.5 text-xs font-semibold transition",
                  activeTab === tab
                    ? "border border-[#d5e54a] bg-[#f0ff5f] text-slate-950"
                    : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
                )}
              >
                {tab === "overview" ? "Overview" : "Issues"}
              </button>
            ))}
          </div>
        </div>

        {activeTab === "overview" ? (
          <div className="flex min-h-0 flex-1 flex-col space-y-2.5 overflow-hidden">
            {overviewNotice ? (
              <InlineAlert tone={overviewNotice.tone} title={overviewNotice.title} className="py-2.5">
                {overviewNotice.message}
              </InlineAlert>
            ) : null}

            {selectedAgent.last_sync_warning ? (
              <InlineAlert tone="warning" title="Sync warning" className="py-2.5">
                {selectedAgent.last_sync_warning}
              </InlineAlert>
            ) : null}

            <div className="grid gap-2 lg:grid-cols-4">
              <div className="rounded-[16px] border border-slate-200 bg-slate-50/80 px-3 py-2">
                <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-slate-500">Revision</p>
                <p className="mt-1 text-base font-semibold text-slate-950">{selectedAgent.active_revision_version ?? "n/a"}</p>
              </div>
              <div className="rounded-[16px] border border-slate-200 bg-slate-50/80 px-3 py-2">
                <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-slate-500">Sync</p>
                <div className="mt-1">
                  <span
                    className={classNames(
                      "inline-flex items-center rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]",
                      getSyncTone(selectedAgent.sync_mode, selectedAgent.fallback_used) === "success"
                        ? "bg-emerald-50 text-emerald-700"
                        : getSyncTone(selectedAgent.sync_mode, selectedAgent.fallback_used) === "warning"
                          ? "bg-amber-50 text-amber-800"
                          : "bg-slate-100 text-slate-700",
                    )}
                  >
                    {selectedAgent.sync_mode}
                  </span>
                </div>
              </div>
              <div className="rounded-[16px] border border-slate-200 bg-slate-50/80 px-3 py-2">
                <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-slate-500">Docs</p>
                <p className="mt-1 text-base font-semibold text-slate-950">{selectedAgent.documents_synced}</p>
              </div>
              <div className="rounded-[16px] border border-slate-200 bg-slate-50/80 px-3 py-2">
                <p className="font-mono text-[9px] uppercase tracking-[0.22em] text-slate-500">Chunks</p>
                <p className="mt-1 text-base font-semibold text-slate-950">{selectedAgent.chunks_synced}</p>
              </div>
            </div>

            <div className="grid min-h-0 flex-1 gap-3 overflow-hidden xl:grid-cols-[minmax(0,1.5fr)_240px]">
              <Card className="flex min-h-0 flex-col space-y-3 overflow-y-auto border border-slate-300 p-3 pr-2 shadow-[0_18px_40px_-34px_rgba(15,23,42,0.32)]">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold text-slate-950">Edit revision inputs</h2>
                    <p className="mt-0.5 text-[11px] leading-4 text-slate-500">Update the KB URL or guidelines, then publish.</p>
                  </div>
                  <StatusBadge tone={isDirty ? "warning" : "neutral"}>{isDirty ? "Unsaved changes" : "In sync"}</StatusBadge>
                </div>

                <label className="block text-sm font-medium text-slate-700">
                  Agent name
                  <input
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-4 py-2.5 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                  />
                </label>
                <label className="block text-sm font-medium text-slate-700">
                  Description
                  <textarea
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    rows={1}
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-4 py-2.5 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                  />
                </label>
                <label className="block text-sm font-medium text-slate-700">
                  Knowledge base URL
                  <input
                    value={knowledgeBaseUrl}
                    onChange={(event) => setKnowledgeBaseUrl(event.target.value)}
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-4 py-2.5 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                  />
                </label>
                <label className="block text-sm font-medium text-slate-700">
                  Additional guidelines
                  <textarea
                    value={guidelines}
                    onChange={(event) => setGuidelines(event.target.value)}
                    rows={5}
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-4 py-2.5 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                  />
                </label>

                <div className="flex flex-wrap gap-3">
                  <button
                    type="button"
                    onClick={() => publishMutation.mutate()}
                    disabled={!selectedAgentId || !isDirty || publishMutation.isPending}
                    className="rounded-full bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
                  >
                    {publishMutation.isPending ? "Publishing..." : "Publish revision"}
                  </button>
                  <button
                    type="button"
                    onClick={() => syncMutation.mutate()}
                    disabled={!selectedAgentId || syncMutation.isPending}
                    className="rounded-full border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                  >
                    {syncMutation.isPending ? "Syncing..." : "Sync sources"}
                  </button>
                </div>
              </Card>

              <Card className="flex min-h-0 flex-col space-y-2.5 bg-slate-50/55 p-3">
                <div>
                  <h2 className="text-base font-semibold text-slate-950">Reference</h2>
                  <p className="mt-1 text-[11px] leading-4 text-slate-500">Compact sync context.</p>
                </div>

                <div className="rounded-[18px] border border-slate-200 bg-white px-3 py-2.5">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Status</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <StatusBadge tone={getSyncTone(selectedAgent.sync_mode, selectedAgent.fallback_used)}>
                      {selectedAgent.sync_status}
                    </StatusBadge>
                    <StatusBadge tone="neutral">{selectedAgent.role}</StatusBadge>
                  </div>
                </div>

                <div className="min-h-0 rounded-[18px] border border-slate-200 bg-white px-3 py-2.5">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Knowledge source summary</p>
                  <div className="mt-2 max-h-28 overflow-y-auto pr-1">
                    <p className="whitespace-pre-wrap text-xs leading-5 text-slate-600">{selectedAgent.source_summary || "No summary available yet."}</p>
                  </div>
                </div>

                <div className="rounded-[18px] border border-slate-200 bg-white px-3 py-2.5">
                  <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Current KB URL</p>
                  <p className="mt-2 break-all text-xs leading-5 text-slate-600">
                    {selectedAgent.knowledge_base_url ?? "No knowledge base URL configured."}
                  </p>
                </div>
              </Card>
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col space-y-3 overflow-hidden">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <div className="flex flex-wrap gap-2">
                {(["open", "archived", "all"] as StatusFilter[]).map((filterValue) => (
                  <button
                    key={filterValue}
                    type="button"
                    onClick={() => setStatusFilter(filterValue)}
                    className={classNames(
                      "rounded-full px-3 py-1.5 text-xs font-semibold transition",
                      statusFilter === filterValue
                        ? "border border-[#d5e54a] bg-[#f0ff5f] text-slate-950"
                        : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
                    )}
                  >
                    {filterValue === "open" ? "Open queue" : filterValue === "archived" ? "Archived" : "All"}
                  </button>
                ))}
              </div>
              <p className="text-sm text-slate-500">{issues.length} issues shown for {selectedAgent.name}</p>
            </div>

            {issuesQuery.isLoading ? (
              <div className="grid gap-3">
                <div className="h-28 animate-pulse rounded-[28px] bg-slate-100" />
                <div className="h-28 animate-pulse rounded-[28px] bg-slate-100" />
              </div>
            ) : issues.length === 0 ? (
              <EmptyState
                title="No issues in this queue"
                description="Once a customer reports a mistake, the diagnosis and replay status will appear here."
              />
            ) : (
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
                {issues.map((issue) => {
                  const expanded = expandedIssueId === issue.id;
                  return (
                    <Card key={issue.id} className="space-y-3 p-4">
                      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                        <div className="space-y-2">
                          <div className="flex flex-wrap gap-2">
                            <StatusBadge tone={getIssueStatusTone(issue.status)}>{issue.status}</StatusBadge>
                            <StatusBadge tone="neutral">{issue.diagnosis_type ?? "issue"}</StatusBadge>
                          </div>
                          <h2 className="text-xl font-semibold text-slate-950">{issue.diagnosis_summary}</h2>
                          <p className="text-xs text-slate-500">Reported {formatTimestamp(issue.created_at)}</p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => setExpandedIssueId(expanded ? null : issue.id)}
                            className="rounded-full border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                          >
                            {expanded ? "Hide details" : "View details"}
                          </button>
                          <button
                            type="button"
                            onClick={() => autoFixMutation.mutate(issue.id)}
                            disabled={autoFixMutation.isPending && autoFixMutation.variables === issue.id}
                            className="rounded-full bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
                          >
                            {autoFixMutation.isPending && autoFixMutation.variables === issue.id ? "Running..." : "Auto-fix"}
                          </button>
                        </div>
                      </div>

                      {expanded ? (
                        <div className="grid gap-4 lg:grid-cols-2">
                          <div className="rounded-[24px] bg-slate-50 px-4 py-4">
                            <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-slate-500">Customer prompt</p>
                            <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{issue.prompt ?? "Prompt unavailable."}</p>
                          </div>
                          <div className="rounded-[24px] bg-slate-50 px-4 py-4">
                            <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-slate-500">Assistant answer</p>
                            <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{issue.answer ?? "Answer unavailable."}</p>
                          </div>
                          <div className="rounded-[24px] bg-slate-50 px-4 py-4">
                            <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-slate-500">Customer note</p>
                            <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{issue.customer_note || "No extra note provided."}</p>
                          </div>
                          <div className="rounded-[24px] bg-slate-50 px-4 py-4">
                            <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-slate-500">Replay and fix state</p>
                            {issue.latest_fix_attempt ? (
                              <div className="mt-3 space-y-2 text-sm leading-6 text-slate-700">
                                <p>
                                  <span className="font-semibold text-slate-900">Patch type:</span> {issue.latest_fix_attempt.patch_type}
                                </p>
                                <p>
                                  <span className="font-semibold text-slate-900">Replay passed:</span>{" "}
                                  {issue.latest_fix_attempt.replay_passed ? "Yes" : "No"}
                                </p>
                                <p>
                                  <span className="font-semibold text-slate-900">Auto published:</span>{" "}
                                  {issue.latest_fix_attempt.auto_published ? "Yes" : "No"}
                                </p>
                                <p className="text-slate-600">{issue.latest_fix_attempt.patch_summary}</p>
                              </div>
                            ) : (
                              <p className="mt-3 text-sm leading-6 text-slate-600">No auto-fix attempt recorded yet.</p>
                            )}
                          </div>
                        </div>
                      ) : null}
                    </Card>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
