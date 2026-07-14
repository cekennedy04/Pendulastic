/**
 * hooks/useCameraCapture.ts
 * ==========================
 * Drives a controlled frame-capture loop against an expo-camera CameraView ref.
 *
 * The loop targets Config.STREAM_FPS.  Each captured JPEG is handed to the
 * provided `onFrame` callback synchronously before the next interval fires.
 * The loop self-throttles: if a capture call is still pending when the next
 * interval fires, that tick is skipped to avoid queue build-up.
 */

import { useCallback, useEffect, useRef } from "react";
import type { CameraView } from "expo-camera";
import { Config } from "../constants/Config";

interface UseCameraCaptureOptions {
  cameraRef:  React.RefObject<CameraView>;
  enabled:    boolean;
  onFrame:    (jpeg: ArrayBuffer, frameIndex: number) => void;
}

export function useCameraCapture({
  cameraRef,
  enabled,
  onFrame,
}: UseCameraCaptureOptions): void {
  const frameIndexRef   = useRef<number>(0);
  const captureActiveRef = useRef<boolean>(false);
  const intervalRef     = useRef<ReturnType<typeof setInterval> | null>(null);

  const captureOne = useCallback(async () => {
    if (!cameraRef.current || captureActiveRef.current) return;
    captureActiveRef.current = true;
    try {
      const result = await cameraRef.current.takePictureAsync({
        base64:         true,
        quality:        Config.JPEG_QUALITY,
        skipProcessing: true,
        exif:           false,
      });

      if (!result?.base64) return;

      // Decode base64 → ArrayBuffer without a native bridge call.
      const binary = atob(result.base64);
      const buf    = new ArrayBuffer(binary.length);
      const view   = new Uint8Array(buf);
      for (let i = 0; i < binary.length; i++) {
        view[i] = binary.charCodeAt(i);
      }

      onFrame(buf, frameIndexRef.current++);
    } catch (err) {
      console.warn("[CameraCapture] takePictureAsync failed:", err);
    } finally {
      captureActiveRef.current = false;
    }
  }, [cameraRef, onFrame]);

  useEffect(() => {
    if (!enabled) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      frameIndexRef.current    = 0;
      captureActiveRef.current = false;
      return;
    }

    const interval = Math.round(1000 / Config.STREAM_FPS);
    intervalRef.current = setInterval(captureOne, interval);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [enabled, captureOne]);
}
