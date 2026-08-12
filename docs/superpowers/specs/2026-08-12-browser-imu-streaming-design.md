# Browser-Based Phone IMU Streaming — Design Spec
**Date:** 2026-08-12
**Status:** Approved (rev. 2 — see Section 2a)

---

## 1. Goal

Today, streaming a phone's IMU into Pendulastic requires installing the third-party "Sensor
Stream" app, connecting the phone to the laptop's Wi-Fi hotspot, and manually typing the laptop's
`ip:port` into that app. This is real setup friction for anyone who isn't already set up. Goal: let
a phone stream accelerometer + gyroscope data by opening a URL/scanning a QR code in its own
browser — no app install, no manual address entry — while leaving the existing Sensor Stream Pro
path fully intact for whoever already has it working.

Scope for this pass: **one phone** (single IMU segment). The existing two-phone (proximal + distal)
simultaneous streaming path, used for the full joint-angle fusion, is unchanged and continues to
require Sensor Stream Pro; two-phone browser support is an explicit non-goal here (Section 4).

## 2. Relationship to Existing Work

**`pendulastic_imu_server.py`** already runs a hand-rolled plain-`ws://` WebSocket server (port
5000 by default) that Sensor Stream Pro connects to on four paths — `/accelerometer`,
`/gyroscope`, `/magnetometer`, `/orientation` — each carrying `{"SensorName","Timestamp","x","y","z"}`
JSON messages. Phones are told apart by connection order (first = proximal, second = distal).
Per-sample ingestion happens on `_IMUDevice` via `on_accel()`, `on_gyro()`, `on_mag()`. This module
is not touched by this spec — the new browser path feeds the same `_IMUDevice` entry points a
Sensor Stream Pro connection would, so everything downstream (AHRS fusion, calibration, recording)
is unmodified and untested-path-free.

**`pendulastic_phone_server.py`** already solves this exact class of problem for the phone
*camera*: it runs a local HTTPS server (self-signed cert via `cryptography`, matching iOS's
requirement that sensitive browser permissions — camera, motion — only get granted on secure
origins) that serves a capture page the phone opens directly, no app needed, paired via a QR code
shown in `pendulastic_app.py`'s Acquisition panel (`show_phone_pairing_panel`). It also already
runs a second, hand-rolled WebSocket server on port 8878 (`_ws_client`) intended for JPEG frame
streaming — see Section 2a on why this is not the pattern to mirror for TLS.

**Magnetometer non-issue:** browsers do not expose raw magnetometer x/y/z (only compass heading via
`DeviceOrientationEvent`, on some platforms). This turns out not to matter: `MadgwickAHRS.update()`
already has a `mag=None` fallback path, and per the 2026-08-10 accel-bias fix, the existing pipeline
already calls it with `mag=None` by default. The browser client only needs to supply accelerometer
+ gyroscope data to match today's actual behavior.

### 2a. Correction from rev. 1 of this spec

**Rev. 1 proposed a new plain-`ws://` port (8879) on `pendulastic_phone_server.py`, described as
mirroring the "already-working pattern" of the existing port-8878 `_ws_client` JPEG handler.** A
Gemini peer review caught that this is a real mixed-content violation, verified directly against
the code before accepting the correction:

- Port 8878's server is `asyncio.start_server(_ws_client, "0.0.0.0", PORT_WS, ...)` with **no SSL
  context** — genuinely plain `ws://`. Only the HTTP server (port 8877) is TLS-wrapped
  (`ctx.wrap_socket(...)`, `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)`). A page served over `https://`
  cannot open a plain `ws://` connection — browsers block it as mixed content. "Mirror the existing
  pattern" was itself an unverified assumption: port 8878 is explicitly `(future)` per this module's
  own docstring, and the module's actual production iOS path is a plain-HTTP record-and-upload
  flow, not this WS path — so there is no evidence it has ever been exercised from an HTTPS page.
- A second, independently-verified option — TLS-wrapping a *new* port with the same self-signed
  cert — has its own real problem: Safari's trust for a self-signed cert is scoped per-port. A
  WebSocket TLS handshake failure has no user-facing "accept this certificate" flow the way a page
  load does (which shows an interstitial the user can click through) — it just silently fails.

