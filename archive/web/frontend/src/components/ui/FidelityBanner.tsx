import React from "react";
import type { FidelityInfo } from "../../types";

interface Props {
  fidelity: FidelityInfo;
}

export default function FidelityBanner({ fidelity }: Props) {
  const { passed, area_ratio_warn, spasticity_type, A0_deg, neutral_deg } = fidelity;

  return (
    <div
      className={[
        "flex items-center gap-4 px-4 py-2 rounded-lg border text-sm",
        passed
          ? "bg-green-950 border-green-800 text-green-300"
          : "bg-yellow-950 border-yellow-700 text-yellow-300",
      ].join(" ")}
    >
      <span className="text-base">{passed ? "✓" : "⚠"}</span>
      <span className="font-semibold">{passed ? "Signal quality: OK" : "Signal quality: Warning"}</span>
      {area_ratio_warn && (
        <span className="text-yellow-400 text-xs">area ratio low</span>
      )}
      <span className="text-xs text-slate-400 ml-auto">
        Type: <b className="text-slate-200">{spasticity_type}</b>
        &nbsp;|&nbsp;A₀: <b className="text-slate-200">{A0_deg.toFixed(1)}°</b>
        &nbsp;|&nbsp;Neutral: <b className="text-slate-200">{neutral_deg.toFixed(1)}°</b>
      </span>
    </div>
  );
}
