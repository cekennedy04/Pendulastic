/**
 * components/camera/CameraStream.tsx
 * =====================================
 * Full live-recording screen: camera + skeleton overlay + real-time angle
 * chart + joint-correction mode.
 *
 * Layout (top → bottom)
 * ─────────────────────
 *   ┌─────────────────────────────┐
 *   │  HUD pills (status)         │  absolute, top of camera
 *   │                             │
 *   │      CAMERA / SKELETON      │  flex: 1  (shrinks when chart opens)
 *   │                             │
 *   │  [correction handles]       │  absolute, when correctionMode
 *   ├─────────────────────────────┤
 *   │   ANGLE CHART  (animated)   │  0 → CHART_H px as recording starts
 *   ├─────────────────────────────┤
 *   │  [Correct]  [●]  [Chart]    │  fixed 92 px controls bar
 *   └─────────────────────────────┘
 *
 * Correction mode
 * ───────────────
 * Tap "Correct" to freeze skeleton updates and show three draggable joint
 * handles (native Views with PanResponder).  Dragging a handle and releasing
 * sends a WsJointCorrection JSON message via `sendJson`.  Tap "Done" to
 * resume normal tracking.
 */

import { CameraView, useCameraPermissions } from "expo-camera";
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import {
  ActivityIndicator,
  Animated,
  Dimensions,
  PanResponder,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useCameraCapture } from "../../hooks/useCameraCapture";
import {
  useWebSocketStream,
  type WsState,
} from "../../hooks/useWebSocketStream";
import type {
  AnglePoint,
  HpeModel,
  LegSide,
  PoseKeypoints,
  TrackingStatus,
  WsJointCorrection,
  WsKeypointPayload,
} from "../../types";
import { KneeAngleChart } from "./KneeAngleChart";
import { SkeletonOverlay } from "./SkeletonOverlay";

// ── Constants ──────────────────────────────────────────────────────────────────

const CHART_H       = 168;
const CONTROLS_H    = 92;
const MAX_HISTORY   = 360;           // 24 s at 15 fps
const CHART_FPS     = 8;             // chart refresh rate (saves JS work)
const CHART_TICK_MS = 1000 / CHART_FPS;
const SCREEN_W      = Dimensions.get("window").width;

const WS_LABEL: Record<WsState, string> = {
  connecting: "Connecting…",
  open:       "Live",
  closed:     "Stopped",
  error:      "Reconnecting…",
};

const TRACKING_COLOR: Record<TrackingStatus, string> = {
  stable:    "#4ade80",
  uncertain: "#facc15",
  lost:      "#f87171",
};

type JointKey = "hip" | "knee" | "ankle";

const JOINT_META: Array<{ key: JointKey; color: string; label: string }> = [
  { key: "hip",   color: "#f97316", label: "H" },
  { key: "knee",  color: "#0ea5e9", label: "K" },
  { key: "ankle", color: "#22c55e", label: "A" },
];

// ── DraggableJoint ─────────────────────────────────────────────────────────────

const HANDLE_R = 22;
const HANDLE_D = HANDLE_R * 2;

interface DraggableJointProps {
  joint:       JointKey;
  initX:       number;
  initY:       number;
  color:       string;
  label:       string;
  containerWRef: React.MutableRefObject<number>;
  containerHRef: React.MutableRefObject<number>;
  onCorrect:   (joint: JointKey, x: number, y: number) => void;
}

