/**
 * Zustand store with immer middleware.
 *
 * Protocol gate: the "analysis" screen is unreachable unless
 * activeTrial.status is "approved" or "analyzed".
 */
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type {
  Screen,
  Trial,
  Participant,
  AnalysisResult,
  MarkerCoords,
} from "../types";

interface AppState {
  // ── navigation ────────────────────────────────────────────────────────────
  screen:       Screen;
  setScreen:    (s: Screen) => void;

  // ── participants ──────────────────────────────────────────────────────────
  participants:     Participant[];
  activeParticipant: Participant | null;
  setParticipants:  (list: Participant[]) => void;
  setActiveParticipant: (p: Participant | null) => void;

  // ── trials ────────────────────────────────────────────────────────────────
  trials:      Trial[];
  activeTrial: Trial | null;
  setTrials:   (list: Trial[]) => void;
  setActiveTrial: (t: Trial | null) => void;
  updateTrialStatus: (id: string, status: Trial["status"]) => void;

  // ── marker adjustment (staging) ───────────────────────────────────────────
  pendingMarkers:   MarkerCoords | null;
  pendingStartFrame: number;
  setPendingMarkers: (m: MarkerCoords) => void;
  setPendingStartFrame: (f: number) => void;

  // ── analysis results ──────────────────────────────────────────────────────
  analysisResult: AnalysisResult | null;
  setAnalysisResult: (r: AnalysisResult | null) => void;

  // ── telemetry panel toggle (Progressive Disclosure) ───────────────────────
  showTelemetry: boolean;
  toggleTelemetry: () => void;
}

export const useStore = create<AppState>()(
  immer((set, get) => ({
    // ── navigation ──────────────────────────────────────────────────────────
    screen: "dashboard",
    setScreen: (s) => {
      // Protocol gate: block Analysis if no approved trial
      if (s === "analysis") {
        const t = get().activeTrial;
        if (!t || (t.status !== "approved" && t.status !== "analyzed")) {
          console.warn("Analysis screen gated: no approved trial");
          return;
        }
      }
      set((state) => { state.screen = s; });
    },

    // ── participants ─────────────────────────────────────────────────────────
    participants:          [],
    activeParticipant:     null,
    setParticipants:       (list) => set((s) => { s.participants = list; }),
    setActiveParticipant:  (p)    => set((s) => { s.activeParticipant = p; }),

    // ── trials ───────────────────────────────────────────────────────────────
    trials:      [],
    activeTrial: null,
    setTrials:   (list) => set((s) => { s.trials = list; }),
    setActiveTrial: (t) => set((s) => {
      s.activeTrial = t;
      // clear stale analysis when switching trials
      s.analysisResult = null;
    }),
    updateTrialStatus: (id, status) => set((s) => {
      const t = s.trials.find((x) => x.id === id);
      if (t) t.status = status;
      if (s.activeTrial?.id === id) s.activeTrial.status = status;
    }),

    // ── marker adjustment ────────────────────────────────────────────────────
    pendingMarkers:     null,
    pendingStartFrame:  0,
    setPendingMarkers:  (m) => set((s) => { s.pendingMarkers = m; }),
    setPendingStartFrame: (f) => set((s) => { s.pendingStartFrame = f; }),

    // ── analysis ─────────────────────────────────────────────────────────────
    analysisResult:    null,
    setAnalysisResult: (r) => set((s) => { s.analysisResult = r; }),

    // ── telemetry ────────────────────────────────────────────────────────────
    showTelemetry:   false,
    toggleTelemetry: () => set((s) => { s.showTelemetry = !s.showTelemetry; }),
  }))
);
