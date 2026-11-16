import React from "react";
import type { PtParameters } from "../../types";
import { useStore } from "../../store";

const ROWS: { key: keyof PtParameters; label: string; unit: string; tip: string }[] = [
  { key: "R2n",              label: "R²ₙ",           unit: "",          tip: "Normalized fit quality (1 = perfect pendulum)" },
  { key: "N",                label: "N",              unit: "cycles",    tip: "Number of oscillation cycles captured" },
  { key: "phi_max_ratio",    label: "φ_max ratio",   unit: "",          tip: "Peak angular excursion normalized to A₀" },
  { key: "omega_max_n",      label: "ω_max n",       unit: "",          tip: "Normalized peak angular velocity" },
  { key: "omega_peak_deg_s", label: "ω_peak",        unit: "°/s",       tip: "Peak angular velocity in degrees per second" },
  { key: "f",                label: "f",              unit: "Hz",        tip: "Oscillation frequency" },
  { key: "area_ratio",       label: "Area ratio",    unit: "",          tip: "Envelope area ratio — deviation from ideal pendulum" },
];

interface Props {
  params: PtParameters;
  masEstimate: number;
  ptScore: number;
}

const MAS_COLOR: Record<number, string> = {
  0: "text-mas-0",
  1: "text-mas-1",
  2: "text-mas-2",
  3: "text-mas-3",
  4: "text-mas-4",
};

export default function ParameterTable({ params, masEstimate, ptScore }: Props) {
  const { showTelemetry } = useStore();

  return (
    <div className="flex flex-col gap-4">
      {/* MAS hero */}
      <div className="card flex items-center gap-6">
        <div className="flex flex-col gap-0.5">
          <span className="label">MAS Estimate</span>
          <span className={`text-5xl font-bold ${MAS_COLOR[masEstimate] ?? "text-slate-200"}`}>
            {masEstimate}
          </span>
        </div>
        <div className="w-px h-16 bg-surface-border" />
        <div className="flex flex-col gap-0.5">
          <span className="label">PT Score</span>
          <span className="text-3xl font-semibold text-slate-200">{ptScore.toFixed(3)}</span>
        </div>
        <div className="ml-auto flex gap-2">
          {[0, 1, 2, 3, 4].map((n) => (
            <span
              key={n}
              className={[
                "w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold border-2 transition-all",
                masEstimate === n
                  ? `border-current ${MAS_COLOR[n]}`
                  : "border-surface-border text-slate-600",
              ].join(" ")}
            >
              {n}
            </span>
          ))}
        </div>
      </div>

      {/* Parameter table — always visible, engineering telemetry gated */}
      <div className="card">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b border-surface-border">
              <th className="label pb-2 font-normal">Parameter</th>
              <th className="label pb-2 font-normal text-right">Value</th>
              <th className="label pb-2 font-normal">Unit</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-border">
            {ROWS.map(({ key, label, unit, tip }) => (
              <tr key={key} title={tip} className="hover:bg-surface-border/30 transition-colors">
                <td className="py-1.5 text-slate-400">{label}</td>
                <td className="py-1.5 text-right font-mono text-slate-100">
                  {params[key].toFixed(4)}
                </td>
                <td className="py-1.5 pl-2 text-slate-500 text-xs">{unit}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {!showTelemetry && (
          <p className="text-xs text-slate-500 mt-2">
            Toggle "Show Telemetry" in the top bar for raw time-series data.
          </p>
        )}
      </div>
    </div>
  );
}
