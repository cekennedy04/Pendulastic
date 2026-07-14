import React from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
} from "recharts";
import type { TimeSeries } from "../../types";
import { useStore } from "../../store";

interface Props {
  ts: TimeSeries;
  neutralDeg: number;
  A0Deg: number;
}

export default function WaveformPlot({ ts, neutralDeg, A0Deg }: Props) {
  const { showTelemetry } = useStore();

  // Merge t+angle and t_r+phi onto a common index
  const rawData = ts.t.map((t, i) => ({
    t:    +t.toFixed(3),
    ang:  +ts.angle[i].toFixed(2),
  }));

  const envData = ts.t_r.map((t, i) => ({
    t:   +t.toFixed(3),
    phi: +ts.phi[i].toFixed(2),
  }));

  return (
    <div className="card flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <span className="label">Knee Angle Waveform</span>
        <span className="text-xs text-slate-400">
          Neutral {neutralDeg.toFixed(1)}° · A₀ {A0Deg.toFixed(1)}°
        </span>
      </div>

      {/* Primary angle trace */}
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={rawData} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="t"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v: number) => `${v.toFixed(1)}s`}
            stroke="#6b7280"
            tick={{ fontSize: 10 }}
          />
          <YAxis
            domain={[0, 180]}
            tickFormatter={(v: number) => `${v}°`}
            stroke="#6b7280"
            tick={{ fontSize: 10 }}
            width={38}
          />
          <Tooltip
            contentStyle={{ backgroundColor: "#1f2937", border: "1px solid #374151", fontSize: 11 }}
            formatter={(v: number) => [`${v.toFixed(1)}°`, "Angle"]}
            labelFormatter={(l: number) => `t = ${l.toFixed(2)} s`}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <ReferenceLine
            y={neutralDeg}
            stroke="#60a5fa"
            strokeDasharray="4 4"
            label={{ value: "neutral", position: "insideTopRight", fontSize: 10, fill: "#60a5fa" }}
          />
          <Line
            type="monotone"
            dataKey="ang"
            name="Knee angle (°)"
            stroke="#0ea5e9"
            dot={false}
            strokeWidth={1.5}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {/* Envelope — shown only in telemetry mode */}
      {showTelemetry && (
        <>
          <span className="label">Envelope φ(t)</span>
          <ResponsiveContainer width="100%" height={140}>
            <ComposedChart data={envData} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis
                dataKey="t"
                type="number"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v: number) => `${v.toFixed(1)}s`}
                stroke="#6b7280"
                tick={{ fontSize: 10 }}
              />
              <YAxis
                tickFormatter={(v: number) => `${v.toFixed(0)}°`}
                stroke="#6b7280"
                tick={{ fontSize: 10 }}
                width={38}
              />
              <Tooltip
                contentStyle={{ backgroundColor: "#1f2937", border: "1px solid #374151", fontSize: 11 }}
                formatter={(v: number) => [`${v.toFixed(2)}°`, "φ"]}
                labelFormatter={(l: number) => `t = ${l.toFixed(2)} s`}
              />
              <Line
                type="monotone"
                dataKey="phi"
                name="φ envelope (°)"
                stroke="#a78bfa"
                dot={false}
                strokeWidth={1.5}
                isAnimationActive={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  );
}
