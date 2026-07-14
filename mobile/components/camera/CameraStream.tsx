/**
 * components/camera/CameraStream.tsx
 * =====================================
 * Composes the camera viewfinder, WebSocket stream, and skeleton overlay into
 * a single full-screen component used by the Record screen.
 *
 * Data flow
 * ---------
 *   CameraView ──(JPEG frames)──▶ useCameraCapture
 *                                        │
 *                                        ▼
 *                              useWebSocketStream ──▶ FastAPI /ws/stream
 *                                        │
 *                              (WsKeypointPayload)
 *                                        │
 *                                        ▼
 *                               SkeletonOverlay (Reanimated)
 *
 * The component is intentionally stateless with respect to trial metadata;
 * all session parameters are injected via props so the parent screen retains
 * sole ownership of trial state.
 */

import { CameraView, useCameraPermissions } from "expo-camera";
import React, { useCallback, useLayoutEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
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
  HpeModel,
  LegSide,
  PoseKeypoints,
  TrackingStatus,
  WsKeypointPayload,
} from "../../types";
import { SkeletonOverlay } from "./SkeletonOverlay";

interface Props {
  trialId:        string;
  model:          HpeModel;
  legSide:        LegSide;
  countdownSecs:  number;
  initialFacing?: "back" | "front";
  /** Invoked once the backend signals the trial CSV is ready for review. */
  onTrialComplete: (csvPath: string) => void;
}

const WS_LABEL: Record<WsState, string> = {
  connecting: "Connecting…",
  open:       "Streaming",
  closed:     "Stopped",
  error:      "Reconnecting…",
};

const TRACKING_COLOR: Record<TrackingStatus, string> = {
  stable:    "#4ade80",
  uncertain: "#facc15",
  lost:      "#f87171",
};

