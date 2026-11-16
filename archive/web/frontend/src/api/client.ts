/**
 * Typed fetch client — all API calls go through here.
 * Base URL is relative so Vite's proxy routes /api → localhost:8000.
 */
import type {
  Trial,
  Participant,
  RecordRequest,
  AdjustMarkersRequest,
  ApprovalRequest,
  AnalysisResult,
} from "../types";

const BASE = "/api";

async function request<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body:    body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${method} ${path} → ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Trials ────────────────────────────────────────────────────────────────────

export const api = {
  health: () => request<{ status: string }>("GET", "/health"),

  // Participants
  createParticipant: (p: Omit<Participant, "id">) =>
    request<Participant>("POST", "/participants", p),

  getParticipant: (id: string) =>
    request<Participant>("GET", `/participants/${id}`),

  listParticipants: () =>
    request<Participant[]>("GET", "/participants"),

  patchParticipant: (id: string, patch: Partial<Participant>) =>
    request<Participant>("PATCH", `/participants/${id}`, patch),

  // Trials
  startRecording: (req: RecordRequest) =>
    request<Trial>("POST", "/trials/record", req),

  adjustMarkers: (req: AdjustMarkersRequest) =>
    request<{ status: string; start_frame: number }>(
      "POST", "/trials/adjust-markers", req
    ),

  approveTrial: (req: ApprovalRequest) =>
    request<{ status: string }>(
      "POST", `/trials/${req.trial_id}/approve`, req
    ),

  getTrial: (id: string) =>
    request<Trial>("GET", `/trials/${id}`),

  listTrials: () =>
    request<Trial[]>("GET", "/trials"),

  getAnalysis: (id: string) =>
    request<AnalysisResult>("GET", `/trials/${id}/analysis`),

  exportCsv: (id: string) => {
    window.open(`${BASE}/trials/${id}/export-csv`, "_blank");
  },
};
