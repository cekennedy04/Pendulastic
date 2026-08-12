# Browser-Based Phone IMU Streaming — Design Spec
**Date:** 2026-08-12
**Status:** Approved

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
runs a second, real (not stub) hand-rolled WebSocket server on port 8878 (`_ws_client`) that
streams JPEG frames from an already-open browser tab. This spec extends this module with a sibling
page + WS port for IMU data, following the same structural pattern rather than introducing a third
server module.

**Magnetometer non-issue:** browsers do not expose raw magnetometer x/y/z (only compass heading via
`DeviceOrientationEvent`, on some platforms). This turns out not to matter: `MadgwickAHRS.update()`
already has a `mag=None` fallback path, and per the 2026-08-10 accel-bias fix, the existing pipeline
already calls it with `mag=None` by default. The browser client only needs to supply accelerometer
+ gyroscope data to match today's actual behavior.

## 3. Design

### 3.1 IMU capture page

New page served by `pendulastic_phone_server.py`, structurally parallel to its existing camera
capture page (`_build_page`). Contains a single "Start Streaming" button — required because iOS
Safari only grants `DeviceMotionEvent`/`DeviceOrientationEvent` permission from inside a user-gesture
handler (`DeviceMotionEvent.requestPermission()`), not on page load. On tap: requests permission,
then on grant, attaches a `devicemotion` listener and begins sending batched JSON samples over the
new WS connection (Section 3.2). Shows a simple connected/streaming status line; no other controls
for v1.

### 3.2 IMU WebSocket endpoint

New `_imu_ws_client` handler in `pendulastic_phone_server.py`, structurally parallel to the
existing `_ws_client` (JPEG) handler — same hand-rolled HTTP-upgrade-then-frame-loop shape, new
port (next free after 8877/8878 — 8879). Receives one JSON message per batch of motion samples
from the page. Does not implement any IMU parsing/fusion logic itself — every sample is handed to
the bridge (3.3) immediately.

### 3.3 Sample-translation bridge

A small, independently unit-testable function: browser `devicemotion` sample shape (SI units:
`acceleration.{x,y,z}` in m/s², `rotationRate.{alpha,beta,gamma}` in deg/s) → the exact calls
`_IMUDevice.on_accel()` / `on_gyro()` already expect from a Sensor Stream Pro connection (unit
conversion where the two differ — Sensor Stream reports gyro in rad/s per the existing protocol
docstring). This is the one place unit/shape mismatches are handled, kept separate from both the WS
plumbing (3.2) and `_IMUDevice` itself so it can be tested with plain sample dicts in, expected
`on_accel`/`on_gyro` call args out — no WebSocket or Tkinter involved.

### 3.4 Acquisition panel entry point

A new "Phone IMU (browser)" option alongside the existing "iPhone IMU" (Sensor Stream Pro)
checkbox in `pendulastic_app.py`'s Acquisition panel, wired the same way `_switch_to_phone_camera`
already wires the camera QR flow: on selection, starts `pendulastic_phone_server`'s IMU page/WS
(if not already running) and calls `show_phone_pairing_panel(url)` with the IMU page's URL,
reusing that QR-rendering code verbatim.

### 3.5 Error handling

- Permission denied, or the page opened on a browser without `DeviceMotionEvent` support: page
  shows a clear inline message; no WS connection is attempted.
- WS connection drops (screen lock, backgrounded tab — iOS aggressively suspends background JS):
  handled the same way `pendulastic_imu_server.py` already tolerates a quiet/dropped Sensor Stream
  socket today — not treated as a fatal error, no crash, device simply stops producing until (if
  ever) the page reconnects.
- Two browser tabs/phones connect to the IMU WS port at once: out of scope (Section 4). Unlike
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

## 5. Testing Plan

1. Sample-translation bridge (3.3): unit tests with plain sample dicts in, asserting the correct
   `on_accel`/`on_gyro` calls and unit conversions out — no server or WS involved, mirroring how
   `tests/test_imu_server.py` already tests `_IMUDevice` directly.
2. `_imu_ws_client` plumbing: thinner tests extending `tests/test_phone_server.py`'s existing
   patterns for the camera `_ws_client` handler (connection upgrade, message framing).
3. Manual smoke test: open the IMU page on a real phone via the QR code, confirm live accel/gyro
   values reach `_IMUDevice` and the existing calibration/recording flow behaves identically to a
   Sensor Stream Pro connection.
