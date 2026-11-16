import React, { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useStore } from "../../store";
import FidelityBanner from "../ui/FidelityBanner";
import WaveformPlot   from "../ui/WaveformPlot";
import ParameterTable from "../ui/ParameterTable";

export default function Analysis() {
  const { activeTrial, analysisResult, setAnalysisResult } = useStore();
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    if (!activeTrial) return;
    if (analysisResult?.trial_id === activeTrial.id) return; // already loaded
    setLoading(true); setError(null);
    api.getAnalysis(activeTrial.id)
      .then(setAnalysisResult)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [activeTrial?.id]);

  if (!activeTrial) {
    return (
      <div className="p-6">
        <p className="text-slate-400">No approved trial — approve a trial in the Review screen first.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="p-6 flex items-center gap-3">
        <span className="w-4 h-4 rounded-full border-2 border-brand border-t-transparent
                         animate-spin" />
        <span className="text-slate-400 text-sm">Computing analysis…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <p className="text-red-400 text-sm">{error}</p>
      </div>
    );
  }

  if (!analysisResult) return null;

  const { fidelity, time_series, parameters, mas_estimate, pt_score } = analysisResult;

  return (
    <div className="p-6 flex flex-col gap-5 max-w-4xl">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-slate-200">Analysis</h2>
          <code className="text-brand text-xs">{activeTrial.id}</code>
          <span className="text-xs text-slate-400 capitalize">
            {activeTrial.leg_side} / {activeTrial.hpe_model}
          </span>
        </div>
        <button
          className="btn-secondary text-xs"
          onClick={() => api.exportCsv(activeTrial.id)}
          title="Download raw knee-angle CSV"
        >
          ↓ Export CSV
        </button>
      </div>

      {/* Signal quality banner */}
      <FidelityBanner fidelity={fidelity} />

      {/* Two-column layout: waveform left, scoreboard right */}
      <div className="grid grid-cols-5 gap-5">
        <div className="col-span-3">
          <WaveformPlot
            ts={time_series}
            neutralDeg={fidelity.neutral_deg}
            A0Deg={fidelity.A0_deg}
          />
        </div>
        <div className="col-span-2">
          <ParameterTable
            params={parameters}
            masEstimate={mas_estimate}
            ptScore={pt_score}
          />
        </div>
      </div>
    </div>
  );
}