const DraggableJoint = React.memo(function DraggableJoint({
  joint, initX, initY, color, label,
  containerWRef, containerHRef, onCorrect,
}: DraggableJointProps) {
  // translateX/Y position the center of the handle at (cx, cy).
  const tx = useRef(new Animated.Value(initX - HANDLE_R)).current;
  const ty = useRef(new Animated.Value(initY - HANDLE_R)).current;

  const posRef   = useRef({ x: initX, y: initY });
  const startRef = useRef({ x: initX, y: initY });
  const dragging = useRef(false);

  // Sync to fresh keypoint position when correction mode is first entered.
  useEffect(() => {
    if (!dragging.current) {
      tx.setValue(initX - HANDLE_R);
      ty.setValue(initY - HANDLE_R);
      posRef.current = { x: initX, y: initY };
    }
  }, [initX, initY, tx, ty]);

  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onPanResponderGrant: () => {
        dragging.current = true;
        startRef.current = { ...posRef.current };
      },
      onPanResponderMove: (_, gs) => {
        const cx = startRef.current.x + gs.dx;
        const cy = startRef.current.y + gs.dy;
        tx.setValue(cx - HANDLE_R);
        ty.setValue(cy - HANDLE_R);
        posRef.current = { x: cx, y: cy };
      },
      onPanResponderRelease: () => {
        dragging.current = false;
        const { x, y } = posRef.current;
        const cw = containerWRef.current || 1;
        const ch = containerHRef.current || 1;
        onCorrect(
          joint,
          Math.max(0, Math.min(1, x / cw)),
          Math.max(0, Math.min(1, y / ch))
        );
      },
    })
  ).current;

  return (
    <Animated.View
      {...panResponder.panHandlers}
      style={[
        styles.dragHandle,
        {
          width:           HANDLE_D,
          height:          HANDLE_D,
          borderRadius:    HANDLE_R,
          borderColor:     color,
          backgroundColor: `${color}28`,
          transform: [{ translateX: tx }, { translateY: ty }],
        },
      ]}
    >
      <Text style={[styles.dragLabel, { color }]}>{label}</Text>
    </Animated.View>
  );
});

// ── Props ──────────────────────────────────────────────────────────────────────

interface Props {
  trialId:        string;
  model:          HpeModel;
  legSide:        LegSide;
  countdownSecs:  number;
  initialFacing?: "back" | "front";
  onTrialComplete: (csvPath: string) => void;
}

// ── CameraStream ───────────────────────────────────────────────────────────────

