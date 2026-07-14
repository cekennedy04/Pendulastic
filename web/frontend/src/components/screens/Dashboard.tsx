import React, { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useStore } from "../../store";
import type { Trial } from "../../types";

const STATUS_BADGE: Record<string, string> = {
  idle:           "bg-slate-700 text-slate-300",
  recording:      "bg-red-900 text-red-300 animate-pulse",
  pending_review: "bg-yellow-900 text-yellow-300",
  approved:       "bg-green-900 text-green-300",
  rejected:       "bg-red-900 text-red-400",
  analyzed:       "bg-sky-900 text-sky-300",
  error:          "bg-red-950 text-red-400",
};

const MAS_BADGE: Record<number, string> = {
  0: "bg-mas-0/20 text-mas-0 border border-mas-0/40",
  1: "bg-mas-1/20 text-mas-1 border border-mas-1/40",
  2: "bg-mas-2/20 text-mas-2 border border-mas-2/40",
  3: "bg-mas-3/20 text-mas-3 border border-mas-3/40",
  4: "bg-mas-4/20 text-mas-4 border border-mas-4/40",
};

export default function Dashboard() {
  const { setScreen, setActiveTrial, setActiveParticipant, participants } = useStore();
  const [trials, setTrials] = useState<Trial[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    api.listTrials()
      .then(setTrials)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const openTrial = (t: Trial) => {
    setActiveTrial(t);
    const p = participants.find((x) => x.id === t.participant_id) ?? null;
    setActiveParticipant(p);
    if (t.status === "pending_review" || t.status === "approved") {
      setScreen("review");
    } else if (t.status === "analyzed") {
      setScreen("analysis");
    }
  };

  return (
    <div className="p-6 flex flex-col gap-6 max-w-5xl">
      {/* summary row */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total Trials",    value: trials.length },
          { label: "Pending Review",  value: trials.filter((t) => t.status === "pending_review").length },
          { label: "Analyzed",        value: trials.filter((t) => t.status === "analyzed").length },
          { label: "Participants",    value: participants.length },
        ].map(({ label, value }) => (
          <div key={label} className="card flex flex-col gap-1">
            <span className="label">{label}</span>
            <span className="text-2xl font-bold text-slate-100">{value}</span>
          </div>
        ))}
      </div>

      {/* recent trials */}
      <div className="card flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">Recent Trials</h2>
          <button className="btn-primary text-xs" onClick={() => setScreen("record")}>
            + New Trial
          </button>
        </div>

        {loading && <p className="text-slate-400 text-sm">Loading…</p>}
        {error   && <p className="text-red-400 text-sm">{error}</p>}

        {!loading && trials.length === 0 && (
          <p className="text-slate-500 text-sm">No trials yet — start a new recording.</p>
        )}

        <div className="flex flex-col gap-2">
          {trials.slice(-20).reverse().map((t) => (
            <button
              key={t.id}
              onClick={() => openTrial(t)}
              className="flex items-center gap-3 px-3 py-2 rounded-lg
                         hover:bg-surface-border transition-colors text-left"
            >
              <code className="text-brand text-xs w-20 shrink-0">{t.id}</code>
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium w-36 text-center
                               ${STATUS_BADGE[t.status] ?? "bg-slate-700"}`}>
                {t.status.replace("_", " ")}
              </span>
              <span className="text-slate-400 text-xs capitalize shrink-0">
                {t.leg_side} / {t.hpe_model}
              </span>
              <span className="text-slate-500 text-xs ml-auto">
                {new Date(t.created_at).toLocaleDateString()}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* MAS legend */}
      <div className="card flex gap-4 flex-wrap items-center">
        <span className="label">MAS Reference</span>
        {[0, 1, 2, 3, 4].map((n) => (
          <span key={n} className={`text-xs px-3 py-1 rounded-full font-semibold ${MAS_BADGE[n]}`}>
            MAS {n}
          </span>
        ))}
      </div>
    </div>
  );
}
