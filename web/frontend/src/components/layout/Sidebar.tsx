import React from "react";
import { useStore } from "../../store";
import type { Screen } from "../../types";

const NAV: { id: Screen; label: string; icon: string }[] = [
  { id: "dashboard",   label: "Dashboard",   icon: "⬡" },
  { id: "participant", label: "Participant",  icon: "⊕" },
  { id: "record",      label: "Record",      icon: "⏺" },
  { id: "review",      label: "Review",      icon: "✓" },
  { id: "analysis",    label: "Analysis",    icon: "∿" },
];

export default function Sidebar() {
  const { screen, setScreen, activeTrial } = useStore();

  const isGated = (id: Screen) =>
    id === "analysis" &&
    activeTrial?.status !== "approved" &&
    activeTrial?.status !== "analyzed";

  return (
    <aside className="w-16 flex flex-col items-center gap-1 bg-surface-card border-r border-surface-border py-4 shrink-0">
      {/* Logo mark */}
      <div className="w-9 h-9 rounded-lg bg-brand flex items-center justify-center
                      text-white font-bold text-base mb-6 select-none">
        P
      </div>

      {NAV.map(({ id, label, icon }) => {
        const active  = screen === id;
        const gated   = isGated(id);
        return (
          <button
            key={id}
            title={gated ? `${label} — approve a trial first` : label}
            disabled={gated}
            onClick={() => setScreen(id)}
            className={[
              "group relative w-10 h-10 flex items-center justify-center rounded-lg",
              "transition-colors text-lg",
              active
                ? "bg-brand text-white"
                : gated
                  ? "text-slate-600 cursor-not-allowed"
                  : "text-slate-400 hover:bg-surface-border hover:text-slate-100",
            ].join(" ")}
          >
            {icon}
            {/* tooltip */}
            <span className="pointer-events-none absolute left-14 bg-surface-card border
                             border-surface-border text-xs text-slate-200 px-2 py-1 rounded
                             whitespace-nowrap opacity-0 group-hover:opacity-100 z-50 transition-opacity">
              {gated ? `${label} (locked)` : label}
            </span>
          </button>
        );
      })}
    </aside>
  );
}
