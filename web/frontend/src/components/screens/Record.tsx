import React, { useState, useRef } from "react";
import { api } from "../../api/client";
import { useStore } from "../../store";
import type { LegSide, HpeModel } from "../../types";

export default function Record() {
  const { activeParticipant, setActiveTrial, setScreen } = useStore();

  const [legSide,    setLegSide]    = useState<LegSide>("left");
  const [hpeModel,   setHpeModel]   = useState<HpeModel>("mediapipe");
  const [videoPath,  setVideoPath]  = useState("");
  const [useCamera,  setUseCamera]  = useState(false);
  const [countdown,  setCountdown]  = useState<number | null>(null);
  const [recording,  setRecording]  = useState(false);
  const [error,      setError]      = useState<string | null>(null);

  const countRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startCountdown = () => {
    setCountdown(3);
    countRef.current = setInterval(() => {
      setCountdown((c) => {
        if (c === null || c <= 1) {
          clearInterval(countRef.current!);
          beginRecording();
          return null;
        }
        return c - 1;
      });
    }, 1000);
  };

  const beginRecording = () => {
    setRecording(true);
    // Flash the screen
    document.body.classList.add("recording-flash");
    setTimeout(() => document.body.classList.remove("recording-flash"), 300);
  };

  const submit = async () => {
    if (!activeParticipant) {
      setError("Select a participant first (Participant screen).");
      return;
    }
    setError(null);
    try {
      const trial = await api.startRecording({
        participant_id: activeParticipant.id,
        leg_side:       legSide,
        hpe_model:      hpeModel,
        video_path:     videoPath || undefined,
      });
      setActiveTrial(trial);
      setScreen("review");
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setRecording(false);
    }
  };

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) setVideoPath(f.name);
  };

  return (
    <div className="p-6 flex flex-col gap-6 max-w-2xl">
      <div className="card flex flex-col gap-5">
        <h2 className="text-sm font-semibold text-slate-200">New Trial Recording</h2>

        {!activeParticipant && (
          <p className="text-yellow-400 text-sm">
            No participant selected — go to Participant screen first.
          </p>
        )}

        {/* Leg side */}
        <div className="flex flex-col gap-1">
          <span className="label">Affected Leg</span>
          <div className="flex gap-2">
            {(["left", "right"] as LegSide[]).map((s) => (
              <button
                key={s}
                onClick={() => setLegSide(s)}
                className={[
                  "px-5 py-2 rounded-lg text-sm font-medium transition-colors",
                  legSide === s
                    ? "bg-brand text-white"
                    : "bg-surface border border-surface-border text-slate-300 hover:border-brand",
                ].join(" ")}
              >
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
        </div>

        {/* HPE model */}
        <div className="flex flex-col gap-1">
          <span className="label">HPE Model</span>
          <div className="flex gap-2 flex-wrap">
            {(["mediapipe", "rtmpose", "hrnet", "vitpose"] as HpeModel[]).map((m) => (
              <button
                key={m}
                onClick={() => setHpeModel(m)}
                className={[
                  "px-4 py-1.5 rounded-lg text-xs font-medium transition-colors",
                  hpeModel === m
                    ? "bg-brand text-white"
                    : "bg-surface border border-surface-border text-slate-300 hover:border-brand",
                ].join(" ")}
              >
                {m}
              </button>
            ))}
          </div>
        </div>

        {/* Source: video file vs live camera */}
        <div className="flex flex-col gap-2">
          <span className="label">Source</span>
          <div className="flex gap-2">
            {[false, true].map((cam) => (
              <button
                key={String(cam)}
                onClick={() => setUseCamera(cam)}
                className={[
                  "px-4 py-2 rounded-lg text-sm font-medium transition-colors",
                  useCamera === cam
                    ? "bg-brand text-white"
                    : "bg-surface border border-surface-border text-slate-300 hover:border-brand",
                ].join(" ")}
              >
                {cam ? "Live Camera" : "Upload Video"}
              </button>
            ))}
          </div>

          {!useCamera && (
            <div className="flex items-center gap-3 mt-1">
              <label className="btn-secondary cursor-pointer text-xs">
                Browse…
                <input type="file" accept="video/*" className="sr-only" onChange={handleFile} />
              </label>
              {videoPath && (
                <span className="text-slate-300 text-xs truncate">{videoPath}</span>
              )}
            </div>
          )}
        </div>

        {/* Countdown / Record */}
        {useCamera && (
          <div className="flex flex-col items-center gap-4 py-4">
            {countdown !== null ? (
              <span className="text-6xl font-bold text-brand animate-pulse">{countdown}</span>
            ) : recording ? (
              <span className="text-red-400 text-sm animate-pulse">● Recording…</span>
            ) : (
              <button className="btn-danger px-8 py-3 text-base" onClick={startCountdown}>
                Start Countdown (3 s)
              </button>
            )}
          </div>
        )}

        {error && <p className="text-red-400 text-sm">{error}</p>}

        <button
          className="btn-primary self-start"
          onClick={submit}
          disabled={recording || countdown !== null || !activeParticipant}
        >
          {recording ? "Processing…" : "Submit Trial"}
        </button>
      </div>
    </div>
  );
}
