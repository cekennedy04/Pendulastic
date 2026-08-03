# Phone Camera as a Recording Source for pendulastic_app.py — Design Spec
**Date:** 2026-08-03
**Status:** Approved
**Builds on:** `camera-selection` branch (`CameraSession`, camera dropdown in `AcquisitionPanel`)

---

## 1. Goal

Add "📱 Phone Camera" as a live recording source in `pendulastic_app.py`'s `AcquisitionPanel`, alongside the USB/webcam dropdown the `camera-selection` branch already provides. A phone opens a URL in its browser (no app install), streams live JPEG frames to the desktop over WebSocket, and the desktop treats that stream exactly like a webcam: live preview before recording, a `VideoWriter` attached/detached at start/stop — same as `CameraSession` does today for USB cameras.

Out of scope: on-phone pose processing (the existing `_TRACKING_PAGE` MediaPipe flow is a separate, heavier feature and is not touched by this work).

---

## 2. Why the existing pieces aren't enough as-is

- `pendulastic_phone_server.py` already runs an HTTP upload server and a WebSocket server whose `_ws_client()` handler decodes binary JPEG frames into `frame_queue` — but nothing today sends frames into that path. Its only phone-facing page (`_TRACKING_PAGE`) does record-then-upload via `MediaRecorder` + XHR, not live streaming, and requires MediaPipe.
- Live streaming needs `getUserMedia`, which browsers only grant in a secure context (HTTPS). The current HTTP server is deliberately plain HTTP (its docstring says "no HTTPS/getUserMedia needed") because the upload flow uses `<input capture>` instead. This does not work for live streaming — HTTPS is required.
- The dormant `mobile/`/`web/` Expo+FastAPI stack has a real JPEG-over-WS protocol (`useWebSocketStream.ts`) but hasn't been touched since 2026-07-31, requires installing/running the Expo app, and its `/ws/stream` backend doesn't exist. Reviving it is more setup than a browser-only page for a PT/clinician workflow.

---

## 3. Highest-Risk Unknown: iOS Safari WSS over a Self-Signed Certificate

iOS Safari is known to handle `wss://` connections over a self-signed certificate more restrictively than plain HTTPS fetches. Accepting the "Advanced → Proceed" warning on an HTTPS page does **not** reliably extend that trust to a WebSocket on a *different port* — and today's `pendulastic_phone_server.py` runs HTTP on 8877 and WS on 8878, two separate TLS origins from the browser's perspective. If this holds on real hardware, the phone can load `_STREAM_PAGE` fine and still silently fail to open its WS connection, with no obvious in-browser override prompt.

Mitigation, in priority order:
1. **Prefer a single HTTPS port** that serves both the page and the WS upgrade, so there is exactly one certificate-trust exception to accept — this removes the dual-origin trust problem structurally rather than working around it. This is a real restructuring of the current dual-server (threaded `HTTPServer` + separate `asyncio` WS server) architecture; scope it explicitly in the implementation plan.
2. If a single port isn't practical, the pairing panel must walk the user through trusting *both* origins explicitly (visit both URLs once before streaming will connect).
3. Either way, **this needs a real-device spike on iOS Safari before deeper implementation** — it is the highest-risk unknown in the whole design. If WSS-over-self-signed proves unworkable on iOS Safari even with mitigation, the documented fallback is the ngrok-tunnel HTTPS option that was set aside earlier in favor of local self-signed HTTPS, and that trade-off would need to be revisited.

---

## 4. File Impact Matrix

