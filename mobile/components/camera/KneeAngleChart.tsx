/**
 * components/camera/KneeAngleChart.tsx
 * ======================================
 * Real-time scrolling knee-angle waveform rendered in pure react-native-svg.
 * Displays the last T_WINDOW seconds of angle data as it arrives from the WS.
 *
 * - Y axis: 80° (full flexion) → 200° (beyond full extension)
 * - X axis: rolling window, newest data at the right edge
 * - Neutral reference: dashed line at 127° (typical resting angle)
 * - Current-angle badge: top-right corner, always readable
 *
 * The component is memo'd; the parent must pass a new `history` array
 * reference whenever it wants a redraw (shallow copy suffices).
 */

import React, { useMemo } from "react";
import Svg, {
  ClipPath,
  Defs,
  G,
  Line,
  Polyline,
  Rect,
  Text,
} from "react-native-svg";
import type { AnglePoint } from "../../types";

// ── Chart geometry ─────────────────────────────────────────────────────────────
const Y_MIN   = 80;
const Y_MAX   = 200;
const Y_RANGE = Y_MAX - Y_MIN;      // 120°
const T_WIN   = 12_000;             // 12 s visible window
const NEUTRAL = 127;                // typical resting neutral (reference line)

const PAD_L = 36;
const PAD_R = 10;
const PAD_T = 10;
const PAD_B = 22;

// ── Colors ─────────────────────────────────────────────────────────────────────
const C_BG      = "rgba(8,12,24,0.97)";
const C_GRID_MJ = "rgba(255,255,255,0.09)";  // major grid (60°)
const C_GRID_MN = "rgba(255,255,255,0.04)";  // minor grid (30°)
const C_LABEL_M = "rgba(255,255,255,0.52)";
const C_LABEL_D = "rgba(255,255,255,0.28)";
const C_NEUTRAL = "rgba(250,204,21,0.32)";
const C_NEUTRAL_LBL = "rgba(250,204,21,0.42)";
const C_WAVE    = "#e2e8f0";
const C_BADGE   = "#facc15";
const C_DIVIDER = "rgba(255,255,255,0.10)";
const C_EMPTY   = "rgba(255,255,255,0.22)";

// ── Component ──────────────────────────────────────────────────────────────────

interface KneeAngleChartProps {
  history:     AnglePoint[];
  width:       number;
  height:      number;
  isRecording: boolean;
}

interface ChartData {
  linePoints:   string | null;
  neutralY:     number;
  yTicks:       Array<{ label: string; y: number; isMajor: boolean }>;
  currentAngle: number | null;
  xAxisLabel:   string | null;
}

