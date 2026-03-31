import { useEffect } from "react";
import { NavLink, Outlet, useLocation, useOutletContext, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchBootstrap } from "../api/client";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/EmptyState";
import { StatusBadge } from "../components/ui/StatusBadge";
import { buildSearch, classNames, getSyncTone } from "../lib/utils";
import type { Agent } from "../types/api";

type AppShellContextValue = {
  agents: Agent[];
  selectedAgent: Agent | null;
  selectedAgentId: string;
  setSelectedAgentId: (agentId: string) => void;
  modelName: string;
};

const routes = [
  {
    path: "/customer",
    label: "Customer",
    description: "Test grounded answers, follow-up handling, and issue reporting.",
  },
  {
    path: "/admin",
    label: "Admin",
    description: "Review sync health, publish revisions, and resolve bot mistakes.",
  },
  {
    path: "/manager",
    label: "Manager",
    description: "Upload docs, author instructions, and generate a new support agent.",
  },
];

export function AppShell() {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const searchText = searchParams.toString();
  const hideAgentSidebar = location.pathname === "/manager";
  const bootstrapQuery = useQuery({
    queryKey: ["bootstrap"],
    queryFn: fetchBootstrap,
  });

  const agents = bootstrapQuery.data?.agents ?? [];
  const requestedAgentId = searchParams.get("agent") ?? "";
  const fallbackAgentId = bootstrapQuery.data?.default_agent_id ?? agents[0]?.id ?? "";
  const selectedAgentId = agents.some((agent) => agent.id === requestedAgentId)
    ? requestedAgentId
    : fallbackAgentId;
  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId) ?? null;

  useEffect(() => {
    if (!selectedAgentId || requestedAgentId === selectedAgentId) {
      return;
    }
    const next = new URLSearchParams(searchText);
    next.set("agent", selectedAgentId);
    next.delete("conversation");
    setSearchParams(next, { replace: true });
  }, [requestedAgentId, searchText, selectedAgentId, setSearchParams]);

  function setSelectedAgentId(agentId: string) {
    const next = new URLSearchParams(searchText);
    if (agentId) {
      next.set("agent", agentId);
    } else {
      next.delete("agent");
    }
    if (agentId !== requestedAgentId) {
      next.delete("conversation");
    }
    setSearchParams(next, { replace: true });
  }

  if (bootstrapQuery.isLoading) {
    return (
      <div className="h-screen overflow-hidden bg-app px-4 py-1.5 text-slate-900 sm:px-6 lg:px-8">
        <div className="mx-auto h-full max-w-7xl space-y-4">
          <div className="h-20 animate-pulse rounded-[24px] bg-slate-100" />
          <div className="grid gap-3 xl:grid-cols-[248px_minmax(0,1fr)]">
            <div className="h-80 animate-pulse rounded-[32px] bg-white/80" />
            <div className="h-[680px] animate-pulse rounded-[32px] bg-white/80" />
          </div>
        </div>
      </div>
    );
  }

  if (bootstrapQuery.isError) {
    return (
      <div className="h-screen overflow-auto bg-app px-4 py-8 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-3xl">
          <EmptyState
            title="The demo could not load"
            description={
              bootstrapQuery.error instanceof Error
                ? bootstrapQuery.error.message
                : "The frontend failed to load the app bootstrap."
            }
            action={
              <button
                type="button"
                onClick={() => void bootstrapQuery.refetch()}
                className="rounded-full bg-slate-950 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800"
              >
                Retry
              </button>
            }
          />
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen overflow-hidden bg-app text-slate-900">
      <div className="mx-auto flex h-full max-w-7xl flex-col overflow-hidden px-4 py-1.5 sm:px-6 lg:px-8">
        <header className="rounded-[16px] border border-slate-200 bg-white px-3 py-2 shadow-[0_10px_24px_-26px_rgba(15,23,42,0.2)]">
          <div className="flex flex-col gap-1.5 xl:flex-row xl:items-center xl:justify-between">
            <div className="max-w-xl">
              <h1 className="text-sm font-semibold tracking-tight text-slate-950 sm:text-base">
                Atome support bot console
              </h1>
            </div>
            <div className="flex flex-wrap items-center gap-2 xl:justify-end">
              <StatusBadge tone="accent">{bootstrapQuery.data?.model ?? "Model unavailable"}</StatusBadge>
              {selectedAgent ? (
                <StatusBadge tone={getSyncTone(selectedAgent.sync_mode, selectedAgent.fallback_used)}>
                  {selectedAgent.sync_mode}
                </StatusBadge>
              ) : null}
            </div>
          </div>

          <nav className="mt-1.5 flex flex-wrap gap-1.5">
            {routes.map((route) => (
              <NavLink
                key={route.path}
                to={{ pathname: route.path, search: buildSearch(searchParams) }}
                className={({ isActive }) =>
                  classNames(
                    "rounded-[14px] border px-2.5 py-1.5 text-left transition",
                    isActive
                      ? "border-[#d5e54a] bg-[#f0ff5f] text-slate-950 shadow-[0_10px_24px_-18px_rgba(170,185,0,0.8)]"
                      : "border-slate-200 bg-slate-50/80 text-slate-700 hover:border-slate-300 hover:bg-white",
                  )
                }
              >
                {({ isActive }) => (
                  <div className="min-w-[126px]">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.06em]">{route.label}</span>
                    <p className={classNames("mt-0.5 text-[9px] leading-3", isActive ? "text-slate-700" : "text-slate-500")}>
                      {route.description}
                    </p>
                  </div>
                )}
              </NavLink>
            ))}
          </nav>
        </header>

        <div
          className={classNames(
            "mt-1.5 grid min-h-0 flex-1 overflow-hidden",
            hideAgentSidebar ? "grid-cols-1" : "gap-3 xl:grid-cols-[232px_minmax(0,1fr)]",
          )}
        >
          {hideAgentSidebar ? null : (
            <aside className="min-h-0 overflow-y-auto pr-1">
              <Card className="space-y-3 p-3">
                <div>
                  <p className="font-mono text-[11px] uppercase tracking-[0.32em] text-slate-500">Active Agent</p>
                  <h2 className="mt-1 text-base font-semibold text-slate-950">Choose agent</h2>
                </div>
                <label className="block text-sm font-medium text-slate-700">
                  Agent
                  <select
                    value={selectedAgentId}
                    onChange={(event) => setSelectedAgentId(event.target.value)}
                    className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                  >
                    {agents.map((agent) => (
                      <option key={agent.id} value={agent.id}>
                        {agent.name}
                      </option>
                    ))}
                  </select>
                </label>

                {selectedAgent ? (
                  <div className="space-y-3">
                    <div className="rounded-[18px] bg-slate-50 px-3 py-2.5">
                      <p className="text-sm font-semibold text-slate-900">{selectedAgent.name}</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <StatusBadge tone="neutral">Revision {selectedAgent.active_revision_version ?? "n/a"}</StatusBadge>
                      <StatusBadge tone={getSyncTone(selectedAgent.sync_mode, selectedAgent.fallback_used)}>
                        {selectedAgent.sync_status}
                      </StatusBadge>
                    </div>
                    <dl className="space-y-2 text-xs text-slate-600">
                      <div className="flex items-center justify-between gap-3">
                        <dt>Sync mode</dt>
                        <dd className="font-medium text-slate-900">{selectedAgent.sync_mode}</dd>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <dt>Indexed docs</dt>
                        <dd className="font-medium text-slate-900">{selectedAgent.documents_synced}</dd>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <dt>Indexed chunks</dt>
                        <dd className="font-medium text-slate-900">{selectedAgent.chunks_synced}</dd>
                      </div>
                    </dl>
                    {selectedAgent.last_sync_warning ? (
                      <div className="rounded-[18px] border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                        {selectedAgent.last_sync_warning}
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <p className="text-sm text-slate-500">No agent is available yet.</p>
                )}
              </Card>
            </aside>
          )}

          <main className="min-h-0 min-w-0 overflow-hidden">
            <Outlet
              context={{
                agents,
                selectedAgent,
                selectedAgentId,
                setSelectedAgentId,
                modelName: bootstrapQuery.data?.model ?? "",
              }}
            />
          </main>
        </div>
      </div>
    </div>
  );
}

export function useAppShell() {
  return useOutletContext<AppShellContextValue>();
}