export function CameraStream({
  trialId,
  model,
  legSide,
  countdownSecs,
  initialFacing = "back",
  onTrialComplete,
}: Props) {
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);

  // Camera area dimensions (updates when chart panel opens/closes).
  const [camLayout, setCamLayout] = useState({ width: 1, height: 1 });
  const camWRef = useRef(1);
  const camHRef = useRef(1);

  // Tracking state.
  const [isRecording,   setIsRecording]   = useState(false);
  const [countdown,     setCountdown]     = useState<number | null>(null);
  const [latestKpts,    setLatestKpts]    = useState<PoseKeypoints | null>(null);
  const [kneeAngle,     setKneeAngle]     = useState(180);
  const [trackStatus,   setTrackStatus]   = useState<TrackingStatus>("stable");
  const [elapsedSec,    setElapsedSec]    = useState(0);
  const [facing,        setFacing]        = useState<"back" | "front">(initialFacing);

  // Chart state.
  const [angleSnapshot, setAngleSnapshot] = useState<AnglePoint[]>([]);
  const [chartOpen,     setChartOpen]     = useState(false);
  const chartAnim       = useRef(new Animated.Value(0)).current;
  const angleHistoryRef = useRef<AnglePoint[]>([]);
  const lastChartTickRef = useRef(0);

  // Correction mode state.
  const [correctionMode, setCorrectionMode] = useState(false);

  // Refs for use inside stable callbacks (avoid stale closures).
  const isRecordingRef    = useRef(false);
  const correctionModeRef = useRef(false);

  const elapsedRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cdTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Keypoint handler (stable — no deps, uses refs only) ────────────────────

  const handleKeypoints = useCallback((payload: WsKeypointPayload) => {
    // While in correction mode, freeze skeleton so the user's drag isn't fought.
    if (!correctionModeRef.current) {
      setLatestKpts(payload.keypoints);
      setKneeAngle(payload.knee_angle_deg);
      setTrackStatus(payload.tracking_status);
    }

    // Buffer angle history whenever recording, regardless of correction mode.
    if (isRecordingRef.current) {
      angleHistoryRef.current.push({
        t:     payload.timestamp_ms,
        angle: payload.knee_angle_deg,
      });
      if (angleHistoryRef.current.length > MAX_HISTORY) {
        angleHistoryRef.current.shift();
      }
      const now = Date.now();
      if (now - lastChartTickRef.current > CHART_TICK_MS) {
        lastChartTickRef.current = now;
        setAngleSnapshot([...angleHistoryRef.current]);
      }
    }
  }, []);

  const { wsState, sendFrame, sendJson, close } = useWebSocketStream({
    trialId, model, legSide,
    width:  camLayout.width,
    height: camLayout.height,
    onKeypoints: handleKeypoints,
    onComplete:  onTrialComplete,
  });

  useCameraCapture({ cameraRef, enabled: isRecording, onFrame: sendFrame });

  // ── Chart panel animation ──────────────────────────────────────────────────

  const openChart = useCallback(() => {
    setChartOpen(true);
    Animated.spring(chartAnim, {
      toValue: CHART_H,
      damping: 22, stiffness: 130, mass: 0.7,
      useNativeDriver: false,
    }).start();
  }, [chartAnim]);

  const closeChart = useCallback(() => {
    Animated.timing(chartAnim, {
      toValue: 0, duration: 260, useNativeDriver: false,
    }).start(() => setChartOpen(false));
  }, [chartAnim]);

  const toggleChart = useCallback(() => {
    if (chartOpen) closeChart();
    else openChart();
  }, [chartOpen, openChart, closeChart]);

  // ── Recording lifecycle ────────────────────────────────────────────────────

  const startCountdown = useCallback(() => {
    let remaining = countdownSecs;
    setCountdown(remaining);
    cdTimerRef.current = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearInterval(cdTimerRef.current!);
        setCountdown(null);
        setIsRecording(true);
        isRecordingRef.current = true;
        angleHistoryRef.current = [];
        openChart();
        elapsedRef.current = setInterval(() => {
          setElapsedSec(s => parseFloat((s + 0.1).toFixed(1)));
        }, 100);
      } else {
        setCountdown(remaining);
      }
    }, 1000);
  }, [countdownSecs, openChart]);

  const stopRecording = useCallback(() => {
    setIsRecording(false);
    isRecordingRef.current = false;
    clearInterval(elapsedRef.current!);
    clearInterval(cdTimerRef.current!);
    setCountdown(null);
    setElapsedSec(0);
    // Exit correction mode if active.
    setCorrectionMode(false);
    correctionModeRef.current = false;
    close();
    // Keep chart open so the clinician can review the trace.
  }, [close]);

  // ── Correction mode ────────────────────────────────────────────────────────

  const toggleCorrection = useCallback(() => {
    const next = !correctionModeRef.current;
    correctionModeRef.current = next;
    setCorrectionMode(next);
  }, []);

  const handleJointCorrect = useCallback(
    (joint: JointKey, x: number, y: number) => {
      const msg: WsJointCorrection = { type: "correction", joint, x, y };
      sendJson(msg);
    },
    [sendJson]
  );

  // ── Cleanup ────────────────────────────────────────────────────────────────

  useLayoutEffect(() => {
    return () => {
      clearInterval(elapsedRef.current!);
      clearInterval(cdTimerRef.current!);
    };
  }, []);

  // ── Permission gates ───────────────────────────────────────────────────────

  if (!permission) return <ActivityIndicator style={styles.fill} />;

  if (!permission.granted) {
    return (
      <View style={styles.permBox}>
        <Text style={styles.permText}>Camera access is required.</Text>
        <Pressable style={styles.permBtn} onPress={requestPermission}>
          <Text style={styles.permBtnText}>Grant permission</Text>
        </Pressable>
      </View>
    );
  }

  // ── Derived values for the correction overlay ──────────────────────────────

  const correctionInitPos = latestKpts
    ? {
        hip:   { x: latestKpts.hip.x   * camLayout.width, y: latestKpts.hip.y   * camLayout.height },
        knee:  { x: latestKpts.knee.x  * camLayout.width, y: latestKpts.knee.y  * camLayout.height },
        ankle: { x: latestKpts.ankle.x * camLayout.width, y: latestKpts.ankle.y * camLayout.height },
      }
    : null;

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <View style={styles.root}>

      {/* ── Camera section ─────────────────────────────────────────────────── */}
      <View
        style={styles.cameraSection}
        onLayout={(e) => {
          const { width, height } = e.nativeEvent.layout;
          setCamLayout({ width, height });
          camWRef.current = width;
          camHRef.current = height;
        }}
      >
        <CameraView
          ref={cameraRef}
          style={StyleSheet.absoluteFill}
          facing={facing}
        />

        {/* Skeleton overlay */}
        <SkeletonOverlay
          width={camLayout.width}
          height={camLayout.height}
          keypoints={latestKpts}
          kneeAngleDeg={kneeAngle}
          trackingStatus={trackStatus}
        />

        {/* Correction joint handles (rendered over camera when active) */}
        {correctionMode && correctionInitPos && (
          <View style={StyleSheet.absoluteFill} pointerEvents="box-none">
            {JOINT_META.map(({ key, color, label }) => (
              <DraggableJoint
                key={key}
                joint={key}
                initX={correctionInitPos[key].x}
                initY={correctionInitPos[key].y}
                color={color}
                label={label}
                containerWRef={camWRef}
                containerHRef={camHRef}
                onCorrect={handleJointCorrect}
              />
            ))}
          </View>
        )}

        {/* Correction mode banner */}
        {correctionMode && (
          <View style={styles.correctionBanner} pointerEvents="none">
            <Text style={styles.correctionBannerText}>
              CORRECTION — drag joints to adjust
            </Text>
          </View>
        )}

        {/* Top HUD */}
        <View style={styles.hudTop}>
          <View style={styles.pill}>
            <View style={[styles.recDot, isRecording && styles.recDotActive]} />
            <Text style={styles.hudText}>
              {isRecording ? `REC  ${elapsedSec.toFixed(1)} s` : WS_LABEL[wsState]}
            </Text>
          </View>
          <View style={styles.pill}>
            <Text style={styles.hudText}>{model}</Text>
          </View>
          <View style={styles.pill}>
            <View style={[styles.trackDot, { backgroundColor: TRACKING_COLOR[trackStatus] }]} />
            <Text style={styles.hudText}>{trackStatus}</Text>
          </View>

          <View style={{ flex: 1 }} />

          {!correctionMode && (
            <Pressable
              style={[styles.pill, isRecording && styles.pillDim]}
              onPress={() => setFacing(f => f === "back" ? "front" : "back")}
              disabled={isRecording}
            >
              <Text style={styles.flipIcon}>⇄</Text>
              <Text style={styles.hudText}>{facing === "back" ? "Rear" : "Front"}</Text>
            </Pressable>
          )}
        </View>

        {/* Countdown overlay */}
        {countdown !== null && (
          <View style={styles.cdOverlay} pointerEvents="none">
            <Text style={styles.cdNum}>{countdown}</Text>
          </View>
        )}
      </View>

      {/* ── Angle chart panel (slides up when recording) ───────────────────── */}
      <Animated.View style={[styles.chartPanel, { height: chartAnim }]}>
        {chartOpen && (
          <KneeAngleChart
            history={angleSnapshot}
            width={SCREEN_W}
            height={CHART_H}
            isRecording={isRecording}
          />
        )}
      </Animated.View>

      {/* ── Controls bar ───────────────────────────────────────────────────── */}
      <View style={styles.controls}>

        {/* Left: correction toggle */}
        <Pressable
          style={[styles.auxBtn, correctionMode && styles.auxBtnActive]}
          onPress={toggleCorrection}
          disabled={!isRecording}
        >
          <View style={[styles.auxBtnIcon, correctionMode && styles.auxBtnIconActive]}>
            <Text style={[styles.auxBtnIconText, correctionMode && { color: "#facc15" }]}>
              {correctionMode ? "✓" : "+"}
            </Text>
          </View>
          <Text style={[styles.auxBtnLabel, correctionMode && { color: "#facc15" }]}>
            {correctionMode ? "Done" : "Correct"}
          </Text>
        </Pressable>

        {/* Centre: record / stop button */}
        <Pressable
          style={styles.recordBtn}
          onPress={isRecording ? stopRecording : startCountdown}
          disabled={wsState === "connecting" || countdown !== null}
        >
          <View style={[styles.recordInner, isRecording && styles.recordInnerStop]} />
        </Pressable>

        {/* Right: chart toggle */}
        <Pressable
          style={[styles.auxBtn, chartOpen && styles.auxBtnActive]}
          onPress={toggleChart}
        >
          <View style={[styles.auxBtnIcon, chartOpen && styles.auxBtnIconActive]}>
            <Text style={[styles.auxBtnIconText, chartOpen && { color: "#0ea5e9" }]}>∿</Text>
          </View>
          <Text style={[styles.auxBtnLabel, chartOpen && { color: "#0ea5e9" }]}>
            Chart
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  root:         { flex: 1, backgroundColor: "#0a0e18" },
  fill:         { flex: 1 },

  // Camera section
  cameraSection: { flex: 1, overflow: "hidden" },

  // HUD
  hudTop: {
    position: "absolute", top: 12, left: 12, right: 12,
    flexDirection: "row", gap: 8, alignItems: "center",
  },
  pill: {
    flexDirection: "row", alignItems: "center", gap: 5,
    backgroundColor: "rgba(0,0,0,0.52)",
    borderRadius: 20, paddingHorizontal: 10, paddingVertical: 4,
    borderWidth: 0.5, borderColor: "rgba(255,255,255,0.14)",
  },
  pillDim:       { opacity: 0.3 },
  hudText:       { color: "#e2e8f0", fontSize: 11 },
  recDot:        { width: 6, height: 6, borderRadius: 3, backgroundColor: "#475569" },
  recDotActive:  { backgroundColor: "#e24b4a" },
  trackDot:      { width: 6, height: 6, borderRadius: 3 },
  flipIcon:      { color: "#e2e8f0", fontSize: 13, marginRight: 2 },

  // Countdown
  cdOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.45)",
    alignItems: "center", justifyContent: "center",
  },
  cdNum: { fontSize: 96, fontWeight: "300", color: "#fff" },

  // Correction mode
  correctionBanner: {
    position: "absolute", bottom: 0, left: 0, right: 0,
    backgroundColor: "rgba(250,204,21,0.18)",
    paddingVertical: 7, alignItems: "center",
    borderTopWidth: 1, borderTopColor: "rgba(250,204,21,0.35)",
  },
  correctionBannerText: {
    color: "#facc15", fontSize: 11, fontWeight: "600",
    letterSpacing: 0.5, textTransform: "uppercase",
  },

  // Draggable joint handle
  dragHandle: {
    position: "absolute", top: 0, left: 0,
    alignItems: "center", justifyContent: "center",
    borderWidth: 2.5,
  },
  dragLabel: {
    fontSize: 10, fontWeight: "700", letterSpacing: 0.5,
  },

  // Chart panel
  chartPanel: { overflow: "hidden", backgroundColor: "#080c18" },

  // Controls bar
  controls: {
    height: CONTROLS_H,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-around",
    paddingHorizontal: 24,
    paddingBottom: 16,
    backgroundColor: "rgba(10,14,24,0.94)",
    borderTopWidth: 0.5,
    borderTopColor: "rgba(255,255,255,0.08)",
  },

  // Aux buttons (left / right)
  auxBtn: {
    alignItems: "center", gap: 5,
    minWidth: 56,
  },
  auxBtnActive: {},
  auxBtnIcon: {
    width: 40, height: 40, borderRadius: 20,
    backgroundColor: "rgba(255,255,255,0.06)",
    borderWidth: 1.5, borderColor: "rgba(255,255,255,0.16)",
    alignItems: "center", justifyContent: "center",
  },
  auxBtnIconActive: {
    backgroundColor: "rgba(255,255,255,0.12)",
    borderColor: "rgba(255,255,255,0.30)",
  },
  auxBtnIconText: { color: "#94a3b8", fontSize: 18, fontWeight: "300" },
  auxBtnLabel:    { color: "#64748b", fontSize: 10, fontWeight: "500" },

  // Record button
  recordBtn: {
    width: 72, height: 72, borderRadius: 36,
    backgroundColor: "rgba(255,255,255,0.07)",
    borderWidth: 3, borderColor: "rgba(255,255,255,0.48)",
    alignItems: "center", justifyContent: "center",
  },
  recordInner: {
    width: 54, height: 54, borderRadius: 27,
    backgroundColor: "#e24b4a",
  },
  recordInnerStop: {
    width: 26, height: 26, borderRadius: 6,
  },

  // Permission screen
  permBox:    { flex: 1, alignItems: "center", justifyContent: "center", gap: 16 },
  permText:   { color: "#e2e8f0", fontSize: 15 },
  permBtn:    { backgroundColor: "#0ea5e9", borderRadius: 10, paddingHorizontal: 20, paddingVertical: 10 },
  permBtnText: { color: "#fff", fontWeight: "500" },
});
