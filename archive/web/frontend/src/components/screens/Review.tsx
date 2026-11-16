import React, { useState, useCallback } from "react";
import { api } from "../../api/client";
import { useStore } from "../../store";
import type { MarkerCoords } from "../../types";
import FittingApproval from "../ui/FittingApproval";
import MarkerAdjust from "../ui/MarkerAdjust";

const DEFAULT_MARKERS: MarkerCoords = {
  hip_x_norm:   0.5,  hip_y_norm:   0.25,
  knee_x_norm:  0.5,  knee_y_norm:  0.5,
  ankle_x_norm: 0.5,  ankle_y_norm: 0.75,
};

export default function Review() {
  const {
    activeTrial,
    updateTrialStatus,
    setScreen,
    setPendingMarkers,
    setPendingStartFrame,
    pendingMarkers,
    pendingStartFrame,
  } = useStore();

  const [busy,     setBusy]    = useState(false);
  const [saved,    setSaved]   = useState(false);
  const [error,    setError]   = useState<string | null>(null);
  const [rejected, setRejected] = useState(false);

  const markers     = pendingMarkers ?? DEFAULT_MARKERS;
  const startFrame  = pendingStartFrame;

  const onMarkersChange = useCallback(
    (m: MarkerCoords, sf: number) => {
      setPendingMarkers(m);
      setPendingStartFrame(sf);
      setSaved(false);
    },
    [setPendingMarkers, setPendingStartFrame]
  );

  const saveMarkers = async () => {
    if (!activeTrial) return;
    setBusy(true); setError(null);
    try {
      await api.adjustMarkers({
        trial_id:    activeTrial.id,
        markers,
        start_frame: startFrame,
      });
      setSaved(true);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doApprove = async () => {
    if (!activeTrial) return;
    // save markers first if not yet saved
    if (!saved) await saveMarkers();
    setBusy(true); setError(null);
    try {
      await api.approveTrial({ trial_id: activeTrial.id, approved: true });
      updateTrialStatus(activeTrial.id, "approved");
      setScreen("analysis");
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doReject = async () => {
    if (!activeTrial) return;
    setBusy(true); setError(null);
    try {
      await api.approveTrial({ trial_id: activeTrial.id, approved: false });
      updateTrialStatus(activeTrial.id, "rejected");
      setRejected(true);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!activeTrial) {
    return (
      <div className="p-6">
        <p className="text-slate-400">No active trial — start a recording first.</p>
      </div>
    );
  }

  if (rejected) {
    return (
      <div className="p-6 flex flex-col gap-4 max-w-lg">
        <div className="card flex flex-col gap-3">
          <h2 className="text-red-400 font-semibold">Trial Rejected</h2>
          <p className="text-slate-400 text-sm">
            Trial <code className="text-brand">{activeTrial.id}</code> has been rejected.
            You can start a new recording from the Record screen.
          </p>
          <button className="btn-secondary self-start" onClick={() => setScreen("record")}>
            New Recording
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 flex flex-col gap-5 max-w-3xl">
      {/* Trial context */}
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold text-slate-200">Review Trial</h2>
        <code className="text-brand text-xs">{activeTrial.id}</code>
        <span className="text-xs text-slate-400 capitalize">
          {activeTrial.leg_side} / {activeTrial.hpe_model}
        </span>
      </div>

      {/* Fitting approval */}
      <FittingApproval onApprove={doApprove} onReject={doReject} busy={busy} />

      {/* Marker adjustment */}
      <div className="card flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-200">Adjust Markers & Start Frame</h3>
          {saved && <span className="text-green-400 text-xs">Saved</span>}
        </div>
        <p className="text-xs text-slate-400">
          Drag the markers to correct joint positions. Set the start frame to the
          point where the leg is horizontal (baseline extension).
        </p>
        <MarkerAdjust
          initialMarkers={markers}
          startFrame={startFrame}
          maxFrame={300}
          onChange={onMarkersChange}
        />
        <button
          className="btn-secondary self-start text-xs"
          onClick={saveMarkers}
          disabled={busy || saved}
        >
          {busy ? "Saving…" : saved ? "Markers Saved" : "Save Marker Positions"}
        </button>
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}
    </div>
  );
}