| File | Nature of change |
|---|---|
| `pendulastic_phone_server.py` | Add HTTPS support (self-signed cert via `cryptography`, generated once and cached on disk). **Prefer serving the WS upgrade on the same HTTPS port as the page** (see §3) rather than the current separate 8877/8878 split; if kept separate, the pairing UX must cover trusting both origins. Add a new, minimal `_STREAM_PAGE` (getUserMedia + canvas capture + WS JPEG send + clock-sync handshake + wake lock + recording indicator) served at a new route, separate from `_TRACKING_PAGE`. `_ws_client()` gains the clock-sync handshake (ping/pong before the frame loop) and frame-header parsing (capture timestamp, frame index) feeding `frame_queue` entries as `(frame, desktop_clock_timestamp)` instead of bare frames. |
| `camera_utils.py` | New `PhoneCameraSession` class — same public surface as `CameraSession` (`rescan`, `open`, `close`, `attach_writer`, `detach_writer`, `.active`, `.frame_size`, `on_frame`/`on_status` callbacks). Internally reads from `pendulastic_phone_server.frame_queue` on a background thread instead of `cv2.VideoCapture`; tracks arrival rate for `degraded: Nfps` status; applies the clock-offset conversion (with outlier-filtered NTP-style handshake results) to each frame's timestamp. |
| `pendulastic_app.py` | `AcquisitionPanel`'s camera dropdown gains a static "📱 Phone Camera" entry alongside enumerated USB devices (not part of `enumerate_cameras()`'s hardware probe). Selecting it starts the phone server (HTTPS) if not running and shows a QR/URL panel (reusing `pendulastic_viewer.py`'s existing `qrcode`-based pattern). `App` swaps `self._camera` between a `CameraSession` instance and a `PhoneCameraSession` instance depending on the selected dropdown entry; all downstream recording/preview code is unchanged since both classes share the same interface. |
| `tests/test_camera_utils.py` | New tests for `PhoneCameraSession` (fake frame source in place of a real WS connection) and for the clock-sync offset/filter function in isolation (synthetic RTT sequences, including injected outliers). |
| `tests/test_app.py` | New tests for the dropdown's phone-entry wiring and the `self._camera` swap between session types. |

---

## 5. Frame Protocol & Timestamping

- **Clock sync**: on WS connect, the desktop and phone perform an NTP-style handshake (`t0` sent → phone echoes `t1` → desktop records `t2`), repeated for several round trips and re-run periodically (~every 30s) to catch drift. Samples are kept in a rolling window; before an offset update, RTT outliers (e.g. from Wi-Fi power-save latency spikes) are rejected via a median/MAD filter rather than trusting the single latest sample.
- **Per-frame capture timestamp**: the phone captures frames via `requestVideoFrameCallback` where available (precise presentation time), falling back to `rAF` + `performance.now()`. Each WS frame message carries an 8-byte header (frame index + phone-local capture timestamp) ahead of the JPEG payload — the same shape the dormant `useWebSocketStream.ts` already uses.
- `PhoneCameraSession` converts the phone timestamp to desktop-clock time using the current filtered offset before invoking `on_frame`, so downstream multi-modal alignment (IMU/OptiTrack/video sync in the Workbench) gets a real capture time rather than jittery network-arrival time. Frames with an implausible timestamp jump (clock re-sync glitch, reordering) are dropped rather than trusted.

---

## 6. Lifecycle, Reconnection & Thermal Visibility

- `_STREAM_PAGE` requests `navigator.wakeLock.request('screen')` on start and re-acquires it on `visibilitychange`; it shows a large, persistent recording indicator (flashing dot + elapsed time) so a throttled/backgrounded phone is immediately obvious to the researcher rather than silently frozen.
- On an unexpected WS drop, the phone page reconnects with debounced exponential backoff (avoiding a reconnect storm from rapid suspend/resume cycling — e.g. a phone call or notification swipe).
- `PhoneCameraSession` extends the existing `live`/`lost` status vocabulary (already used for USB-camera loss) with a `degraded: Nfps` state, computed from frame arrival rate. **Hysteresis**: the status only flips to `degraded` after the frame rate stays below threshold for a sustained window (2-3 consecutive seconds), not on a single low reading — a momentary Wi-Fi micro-interruption during a trial should not flicker the UI between `live`/`degraded`.
- A gap beyond a threshold (reconnect took too long) is recorded as a flagged gap in the trial's metadata rather than stitched over silently — consistent with how a lost USB camera is surfaced today rather than hidden.
- **Resolution, quality & backpressure**: `_STREAM_PAGE` requests `getUserMedia` at a capped resolution (default 720p) and encodes JPEG at a capped quality (default ~70) rather than streaming full sensor resolution — reducing the phone-side encode cost that drives thermal throttling in the first place. `frame_queue`'s existing drop-oldest-on-full behavior remains the desktop-side backpressure valve: under sustained thermal/network pressure, frames are dropped rather than queued into unbounded latency, and the resulting drop in arrival rate is exactly what the `degraded: Nfps` status above detects and surfaces.

---

## 7. Connection / Pairing UX

- The "📱 Phone Camera" dropdown entry is always present (it doesn't require hardware probing to exist as an option, unlike USB entries from `enumerate_cameras()`).
- Selecting it starts the HTTPS(+WS) server if not already running and shows a panel with the HTTPS URL, its QR code (reusing `pendulastic_viewer.py`'s existing `qrcode` usage), and a one-line note that the phone will show a self-signed-certificate warning to dismiss (expected, not an error).
- **IP selection**: the QR/URL panel reuses `get_all_local_ips()` as-is — its primary (socket-route) IP is the correct LAN address regardless of any VPN/Docker/virtual adapters present, since it's derived from the OS's actual outbound routing decision rather than enumerating all interfaces blindly. That primary IP is encoded in the QR code and shown first; the function's secondary, scored fallback list is shown as manual-entry alternates in case the primary doesn't reach the phone (e.g. AP client isolation).
- Status text follows the same vocabulary as the USB path (`waiting for phone` → `live` → `degraded: Nfps` / `lost`), shown in the same status area `CameraSession` already drives.
- "Rescan" on the phone entry doesn't re-probe hardware — it re-shows the QR/URL panel and resets to "waiting for phone."
- Only one phone connection is treated as active at a time; a new phone connecting while one is already streaming replaces it (matching "one camera selected at a time" semantics), and the status reflects the switch.

---

## 8. Error Handling

- `getUserMedia` denied/unavailable → clear inline error on `_STREAM_PAGE`, not a silent stall.
- No frame arrives within a timeout after "waiting for phone" → status hints at likely causes (different Wi-Fi network, cert warning not dismissed on one or both origins per §3).
- Mid-recording WS drop → reconnect-with-backoff as above; a gap beyond threshold is flagged in trial metadata.

---

## 9. Testing

- `PhoneCameraSession`: unit tests mirroring `tests/test_camera_utils.py`'s existing `CameraSession` pattern, with an injected fake frame source in place of a real WS connection — covering attach/detach writer, `on_frame`/`on_status` callbacks, and `degraded: Nfps` detection.
- Clock-sync offset/filter logic: tested as a pure function against synthetic RTT sample sequences, including injected outliers, independent of any real socket.
- `AcquisitionPanel`/`App` wiring: tests for the phone dropdown entry and the `self._camera` swap between `CameraSession` and `PhoneCameraSession`.
- `_STREAM_PAGE`'s JS (getUserMedia, wake lock, canvas encode/send) cannot be meaningfully unit-tested and requires manual verification with a real phone (iOS Safari + Android Chrome at minimum) before shipping — thermal throttling and wake-lock reliability only show up on real hardware over a trial-length session.
- **First manual step, before building the rest**: a minimal spike verifying `wss://` over the chosen self-signed-cert setup actually connects from iOS Safari (see §3). This gates the port-architecture decision (single port vs. dual-origin trust flow) and should happen before the full frame protocol/timestamping work is built on top of it.
