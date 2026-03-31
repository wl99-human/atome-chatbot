export function classNames(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

export function buildSearch(params: URLSearchParams) {
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function formatTimestamp(value?: string | null) {
  if (!value) {
    return "Unknown time";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("en-SG", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function formatFileSize(bytes: number) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function getIssueStatusTone(status: string) {
  if (status === "archived" || status === "published") {
    return "success";
  }
  if (status === "validated_pending_review") {
    return "accent";
  }
  if (status === "rejected") {
    return "danger";
  }
  return "warning";
}

export function getSyncTone(syncMode: string, fallbackUsed: boolean) {
  if (fallbackUsed) {
    return "warning";
  }
  if (syncMode === "live_api") {
    return "success";
  }
  return "neutral";
}

export function describePendingAction(pendingAction?: string | null) {
  if (pendingAction === "application_status") {
    return "Waiting for an application reference number so the bot can run the status lookup.";
  }
  if (pendingAction === "failed_transaction") {
    return "Waiting for a transaction ID so the bot can check the failed payment.";
  }
  return "";
}

export function getDraftStatusTone(status: string) {
  if (status === "ready_to_generate" || status === "generated") {
    return "success";
  }
  return "warning";
}

export function mergeFiles(currentFiles: File[], nextFiles: File[]) {
  const seen = new Set(currentFiles.map((file) => `${file.name}-${file.size}-${file.lastModified}`));
  const merged = [...currentFiles];
  nextFiles.forEach((file) => {
    const key = `${file.name}-${file.size}-${file.lastModified}`;
    if (!seen.has(key)) {
      merged.push(file);
      seen.add(key);
    }
  });
  return merged;
}
