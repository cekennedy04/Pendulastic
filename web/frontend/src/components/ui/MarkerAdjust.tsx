/**
 * MarkerAdjust
 * Lets the clinician drag HIP / KNEE / ANKLE markers on a video frame image.
 * Marker positions are stored as normalized [0,1] coordinates.
 */
import React, { useRef, useState, useCallback } from "react";
import type { MarkerCoords } from "../../types";

type MarkerKey = "hip" | "knee" | "ankle";

const COLORS: Record<MarkerKey, string> = {
  hip:   "#f97316",
  knee:  "#0ea5e9",
  ankle: "#22c55e",
};

interface Props {
  initialMarkers: MarkerCoords;
  startFrame:     number;
  maxFrame:       number;
  /** Called whenever the user releases a marker or changes the start frame */
  onChange: (m: MarkerCoords, startFrame: number) => void;
}

export default function MarkerAdjust({
  initialMarkers,
  startFrame: initFrame,
  maxFrame,
  onChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [markers, setMarkers] = useState<MarkerCoords>(initialMarkers);
  const [frame,   setFrame]   = useState(initFrame);
  const [dragging, setDragging] = useState<MarkerKey | null>(null);

  const notify = useCallback(
    (m: MarkerCoords, f: number) => onChange(m, f),
    [onChange]
  );

  const onMouseDown = (key: MarkerKey) => (e: React.MouseEvent) => {
    e.preventDefault();
    setDragging(key);
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (!dragging || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const nx = Math.min(1, Math.max(0, (e.clientX - rect.left)  / rect.width));
    const ny = Math.min(1, Math.max(0, (e.clientY - rect.top)   / rect.height));
    const updated: MarkerCoords = {
      ...markers,
      [`${dragging}_x_norm`]: nx,
      [`${dragging}_y_norm`]: ny,
    } as MarkerCoords;
    setMarkers(updated);
    notify(updated, frame);
  };

  const onMouseUp = () => setDragging(null);

  const changeFrame = (f: number) => {
    setFrame(f);
    notify(markers, f);
  };

  const markerPx = (key: MarkerKey): { left: string; top: string } => {
    const xNorm = markers[`${key}_x_norm` as keyof MarkerCoords];
    const yNorm = markers[`${key}_y_norm` as keyof MarkerCoords];
    return {
      left: `${(xNorm * 100).toFixed(2)}%`,
      top:  `${(yNorm * 100).toFixed(2)}%`,
    };
  };

  return (
    <div className="flex flex-col gap-3">
      {/* start-frame scrubber */}
      <div className="flex items-center gap-3">
        <span className="label shrink-0">Start Frame</span>
        <input
          type="range"
          min={0}
          max={maxFrame}
          value={frame}
          onChange={(e) => changeFrame(Number(e.target.value))}
          className="flex-1 accent-brand"
        />
        <span className="text-slate-300 text-sm w-12 text-right">{frame}</span>
      </div>

      {/* marker canvas */}
      <div
        ref={containerRef}
        className="relative w-full aspect-video bg-slate-900 border border-surface-border
                   rounded-xl overflow-hidden cursor-crosshair select-none"
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <p className="absolute inset-0 flex items-center justify-center text-slate-600 text-sm
                      pointer-events-none">
          Video frame preview (Frame {frame})
        </p>

        {(["hip", "knee", "ankle"] as MarkerKey[]).map((key) => {
          const { left, top } = markerPx(key);
          return (
            <div
              key={key}
              onMouseDown={onMouseDown(key)}
              style={{ left, top, borderColor: COLORS[key] }}
              className="absolute -translate-x-1/2 -translate-y-1/2 w-5 h-5 rounded-full
                         border-2 cursor-grab active:cursor-grabbing bg-black/40
                         flex items-center justify-center"
              title={`Drag to move ${key.toUpperCase()} marker`}
            >
              <span className="text-[8px] uppercase font-bold"
                    style={{ color: COLORS[key] }}>
                {key[0]}
              </span>
            </div>
          );
        })}
      </div>

      {/* coordinate readout */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        {(["hip", "knee", "ankle"] as MarkerKey[]).map((key) => (
          <div key={key} className="flex flex-col gap-0.5 px-2 py-1 rounded bg-surface border border-surface-border">
            <span className="font-semibold uppercase" style={{ color: COLORS[key] }}>{key}</span>
            <span className="text-slate-400 font-mono">
              x {markers[`${key}_x_norm` as keyof MarkerCoords].toFixed(3)},&nbsp;
              y {markers[`${key}_y_norm` as keyof MarkerCoords].toFixed(3)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
