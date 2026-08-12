# Browser-Based Phone IMU Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a phone stream accelerometer + gyroscope data into Pendulastic by opening a URL/QR
code in its own browser — no Sensor Stream Pro app install — while leaving the existing Sensor
Stream Pro path completely untouched.

**Architecture:** `pendulastic_phone_server.py` gains a fourth server surface — a dedicated
single-port HTTPS+WS server (`_ImuStreamHandler`, mirroring the existing `_StreamHandler` camera
pattern exactly: page and WebSocket share one port, so the page's already-accepted TLS trust
covers the WS upgrade too, avoiding the mixed-content/cross-port-trust problems a separate port
would hit). Each incoming browser sample is repackaged into Sensor Stream Pro's own wire shape and
handed to `pendulastic_imu_server._dispatch()` — the exact function Sensor Stream Pro's own
connection handler already calls — so `pendulastic_imu_server.py` requires zero changes and 100%
of its existing AHRS/calibration/recording pipeline is reused unmodified.

**Tech Stack:** Python stdlib `http.server` + hand-rolled WebSocket framing (already implemented
and tested in `pendulastic_phone_server.py` — this plan reuses `compute_ws_accept_key`,
`read_ws_frame`, `build_ws_text_frame`, `_build_ws_frame`, `get_or_create_self_signed_cert`
verbatim), vanilla browser JS (`DeviceMotionEvent`, `WebSocket`, Wake Lock API). No new
dependencies — `cryptography` and `qrcode` are already in `requirements.txt`.

## Global Constraints

- Additive only: `pendulastic_imu_server.py` is not modified by this plan (per the approved spec,
  Section 4).
- No new pip dependencies.
- Reuse existing WS framing helpers (`compute_ws_accept_key`, `read_ws_frame`,
  `build_ws_text_frame`, `_build_ws_frame`) and cert helper (`get_or_create_self_signed_cert`) from
  `pendulastic_phone_server.py` rather than reimplementing them.
- Browser accel must be `event.accelerationIncludingGravity`, not `event.acceleration` (per spec
  Section 3.3 — `_gravity_seed()` needs the gravity component).
- Sample timestamps sent to `pendulastic_imu_server._dispatch()` must be **server-side epoch
  milliseconds** (`time.time() * 1000`), not the browser's raw `event.timeStamp`. Verified against
  `_payload_ts()`: its seconds-vs-ms heuristic (`< 1e11` → treat as seconds, multiply by 1000) is
  built for epoch-scale Sensor-Stream-Pro timestamps and would silently corrupt a browser's
  small, page-load-relative `event.timeStamp` by 1000x. This is not in the spec document itself —
  found during planning by reading `_payload_ts()` directly — but follows directly from the spec's
  "feed the same `_IMUDevice` entry points, everything downstream unmodified" requirement.

---

## Task 1: Sample-translation bridge (`_forward_imu_batch`)

**Files:**
- Modify: `pendulastic_phone_server.py` (add near the top, after the existing WS-framing helpers
  around line 274 — i.e. after `build_ws_close_frame`)
- Test: `tests/test_phone_server.py`