Rev. 2 (this document) instead multiplexes the IMU WebSocket onto the **same port 8877** the
capture page itself was already loaded from and already trusted (Section 3.2) — the only option
that avoids both problems. Section 3.3 also gained two more verified corrections from the same
review round: the AHRS gravity-seeding requirement, and an explicit wire payload schema.

## 3. Design

### 3.1 IMU capture page

New page served by `pendulastic_phone_server.py`, structurally parallel to its existing camera
capture page (`_build_page`). Contains a single "Start Streaming" button — required because iOS
Safari only grants `DeviceMotionEvent`/`DeviceOrientationEvent` permission from inside a user-gesture
handler (`DeviceMotionEvent.requestPermission()`), not on page load. On tap: requests permission,
then on grant:
- Acquires a Screen Wake Lock (`navigator.wakeLock.request('screen')`, best-effort — during an
  actual trial the user isn't touching the phone, and mobile OSes dim/lock the screen after 30-60s
  of inactivity, which suspends JS and drops the WS connection; older iOS Safari without the Wake
  Lock API falls back to a muted, looping, hidden `<video>` element, a known workaround for forcing
  the display to stay active).
- Attaches a `devicemotion` listener and begins sending batched JSON samples (Section 3.3) over the
  WS connection (Section 3.2).

If permission is denied, shows a clear inline message (Safari persists a "Don't Allow" choice for
the session — the message should say so and suggest reloading the tab, since there is no in-page
way to re-prompt once denied). Shows a simple connected/streaming status line; no other controls
for v1.

### 3.2 IMU WebSocket endpoint (multiplexed on the existing HTTPS port)

*(Revised in rev. 2 — see Section 2a for why a separate port was rejected.)*

Rather than a new port, `_PageHandler.do_GET` (the existing HTTPS request handler on port 8877)
gains a branch: a request to a new path (e.g. `/imu_ws`) with an `Upgrade: websocket` header hijacks
the connection — same hand-rolled HTTP-upgrade-then-frame-loop logic `_ws_client` already
implements, adapted from `_ws_client`'s asyncio-stream I/O to `BaseHTTPRequestHandler`'s synchronous
`self.connection`/`self.rfile`/`self.wfile` (each request already runs on its own thread under
`ThreadingHTTPServer`, so holding one open for the life of a streaming connection doesn't block
other requests). Because the WS upgrade happens on the same host:port the page was already loaded
from over HTTPS, it inherits that connection's already-accepted TLS trust — no separate cert
acceptance, no cross-port trust question.

Receives one JSON message per batch of motion samples from the page (schema in 3.3). Does not
implement any IMU parsing/fusion logic itself — every sample is handed to the bridge immediately.

### 3.3 Sample-translation bridge

A small, independently unit-testable function translating one browser-reported batch into calls
against `_IMUDevice.on_accel()` / `on_gyro()`.

**Wire payload** (client → server, one message per batch):
```json
{
  "batch": [
    {"ts": 1234.5, "accel": {"x": 0.12, "y": 9.81, "z": 0.05},
                   "gyro":  {"x": 0.01, "y": -0.02, "z": 0.00}}
  ]
}
```
`ts` is the browser's `event.timeStamp` in milliseconds (a `DOMHighResTimeStamp` — relative to
navigation start, *not* Unix epoch). This is fine as-is: `on_gyro()`'s existing `dt` calculation
(`dt = (ts - self.last_gyro_t) / 1000.0`, sanity-clamped to `0 < dt < 0.5` with a `0.01`s fallback)
only ever uses `ts` as a same-device difference, never compares it across devices or to wall time —
the bridge just needs to consistently pass milliseconds, matching what `on_accel`/`on_gyro` already
expect from Sensor Stream Pro's own millisecond timestamps.

