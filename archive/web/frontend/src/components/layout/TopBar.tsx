import React from "react";
import { useStore } from "../../store";

const STATUS_COLOR: Record<string, string> = {
  idle:           "bg-slate-500",
  recording:      "bg-red-500 animate-pulse",
  pending_review: "bg-yellow-400",
  approved:       "bg-green-500",
  rejected:       "bg-red-600",
  analyzed:       "bg-brand",
  error:          "bg-red-700",
};

export default function TopBar() {
  const { activeTrial, activeParticipant, showTelemetry, toggleTelemetry } = useStore();

  return (
    <header className="h-12 flex items-center justify-between px-5
                       bg-surface-card border-b border-surface-border shrink-0 gap-4">
      {/* participant / trial context */}
      <div className="flex items-center gap-3 min-w-0">
        <span className="text-slate-400 text-xs uppercase tracking-widest shrink-0">
          Pendulastic Clinical
        </span>
        {activeParticipant && (
          <>
            <span className="text-surface-border">|</span>
            <span className="text-slate-200 text-sm truncate">
              {activeParticipant.first_name} {activeParticipant.last_name}
            </span>
          </>
        )}
        {activeTrial && (
          <>
            <span className="text-surface-border">|</span>
            <span className="text-xs text-slate-400">Trial&nbsp;</span>
            <code className="text-brand text-xs">{activeTrial.id}</code>
            <span
              className={`w-2 h-2 rounded-full shrink-0 ${STATUS_COLOR[activeTrial.status] ?? "bg-slate-500"}`}
              title={activeTrial.status}
            />
            <span className="text-xs text-slate-400 capitalize">
              {activeTrial.status.replace("_", " ")}
            </span>
          </>
        )}
      </div>

      {/* right controls */}
      <div className="flex items-center gap-3 shrink-0">
        <button
          onClick={toggleTelemetry}
          className={[
            "text-xs px-3 py-1 rounded border transition-colors",
            showTelemetry
              ? "bg-brand/20 border-brand text-brand"
              : "border-surface-border text-slate-400 hover:text-slate-200",
          ].join(" ")}
          title="Toggle engineering telemetry"
        >
          {showTelemetry ? "Hide Telemetry" : "Show Telemetry"}
        </button>
      </div>
    </header>
  );
}
