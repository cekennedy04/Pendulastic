/**
 * constants/Config.ts
 * ===================
 * Runtime configuration constants.
 *
 * WS_BASE and API_BASE point at the FastAPI backend.  During development the
 * backend is expected to run on the host machine reachable via the LAN IP
 * printed by `uvicorn`.  Replace with the production hostname before release.
 */

const DEV_HOST = "172.20.10.2"; // ← replace with the uvicorn host LAN IP

export const Config = {
  API_BASE: `http://${DEV_HOST}:8000/api`,
  WS_BASE:  `ws://${DEV_HOST}:8000`,

  // Frame capture target rate sent to the backend (frames per second).
  // Clinical accuracy is sufficient at 15 fps; the backend processes each
  // frame asynchronously and the skeleton overlay interpolates between updates.
  STREAM_FPS: 15,

  // JPEG quality (0–1) used when encoding frames before transmission.
  // 0.55 provides an adequate balance between keypoint accuracy and bandwidth.
  JPEG_QUALITY: 0.55,

  // WebSocket reconnect back-off ceiling (milliseconds).
  WS_RECONNECT_MAX_MS: 8000,
} as const;