export const KneeAngleChart = React.memo(function KneeAngleChart({
  history,
  width,
  height,
  isRecording,
}: KneeAngleChartProps) {
  const plotW = width  - PAD_L - PAD_R;
  const plotH = height - PAD_T - PAD_B;

  const data: ChartData = useMemo(() => {
    const toY = (ang: number) =>
      PAD_T + ((Y_MAX - ang) / Y_RANGE) * plotH;

    // Y-axis ticks at every 30°; major every 60°
    const yTicks: Array<{ label: string; y: number; isMajor: boolean }> = [];
    for (let ang = 90; ang <= 190; ang += 30) {
      yTicks.push({ label: `${ang}`, y: toY(ang), isMajor: ang % 60 === 0 });
    }

    const neutralY = toY(NEUTRAL);

    if (history.length < 2 || plotW <= 0 || plotH <= 0) {
      return { linePoints: null, neutralY, yTicks, currentAngle: null, xAxisLabel: null };
    }

    const tMax = history[history.length - 1].t;
    const tMin = tMax - T_WIN;
    const visible = history.filter(p => p.t >= tMin);
    if (visible.length < 2) {
      return { linePoints: null, neutralY, yTicks, currentAngle: null, xAxisLabel: null };
    }

    const toX = (t: number) => PAD_L + ((t - tMin) / T_WIN) * plotW;

    const pts = visible
      .map(p => `${toX(p.t).toFixed(1)},${toY(p.angle).toFixed(1)}`)
      .join(" ");

    // Elapsed time label for x-axis right edge
    const elapsedMs = tMax - history[0].t;
    const elapsedS  = (elapsedMs / 1000).toFixed(1);
    const xAxisLabel = `${elapsedS} s`;

    return {
      linePoints:   pts,
      neutralY,
      yTicks,
      currentAngle: visible[visible.length - 1].angle,
      xAxisLabel,
    };
  }, [history, plotW, plotH]);

  const { linePoints, neutralY, yTicks, currentAngle, xAxisLabel } = data;

  return (
    <Svg width={width} height={height} style={{ backgroundColor: C_BG }}>
      <Defs>
        <ClipPath id="chartClip">
          <Rect x={PAD_L} y={PAD_T} width={plotW} height={plotH} />
        </ClipPath>
      </Defs>

      {/* Top divider */}
      <Line x1={0} y1={0.5} x2={width} y2={0.5}
        stroke={C_DIVIDER} strokeWidth={1} />

      {/* Y-axis backbone */}
      <Line
        x1={PAD_L} y1={PAD_T}
        x2={PAD_L} y2={PAD_T + plotH}
        stroke="rgba(255,255,255,0.14)" strokeWidth={1}
      />

      {/* Grid lines + Y labels */}
      {yTicks.map(({ label, y, isMajor }) => (
        <G key={label}>
          <Line
            x1={PAD_L} y1={y} x2={PAD_L + plotW} y2={y}
            stroke={isMajor ? C_GRID_MJ : C_GRID_MN}
            strokeWidth={1}
            clipPath="url(#chartClip)"
          />
          <Text
            x={PAD_L - 4} y={y + 4}
            fill={isMajor ? C_LABEL_M : C_LABEL_D}
            fontSize={9} textAnchor="end" fontFamily="monospace"
          >
            {label}
          </Text>
        </G>
      ))}

      {/* Neutral reference line */}
      <Line
        x1={PAD_L} y1={neutralY} x2={PAD_L + plotW} y2={neutralY}
        stroke={C_NEUTRAL} strokeWidth={1}
        strokeDasharray="3 5"
        clipPath="url(#chartClip)"
      />
      <Text
        x={PAD_L + 4} y={neutralY - 3}
        fill={C_NEUTRAL_LBL} fontSize={7.5}
      >
        ~neutral
      </Text>

      {/* Waveform */}
      {linePoints && (
        <Polyline
          points={linePoints}
          fill="none"
          stroke={C_WAVE}
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
          clipPath="url(#chartClip)"
        />
      )}

      {/* X-axis elapsed time label */}
      {xAxisLabel && (
        <Text
          x={PAD_L + plotW} y={PAD_T + plotH + 14}
          fill="rgba(255,255,255,0.30)" fontSize={8.5}
          textAnchor="end" fontFamily="monospace"
        >
          {xAxisLabel}
        </Text>
      )}
      <Text
        x={PAD_L} y={PAD_T + plotH + 14}
        fill="rgba(255,255,255,0.20)" fontSize={8.5}
        textAnchor="start" fontFamily="monospace"
      >
        -12 s
      </Text>

      {/* Current angle badge */}
      {currentAngle != null && (
        <>
          <Rect
            x={width - PAD_R - 54} y={PAD_T + 1}
            width={52} height={22} rx={5}
            fill="rgba(0,0,0,0.68)"
          />
          <Text
            x={width - PAD_R - 28} y={PAD_T + 15}
            fill={C_BADGE} fontSize={12.5}
            textAnchor="middle" fontFamily="monospace" fontWeight="bold"
          >
            {`${Math.round(currentAngle)}°`}
          </Text>
        </>
      )}

      {/* Empty-state prompt */}
      {!linePoints && (
        <Text
          x={width / 2} y={height / 2 + 5}
          fill={C_EMPTY} fontSize={11}
          textAnchor="middle"
        >
          {isRecording
            ? "Waiting for tracking data…"
            : "Start recording to see angle trace"}
        </Text>
      )}
    </Svg>
  );
});
