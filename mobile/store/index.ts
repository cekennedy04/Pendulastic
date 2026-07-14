/**
 * store/index.ts
 * ==============
 * Zustand store shared across all screens.
 * Mirrors the web frontend store with mobile-appropriate additions.
 */
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { Participant, Trial } from "../types";

interface AppState {
  activeParticipant:    Participant | null;
  activeTrial:          Trial | null;
  analysisUnlocked:     boolean;

  setActiveParticipant: (p: Participant | null) => void;
  setActiveTrial:       (t: Trial | null) => void;
  setAnalysisUnlocked:  (v: boolean) => void;
  updateTrialStatus:    (id: string, status: Trial["status"]) => void;
}

export const useStore = create<AppState>()(
  immer((set) => ({
    activeParticipant:    null,
    activeTrial:          null,
    analysisUnlocked:     false,

    setActiveParticipant: (p) => set((s) => { s.activeParticipant = p; }),
    setActiveTrial:       (t) => set((s) => { s.activeTrial = t; s.analysisUnlocked = false; }),
    setAnalysisUnlocked:  (v) => set((s) => { s.analysisUnlocked = v; }),
    updateTrialStatus: (id, status) =>
      set((s) => {
        if (s.activeTrial?.id === id) {
          s.activeTrial.status = status;
          if (status === "approved") s.analysisUnlocked = true;
        }
      }),
  }))
);