**Interfaces:**
- Consumes: `pendulastic_imu_server._dispatch(path: str, message: str, ip: str)` (existing,
  unchanged — this is the exact function Sensor Stream Pro's own message handler already calls).
- Produces: `_forward_imu_batch(batch: dict, ip: str) -> int` — decodes one browser batch message,
  forwards each sample to `_dispatch()`, returns the count of samples successfully forwarded
  (0 if `batch` is malformed — never raises).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_phone_server.py` (new section near the top, after the existing WS-framing-helper
tests around line 170, before the `import json` / TLS-integration block):

```python
def test_forward_imu_batch_dispatches_accel_and_gyro(monkeypatch):
    calls = []
    monkeypatch.setattr(pps.imu_server, "_dispatch",
                        lambda path, message, ip: calls.append((path, json.loads(message), ip)))
    monkeypatch.setattr(pps.time, "time", lambda: 1723456789.0)

    batch = {"batch": [
        {"ts": 1234.5,
         "accel": {"x": 0.12, "y": 9.81, "z": 0.05},
         "gyro":  {"x": 0.01, "y": -0.02, "z": 0.0}},
    ]}
    n = pps._forward_imu_batch(batch, "10.0.0.5")

    assert n == 1
    assert len(calls) == 2
    accel_call = next(c for c in calls if c[0] == "/accelerometer")
    gyro_call  = next(c for c in calls if c[0] == "/gyroscope")
    assert accel_call[2] == "10.0.0.5"
    assert accel_call[1]["x"] == 0.12
    assert accel_call[1]["y"] == 9.81
    assert accel_call[1]["z"] == 0.05
    assert accel_call[1]["Timestamp"] == 1723456789000
    assert gyro_call[1]["x"] == 0.01
    assert gyro_call[1]["Timestamp"] == 1723456789000


def test_forward_imu_batch_processes_multiple_samples_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(pps.imu_server, "_dispatch",
                        lambda path, message, ip: calls.append(path))
    monkeypatch.setattr(pps.time, "time", lambda: 1000.0)

    batch = {"batch": [
        {"ts": 0, "accel": {"x": 0, "y": 0, "z": 0}, "gyro": {"x": 0, "y": 0, "z": 0}},
        {"ts": 10, "accel": {"x": 1, "y": 1, "z": 1}, "gyro": {"x": 1, "y": 1, "z": 1}},
    ]}
    n = pps._forward_imu_batch(batch, "10.0.0.5")

    assert n == 2
    assert calls == ["/accelerometer", "/gyroscope", "/accelerometer", "/gyroscope"]


def test_forward_imu_batch_missing_batch_key_returns_zero():
    assert pps._forward_imu_batch({}, "10.0.0.5") == 0


def test_forward_imu_batch_skips_sample_missing_accel_or_gyro(monkeypatch):
    calls = []
    monkeypatch.setattr(pps.imu_server, "_dispatch",
                        lambda path, message, ip: calls.append(path))
    batch = {"batch": [{"ts": 0, "accel": {"x": 0, "y": 0, "z": 0}}]}   # no gyro
    n = pps._forward_imu_batch(batch, "10.0.0.5")
    assert n == 0
    assert calls == []


def test_forward_imu_batch_not_a_dict_returns_zero():
    assert pps._forward_imu_batch("not a dict", "10.0.0.5") == 0
    assert pps._forward_imu_batch(None, "10.0.0.5") == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phone_server.py -k forward_imu_batch -v`
Expected: FAIL — `module 'pendulastic_phone_server' has no attribute '_forward_imu_batch'` (and
`pps.imu_server` doesn't exist yet either).

- [ ] **Step 3: Implement**

Add near the top of `pendulastic_phone_server.py`, in the imports block (after the existing
`from cryptography...` imports):

```python
import pendulastic_imu_server as imu_server
```

Add after `build_ws_close_frame()` (end of the WS-framing-helpers section, before the
`# ─── minimal phone-camera streaming page` comment):

```python
def _forward_imu_batch(batch, ip: str) -> int:
    """Decode one browser IMU batch message and forward each sample into
    pendulastic_imu_server._dispatch() -- the same entry point Sensor Stream
    Pro's own connection handler already calls, so every downstream
    consumer (AHRS fusion, calibration, recording) is unmodified.

    Timestamps sent to _dispatch() are this server's own receipt-time in
    epoch ms, NOT the browser's event.timeStamp -- _payload_ts()'s
    seconds-vs-ms heuristic (anything under ~1e11 is treated as seconds and
    multiplied by 1000) is built for epoch-scale Sensor-Stream-Pro
    timestamps and would silently corrupt a browser's small,
    page-load-relative event.timeStamp by 1000x.

    Never raises -- malformed input yields 0 forwarded samples."""
    if not isinstance(batch, dict):
        return 0
    samples = batch.get("batch")
    if not isinstance(samples, list):
        return 0

    n = 0
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        accel = sample.get("accel")
        gyro  = sample.get("gyro")
        if not isinstance(accel, dict) or not isinstance(gyro, dict):
            continue
        try:
            ax, ay, az = float(accel["x"]), float(accel["y"]), float(accel["z"])
            gx, gy, gz = float(gyro["x"]),  float(gyro["y"]),  float(gyro["z"])
        except (KeyError, TypeError, ValueError):
            continue

        ts_ms = int(time.time() * 1000)
        imu_server._dispatch("/accelerometer",
                             json.dumps({"Timestamp": ts_ms, "x": ax, "y": ay, "z": az}), ip)
        imu_server._dispatch("/gyroscope",
                             json.dumps({"Timestamp": ts_ms, "x": gx, "y": gy, "z": gz}), ip)
        n += 1
    return n
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phone_server.py -k forward_imu_batch -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test_phone_server.py suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phone_server.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_phone_server.py tests/test_phone_server.py
git commit -m "feat: add browser-to-Sensor-Stream IMU sample translation bridge"
```

---

## Task 2: IMU capture page

**Files:**
- Modify: `pendulastic_phone_server.py` (add the `_IMU_PAGE` constant, near `_STREAM_PAGE`, after
  its closing `"""` and before the `# ─── single-port HTTPS + WS stream server` comment)
- Test: `tests/test_phone_server.py`

**Interfaces:**
- Produces: `_IMU_PAGE: str` (a complete HTML document, UTF-8, referenced by Task 3's
  `_ImuStreamHandler`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_phone_server.py`, in the same section as the existing
`test_stream_page_is_well_formed_utf8_html` / `test_stream_page_uses_wake_lock_and_reconnects` /
`test_stream_page_has_no_mediapipe_dependency` tests:

```python
def test_imu_page_is_well_formed_utf8_html():
    from html.parser import HTMLParser
    page = pps._IMU_PAGE.encode("utf-8").decode("utf-8")
    assert page.strip().startswith("<!DOCTYPE html>")
    HTMLParser().feed(page)   # raises on structurally broken markup


def test_imu_page_requests_motion_permission_and_uses_gravity_inclusive_accel():
    page = pps._IMU_PAGE
    assert "DeviceMotionEvent.requestPermission" in page
    assert "accelerationIncludingGravity" in page
    assert "event.acceleration." not in page   # must not use the gravity-excluded property


def test_imu_page_uses_wake_lock_and_reconnects():
    page = pps._IMU_PAGE
    assert "wakeLock" in page
    assert "navigator.wakeLock.request" in page
    assert "onclose" in page and "setTimeout" in page   # reconnect-with-backoff, mirrors camera page


def test_imu_page_connects_to_same_origin_wss_imu_ws_path():
    assert "wss://' + location.host + '/imu_ws'" in pps._IMU_PAGE


def test_imu_page_maps_rotation_rate_axes_correctly():
    """DeviceMotionEvent.rotationRate's axis names do not map 1:1 by
    position -- beta is rotation around X, gamma around Y, alpha around Z
    (spec Section 3.3). Pin the exact mapping so a future edit can't
    silently swap it."""
    assert "gyro:  {x: r.beta, y: r.gamma, z: r.alpha}" in pps._IMU_PAGE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phone_server.py -k imu_page -v`
Expected: FAIL — `module 'pendulastic_phone_server' has no attribute '_IMU_PAGE'`.

- [ ] **Step 3: Implement**

Add to `pendulastic_phone_server.py`, after `_STREAM_PAGE`'s closing `"""`:

```python
# ─── minimal phone-IMU streaming page (accel + gyro only, no camera) ──────────

_IMU_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no,viewport-fit=cover">
<title>Pendulastic — Phone IMU</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;overflow:hidden;background:#000;
  font-family:-apple-system,BlinkMacSystemFont,sans-serif;color:#e2e8f0;
  display:flex;flex-direction:column;align-items:center;justify-content:center}
#status{font-size:16px;margin-bottom:16px;text-align:center;padding:0 24px}
#start{font-size:18px;padding:14px 32px;border-radius:10px;border:none;
  background:#2563eb;color:#fff;cursor:pointer}
#start:disabled{background:#475569}
#error{display:none;color:#fca5a5;padding:16px 24px;text-align:center;font-size:14px}
</style>
</head>
<body>
<div id="status">Tap Start, then keep this tab open and the screen on.</div>
<button id="start">Start Streaming</button>
<div id="error"></div>
<script>
const statusEl = document.getElementById('status');
const errorEl  = document.getElementById('error');
const startBtn = document.getElementById('start');

let ws = null, closedByUser = false, backoff = 500, wakeLock = null;
let sendBuf = [];

function showError(msg) {
  errorEl.textContent = msg;
  errorEl.style.display = 'block';
}

async function acquireWakeLock() {
  try {
    wakeLock = await navigator.wakeLock.request('screen');
  } catch (e) { /* not fatal -- streaming still works, screen may just sleep */ }
}
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState === 'visible' && ws) await acquireWakeLock();
});

function connectWs() {
  if (closedByUser) return;
  statusEl.textContent = 'Connecting...';
  ws = new WebSocket('wss://' + location.host + '/imu_ws');

  ws.onopen = () => {
    statusEl.textContent = 'Streaming';
    backoff = 500;
  };
  ws.onerror = () => { statusEl.textContent = 'Connection error'; };
  ws.onclose = () => {
    if (closedByUser) return;
    statusEl.textContent = 'Reconnecting...';
    setTimeout(connectWs, backoff);
    backoff = Math.min(backoff * 2, 8000);
  };
}

function flushBuffer() {
  if (ws && ws.readyState === WebSocket.OPEN && sendBuf.length) {
    ws.send(JSON.stringify({batch: sendBuf}));
    sendBuf = [];
  }
}
setInterval(flushBuffer, 50);   // batch at ~20Hz to keep message count low

function onMotion(event) {
  const a = event.accelerationIncludingGravity;
  const r = event.rotationRate;
  if (!a || a.x === null || !r || r.beta === null) return;
  sendBuf.push({
    ts: event.timeStamp,
    accel: {x: a.x, y: a.y, z: a.z},
    gyro:  {x: r.beta, y: r.gamma, z: r.alpha},
  });
}

async function start() {
  startBtn.disabled = true;
  try {
    if (typeof DeviceMotionEvent !== 'undefined'
        && typeof DeviceMotionEvent.requestPermission === 'function') {
      const result = await DeviceMotionEvent.requestPermission();
      if (result !== 'granted') {
        showError('Motion permission denied. Reload this page and tap Start again to retry.');
        startBtn.disabled = false;
        return;
      }
    }
    await acquireWakeLock();
    window.addEventListener('devicemotion', onMotion);
    connectWs();
    startBtn.style.display = 'none';
  } catch (e) {
    showError('Could not start motion streaming: ' + e.message);
    startBtn.disabled = false;
  }
}

if (typeof DeviceMotionEvent === 'undefined') {
  showError('This browser does not support motion sensors.');
  startBtn.disabled = true;
} else {
  startBtn.addEventListener('click', start);
}
</script>
</body>
</html>
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phone_server.py -k imu_page -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test_phone_server.py suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phone_server.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_phone_server.py tests/test_phone_server.py
git commit -m "feat: add browser IMU capture page (accel+gyro, no app install)"
```

---

## Task 3: `_ImuStreamHandler` + `start_imu_stream_server`/`stop_imu_stream_server`

**Files:**
- Modify: `pendulastic_phone_server.py` (add `PORT_IMU_HTTPS` constant near `PORT_STREAM_HTTPS`;
  add the handler class and start/stop functions near `_StreamHandler`/`start_stream_server`/
  `stop_stream_server`, i.e. right after `stop_stream_server()` and before
  `# ─── HTTP server ───` )
- Test: `tests/test_phone_server.py`

**Interfaces:**
- Consumes: `_forward_imu_batch` (Task 1), `_IMU_PAGE` (Task 2), and the existing
  `compute_ws_accept_key`, `read_ws_frame`, `_build_ws_frame`, `get_or_create_self_signed_cert`,
  `get_local_ip`, `_ThreadingHTTPSServer`.
- Produces: `start_imu_stream_server(cert_dir: str | None = None, port: int | None = None) ->
  tuple[str, int]` (local_ip, actual_port), `stop_imu_stream_server() -> None`. Both idempotent,
  same contract as `start_stream_server`/`stop_stream_server`. Consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_phone_server.py`, in the TLS-integration section (after the existing
`test_start_stream_server_is_idempotent`, reusing that section's `_connect_tls` helper and
imports):

```python
def test_start_imu_stream_server_serves_the_page_over_https(tmp_path):
    ip, port = pps.start_imu_stream_server(cert_dir=str(tmp_path), port=0)
    try:
        sock = _connect_tls(port)
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        assert b"200" in data.split(b"\r\n", 1)[0]
        assert b"Start Streaming" in data
    finally:
        pps.stop_imu_stream_server()


def test_imu_stream_server_websocket_batch_reaches_dispatch(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pps.imu_server, "_dispatch",
                        lambda path, message, ip: calls.append((path, ip)))

    ip, port = pps.start_imu_stream_server(cert_dir=str(tmp_path), port=0)
    try:
        sock = _connect_tls(port)
        req = (
            "GET /imu_ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(req.encode())
        resp = sock.recv(4096)
        assert b"101" in resp.split(b"\r\n", 1)[0]

        payload = json.dumps({"batch": [
            {"ts": 0, "accel": {"x": 0.1, "y": 9.8, "z": 0.0},
                      "gyro":  {"x": 0.0, "y": 0.0, "z": 0.0}},
        ]}).encode()
        mask_key = b"\x11\x22\x33\x44"
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        plen = len(masked)
        frame_hdr = bytes([0x81, 0x80 | plen]) if plen <= 125 else \
            bytes([0x81, 0x80 | 126]) + _struct.pack(">H", plen)
        sock.sendall(frame_hdr + mask_key + masked)

        _time.sleep(0.3)
        assert ("/accelerometer", "127.0.0.1") in calls
        assert ("/gyroscope", "127.0.0.1") in calls
    finally:
        pps.stop_imu_stream_server()


def test_start_imu_stream_server_is_idempotent(tmp_path):
    ip1, port1 = pps.start_imu_stream_server(cert_dir=str(tmp_path), port=0)
    ip2, port2 = pps.start_imu_stream_server(cert_dir=str(tmp_path), port=0)
    try:
        assert (ip1, port1) == (ip2, port2)
    finally:
        pps.stop_imu_stream_server()


def test_imu_stream_server_new_connection_replaces_old_active_one(tmp_path):
    ip, port = pps.start_imu_stream_server(cert_dir=str(tmp_path), port=0)
    try:
        sock1 = _connect_tls(port)
        req = (
            "GET /imu_ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock1.sendall(req.encode())
        assert b"101" in sock1.recv(4096).split(b"\r\n", 1)[0]

        sock2 = _connect_tls(port)
        sock2.sendall(req.encode())
        assert b"101" in sock2.recv(4096).split(b"\r\n", 1)[0]

        sock1.settimeout(2.0)
        # The first connection's generation is now stale -- its read loop
        # must exit (socket closes) rather than staying open forever.
        data = sock1.recv(4096)
        assert data == b""
    finally:
        pps.stop_imu_stream_server()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phone_server.py -k imu_stream_server -v`
Expected: FAIL — `module 'pendulastic_phone_server' has no attribute 'start_imu_stream_server'`.

- [ ] **Step 3: Implement**

Add the port constant right after `PORT_STREAM_HTTPS = 8880` (line 52):

```python
PORT_IMU_HTTPS = 8881
```

Add module-level state right after the existing `_stream_active_generation` global and its
trailing comment (lines 67-74, ending `# ... notice it's been superseded and stop`):

```python
_imu_server = None
_imu_thread = None
_imu_running = False
_imu_local_ip = "127.0.0.1"
_imu_port = PORT_IMU_HTTPS
_imu_active_generation = 0   # bumped by each new WS connection; lets an
                             # older, still-technically-open connection
                             # notice it's been superseded and stop
```

Add the handler class right after `_StreamHandler` (before `def log_message`'s enclosing class
ends is fine too — place it as its own top-level class immediately after `_StreamHandler`):

```python
class _ImuStreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._handle_ws_upgrade()
            return
        page = _IMU_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.end_headers()
        self.wfile.write(page)

    def _handle_ws_upgrade(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = compute_ws_accept_key(key)
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self._serve_imu_connection()

    def _serve_imu_connection(self) -> None:
        global _imu_active_generation
        _imu_active_generation += 1
        my_generation = _imu_active_generation
        source_ip = self.client_address[0]

        self.connection.settimeout(1.0)

        def recv_exact(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = self.rfile.read(n - len(buf))
                if not chunk:
                    raise ConnectionError("peer closed")
                buf += chunk
            return buf

        try:
            while True:
                if my_generation != _imu_active_generation:
                    # A newer phone connection has taken over.
                    break
                try:
                    opcode, payload = read_ws_frame(recv_exact)
                except socket.timeout:
                    continue

                if my_generation != _imu_active_generation:
                    break

                if opcode == 0x8:
                    break
                elif opcode == 0x9:
                    self.wfile.write(_build_ws_frame(0xA, payload[:125]))
                elif opcode == 0x1:
                    try:
                        batch = json.loads(payload.decode("utf-8"))
                    except Exception:
                        continue
                    _forward_imu_batch(batch, source_ip)
        except Exception:
            pass

    def log_message(self, *_):
        pass


def start_imu_stream_server(cert_dir: str | None = None, port: int | None = None) -> tuple[str, int]:
    """Start the single-port HTTPS+WS phone-IMU stream server. Idempotent."""
    global _imu_server, _imu_thread, _imu_running, _imu_local_ip, _imu_port

    if _imu_running:
        return _imu_local_ip, _imu_port

    if cert_dir is None:
        cert_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".certs")
    bind_port = port if port is not None else PORT_IMU_HTTPS

    _imu_local_ip = get_local_ip()
    cert_path, key_path = get_or_create_self_signed_cert(cert_dir, _imu_local_ip)

    server = _ThreadingHTTPSServer(("0.0.0.0", bind_port), _ImuStreamHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    _imu_server = server
    _imu_port   = server.server_address[1]
    _imu_thread = threading.Thread(target=server.serve_forever, daemon=True, name="pps-imu")
    _imu_thread.start()
    _imu_running = True
    return _imu_local_ip, _imu_port


def stop_imu_stream_server() -> None:
    global _imu_server, _imu_running
    _imu_running = False
    try:
        if _imu_server:
            _imu_server.shutdown()
            _imu_server.server_close()
    except Exception:
        pass
    _imu_server = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phone_server.py -k imu_stream_server -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test_phone_server.py suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phone_server.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_phone_server.py tests/test_phone_server.py
git commit -m "feat: add single-port HTTPS+WS IMU stream server"
```

---

## Task 4: Acquisition panel entry point

**Files:**
- Modify: `pendulastic_app.py` (`AcquisitionPanel` — add the checkbox near `chk_imu`/`chk_rgb`
  around line 560-573; `App` — add the start/stop wiring near `_switch_to_phone_camera`, currently
  at line 2814)
- Test: `tests/test_acquisition_panel.py`

**Interfaces:**
- Consumes: `pendulastic_phone_server.start_imu_stream_server()` /
  `pendulastic_phone_server.stop_imu_stream_server()` (Task 3),
  `AcquisitionPanel.show_phone_pairing_panel(url)` / `hide_phone_pairing_panel()` (existing,
  unchanged).
- Produces: `AcquisitionPanel._src_imu_browser: tk.BooleanVar` (new checkbox state, default
  `False`), `App._on_imu_browser_toggled() -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_acquisition_panel.py`, near the existing `test_phone_camera_entry_constant_has_expected_shape`
/ `test_show_phone_pairing_panel_displays_url_text` tests:

```python
def test_imu_browser_checkbox_default_off():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        assert p._src_imu_browser.get() is False
    finally:
        r.destroy()


def test_imu_browser_checkbox_checking_starts_server_and_shows_qr(monkeypatch):
    from pendulastic_app import App
    calls = []
    monkeypatch.setattr(
        "pendulastic_phone_server.start_imu_stream_server",
        lambda: calls.append("start") or ("192.168.1.50", 8881))
    app = App()
    try:
        app._acq._src_imu_browser.set(True)
        app._on_imu_browser_toggled()
        app.update()
        assert calls == ["start"]
        assert app._acq._phone_pairing_frame.winfo_manager() == "pack"
        assert "192.168.1.50" in app._acq._phone_pairing_url_var.get()
        assert "8881" in app._acq._phone_pairing_url_var.get()
    finally:
        app.destroy()


def test_imu_browser_checkbox_unchecking_stops_server_and_hides_qr(monkeypatch):
    from pendulastic_app import App
    stop_calls = []
    monkeypatch.setattr(
        "pendulastic_phone_server.start_imu_stream_server",
        lambda: ("192.168.1.50", 8881))
    monkeypatch.setattr(
        "pendulastic_phone_server.stop_imu_stream_server",
        lambda: stop_calls.append("stop"))
    app = App()
    try:
        app._acq._src_imu_browser.set(True)
        app._on_imu_browser_toggled()
        app.update()
        app._acq._src_imu_browser.set(False)
        app._on_imu_browser_toggled()
        app.update()
        assert stop_calls == ["stop"]
        assert app._acq._phone_pairing_frame.winfo_manager() == ""
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_acquisition_panel.py -k imu_browser -v`
Expected: FAIL — `AcquisitionPanel` has no attribute `_src_imu_browser`.

- [ ] **Step 3: Implement**

In `pendulastic_app.py`, add the import near the existing guarded imports (find with
`grep -n "import pendulastic_phone_server as _pps"` — it's already imported as `_pps` at line 73;
no new import needed, reuse `_pps`).

In `AcquisitionPanel.__init__`, right after the existing `chk_imu`/`chk_rgb` block (after the
`for chk in (chk_imu, chk_rgb): chk.pack(side="left", padx=8)` loop, before the
`# IMU pairing hint` comment):

```python
        self._src_imu_browser = tk.BooleanVar(value=False)
        chk_imu_browser = tk.Checkbutton(
            chk_row, text="Phone IMU (browser)",
            variable=self._src_imu_browser,
            bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
            selectcolor=ws.PALETTE["SURFACE"],
            activebackground=ws.PALETTE["PANEL"],
            command=lambda: self.controller.on_imu_browser_toggled())
        chk_imu_browser.pack(side="left", padx=8)
```

`AcquisitionPanel.__init__(self, parent, controller)` already stores `controller` as
`self.controller` (verified: `AcquisitionPanel`'s docstring documents it as "controller: App
instance"), matching the pattern `self.controller.on_workbench_load_another()` already uses
elsewhere in this file.

In `App`, add right after `_switch_to_phone_camera` (which ends at the `self._acq.show_phone_pairing_panel(url)`
line, currently 2824):

```python
    def on_imu_browser_toggled(self) -> None:
        if self._acq._src_imu_browser.get():
            ip, port = _pps.start_imu_stream_server()
            self._acq.show_phone_pairing_panel(f"https://{ip}:{port}/")
        else:
            _pps.stop_imu_stream_server()
            self._acq.hide_phone_pairing_panel()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_acquisition_panel.py -k imu_browser -v`
Expected: PASS

- [ ] **Step 5: Run the full existing test_acquisition_panel.py suite to confirm no regression**

Run: `.venv\Scripts\python.exe -m pytest tests/test_acquisition_panel.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: add Phone IMU (browser) option to Acquisition panel"
```

---

## Task 5: Full regression + manual smoke test

**Files:** none modified — verification only.

- [ ] **Step 1: Run the entire test suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: all PASS, no skips due to import errors. (Pre-existing, unrelated environment failures —
the `mediapipe`-missing-`solutions` collection errors in `test_metrics.py`/`test_pose.py`, and any
Tcl/Tk multi-window flakes in a full-suite run — are not regressions from this plan; verify by
re-running any failure in isolation before treating it as real.)

- [ ] **Step 2: Spec-coverage check against `docs/superpowers/specs/2026-08-12-browser-imu-streaming-design.md`**

Walk each subsection and confirm a task above implements it:
- 3.1 IMU capture page, Wake Lock, permission handling → Task 2
- 3.2 IMU WebSocket endpoint, same-port multiplexing → Task 3
- 3.3 Sample-translation bridge, gravity-inclusive accel, axis mapping, wire schema → Task 1
- 3.4 Acquisition panel entry point → Task 4
- 3.5 Error handling (permission denial, WS drop, second-connection replacement) → Tasks 2 & 3
- Section 4 (out of scope items) → confirm no task touched `pendulastic_imu_server.py`

- [ ] **Step 3: Manual smoke test (requires a real phone)**

Run the app: `.venv\Scripts\python.exe pendulastic_app.py`. In the Acquisition panel, check "Phone
IMU (browser)". Confirm:
- A QR code appears; scanning it on a phone opens the IMU page over HTTPS (accepting the
  self-signed-cert warning once).
- Tapping "Start Streaming" prompts for motion-sensor permission (iOS) and, once granted, shows
  "Streaming".
- The phone's screen does not sleep while the tab stays open (Wake Lock working).
- Rotating the phone changes the live orientation/angle readout in the app the same way a Sensor
  Stream Pro connection would.
- Backgrounding the phone's browser tab and returning to it causes a brief "Reconnecting..." on
  the phone and then resumes streaming, without crashing the desktop app.
- Unchecking "Phone IMU (browser)" stops the server and hides the QR panel.

- [ ] **Step 4: Final commit (only if Step 3 surfaces a fix)**

If the manual smoke test finds nothing to fix, no commit is needed for this task. If it does, make
the minimal fix, re-run the full suite (Step 1), and commit with a message describing what the
smoke test caught.