**Field mapping, verified against `_IMUDevice`:**
- `accel.{x,y,z}` **must** come from `event.accelerationIncludingGravity`, not `event.acceleration`.
  `_IMUDevice`'s AHRS seeding (`_gravity_seed(raw_accel)`) reads the gravity component out of the
  raw accelerometer vector to establish initial orientation — gravity-excluded linear acceleration
  would seed it from near-zero magnitude and produce garbage orientation at rest.
- `gyro.{x,y,z}` comes from `event.rotationRate`, converted deg/s → rad/s (matching Sensor Stream's
  own rad/s convention per the protocol docstring in Section 2). Per the `DeviceMotionEvent` spec,
  `rotationRate`'s axis names do not map 1:1 by position — `beta` is rotation around the X axis,
  `gamma` around Y, `alpha` around Z. The bridge's unit test should pin this mapping explicitly
  (and confirm sign convention against a real device during the manual smoke test — Section 5.3 —
  since right-hand-rule sign is not guaranteed identical to Sensor Stream Pro's own frame).

This is the one place unit/shape mismatches are handled, kept separate from both the WS plumbing
(3.2) and `_IMUDevice` itself so it can be tested with plain sample dicts in, expected
`on_accel`/`on_gyro` call args out — no WebSocket or Tkinter involved.

### 3.4 Acquisition panel entry point

A new "Phone IMU (browser)" option alongside the existing "iPhone IMU" (Sensor Stream Pro)
checkbox in `pendulastic_app.py`'s Acquisition panel, wired the same way `_switch_to_phone_camera`
already wires the camera QR flow: on selection, starts `pendulastic_phone_server`'s IMU page/WS
(if not already running) and calls `show_phone_pairing_panel(url)` with the IMU page's URL,
reusing that QR-rendering code verbatim.

### 3.5 Error handling

- Permission denied, or the page opened on a browser without `DeviceMotionEvent` support: page
  shows a clear inline message (see 3.1); no WS connection is attempted.
- WS connection drops (screen lock despite the Wake Lock attempt, backgrounded tab — iOS
  aggressively suspends background JS): handled the same way `pendulastic_imu_server.py` already
  tolerates a quiet/dropped Sensor Stream socket today — not treated as a fatal error, no crash,
  device simply stops producing until (if ever) the page reconnects.
- Two browser tabs/phones connect to the IMU WS endpoint at once: out of scope (Section 4). Unlike
  Sensor Stream Pro's `_device_for(ip)` (which keys a device by source IP and assigns roles across
  up to two IPs), the v1 bridge only ever feeds one fixed `_IMUDevice` — there is no role
  assignment to do. A second incoming connection simply takes over feeding that same device; the
  first connection's socket is closed.

## 4. Out of Scope

- Two-phone (proximal + distal) simultaneous browser streaming for full joint-angle fusion —
  Sensor Stream Pro remains the only path for that until/unless a future spec extends this one.
- Removing or modifying Sensor Stream Pro support in any way — this is purely additive.
- Raw magnetometer capture from the browser (not reliably available; not needed — Section 2).
- Any change to `pendulastic_imu_server.py`'s own protocol, ports, or parsing.
- Porting port 8878's plain-`ws://` JPEG streaming to TLS — a real gap the same mixed-content issue
  applies to, but it's pre-existing, unrelated to IMU, and not this spec's to fix.

## 5. Testing Plan

1. Sample-translation bridge (3.3): unit tests with plain sample dicts in, asserting the correct
   `on_accel`/`on_gyro` calls, unit conversions, and the `alpha`/`beta`/`gamma` → `z`/`x`/`y` axis
   mapping — no server or WS involved, mirroring how `tests/test_imu_server.py` already tests
   `_IMUDevice` directly.
2. WS-upgrade-on-`do_GET` plumbing: thinner tests extending `tests/test_phone_server.py`'s existing
   patterns (connection upgrade, message framing), plus a regression test that a plain (non-upgrade)
   GET to `/imu_ws` still 404s/behaves sanely rather than hanging.
3. Manual smoke test: open the IMU page on a real phone via the QR code, confirm live accel/gyro
   values reach `_IMUDevice`, verify the gyro axis sign convention against a known rotation, and
   confirm the existing calibration/recording flow behaves identically to a Sensor Stream Pro
   connection.
