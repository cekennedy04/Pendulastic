/**
 * hooks/useWebSocketStream.ts
 * ============================
 * Manages the full WebSocket lifecycle for a live recording session.
 *
 * Responsibilities
 * ----------------
 * 1. Opens a persistent connection to the backend `/ws/stream` endpoint.
 * 2. Transmits session metadata as the first JSON message.
 * 3. Accepts raw JPEG ArrayBuffers from the camera capture loop and prefixes
 *    each with an 8-byte binary header before transmission.
 * 4. Deserialises incoming server messages and exposes the latest keypoint
 *    payload to the rendering layer via a React ref (avoiding re-renders on
 *    every frame).
 * 5. Implements exponential back-off reconnection on unexpected closure.
 * 6. Exposes imperative `sendFrame` and `close` handles to the camera layer.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Config } from "../constants/Config";
import type {
  HpeModel,
  LegSide,
  WsKeypointPayload,
  WsServerMessage,
  WsSessionMeta,
} from "../types";

export type WsState = "connecting" | "open" | "closed" | "error";

interface UseWebSocketStreamOptions {
  trialId:  string;
  model:    HpeModel;
  legSide:  LegSide;
  width:    number;
  height:   number;
  /** Called on every processed-frame payload from the backend. */
  onKeypoints: (payload: WsKeypointPayload) => void;
  /** Called when the backend signals trial completion. */
  onComplete?: (csvPath: string) => void;
}

interface UseWebSocketStreamReturn {
  wsState:    WsState;
  /** Transmit one JPEG frame to the backend. Thread-safe across the JS event loop. */
  sendFrame:  (jpeg: ArrayBuffer, frameIndex: number) => void;
  /** Send an arbitrary JSON message (e.g. joint correction). No-op if not connected. */
  sendJson:   (msg: object) => void;
  close:      () => void;
}

export function useWebSocketStream(
  opts: UseWebSocketStreamOptions
): UseWebSocketStreamReturn {
  const [wsState, setWsState] = useState<WsState>("connecting");

  const wsRef          = useRef<WebSocket | null>(null);
  const frameIndexRef  = useRef<number>(0);
  const backOffRef     = useRef<number>(500);
  const closedByUser   = useRef<boolean>(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    if (closedByUser.current) return;
    setWsState("connecting");

    const ws = new WebSocket(`${Config.WS_BASE}/ws/stream`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setWsState("open");
      backOffRef.current = 500;

      // Transmit session metadata as the mandatory first message.
      const meta: WsSessionMeta = {
        type:     "session_meta",
        trial_id: opts.trialId,
        model:    opts.model,
        leg_side: opts.legSide,
        width:    opts.width,
        height:   opts.height,
      };
      ws.send(JSON.stringify(meta));
    };

    ws.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      try {
        const msg = JSON.parse(event.data) as WsServerMessage;
        if (msg.type === "keypoints") {
          opts.onKeypoints(msg);
        } else if (msg.type === "complete" && opts.onComplete) {
          opts.onComplete(msg.csv_path);
        } else if (msg.type === "warning") {
          console.warn("[WS] backend warning:", msg.message);
        }
      } catch {
        console.warn("[WS] unparseable server message");
      }
    };

    ws.onerror = () => {
      setWsState("error");
    };

    ws.onclose = (ev) => {
      wsRef.current = null;
      if (closedByUser.current) {
        setWsState("closed");
        return;
      }
      // Unexpected closure — schedule reconnection with exponential back-off.
      console.warn(`[WS] closed unexpectedly (code ${ev.code}), reconnecting in ${backOffRef.current}ms`);
      setWsState("error");
      reconnectTimer.current = setTimeout(() => {
        backOffRef.current = Math.min(
          backOffRef.current * 2,
          Config.WS_RECONNECT_MAX_MS
        );
        connect();
      }, backOffRef.current);
    };
  }, [opts]);

  useEffect(() => {
    closedByUser.current = false;
    connect();
    return () => {
      closedByUser.current = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close(1000, "component unmounted");
    };
  }, [connect]);

  /**
   * Transmit one JPEG frame with an 8-byte binary header:
   *   [0-3]  uint32LE  frame_index
   *   [4-7]  uint32LE  timestamp_ms mod 2^32
   *   [8..]  JPEG payload bytes
   */
  const sendFrame = useCallback(
    (jpeg: ArrayBuffer, frameIndex: number) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      const header  = new ArrayBuffer(8);
      const view    = new DataView(header);
      view.setUint32(0, frameIndex, true);
      view.setUint32(4, Date.now() & 0xffffffff, true);

      // Concatenate header + JPEG into a single buffer for one send() call.
      const combined = new Uint8Array(8 + jpeg.byteLength);
      combined.set(new Uint8Array(header), 0);
      combined.set(new Uint8Array(jpeg), 8);

      ws.send(combined.buffer);
      frameIndexRef.current = frameIndex + 1;
    },
    []
  );

  const sendJson = useCallback((msg: object) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(msg));
  }, []);

  const close = useCallback(() => {
    closedByUser.current = true;
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
    wsRef.current?.close(1000, "session ended by user");
    setWsState("closed");
  }, []);

  return { wsState, sendFrame, sendJson, close };
}
