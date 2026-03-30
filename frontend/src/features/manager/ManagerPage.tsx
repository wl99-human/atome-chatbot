import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { generateAgent } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { InlineAlert } from "../../components/ui/InlineAlert";
import { StatusBadge } from "../../components/ui/StatusBadge";
import { useToast } from "../../components/ui/ToastProvider";
import { useAppShell } from "../../layout/AppShell";
import { classNames, formatFileSize, mergeFiles } from "../../lib/utils";
import type { Blueprint } from "../../types/api";

const instructionTemplates = [
  {
    label: "Grounded FAQ",
    text: "Answer only from the uploaded knowledge. If the answer is not supported, say that clearly and avoid making up policies or timelines.",
  },
  {
    label: "Lookup-first support",
    text: "If a customer asks about their own account status or a failed payment, ask for the required reference before using any enabled lookup tool.",
  },
];

export function ManagerPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { setSelectedAgentId } = useAppShell();
  const [agentName, setAgentName] = useState("Manager-Built Support Agent");
  const [description, setDescription] = useState(
    "A support agent generated from uploaded documents and manager instructions.",
  );
  const [instructions, setInstructions] = useState(
    "Answer only from uploaded knowledge. Ask follow-up questions before using any enabled account lookup tool.",
  );
  const [files, setFiles] = useState<File[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [latestBlueprint, setLatestBlueprint] = useState<Blueprint | null>(null);
  const [managerError, setManagerError] = useState("");
  const [generatedAgentId, setGeneratedAgentId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const generateMutation = useMutation({
    mutationFn: (formData: FormData) => generateAgent(formData),
    onSuccess: async (response) => {
      setLatestBlueprint(response.blueprint ?? null);
      setGeneratedAgentId(response.agent.id);
      setSelectedAgentId(response.agent.id);
      setManagerError("");
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] });
      showToast({
        title: "Agent generated",
        description: `${response.agent.name} is ready to test in the customer view.`,
        tone: "success",
      });
    },
    onError: (error: Error) => {
      setManagerError(error.message || "Agent generation failed.");
    },
  });

  function handleFileSelection(nextFiles: FileList | null) {
    if (!nextFiles) {
      return;
    }
    setFiles((current) => mergeFiles(current, Array.from(nextFiles)));
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    handleFileSelection(event.dataTransfer.files);
  }

  function applyTemplate(templateText: string) {
    setInstructions(templateText);
  }

  function removeFile(indexToRemove: number) {
    setFiles((current) => current.filter((_, index) => index !== indexToRemove));
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setManagerError("");
    const formData = new FormData();
    formData.append("agent_name", agentName);
    formData.append("description", description);
    formData.append("instructions", instructions);
    files.forEach((file) => formData.append("files", file));
    await generateMutation.mutateAsync(formData);
  }

  return (
    <div className="h-full min-h-0">
      <Card className="flex h-full min-h-0 flex-col space-y-2.5 overflow-hidden p-3">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate-500">Manager Workspace</p>
          <h1 className="mt-0.5 text-lg font-semibold tracking-tight text-slate-950">Build a support agent</h1>
        </div>

        {managerError ? (
          <InlineAlert tone="danger" title="Generation failed" className="py-2.5">
            {managerError}
          </InlineAlert>
        ) : null}

        {generatedAgentId ? (
          <InlineAlert tone="success" title="Agent generated" className="py-2.5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-sm leading-5">
                The generated agent is ready. Open it in customer view or keep refining the manager inputs.
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => navigate({ pathname: "/customer", search: `?agent=${generatedAgentId}` })}
                  className="rounded-full bg-slate-950 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800"
                >
                  Open in customer view
                </button>
                <button
                  type="button"
                  onClick={() => setGeneratedAgentId(null)}
                  className="rounded-full border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                >
                  Keep editing
                </button>
              </div>
            </div>
          </InlineAlert>
        ) : null}

        <div className="grid min-h-0 flex-1 gap-3 overflow-hidden xl:grid-cols-[minmax(0,1.3fr)_296px]">
          <form onSubmit={handleSubmit} className="flex min-h-0 flex-col gap-2.5 overflow-y-auto pr-1">
            <Card className="space-y-2.5 rounded-[18px] bg-slate-50 p-3">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-950 text-xs font-semibold text-white">1</div>
                <div>
                  <h2 className="text-sm font-semibold text-slate-950">Define the operating instructions</h2>
                  <p className="text-xs leading-5 text-slate-500">Describe how the generated agent should answer and handle lookups.</p>
                </div>
              </div>

              <label className="block text-sm font-medium text-slate-700">
                Agent name
                <input
                  value={agentName}
                  onChange={(event) => setAgentName(event.target.value)}
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                />
              </label>
              <label className="block text-sm font-medium text-slate-700">
                Description
                <textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  rows={1}
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                />
              </label>
              <div className="flex flex-wrap gap-2">
                {instructionTemplates.map((template) => (
                  <button
                    key={template.label}
                    type="button"
                    onClick={() => applyTemplate(template.text)}
                    className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                  >
                    Use {template.label}
                  </button>
                ))}
              </div>
              <label className="block text-sm font-medium text-slate-700">
                Instructions
                <textarea
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                    rows={2}
                    className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-slate-400"
                  />
                </label>
              </Card>

            <Card className="space-y-2.5 rounded-[18px] bg-slate-50 p-3">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-950 text-xs font-semibold text-white">2</div>
                <div>
                  <h2 className="text-sm font-semibold text-slate-950">Update knowledge base</h2>
                  <p className="text-xs leading-5 text-slate-500">Bring in the knowledge base material the agent should answer from.</p>
                </div>
              </div>

              <div
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragActive(true);
                }}
                onDragLeave={() => setDragActive(false)}
                onDrop={handleDrop}
                className={classNames(
                  "rounded-2xl border-2 border-dashed px-3 py-3 text-center transition",
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
                <p className="text-xs font-semibold text-slate-900">Drag files here or browse from disk</p>
                <p className="mt-0.5 text-[11px] text-slate-500">Uses the document types already supported by the backend parser.</p>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="mt-2 rounded-full border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                >
                  Choose files
                </button>
              </div>

              {files.length ? (
                <div className="max-h-20 space-y-1.5 overflow-y-auto pr-1">
                  {files.map((file, index) => (
                    <div key={`${file.name}-${file.size}-${file.lastModified}`} className="flex items-center justify-between gap-3 rounded-2xl bg-white px-3 py-2">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-semibold text-slate-900">{file.name}</p>
                        <p className="text-[11px] text-slate-500">{formatFileSize(file.size)}</p>
                      </div>
                      <button
                        type="button"
                        onClick={() => removeFile(index)}
                        className="rounded-full border border-slate-200 px-2.5 py-1 text-[11px] font-semibold text-slate-700 transition hover:bg-slate-50"
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs leading-5 text-slate-500">No files selected yet. You can still generate an agent from instructions alone.</p>
              )}
            </Card>

            <Card className="space-y-2.5 rounded-[18px] bg-slate-50 p-3">
              <div className="flex items-center gap-3">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-950 text-xs font-semibold text-white">3</div>
                <div>
                  <h2 className="text-sm font-semibold text-slate-950">Generate the agent</h2>
                  <p className="text-xs leading-5 text-slate-500">Create a structured blueprint and publish the resulting demo agent.</p>
                </div>
              </div>
              <button
                type="submit"
                disabled={generateMutation.isPending}
                className="rounded-full bg-orange-500 px-5 py-2.5 text-sm font-semibold text-white shadow-[0_18px_32px_-20px_rgba(249,115,22,0.9)] transition hover:bg-orange-600"
              >
                {generateMutation.isPending ? "Generating..." : "Generate agent"}
              </button>
            </Card>
          </form>

          <div className="min-h-0">
            <Card className="flex h-full min-h-0 flex-col space-y-2.5 p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-slate-500">Latest blueprint</p>
                  <h2 className="mt-1 text-lg font-semibold text-slate-950">Generated specification</h2>
                </div>
                {latestBlueprint ? <StatusBadge tone="success">Ready</StatusBadge> : null}
              </div>

              {latestBlueprint ? (
                <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
                  <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                    <p className="text-base font-semibold text-slate-950">{latestBlueprint.name}</p>
                    <p className="mt-1 text-sm leading-5 text-slate-600">{latestBlueprint.description}</p>
                  </div>
                  <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                    <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Instructions</p>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-5 text-slate-700">{latestBlueprint.instructions}</p>
                  </div>
                  <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                    <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Knowledge summary</p>
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-5 text-slate-700">{latestBlueprint.knowledge_summary}</p>
                  </div>
                  <div className="rounded-[18px] bg-slate-50 px-3 py-3">
                    <p className="font-mono text-[10px] uppercase tracking-[0.26em] text-slate-500">Enabled tools</p>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {latestBlueprint.enabled_tools.map((tool) => (
                        <StatusBadge key={tool} tone="accent">
                          {tool}
                        </StatusBadge>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <EmptyState
                  title="No blueprint yet"
                  description="Generate an agent and the structured blueprint will appear here with its instructions, knowledge summary, and enabled tools."
                />
              )}
            </Card>
          </div>
        </div>
      </Card>
    </div>
  );
}