export function CameraStream({
  trialId,
  model,
  legSide,
  countdownSecs,
  initialFacing = "back",
  onTrialComplete,
}: Props) {
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef   = useRef<CameraView>(null);

  const [layout, setLayout] = useState({ width: 1, height: 1 });
  const [isRecording,  setIsRecording]  = useState(false);
  const [countdown,    setCountdown]    = useState<number | null>(null);
  const [latestKpts,   setLatestKpts]   = useState<PoseKeypoints | null>(null);
  const [kneeAngle,    setKneeAngle]    = useState(180);
  const [trackStatus,  setTrackStatus]  = useState<TrackingStatus>("stable");
  const [elapsedSec,   setElapsedSec]   = useState(0);
  const [facing,       setFacing]       = useState<"back" | "front">(initialFacing);

  const elapsedRef   = useRef<ReturnType<typeof setInterval> | null>(null);
  const cdTimerRef   = useRef<ReturnType<typeof setInterval> | null>(null);

  // Keypoint callback — deliberately a ref to avoid re-creating sendFrame.
  const handleKeypoints = useCallback((payload: WsKeypointPayload) => {
    setLatestKpts(payload.keypoints);
    setKneeAngle(payload.knee_angle_deg);
    setTrackStatus(payload.tracking_status);
  }, []);

  const { wsState, sendFrame, close } = useWebSocketStream({
    trialId, model, legSide,
    width:  layout.width,
    height: layout.height,
    onKeypoints: handleKeypoints,
    onComplete:  onTrialComplete,
  });

  useCameraCapture({
    cameraRef,
    enabled: isRecording,
    onFrame: sendFrame,
  });

  const startCountdown = useCallback(() => {
    let remaining = countdownSecs;
    setCountdown(remaining);
    cdTimerRef.current = setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) {
        clearInterval(cdTimerRef.current!);
        setCountdown(null);
        setIsRecording(true);
        // Elapsed timer for the HUD display.
        elapsedRef.current = setInterval(() => {
          setElapsedSec((s) => parseFloat((s + 0.1).toFixed(1)));
        }, 100);
      } else {
        setCountdown(remaining);
      }
    }, 1000);
  }, [countdownSecs]);

  const stopRecording = useCallback(() => {
    setIsRecording(false);
    clearInterval(elapsedRef.current!);
    clearInterval(cdTimerRef.current!);
    setCountdown(null);
    setElapsedSec(0);
    close();
  }, [close]);

  useLayoutEffect(() => {
    return () => {
      clearInterval(elapsedRef.current!);
      clearInterval(cdTimerRef.current!);
    };
  }, []);

  if (!permission) return <ActivityIndicator style={styles.fill} />;

  if (!permission.granted) {
    return (
      <View style={styles.permBox}>
        <Text style={styles.permText}>Camera access is required.</Text>
        <Pressable style={styles.btn} onPress={requestPermission}>
          <Text style={styles.btnText}>Grant permission</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={styles.fill}>
      <CameraView
        ref={cameraRef}
        style={styles.fill}
        facing={facing}
        onLayout={(e) => {
          const { width, height } = e.nativeEvent.layout;
          setLayout({ width, height });
        }}
      />

      {/* Skeleton overlay — rendered on top of the camera preview */}
      <SkeletonOverlay
        width={layout.width}
        height={layout.height}
        keypoints={latestKpts}
        kneeAngleDeg={kneeAngle}
        trackingStatus={trackStatus}
      />

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
          <View
            style={[
              styles.trackDot,
              { backgroundColor: TRACKING_COLOR[trackStatus] },
            ]}
          />
          <Text style={styles.hudText}>{trackStatus}</Text>
        </View>

        {/* Spacer pushes flip button to the right */}
        <View style={{ flex: 1 }} />

        {/* Flip camera — disabled while recording */}
        <Pressable
          style={[styles.pill, isRecording && styles.flipBtnDim]}
          onPress={() => setFacing((f) => f === "back" ? "front" : "back")}
          disabled={isRecording}
        >
          <Text style={styles.flipIcon}>⇄</Text>
          <Text style={styles.hudText}>{facing === "back" ? "Rear" : "Front"}</Text>
        </Pressable>
      </View>

      {/* Countdown overlay */}
      {countdown !== null && (
        <View style={styles.cdOverlay} pointerEvents="none">
          <Text style={styles.cdNum}>{countdown}</Text>
        </View>
      )}

      {/* Bottom controls */}
      <View style={styles.controls}>
        <Pressable
          style={styles.recordBtn}
          onPress={isRecording ? stopRecording : startCountdown}
          disabled={wsState === "connecting" || countdown !== null}
        >
          <View
            style={[
              styles.recordInner,
              isRecording && styles.recordInnerStop,
            ]}
          />
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  fill:          { flex: 1 },
  permBox:       { flex: 1, alignItems: "center", justifyContent: "center", gap: 16 },
  permText:      { color: "#e2e8f0", fontSize: 15 },
  btn:           { backgroundColor: "#0ea5e9", borderRadius: 10, paddingHorizontal: 20, paddingVertical: 10 },
  btnText:       { color: "#fff", fontWeight: "500" },
  hudTop: {
    position: "absolute", top: 12, left: 12, right: 12,
    flexDirection: "row", gap: 8, alignItems: "center",
  },
  pill: {
    flexDirection: "row", alignItems: "center", gap: 5,
    backgroundColor: "rgba(0,0,0,0.5)",
    borderRadius: 20, paddingHorizontal: 10, paddingVertical: 4,
    borderWidth: 0.5, borderColor: "rgba(255,255,255,0.15)",
  },
  hudText:       { color: "#e2e8f0", fontSize: 11 },
  recDot:        { width: 6, height: 6, borderRadius: 3, backgroundColor: "#475569" },
  recDotActive:  { backgroundColor: "#e24b4a" },
  trackDot:      { width: 6, height: 6, borderRadius: 3 },
  cdOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.45)",
    alignItems: "center", justifyContent: "center",
  },
  cdNum:         { fontSize: 96, fontWeight: "400", color: "#fff" },
  controls: {
    position: "absolute", bottom: 0, left: 0, right: 0,
    paddingBottom: 32, paddingTop: 16,
    backgroundColor: "rgba(10,14,18,0.85)",
    alignItems: "center",
  },
  recordBtn: {
    width: 72, height: 72, borderRadius: 36,
    backgroundColor: "rgba(255,255,255,0.08)",
    borderWidth: 3, borderColor: "rgba(255,255,255,0.5)",
    alignItems: "center", justifyContent: "center",
  },
  recordInner: {
    width: 54, height: 54, borderRadius: 27,
    backgroundColor: "#e24b4a",
  },
  recordInnerStop: {
    width: 26, height: 26, borderRadius: 6,
  },
  flipBtnDim: { opacity: 0.3 },
  flipIcon:   { color: "#e2e8f0", fontSize: 13, marginRight: 2 },
});
