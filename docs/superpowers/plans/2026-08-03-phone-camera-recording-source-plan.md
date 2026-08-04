# Phone Camera as a Recording Source for pendulastic_app.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add "📱 Phone Camera" as a live recording source in `pendulastic_app.py`'s `AcquisitionPanel`, alongside the USB/webcam dropdown the `camera-selection` branch already provides. A phone opens a URL in its browser (no app install), streams live JPEG frames to the desktop over a same-port HTTPS+WebSocket connection, and the desktop treats that stream exactly like a webcam: live preview before recording, a `VideoWriter` attached/detached at start/stop.

**Architecture:** A new single-port HTTPS server in `pendulastic_phone_server.py` (self-signed cert, generated once and cached) serves a minimal phone-facing page (`_STREAM_PAGE`) and handles its WebSocket upgrade on the *same port* — avoiding the dual-origin certificate-trust problem a two-port design would hit on iOS Safari. Frames arrive with an NTP-style-synchronized, phone-captured timestamp and land in a new `stream_frame_queue`. `camera_utils.py` gains `PhoneCameraSession`, mirroring `CameraSession`'s public surface (`open`/`close`/`attach_writer`/`detach_writer`/`.active`/`.frame_size`/`on_frame`/`on_status`) but backed by that queue instead of `cv2.VideoCapture`. `pendulastic_app.py`'s `App` swaps `self._camera` between a `CameraSession` and a `PhoneCameraSession` depending on which dropdown entry is selected — the recording/preview code that calls through that interface needs no changes.

**Tech Stack:** Python 3.13, `cryptography` (self-signed cert generation), `qrcode` + Pillow (`ImageTk`, pairing QR — pattern already used in `pendulastic_viewer.py`), stdlib `http.server`/`ssl`/`socket` (single-port HTTPS+WS, no new WS library), vanilla JS (`_STREAM_PAGE`: `getUserMedia`, `requestVideoFrameCallback`, `navigator.wakeLock`, `canvas.toBlob`), pytest.

Full design rationale: `docs/superpowers/specs/2026-08-03-phone-camera-recording-source-design.md`.

## Global Constraints

- Full spec: `docs/superpowers/specs/2026-08-03-phone-camera-recording-source-design.md`. Builds on `docs/superpowers/specs/2026-07-31-camera-selection-design.md` / `docs/superpowers/plans/2026-07-31-camera-selection-plan.md` (`CameraSession`, `AcquisitionPanel`'s camera dropdown) — all work in this plan happens on the `camera-selection` branch, on top of that already-merged-to-this-branch code.
- `cryptography` and `qrcode` become **direct** dependencies of this feature (both already present in `.venv` transitively, but not listed in `requirements.txt`) — Task 2 adds them explicitly.
- Tests run from the worktree root with the shared repo venv (the worktree has no `.venv` of its own): `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/<file> -v`, executed with the worktree (`C:\Users\cladi\Pendulastic\.worktrees\camera-selection`) as the working directory.
- **`PhoneCameraSession`'s public surface must match `CameraSession`'s exactly** for the methods `App` calls polymorphically through the swapped `self._camera` reference: `open(cam) -> bool`, `close() -> None`, `attach_writer(writer) -> None`, `detach_writer()`, `.active` (dict or `None`), `.frame_size` (tuple or `None`), and the `on_frame(frame_bgr)` / `on_status(msg: str)` callback signatures (single positional arg each, unchanged from `CameraSession` — `App._on_camera_frame`/`_on_camera_status` need zero changes). `PhoneCameraSession` does **not** implement `.rescan()` — dropdown population is handled once, uniformly, at the `App` level (Task 9), not duplicated per session type.
- Status vocabulary reused from `CameraSession`'s existing `"live"`/`"lost"` strings, extended with `f"degraded: {fps}fps"` — `App._on_camera_status` (existing, untouched dispatch logic keyed on exact string `"live"`/`"lost"`, else falls through to displaying the raw message) already handles an arbitrary status string as a fallback display, so no change is needed there for the new `degraded:` message to show up; it *is* extended in Task 10 to handle phone-specific gap-flagging during recording.
- New module-level constants in `pendulastic_phone_server.py`: `PORT_STREAM_HTTPS = 8880`, `STREAM_RESOLUTION = (1280, 720)`, `STREAM_JPEG_QUALITY = 0.7`, `CLOCK_SYNC_INITIAL_ROUNDS = 5`, `CLOCK_SYNC_RESYNC_INTERVAL_S = 30.0`, `CLOCK_SYNC_WINDOW = 10`, `CLOCK_SYNC_MAD_K = 3.0`, `PHONE_TARGET_FPS = 24.0`, `PHONE_DEGRADED_FPS = 12.0`, `PHONE_DEGRADED_HYSTERESIS_S = 2.5`, `PHONE_LOST_TIMEOUT_S = 5.0`. These are the concrete numeric defaults referenced qualitatively in the spec (720p/quality~70, 2-3s hysteresis, ~30s re-sync).
- `stream_frame_queue` (new, in `pendulastic_phone_server.py`) uses the same drop-oldest-when-full backpressure as the existing `frame_queue`: `maxsize=4`, and on a full queue the oldest entry is discarded via `get_nowait()` before `put_nowait()`.
- This plan does **not** modify the existing `PORT_HTTP`/`PORT_WS`/`_ws_client`/`frame_queue`/`_TRACKING_PAGE`/`start()`/`stop()` machinery in `pendulastic_phone_server.py` at all — those are relied on today by `pendulastic_viewer.py` and are out of scope. The new single-port HTTPS+WS server (`start_stream_server()`/`stop_stream_server()`) is entirely separate, on its own port, with its own queue.
- Cert files are cached under `<repo_root>/.certs/stream_cert.pem` / `.certs/stream_key.pem`, regenerated automatically if missing, expired, or if the cached cert's Subject Alternative Name IP no longer matches the desktop's current LAN IP (it changes between networks).

---

### Task 1: Manual spike — verify `wss://` over a self-signed cert on iOS Safari (single port)

This is the design's highest-risk unknown (spec §3) and gates the whole single-port architecture. It must happen **before** Task 2 — if it fails, stop and revisit the design with the user rather than continuing.

**Files:**
- Create: `spike_wss_selfsigned.py` (throwaway diagnostic script, root-level — matches the existing convention of standalone `diagnose_*.py`/`spike_*.py`-style scripts already in the repo root)

**Interfaces:**
- Produces: nothing consumed by later tasks — this is a standalone, disposable verification script with its own inline (duplicated, intentionally — see below) minimal cert generation and WS handling. It is **not** wired into `pendulastic_phone_server.py`.

- [ ] **Step 1: Write the spike script**

```python
"""
spike_wss_selfsigned.py — throwaway spike to verify iOS Safari accepts a
wss:// connection over a self-signed cert on the SAME port as the page that
served the "Advanced -> Proceed" warning. Gates the phone-camera feature's
single-port HTTPS+WS design (see docs/superpowers/specs/2026-08-03-phone-
camera-recording-source-design.md section 3). Run this, then follow the
manual checklist printed at the bottom. Not part of the shipped feature.
"""
import base64
import datetime
import hashlib
import http.server
import ipaddress
import os
import socket
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

PORT = 8899
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _make_cert(ip: str, cert_path: str, key_path: str) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=7))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]),
            critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))


_PAGE = b"""<!DOCTYPE html><html><body style="font-family:sans-serif;padding:20px">
<h2>WSS Spike</h2>
<p id="status">connecting...</p>
<script>
const ws = new WebSocket("wss://" + location.host + "/ws");
ws.onopen    = () => { document.getElementById("status").textContent = "WS: CONNECTED"; };
ws.onerror   = () => { document.getElementById("status").textContent = "WS: ERROR (see console)"; };
ws.onclose   = (e) => { document.getElementById("status").textContent = "WS: CLOSED code=" + e.code; };
</script>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            key = self.headers.get("Sec-WebSocket-Key", "")
            accept = base64.b64encode(
                hashlib.sha1((key + _WS_MAGIC).encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            print("[spike] WS upgrade completed from", self.client_address)
            try:
                while True:
                    hdr = self.rfile.read(2)
                    if not hdr:
                        break
            except Exception:
                pass
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ip = _local_ip()
    cert_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".certs")
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, "spike_cert.pem")
    key_path  = os.path.join(cert_dir, "spike_key.pem")
    _make_cert(ip, cert_path, key_path)

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    print(f"\nOpen this on your iPhone (Safari): https://{ip}:{PORT}/\n")
    print("MANUAL CHECKLIST:")
    print("  1. Open the URL above in iOS Safari.")
    print("  2. Tap Advanced -> Proceed to accept the self-signed cert warning.")
    print("  3. The page should show 'WS: CONNECTED' within ~1s.")
    print("     - If it shows 'WS: CONNECTED': single-port design is viable. Proceed to Task 2.")
    print("     - If it shows 'WS: ERROR' or hangs on 'connecting...': stop here and")
    print("       revisit the design with the user (spec section 3, mitigation options 2-3).")
    print("  4. Repeat on Android Chrome for confirmation (expected to be less strict).")
    print("Press Ctrl+C to stop.\n")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        while True:
            input()
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 2: Run it and follow the manual checklist**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" spike_wss_selfsigned.py`

This prints a URL and a manual checklist. This step **requires a physical iPhone on the same network** (or reachable via the desktop's LAN IP) — it cannot be automated. Follow the printed checklist exactly.

**Decision gate:** if iOS Safari shows `WS: CONNECTED`, proceed to Task 2 (single-port design, as planned below). If it does not, **stop** — do not proceed with Tasks 2-11 as written; report the failure to the user so the design can be revisited (spec §3 mitigation options 2-3, which this plan does not detail).

- [ ] **Step 3: Commit the spike script (regardless of outcome — it's documentation of the verification)**

```bash
git add spike_wss_selfsigned.py
git commit -m "spike: verify wss:// over self-signed cert works on iOS Safari (single port)"
```

---

### Task 2: Self-signed certificate generation & caching

**Files:**
- Modify: `pendulastic_phone_server.py` (add near the top, after the ngrok section — new "─── TLS certificate ───" section)
- Modify: `requirements.txt` (add `cryptography>=42.0.0` and `qrcode>=8.0.0`)
- Test: `tests/test_phone_server.py` (new)

**Interfaces:**
- Produces: `pendulastic_phone_server.get_or_create_self_signed_cert(cert_dir: str, common_name: str) -> tuple[str, str]` — returns `(cert_path, key_path)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phone_server.py`:

```python
# tests/test_phone_server.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from cryptography import x509

import pendulastic_phone_server as pps


def test_get_or_create_self_signed_cert_creates_files(tmp_path):
    cert_path, key_path = pps.get_or_create_self_signed_cert(str(tmp_path), "192.168.1.50")
    assert os.path.exists(cert_path)
    assert os.path.exists(key_path)


def test_cert_has_matching_san_ip(tmp_path):
    cert_path, _ = pps.get_or_create_self_signed_cert(str(tmp_path), "192.168.1.50")
    with open(cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    ips = san.value.get_values_for_type(x509.IPAddress)
    assert str(ips[0]) == "192.168.1.50"


def test_repeated_call_with_same_ip_reuses_cached_cert(tmp_path):
    cert_path, key_path = pps.get_or_create_self_signed_cert(str(tmp_path), "192.168.1.50")
    mtime1 = os.path.getmtime(cert_path)
    cert_path2, key_path2 = pps.get_or_create_self_signed_cert(str(tmp_path), "192.168.1.50")
    assert cert_path2 == cert_path
    assert os.path.getmtime(cert_path2) == mtime1


def test_call_with_different_ip_regenerates_cert(tmp_path):
    cert_path, _ = pps.get_or_create_self_signed_cert(str(tmp_path), "192.168.1.50")
    with open(cert_path, "rb") as f:
        cert1 = x509.load_pem_x509_certificate(f.read())
    pps.get_or_create_self_signed_cert(str(tmp_path), "10.0.0.7")
    with open(cert_path, "rb") as f:
        cert2 = x509.load_pem_x509_certificate(f.read())
    assert cert1.serial_number != cert2.serial_number
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v`
Expected: FAIL with `AttributeError: module 'pendulastic_phone_server' has no attribute 'get_or_create_self_signed_cert'`

- [ ] **Step 3: Add the dependencies**

In `requirements.txt`, append:

```
cryptography>=42.0.0
qrcode>=8.0.0
```

- [ ] **Step 4: Implement `get_or_create_self_signed_cert`**

In `pendulastic_phone_server.py`, add near the top after the existing imports:

```python
import datetime
import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
```

Add a new section (e.g. after the `_ngrok_worker` function, before `# ─── IP discovery ───`):

```python
# ─── TLS certificate (self-signed, for the single-port HTTPS+WS stream server) ─

def get_or_create_self_signed_cert(cert_dir: str, common_name: str) -> tuple[str, str]:
    """Return (cert_path, key_path) for a self-signed cert whose Subject
    Alternative Name matches `common_name` (a LAN IP). Reuses a cached cert
    if one already exists for this exact IP and isn't expired; regenerates
    otherwise (e.g. the desktop's LAN IP changed between networks)."""
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, "stream_cert.pem")
    key_path  = os.path.join(cert_dir, "stream_key.pem")

    if os.path.exists(cert_path) and os.path.exists(key_path):
        try:
            with open(cert_path, "rb") as f:
                existing = x509.load_pem_x509_certificate(f.read())
            san = existing.extensions.get_extension_for_class(
                x509.SubjectAlternativeName)
            ips = [str(ip) for ip in san.value.get_values_for_type(x509.IPAddress)]
            not_after = existing.not_valid_after_utc
            if common_name in ips and not_after > datetime.datetime.now(datetime.timezone.utc):
                return cert_path, key_path
        except Exception:
            pass   # fall through and regenerate on any parse failure

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=397))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(common_name))]),
            critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
    return cert_path, key_path
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_phone_server.py requirements.txt tests/test_phone_server.py
git commit -m "feat: add self-signed cert generation/caching for the phone-stream HTTPS server"
```

---

### Task 3: NTP-style clock offset estimator with outlier rejection

**Files:**
- Modify: `pendulastic_phone_server.py` (new class, near the TLS section)
- Test: `tests/test_phone_server.py` (extend)

**Interfaces:**
- Produces: `pendulastic_phone_server.ClockSyncEstimator` — `.add_sample(t0: float, t1: float, t2: float) -> None` (all in milliseconds: `t0`=desktop send time, `t1`=phone echo time, `t2`=desktop receive time), `.offset_ms` property (`float | None` — `None` until enough samples).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phone_server.py`:

```python
def test_clock_sync_estimator_none_before_any_samples():
    est = pps.ClockSyncEstimator()
    assert est.offset_ms is None


def test_clock_sync_estimator_computes_offset_from_consistent_samples():
    est = pps.ClockSyncEstimator()
    # Phone clock is exactly 500ms ahead of desktop clock; near-zero RTT.
    for t0 in (1000.0, 1010.0, 1020.0, 1030.0, 1040.0):
        est.add_sample(t0=t0, t1=t0 + 500.0, t2=t0 + 2.0)
    assert est.offset_ms is not None
    assert abs(est.offset_ms - 500.0) < 5.0


def test_clock_sync_estimator_rejects_rtt_outlier():
    est = pps.ClockSyncEstimator()
    for t0 in (1000.0, 1010.0, 1020.0, 1030.0):
        est.add_sample(t0=t0, t1=t0 + 500.0, t2=t0 + 2.0)
    # One sample with a huge RTT (Wi-Fi power-save latency spike) and a
    # correspondingly skewed apparent offset — must be filtered out.
    est.add_sample(t0=1040.0, t1=1040.0 + 500.0 + 300.0, t2=1040.0 + 600.0)
    assert abs(est.offset_ms - 500.0) < 5.0


def test_clock_sync_estimator_window_is_bounded():
    est = pps.ClockSyncEstimator()
    for i in range(50):
        t0 = float(i * 10)
        est.add_sample(t0=t0, t1=t0 + 500.0, t2=t0 + 2.0)
    assert len(est._samples) <= pps.CLOCK_SYNC_WINDOW
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v -k clock_sync`
Expected: FAIL with `AttributeError: module 'pendulastic_phone_server' has no attribute 'ClockSyncEstimator'`

- [ ] **Step 3: Implement `ClockSyncEstimator`**

Add to `pendulastic_phone_server.py`, in the constants section near the top:

```python
CLOCK_SYNC_WINDOW = 10
CLOCK_SYNC_MAD_K  = 3.0
```

Add the class (after the TLS section):

```python
# ─── Clock synchronization (NTP-style, outlier-filtered) ───────────────────────

class ClockSyncEstimator:
    """Rolling window of (t0, t1, t2) round-trip samples -> a filtered
    phone-clock-to-desktop-clock offset in milliseconds. t0=desktop send,
    t1=phone echo, t2=desktop receive. A single Wi-Fi power-save latency
    spike must not skew the offset — RTT outliers are rejected via a
    median/MAD filter before the offset is (re)computed."""

    def __init__(self):
        import collections
        self._samples = collections.deque(maxlen=CLOCK_SYNC_WINDOW)
        self._offset_ms = None

    def add_sample(self, t0: float, t1: float, t2: float) -> None:
        rtt    = t2 - t0
        offset = t1 - (t0 + t2) / 2.0
        self._samples.append((rtt, offset))
        self._recompute()

    def _recompute(self) -> None:
        if not self._samples:
            self._offset_ms = None
            return
        rtts = sorted(r for r, _ in self._samples)
        n = len(rtts)
        median_rtt = rtts[n // 2] if n % 2 else (rtts[n // 2 - 1] + rtts[n // 2]) / 2.0
        deviations = sorted(abs(r - median_rtt) for r, _ in self._samples)
        mad = deviations[len(deviations) // 2] or 1.0   # avoid div-by-zero when all equal
        kept = [o for r, o in self._samples if abs(r - median_rtt) <= CLOCK_SYNC_MAD_K * mad]
        if not kept:
            kept = [o for _, o in self._samples]   # degenerate: keep everything rather than nothing
        self._offset_ms = sum(kept) / len(kept)

    @property
    def offset_ms(self):
        return self._offset_ms
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v -k clock_sync`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add pendulastic_phone_server.py tests/test_phone_server.py
git commit -m "feat: add NTP-style clock offset estimator with RTT outlier rejection"
```

---

### Task 4: WebSocket frame protocol primitives (pure functions)

**Files:**
- Modify: `pendulastic_phone_server.py` (new section, reused by Task 6's connection handler)
- Test: `tests/test_phone_server.py` (extend)

**Interfaces:**
- Produces:
  - `compute_ws_accept_key(sec_websocket_key: str) -> str`
  - `read_ws_frame(recv_exact: Callable[[int], bytes]) -> tuple[int, bytes]` — returns `(opcode, payload)`; `recv_exact(n)` must return exactly `n` bytes (blocking).
  - `build_ws_text_frame(text: str) -> bytes` (unmasked server->client frame)
  - `build_ws_close_frame() -> bytes`
  - `parse_stream_frame_payload(payload: bytes) -> tuple[int, int, bytes]` — returns `(frame_index, phone_ts_ms, jpeg_bytes)` from an 8-byte-header-prefixed binary WS payload.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phone_server.py`:

```python
import struct


def _mask(payload: bytes, mask_key: bytes) -> bytes:
    full = bytes(mask_key[i % 4] for i in range(len(payload)))
    return bytes(p ^ m for p, m in zip(payload, full))


def _build_masked_frame(opcode: int, payload: bytes) -> bytes:
    mask_key = b"\x01\x02\x03\x04"
    plen = len(payload)
    if plen <= 125:
        hdr = bytes([0x80 | opcode, 0x80 | plen])
    elif plen <= 0xFFFF:
        hdr = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", plen)
    else:
        hdr = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", plen)
    return hdr + mask_key + _mask(payload, mask_key)


def test_compute_ws_accept_key_matches_rfc6455_example():
    # RFC 6455 section 1.3 worked example.
    assert pps.compute_ws_accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_read_ws_frame_unmasks_text_payload():
    raw = _build_masked_frame(0x1, b'{"type":"sync_resp"}')
    buf = bytearray(raw)
    def recv_exact(n):
        chunk = bytes(buf[:n]); del buf[:n]; return chunk
    opcode, payload = pps.read_ws_frame(recv_exact)
    assert opcode == 0x1
    assert payload == b'{"type":"sync_resp"}'


def test_read_ws_frame_handles_extended_length_and_binary_opcode():
    big_payload = b"\xff" * 200
    raw = _build_masked_frame(0x2, big_payload)
    buf = bytearray(raw)
    def recv_exact(n):
        chunk = bytes(buf[:n]); del buf[:n]; return chunk
    opcode, payload = pps.read_ws_frame(recv_exact)
    assert opcode == 0x2
    assert payload == big_payload


def test_build_ws_text_frame_is_unmasked_and_round_trips():
    frame = pps.build_ws_text_frame('{"type":"sync_req","t0":123}')
    assert frame[0] == 0x81          # FIN + text opcode
    assert (frame[1] & 0x80) == 0    # server frames are never masked
    buf = bytearray(frame)
    def recv_exact(n):
        chunk = bytes(buf[:n]); del buf[:n]; return chunk
    # read_ws_frame supports unmasked frames too (mask bit optional on read)
    opcode, payload = pps.read_ws_frame(recv_exact)
    assert opcode == 0x1
    assert payload == b'{"type":"sync_req","t0":123}'


def test_parse_stream_frame_payload_extracts_header_and_jpeg():
    header = struct.pack("<II", 42, 1_700_000_123 & 0xFFFFFFFF)
    payload = header + b"\xff\xd8\xff\xe0FAKEJPEGBYTES"
    idx, ts, jpeg = pps.parse_stream_frame_payload(payload)
    assert idx == 42
    assert ts == 1_700_000_123 & 0xFFFFFFFF
    assert jpeg == b"\xff\xd8\xff\xe0FAKEJPEGBYTES"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v -k "ws_frame or accept_key or stream_frame_payload"`
Expected: FAIL with `AttributeError` for each missing function.

- [ ] **Step 3: Implement the primitives**

Add to `pendulastic_phone_server.py` (after the `ClockSyncEstimator` section):

```python
# ─── WebSocket frame protocol primitives (synchronous, used by the single-port

def compute_ws_accept_key(sec_websocket_key: str) -> str:
    return base64.b64encode(
        hashlib.sha1((sec_websocket_key + _WS_MAGIC).encode("ascii")).digest()
    ).decode("ascii")


def read_ws_frame(recv_exact):
    """Read one WS frame using `recv_exact(n) -> bytes` (must return exactly
    n bytes; raises/returns short on EOF, caller's problem). Returns
    (opcode, unmasked_payload). Supports both masked (client->server) and
    unmasked (server->server, used only by tests) frames."""
    hdr = recv_exact(2)
    opcode  = hdr[0] & 0x0F
    is_mask = bool(hdr[1] & 0x80)
    plen    = hdr[1] & 0x7F
    if plen == 126:
        plen = struct.unpack(">H", recv_exact(2))[0]
    elif plen == 127:
        plen = struct.unpack(">Q", recv_exact(8))[0]
    mask_key = recv_exact(4) if is_mask else b""
    payload  = recv_exact(plen) if plen else b""
    if is_mask and mask_key and payload:
        pa = bytearray(payload)
        for i in range(len(pa)):
            pa[i] ^= mask_key[i % 4]
        payload = bytes(pa)
    return opcode, payload


def _build_ws_frame(opcode: int, payload: bytes) -> bytes:
    plen = len(payload)
    if plen <= 125:
        hdr = bytes([0x80 | opcode, plen])
    elif plen <= 0xFFFF:
        hdr = bytes([0x80 | opcode, 126]) + struct.pack(">H", plen)
    else:
        hdr = bytes([0x80 | opcode, 127]) + struct.pack(">Q", plen)
    return hdr + payload


def build_ws_text_frame(text: str) -> bytes:
    return _build_ws_frame(0x1, text.encode("utf-8"))


def build_ws_close_frame() -> bytes:
    return _build_ws_frame(0x8, b"")


def parse_stream_frame_payload(payload: bytes) -> tuple[int, int, bytes]:
    """8-byte header (frame_index uint32LE, phone capture timestamp ms
    mod 2**32, uint32LE) + raw JPEG bytes — same shape as the dormant
    mobile app's useWebSocketStream.ts sendFrame() header."""
    frame_index, phone_ts_ms = struct.unpack("<II", payload[:8])
    return frame_index, phone_ts_ms, payload[8:]
```

`struct` is already imported at the top of `pendulastic_phone_server.py` (used by the existing `_ws_client`) — no new import needed for that. `base64`/`hashlib` are likewise already imported.

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v -k "ws_frame or accept_key or stream_frame_payload"`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add pendulastic_phone_server.py tests/test_phone_server.py
git commit -m "feat: add synchronous WebSocket frame protocol primitives for the stream server"
```

---

### Task 5: `_STREAM_PAGE` phone-facing content

**Files:**
- Modify: `pendulastic_phone_server.py` (new page constant, separate from `_TRACKING_PAGE`)
- Test: `tests/test_phone_server.py` (extend)

**Interfaces:**
- Produces: `pendulastic_phone_server._STREAM_PAGE: bytes` (UTF-8 encoded HTML).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phone_server.py`:

```python
def test_stream_page_is_well_formed_utf8_html():
    text = pps._STREAM_PAGE.decode("utf-8")
    assert text.strip().startswith("<!DOCTYPE html>")


def test_stream_page_requests_capped_resolution_and_quality():
    text = pps._STREAM_PAGE.decode("utf-8")
    assert "1280" in text and "720" in text
    assert "0.7" in text   # JPEG quality passed to canvas.toBlob


def test_stream_page_uses_wake_lock_and_reconnects():
    text = pps._STREAM_PAGE.decode("utf-8")
    assert "wakeLock" in text
    assert "visibilitychange" in text
    assert "new WebSocket" in text


def test_stream_page_has_no_mediapipe_dependency():
    # This page must stay a minimal camera-only page (design decision) —
    # it must not grow the _TRACKING_PAGE's MediaPipe/pose dependency.
    text = pps._STREAM_PAGE.decode("utf-8")
    assert "mediapipe" not in text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v -k stream_page`
Expected: FAIL with `AttributeError: module 'pendulastic_phone_server' has no attribute '_STREAM_PAGE'`

- [ ] **Step 3: Implement `_STREAM_PAGE`**

Add to `pendulastic_phone_server.py`, after the existing `_TRACKING_PAGE`/`_build_page` section:

```python
# ─── minimal phone-camera streaming page (no MediaPipe — recording only) ──────

_STREAM_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no,viewport-fit=cover">
<title>Pendulastic — Phone Camera</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;overflow:hidden;background:#000;
  font-family:-apple-system,BlinkMacSystemFont,sans-serif;color:#e2e8f0}
#video{width:100%;height:100%;object-fit:contain;display:block}
#status{position:absolute;top:12px;left:50%;transform:translateX(-50%);
  background:rgba(0,0,0,.72);padding:6px 16px;border-radius:20px;font-size:14px;
  white-space:nowrap;z-index:20}
#rec{position:absolute;top:12px;right:12px;display:none;align-items:center;gap:6px;
  background:rgba(0,0,0,.65);padding:6px 12px;border-radius:20px;z-index:20;
  font-variant-numeric:tabular-nums;font-size:13px}
#dot{width:10px;height:10px;border-radius:50%;background:#ef4444;
  animation:blink 1s step-start infinite}
@keyframes blink{50%{opacity:0.15}}
#error{position:absolute;inset:0;display:none;align-items:center;justify-content:center;
  background:rgba(0,0,0,.9);color:#fca5a5;padding:24px;text-align:center;font-size:14px;
  z-index:30}
#warn{position:absolute;bottom:12px;left:50%;transform:translateX(-50%);
  background:rgba(0,0,0,.72);padding:6px 16px;border-radius:12px;font-size:12px;
  color:#fbbf24;z-index:20;display:none}
</style>
</head>
<body>
<video id="video" autoplay playsinline muted></video>
<div id="status">Starting camera...</div>
<div id="rec"><span id="dot"></span><span id="elapsed">0:00</span></div>
<div id="warn">Keep this screen on and Safari in the foreground while recording.</div>
<div id="error"></div>
<canvas id="canvas" style="display:none"></canvas>
<script>
const RES_W = 1280, RES_H = 720, JPEG_QUALITY = 0.7;
const statusEl = document.getElementById('status');
const errorEl  = document.getElementById('error');
const recEl    = document.getElementById('rec');
const elapsedEl= document.getElementById('elapsed');
const warnEl   = document.getElementById('warn');
const video    = document.getElementById('video');
const canvas   = document.getElementById('canvas');
const ctx      = canvas.getContext('2d');

let ws = null, closedByUser = false, backoff = 500;
let frameIndex = 0, startedAt = null, elapsedTimer = null;
let wakeLock = null;

function showError(msg) {
  errorEl.textContent = msg;
  errorEl.style.display = 'flex';
}

async function acquireWakeLock() {
  try {
    wakeLock = await navigator.wakeLock.request('screen');
  } catch (e) { /* not fatal — the on-screen warning covers it */ }
}
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState === 'visible') await acquireWakeLock();
});

function startElapsedTimer() {
  startedAt = Date.now();
  recEl.style.display = 'flex';
  elapsedTimer = setInterval(() => {
    const s = Math.floor((Date.now() - startedAt) / 1000);
    elapsedEl.textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }, 1000);
}

function connectWs() {
  if (closedByUser) return;
  statusEl.textContent = 'Connecting...';
  ws = new WebSocket('wss://' + location.host + '/ws');
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    statusEl.textContent = 'Live';
    backoff = 500;
    warnEl.style.display = 'block';
    if (!startedAt) startElapsedTimer();
  };
  ws.onmessage = (event) => {
    if (typeof event.data !== 'string') return;
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'sync_req') {
        ws.send(JSON.stringify({type: 'sync_resp', t0: msg.t0, t1: Date.now()}));
      }
    } catch (e) { /* ignore unparseable control messages */ }
  };
  ws.onerror = () => { statusEl.textContent = 'Connection error'; };
  ws.onclose = () => {
    if (closedByUser) return;
    statusEl.textContent = 'Reconnecting...';
    setTimeout(connectWs, backoff);
    backoff = Math.min(backoff * 2, 8000);
  };
}

function sendFrame(blob) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  blob.arrayBuffer().then(buf => {
    const header = new ArrayBuffer(8);
    const view = new DataView(header);
    view.setUint32(0, frameIndex, true);
    view.setUint32(4, Date.now() >>> 0, true);
    frameIndex += 1;
    const combined = new Uint8Array(8 + buf.byteLength);
    combined.set(new Uint8Array(header), 0);
    combined.set(new Uint8Array(buf), 8);
    ws.send(combined.buffer);
  });
}

function captureLoop() {
  if (video.videoWidth) {
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(blob => { if (blob) sendFrame(blob); }, 'image/jpeg', JPEG_QUALITY);
  }
  if (video.requestVideoFrameCallback) {
    video.requestVideoFrameCallback(captureLoop);
  } else {
    requestAnimationFrame(captureLoop);
  }
}

async function init() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {facingMode: 'environment', width: {ideal: RES_W, max: RES_W},
              height: {ideal: RES_H, max: RES_H}},
      audio: false
    });
    video.srcObject = stream;
    await new Promise(res => { video.onloadedmetadata = res; });
    await video.play();
    await acquireWakeLock();
    connectWs();
    if (video.requestVideoFrameCallback) {
      video.requestVideoFrameCallback(captureLoop);
    } else {
      requestAnimationFrame(captureLoop);
    }
  } catch (e) {
    showError('Could not start the camera: ' + e.message);
  }
}

init();
</script>
</body>
</html>
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v -k stream_page`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add pendulastic_phone_server.py tests/test_phone_server.py
git commit -m "feat: add minimal phone-camera streaming page (getUserMedia + wake lock + reconnect)"
```

---

### Task 6: Single-port HTTPS+WS stream server (`start_stream_server`/`stop_stream_server`)

This is where Tasks 2-5 are wired together into a running server.

**Files:**
- Modify: `pendulastic_phone_server.py` (new section, near `start()`/`stop()`)
- Test: `tests/test_phone_server.py` (extend)

**Interfaces:**
- Produces:
  - `pendulastic_phone_server.stream_frame_queue: "queue.Queue[dict]"` — each item: `{"frame": np.ndarray, "frame_index": int, "phone_ts_ms": int, "desktop_ts_ms": int}`. `desktop_ts_ms` is `phone_ts_ms + offset` if a clock offset is available yet, else `None` (Task 7 must handle the `None` case).
  - `pendulastic_phone_server.start_stream_server(cert_dir: str | None = None, port: int | None = None) -> tuple[str, int]` — returns `(local_ip, port)`. Idempotent (calling while already running is a no-op returning the same values). `port=None` uses `PORT_STREAM_HTTPS`; tests pass `port=0` for an OS-assigned ephemeral port.
  - `pendulastic_phone_server.stop_stream_server() -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phone_server.py`:

```python
import json
import ssl as _ssl
import socket as _socket
import struct as _struct
import time as _time

import cv2
import numpy as np


def _connect_tls(port):
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    raw = _socket.create_connection(("127.0.0.1", port), timeout=5.0)
    return ctx.wrap_socket(raw, server_hostname="127.0.0.1")


def test_start_stream_server_serves_the_page_over_https(tmp_path):
    ip, port = pps.start_stream_server(cert_dir=str(tmp_path), port=0)
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
        assert b"getUserMedia" in data
    finally:
        pps.stop_stream_server()


def test_stream_server_websocket_frame_lands_in_queue(tmp_path):
    ip, port = pps.start_stream_server(cert_dir=str(tmp_path), port=0)
    try:
        sock = _connect_tls(port)
        req = (
            "GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(req.encode())
        resp = sock.recv(4096)
        assert b"101" in resp.split(b"\r\n", 1)[0]

        # Build and send one binary frame: 8-byte header + a tiny real JPEG.
        img = np.zeros((4, 4, 3), dtype="uint8")
        ok, buf = cv2.imencode(".jpg", img)
        assert ok
        header = _struct.pack("<II", 7, 123456)
        payload = header + buf.tobytes()
        mask_key = b"\x11\x22\x33\x44"
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        plen = len(masked)
        if plen <= 125:
            frame_hdr = bytes([0x82, 0x80 | plen])
        else:
            frame_hdr = bytes([0x82, 0x80 | 126]) + _struct.pack(">H", plen)
        sock.sendall(frame_hdr + mask_key + masked)

        item = pps.stream_frame_queue.get(timeout=5.0)
        assert item["frame_index"] == 7
        assert item["phone_ts_ms"] == 123456
        assert item["frame"].shape == (4, 4, 3)
    finally:
        pps.stop_stream_server()


def test_start_stream_server_is_idempotent(tmp_path):
    ip1, port1 = pps.start_stream_server(cert_dir=str(tmp_path), port=0)
    ip2, port2 = pps.start_stream_server(cert_dir=str(tmp_path), port=0)
    try:
        assert (ip1, port1) == (ip2, port2)
    finally:
        pps.stop_stream_server()


def _ws_handshake(sock):
    req = (
        "GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode())
    resp = sock.recv(4096)
    assert b"101" in resp.split(b"\r\n", 1)[0]


def _send_binary_frame(sock, frame_index, phone_ts_ms, jpeg_bytes):
    header = _struct.pack("<II", frame_index, phone_ts_ms)
    payload = header + jpeg_bytes
    mask_key = b"\x11\x22\x33\x44"
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    plen = len(masked)
    if plen <= 125:
        frame_hdr = bytes([0x82, 0x80 | plen])
    else:
        frame_hdr = bytes([0x82, 0x80 | 126]) + _struct.pack(">H", plen)
    sock.sendall(frame_hdr + mask_key + masked)


def _tiny_jpeg_bytes():
    img = np.zeros((4, 4, 3), dtype="uint8")
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def test_stream_server_drops_frame_with_implausible_timestamp_jump(tmp_path):
    ip, port = pps.start_stream_server(cert_dir=str(tmp_path), port=0)
    try:
        sock = _connect_tls(port)
        _ws_handshake(sock)
        jpeg = _tiny_jpeg_bytes()
        _send_binary_frame(sock, 1, 1_000_000, jpeg)
        item1 = pps.stream_frame_queue.get(timeout=5.0)
        assert item1["frame_index"] == 1
        # Wildly out-of-range jump vs. the previous frame's timestamp —
        # simulates a phone clock re-sync glitch or reordering; must be
        # dropped rather than queued.
        _send_binary_frame(sock, 2, 1_000_000 - 10_000_000, jpeg)
        _send_binary_frame(sock, 3, 1_000_050, jpeg)   # plausible next frame
        item2 = pps.stream_frame_queue.get(timeout=5.0)
        assert item2["frame_index"] == 3   # frame 2 was dropped, not queued
    finally:
        pps.stop_stream_server()


def test_stream_server_new_connection_replaces_old_active_one(tmp_path):
    ip, port = pps.start_stream_server(cert_dir=str(tmp_path), port=0)
    try:
        sock_a = _connect_tls(port)
        _ws_handshake(sock_a)
        jpeg = _tiny_jpeg_bytes()
        _send_binary_frame(sock_a, 1, 1_000_000, jpeg)
        assert pps.stream_frame_queue.get(timeout=5.0)["frame_index"] == 1

        sock_b = _connect_tls(port)
        _ws_handshake(sock_b)
        _send_binary_frame(sock_b, 100, 2_000_000, jpeg)
        assert pps.stream_frame_queue.get(timeout=5.0)["frame_index"] == 100

        # sock_a is now stale — it must stop contributing frames to the
        # queue even though its TCP connection may still be technically open.
        while not pps.stream_frame_queue.empty():
            pps.stream_frame_queue.get_nowait()
        _send_binary_frame(sock_a, 2, 1_000_100, jpeg)
        import time as _t
        _t.sleep(0.5)
        assert pps.stream_frame_queue.empty()
    finally:
        pps.stop_stream_server()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v -k stream_server`
Expected: FAIL with `AttributeError: module 'pendulastic_phone_server' has no attribute 'start_stream_server'`

- [ ] **Step 3: Implement the stream server**

Add to `pendulastic_phone_server.py`, in the constants section near the top:

```python
PORT_STREAM_HTTPS = 8880
STREAM_RESOLUTION = (1280, 720)
STREAM_JPEG_QUALITY = 0.7
CLOCK_SYNC_INITIAL_ROUNDS = 5
CLOCK_SYNC_RESYNC_INTERVAL_S = 30.0
MAX_FRAME_TS_JUMP_MS = 2000   # implausible jump vs. previous frame -> drop

stream_frame_queue: "queue.Queue[dict]" = queue.Queue(maxsize=4)

_stream_server = None
_stream_thread = None
_stream_running = False
_stream_local_ip = "127.0.0.1"
_stream_port = PORT_STREAM_HTTPS
_stream_active_generation = 0   # bumped by each new WS connection; lets an
                                 # older, still-technically-open connection
                                 # notice it's been superseded and stop
                                 # contributing frames (spec: only one phone
                                 # connection is active at a time).
```

Add the handler and server-lifecycle functions (after the WS frame primitives section):

```python
# ─── single-port HTTPS + WS stream server ──────────────────────────────────────

class _StreamHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._handle_ws_upgrade()
            return
        page = _STREAM_PAGE.encode("utf-8")
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
        self._serve_stream_connection()

    def _serve_stream_connection(self) -> None:
        global _stream_active_generation
        _stream_active_generation += 1
        my_generation = _stream_active_generation

        estimator = ClockSyncEstimator()
        self.connection.settimeout(1.0)
        last_sync = 0.0
        sync_rounds_sent = 0
        last_phone_ts_ms = None

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
                if my_generation != _stream_active_generation:
                    # A newer phone connection has taken over — only one
                    # active connection at a time (spec section 7).
                    break

                now = time.time() * 1000.0
                need_initial = sync_rounds_sent < CLOCK_SYNC_INITIAL_ROUNDS
                need_resync  = (now - last_sync) > (CLOCK_SYNC_RESYNC_INTERVAL_S * 1000.0)
                if need_initial or need_resync:
                    t0 = time.time() * 1000.0
                    self.wfile.write(build_ws_text_frame(json.dumps({"type": "sync_req", "t0": t0})))
                    last_sync = now
                    sync_rounds_sent += 1

                try:
                    opcode, payload = read_ws_frame(recv_exact)
                except _socket.timeout:
                    continue

                if my_generation != _stream_active_generation:
                    # Went stale while blocked waiting for this frame to
                    # arrive — discard it rather than queueing/acking it.
                    break

                if opcode == 0x8:
                    break
                elif opcode == 0x9:
                    self.wfile.write(_build_ws_frame(0xA, payload[:125]))
                elif opcode == 0x1:
                    try:
                        msg = json.loads(payload.decode("utf-8"))
                    except Exception:
                        continue
                    if msg.get("type") == "sync_resp":
                        estimator.add_sample(t0=msg["t0"], t1=msg["t1"], t2=time.time() * 1000.0)
                elif opcode == 0x2:
                    frame_index, phone_ts_ms, jpeg_bytes = parse_stream_frame_payload(payload)

                    # Reject an implausible jump vs. the previous frame's
                    # timestamp (phone clock re-sync glitch, reordering)
                    # rather than trusting it (spec section 5).
                    if last_phone_ts_ms is not None \
                            and abs(phone_ts_ms - last_phone_ts_ms) > MAX_FRAME_TS_JUMP_MS:
                        continue
                    last_phone_ts_ms = phone_ts_ms

                    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    desktop_ts_ms = (
                        phone_ts_ms + estimator.offset_ms
                        if estimator.offset_ms is not None else None
                    )
                    item = {
                        "frame": frame, "frame_index": frame_index,
                        "phone_ts_ms": phone_ts_ms, "desktop_ts_ms": desktop_ts_ms,
                    }
                    if stream_frame_queue.full():
                        try:
                            stream_frame_queue.get_nowait()
                        except Exception:
                            pass
                    try:
                        stream_frame_queue.put_nowait(item)
                    except Exception:
                        pass
        except Exception:
            pass

    def log_message(self, *_):
        pass


class _ThreadingHTTPSServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_stream_server(cert_dir: str | None = None, port: int | None = None) -> tuple[str, int]:
    """Start the single-port HTTPS+WS phone-camera stream server. Idempotent."""
    global _stream_server, _stream_thread, _stream_running, _stream_local_ip, _stream_port

    if _stream_running:
        return _stream_local_ip, _stream_port

    if cert_dir is None:
        cert_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".certs")
    bind_port = port if port is not None else PORT_STREAM_HTTPS

    _stream_local_ip = get_local_ip()
    cert_path, key_path = get_or_create_self_signed_cert(cert_dir, _stream_local_ip)

    server = _ThreadingHTTPSServer(("0.0.0.0", bind_port), _StreamHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    _stream_server = server
    _stream_port   = server.server_address[1]
    _stream_thread = threading.Thread(target=server.serve_forever, daemon=True, name="pps-stream")
    _stream_thread.start()
    _stream_running = True
    return _stream_local_ip, _stream_port


def stop_stream_server() -> None:
    global _stream_server, _stream_running
    _stream_running = False
    try:
        if _stream_server:
            _stream_server.shutdown()
            _stream_server.server_close()
    except Exception:
        pass
    _stream_server = None
    while not stream_frame_queue.empty():
        try:
            stream_frame_queue.get_nowait()
        except Exception:
            break
```

Add the new stdlib imports at the top of `pendulastic_phone_server.py` (`http.server` and `ssl` are already imported for the legacy server — verify, and add only what's missing; `json`, `time`, `threading`, `struct`, `cv2`, `np` are already imported).

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v -k stream_server`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full test file to check for regressions**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_phone_server.py -v`
Expected: PASS (all tests from Tasks 2-6)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_phone_server.py tests/test_phone_server.py
git commit -m "feat: add single-port HTTPS+WS phone-camera stream server"
```

---

### Task 7: `PhoneCameraSession` in `camera_utils.py`

**Files:**
- Modify: `camera_utils.py`
- Test: `tests/test_camera_utils.py` (extend)

**Interfaces:**
- Consumes: `pendulastic_phone_server.start_stream_server`, `.stop_stream_server`, `.stream_frame_queue`, `.PHONE_TARGET_FPS`, `.PHONE_DEGRADED_FPS`, `.PHONE_DEGRADED_HYSTERESIS_S`, `.PHONE_LOST_TIMEOUT_S` (Task 6 + this task's new constants below).
- Produces: `camera_utils.PhoneCameraSession(on_frame, on_status=None, server_module=None)` — `server_module` defaults to the real `pendulastic_phone_server` module, injectable in tests. Methods: `open(cam: dict) -> bool`, `close() -> None`, `attach_writer(writer) -> None`, `detach_writer()`, `attach_timestamp_sink(callback) -> None`, `detach_timestamp_sink()`, properties `.active`, `.frame_size`.

- [ ] **Step 1: Add the new status-related constants to `pendulastic_phone_server.py`**

In the constants section from Task 6, add:

```python
PHONE_TARGET_FPS            = 24.0
PHONE_DEGRADED_FPS          = 12.0
PHONE_DEGRADED_HYSTERESIS_S = 2.5
PHONE_LOST_TIMEOUT_S        = 5.0
PHONE_WAITING_HINT_S        = 15.0
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_camera_utils.py`:

```python
class _FakeServerModule:
    """Stands in for pendulastic_phone_server in PhoneCameraSession tests —
    a real queue.Queue, but start/stop are just call-tracking, no sockets."""
    def __init__(self):
        import queue as _q
        self.stream_frame_queue = _q.Queue(maxsize=4)
        self.started = []
        self.stopped = 0
        self.PHONE_TARGET_FPS = 24.0
        self.PHONE_DEGRADED_FPS = 12.0
        self.PHONE_DEGRADED_HYSTERESIS_S = 0.2   # short, for fast tests
        self.PHONE_LOST_TIMEOUT_S = 0.3          # short, for fast tests
        self.PHONE_WAITING_HINT_S = 0.15         # short, for fast tests

    def start_stream_server(self, cert_dir=None, port=None):
        self.started.append((cert_dir, port))
        return "192.168.1.50", 8880

    def stop_stream_server(self):
        self.stopped += 1


def _push_frame(server, frame_index=0, desktop_ts_ms=1000):
    import numpy as np
    server.stream_frame_queue.put_nowait({
        "frame": np.zeros((4, 4, 3), dtype="uint8"),
        "frame_index": frame_index, "phone_ts_ms": desktop_ts_ms, "desktop_ts_ms": desktop_ts_ms,
    })


def test_phone_camera_session_open_starts_server_and_sets_active():
    server = _FakeServerModule()
    sess = camera_utils.PhoneCameraSession(on_frame=lambda f: None, server_module=server)
    ok = sess.open({"kind": "phone", "label": "Phone"})
    assert ok is True
    assert sess.active == {"kind": "phone", "label": "Phone"}
    assert len(server.started) == 1
    sess.close()


def test_phone_camera_session_reports_waiting_then_live_on_first_frame():
    server = _FakeServerModule()
    statuses = []
    sess = camera_utils.PhoneCameraSession(
        on_frame=lambda f: None, on_status=statuses.append, server_module=server)
    sess.open({"kind": "phone", "label": "Phone"})
    assert "waiting for phone" in statuses
    _push_frame(server)
    import time
    for _ in range(50):
        if "live" in statuses:
            break
        time.sleep(0.02)
    assert "live" in statuses
    sess.close()


def test_phone_camera_session_frame_size_from_decoded_frame():
    server = _FakeServerModule()
    sess = camera_utils.PhoneCameraSession(on_frame=lambda f: None, server_module=server)
    sess.open({"kind": "phone", "label": "Phone"})
    _push_frame(server)
    import time
    for _ in range(50):
        if sess.frame_size is not None:
            break
        time.sleep(0.02)
    assert sess.frame_size == (4, 4)
    sess.close()


def test_phone_camera_session_attach_writer_writes_frames():
    server = _FakeServerModule()
    got = []
    sess = camera_utils.PhoneCameraSession(on_frame=got.append, server_module=server)
    sess.open({"kind": "phone", "label": "Phone"})

    class _FakeWriter:
        def __init__(self): self.frames = []
        def write(self, f): self.frames.append(f)

    writer = _FakeWriter()
    sess.attach_writer(writer)
    _push_frame(server)
    import time
    for _ in range(50):
        if got:
            break
        time.sleep(0.02)
    assert len(writer.frames) == 1
    assert len(got) == 1
    sess.close()


def test_phone_camera_session_close_stops_server_and_clears_active():
    server = _FakeServerModule()
    sess = camera_utils.PhoneCameraSession(on_frame=lambda f: None, server_module=server)
    sess.open({"kind": "phone", "label": "Phone"})
    sess.close()
    assert server.stopped == 1
    assert sess.active is None


def test_phone_camera_session_degraded_status_after_sustained_low_fps():
    server = _FakeServerModule()   # PHONE_DEGRADED_HYSTERESIS_S = 0.2 in the fake
    statuses = []
    sess = camera_utils.PhoneCameraSession(
        on_frame=lambda f: None, on_status=statuses.append, server_module=server)
    sess.open({"kind": "phone", "label": "Phone"})
    _push_frame(server)
    import time
    time.sleep(0.4)   # longer than the fake's hysteresis window, no more frames pushed
    for _ in range(50):
        if any(s.startswith("degraded:") for s in statuses):
            break
        time.sleep(0.02)
    assert any(s.startswith("degraded:") for s in statuses)
    sess.close()


def test_phone_camera_session_hints_after_prolonged_no_frames(monkeypatch):
    server = _FakeServerModule()   # PHONE_WAITING_HINT_S = 0.15 in the fake
    statuses = []
    sess = camera_utils.PhoneCameraSession(
        on_frame=lambda f: None, on_status=statuses.append, server_module=server)
    sess.open({"kind": "phone", "label": "Phone"})   # no frames ever pushed
    import time
    time.sleep(0.4)
    assert any("waiting" in s.lower() and s != "waiting for phone" for s in statuses), statuses
    sess.close()


def test_phone_camera_session_timestamp_sink_receives_desktop_ts():
    server = _FakeServerModule()
    sess = camera_utils.PhoneCameraSession(on_frame=lambda f: None, server_module=server)
    sess.open({"kind": "phone", "label": "Phone"})
    got = []
    sess.attach_timestamp_sink(lambda idx, ts: got.append((idx, ts)))
    _push_frame(server, frame_index=5, desktop_ts_ms=9999)
    import time
    for _ in range(50):
        if got:
            break
        time.sleep(0.02)
    assert got == [(5, 9999)]
    sess.close()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_camera_utils.py -v -k phone_camera_session`
Expected: FAIL with `AttributeError: module 'camera_utils' has no attribute 'PhoneCameraSession'`

- [ ] **Step 4: Implement `PhoneCameraSession`**

Append to `camera_utils.py`:

```python
class PhoneCameraSession:
    """Mirrors CameraSession's public surface (open/close/attach_writer/
    detach_writer/.active/.frame_size/on_frame/on_status) but is backed by
    pendulastic_phone_server's single-port HTTPS+WS stream server instead of
    cv2.VideoCapture. Does not implement rescan() — dropdown population for
    the static phone entry is handled once, at the App level, not per
    session type (see plan Global Constraints)."""

    def __init__(self, on_frame, on_status=None, server_module=None):
        if server_module is None:
            import pendulastic_phone_server as server_module
        self._server = server_module
        self._on_frame = on_frame
        self._on_status = on_status or (lambda msg: None)
        self.active = None
        self._frame_size = None
        self._writer = None
        self._ts_sink = None
        self._lock = threading.Lock()
        self._thread = None
        self._stop_evt = None

    @property
    def frame_size(self):
        return self._frame_size

    def open(self, cam: dict) -> bool:
        self.close()
        self._server.start_stream_server()
        self.active = dict(cam)
        self._safe_status("waiting for phone")
        stop_evt = threading.Event()
        self._stop_evt = stop_evt
        thread = threading.Thread(target=self._consume_loop, args=(stop_evt,), daemon=True)
        self._thread = thread
        thread.start()
        return True

    def close(self) -> None:
        if self._stop_evt is not None:
            self._stop_evt.set()
        thread = self._thread
        self._thread = None
        self._stop_evt = None
        with self._lock:
            self._writer = None
            self._ts_sink = None
        self.active = None
        self._frame_size = None
        if thread is not None:
            thread.join(timeout=2.0)
        self._server.stop_stream_server()

    def attach_writer(self, writer) -> None:
        with self._lock:
            self._writer = writer

    def detach_writer(self):
        with self._lock:
            w, self._writer = self._writer, None
        return w

    def attach_timestamp_sink(self, callback) -> None:
        with self._lock:
            self._ts_sink = callback

    def detach_timestamp_sink(self) -> None:
        with self._lock:
            self._ts_sink = None

    def _safe_status(self, msg: str) -> None:
        try:
            self._on_status(msg)
        except Exception:
            pass

    def _consume_loop(self, stop_evt) -> None:
        q = self._server.stream_frame_queue
        degraded_fps = self._server.PHONE_DEGRADED_FPS
        hysteresis_s = self._server.PHONE_DEGRADED_HYSTERESIS_S
        lost_timeout = self._server.PHONE_LOST_TIMEOUT_S
        waiting_hint_s = self._server.PHONE_WAITING_HINT_S

        went_live = False
        hinted_waiting = False
        opened_at = time.time()
        last_frame_at = None
        low_fps_since = None
        currently_degraded = False
        recent_ts = []   # rolling list of frame arrival times (seconds) for fps estimate

        while not stop_evt.is_set():
            try:
                item = q.get(timeout=0.5)
            except Exception:
                item = None

            now = time.time()
            if not went_live and not hinted_waiting and (now - opened_at) >= waiting_hint_s:
                hinted_waiting = True
                self._safe_status(
                    "Still waiting for phone - check it's on the same network "
                    "and the certificate warning was accepted.")
            if item is not None:
                last_frame_at = now
                recent_ts.append(now)
                recent_ts = [t for t in recent_ts if now - t <= 1.0]
                fps = float(len(recent_ts))

                if not went_live:
                    went_live = True
                    self._safe_status("live")

                if self._frame_size is None:
                    h, w = item["frame"].shape[:2]
                    self._frame_size = (w, h)

                if fps < degraded_fps:
                    if low_fps_since is None:
                        low_fps_since = now
                    elif not currently_degraded and (now - low_fps_since) >= hysteresis_s:
                        currently_degraded = True
                        self._safe_status(f"degraded: {int(fps)}fps")
                else:
                    low_fps_since = None
                    currently_degraded = False

                with self._lock:
                    w = self._writer
                    sink = self._ts_sink
                if w is not None:
                    try:
                        w.write(item["frame"])
                    except Exception:
                        pass
                if sink is not None and item["desktop_ts_ms"] is not None:
                    try:
                        sink(item["frame_index"], item["desktop_ts_ms"])
                    except Exception:
                        pass
                try:
                    self._on_frame(item["frame"])
                except Exception:
                    pass
            else:
                if went_live and last_frame_at is not None and (now - last_frame_at) >= lost_timeout:
                    self._safe_status("lost")
                    went_live = False
                    hinted_waiting = False   # re-engage the waiting-hint for the new gap
                    opened_at = now
```

`import threading, time` are already imported at the top of `camera_utils.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_camera_utils.py -v -k phone_camera_session`
Expected: PASS (8 tests)

- [ ] **Step 6: Run the full camera_utils test file to check for regressions**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_camera_utils.py -v`
Expected: PASS (all `CameraSession` tests plus the new `PhoneCameraSession` tests)

- [ ] **Step 7: Commit**

```bash
git add camera_utils.py tests/test_camera_utils.py
git commit -m "feat: add PhoneCameraSession, mirroring CameraSession's interface over the stream server"
```

---

### Task 8: `AcquisitionPanel` — static phone dropdown entry + QR/URL pairing panel

**Files:**
- Modify: `pendulastic_app.py` (`AcquisitionPanel` class)
- Test: `tests/test_acquisition_panel.py` (extend)

**Interfaces:**
- Produces: `pendulastic_app.PHONE_CAMERA_LABEL = "📱 Phone Camera"`, `pendulastic_app.PHONE_CAMERA_ENTRY = {"kind": "phone", "label": PHONE_CAMERA_LABEL}`. `AcquisitionPanel.show_phone_pairing_panel(url: str) -> None`, `AcquisitionPanel.hide_phone_pairing_panel() -> None`.
- Consumes: `qrcode`, `PIL.ImageTk` (already an existing dependency via `pendulastic_viewer.py`'s use of `ImageTk`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_acquisition_panel.py`, matching its existing convention: a module-level `_root()` helper (`tk.Tk()` + `withdraw()`) and a `_Ctrl` fake controller class — both already defined near the top of the file, reused here rather than redefined:

```python
def test_phone_camera_entry_constant_has_expected_shape():
    from pendulastic_app import PHONE_CAMERA_ENTRY, PHONE_CAMERA_LABEL
    assert PHONE_CAMERA_ENTRY == {"kind": "phone", "label": PHONE_CAMERA_LABEL}


def test_show_phone_pairing_panel_displays_url_text():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pack()
        p.show_phone_pairing_panel("https://192.168.1.50:8880/")
        r.update()
        assert p._phone_pairing_frame.winfo_ismapped()
        assert "192.168.1.50" in p._phone_pairing_url_var.get()
        p.hide_phone_pairing_panel()
        r.update()
        assert not p._phone_pairing_frame.winfo_ismapped()
    finally:
        r.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_acquisition_panel.py -v -k phone`
Expected: FAIL — `PHONE_CAMERA_ENTRY` / `show_phone_pairing_panel` don't exist yet.

- [ ] **Step 3: Add the module-level constants**

In `pendulastic_app.py`, near the other module-level constants (`BASE_DIR`, etc.):

```python
PHONE_CAMERA_LABEL = "📱 Phone Camera"
PHONE_CAMERA_ENTRY = {"kind": "phone", "label": PHONE_CAMERA_LABEL}
```

- [ ] **Step 4: Add the pairing panel widgets and methods to `AcquisitionPanel`**

In `AcquisitionPanel._build_widgets`, after the existing `self._cam_frame` block (around where `self._camera_live = False` is set), add:

```python
        # Phone pairing panel — shown when the phone dropdown entry is
        # selected; hidden otherwise. Reuses pendulastic_viewer.py's
        # qrcode-based QR generation pattern.
        self._phone_pairing_frame = tk.Frame(meth_f, relief="groove", borderwidth=1)
        self._phone_pairing_url_var = tk.StringVar(value="")
        tk.Label(self._phone_pairing_frame, text="Open on your phone:",
                  font=("Segoe UI", 8, "bold")).pack(side="top", anchor="w", padx=6, pady=(4, 0))
        self._phone_qr_label = tk.Label(self._phone_pairing_frame)
        self._phone_qr_label.pack(side="top", padx=6, pady=4)
        tk.Entry(self._phone_pairing_frame, textvariable=self._phone_pairing_url_var,
                  font=("Consolas", 8), width=32, state="readonly").pack(
            side="top", padx=6, pady=(0, 4))
        tk.Label(self._phone_pairing_frame,
                  text="Your phone will warn about the connection's security\n"
                       "certificate — tap Advanced -> Proceed. This is expected.",
                  font=("Segoe UI", 7), fg="gray", justify="left").pack(
            side="top", anchor="w", padx=6, pady=(0, 4))
        self._phone_pairing_frame.pack_forget()
```

Add the two methods (in the "Public state transitions" section, near `set_camera_live`):

```python
    def show_phone_pairing_panel(self, url: str) -> None:
        self._phone_pairing_url_var.set(url)
        try:
            import qrcode
            from PIL import ImageTk
            qr = qrcode.QRCode(box_size=5, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            raw = qr.make_image(fill_color="black", back_color="white")
            pil_img = raw.get_image() if hasattr(raw, "get_image") else raw
            photo = ImageTk.PhotoImage(pil_img.convert("RGB"))
            self._phone_qr_label.config(image=photo, text="")
            self._phone_qr_label._photo = photo   # prevent GC
        except Exception as exc:
            self._phone_qr_label.config(image="", text=f"(QR unavailable: {exc})")
        self._phone_pairing_frame.pack(side="top", anchor="w", pady=(4, 0), fill="x")

    def hide_phone_pairing_panel(self) -> None:
        self._phone_pairing_frame.pack_forget()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_acquisition_panel.py -v -k phone`
Expected: PASS

- [ ] **Step 6: Run the full acquisition panel test file to check for regressions**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_acquisition_panel.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: add phone-camera dropdown entry and QR pairing panel to AcquisitionPanel"
```

---

### Task 9: `App` — swap `self._camera` between `CameraSession` and `PhoneCameraSession`

This is the integration task: dropdown population always includes the phone entry, and selecting it swaps which session type `self._camera` holds.

**Files:**
- Modify: `pendulastic_app.py` (`App` class: `__init__`, `on_rescan_cameras`, `on_camera_selected`, `on_camera_disabled`)
- Test: `tests/test_app.py` (extend)

**Interfaces:**
- Consumes: `camera_utils.PhoneCameraSession` (Task 7), `AcquisitionPanel.show_phone_pairing_panel`/`hide_phone_pairing_panel` (Task 8), `pendulastic_phone_server.get_all_local_ips` (existing), `PHONE_CAMERA_ENTRY`/`PHONE_CAMERA_LABEL` (Task 8).
- Produces: `App._switch_to_usb_camera(cam: dict) -> None`, `App._switch_to_phone_camera() -> None` (used internally, but named per this contract so Task 10/11 can reference them).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_on_rescan_cameras_always_appends_phone_entry(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        monkeypatch.setattr(_m, "enumerate_cameras", lambda: [])
        monkeypatch.setattr(app._camera, "close", lambda: None)
        app.on_rescan_cameras()
        assert list(app._acq.drop_cam["values"])[-1] == _m.PHONE_CAMERA_LABEL
    finally:
        app.destroy()


def test_selecting_phone_entry_swaps_camera_to_phone_session(monkeypatch):
    import pendulastic_app as _m
    import camera_utils
    app = _m.App()
    try:
        app._known_cameras = [_m.PHONE_CAMERA_ENTRY]
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "open",
            lambda self, cam: (setattr(self, "active", cam) or True))
        monkeypatch.setattr(
            "pendulastic_phone_server.get_all_local_ips", lambda: ["192.168.1.50"], raising=False)
        app.on_camera_selected(_m.PHONE_CAMERA_LABEL)
        assert isinstance(app._camera, camera_utils.PhoneCameraSession)
        assert app._acq._phone_pairing_frame.winfo_ismapped()
    finally:
        app.destroy()


def test_selecting_usb_entry_after_phone_swaps_back_to_camera_session(monkeypatch):
    import pendulastic_app as _m
    import camera_utils
    app = _m.App()
    try:
        fake_usb = {"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"}
        app._known_cameras = [fake_usb, _m.PHONE_CAMERA_ENTRY]
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "open",
            lambda self, cam: (setattr(self, "active", cam) or True))
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "close", lambda self: setattr(self, "active", None))
        monkeypatch.setattr(
            "pendulastic_phone_server.get_all_local_ips", lambda: ["192.168.1.50"], raising=False)
        app.on_camera_selected(_m.PHONE_CAMERA_LABEL)
        assert isinstance(app._camera, camera_utils.PhoneCameraSession)

        opened = []
        monkeypatch.setattr(
            camera_utils.CameraSession, "open", lambda self, cam: opened.append(cam) or True)
        app.on_camera_selected("Camera 0 (MSMF)")
        assert isinstance(app._camera, camera_utils.CameraSession)
        assert opened == [fake_usb]
        assert not app._acq._phone_pairing_frame.winfo_ismapped()
    finally:
        app.destroy()


def test_on_camera_disabled_closes_whichever_session_type_is_active(monkeypatch):
    import pendulastic_app as _m
    import camera_utils
    app = _m.App()
    try:
        app._known_cameras = [_m.PHONE_CAMERA_ENTRY]
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "open",
            lambda self, cam: (setattr(self, "active", cam) or True))
        closed = []
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "close", lambda self: closed.append(True))
        monkeypatch.setattr(
            "pendulastic_phone_server.get_all_local_ips", lambda: ["192.168.1.50"], raising=False)
        app.on_camera_selected(_m.PHONE_CAMERA_LABEL)
        app.on_camera_disabled()
        assert closed == [True]
        assert not app._acq._phone_pairing_frame.winfo_ismapped()
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_app.py -v -k "phone or swaps"`
Expected: FAIL (phone entry not appended, `on_camera_selected` doesn't know about `"kind": "phone"`).

- [ ] **Step 3: Add the import**

Near the existing `from camera_utils import CameraSession` guarded import in `pendulastic_app.py`, extend it:

```python
try:
    import cv2 as _cv2
    from camera_utils import CameraSession, PhoneCameraSession
    _CV2_AVAIL = True
except ImportError:
    _cv2 = None
    CameraSession = None
    PhoneCameraSession = None
    _CV2_AVAIL = False

try:
    import pendulastic_phone_server as _pps
    _PPS_AVAIL = True
except Exception:
    _pps = None
    _PPS_AVAIL = False
```

- [ ] **Step 4: Rewrite `on_rescan_cameras`, `on_camera_selected`, `on_camera_disabled`**

Replace the existing `on_rescan_cameras` (from the `camera-selection` branch) with:

```python
    def on_rescan_cameras(self) -> None:
        if self._state == "recording":
            return
        if isinstance(self._camera, PhoneCameraSession):
            # Rescan on the phone entry doesn't re-probe hardware — it
            # restarts the stream server for a fresh pairing panel.
            self._switch_to_phone_camera()
            return
        if self._camera is None:
            return
        self._camera.close()
        self._acq.set_camera_live(False)
        self._acq.status_var.set("Scanning for cameras…")
        self.update_idletasks()
        usb_cams = enumerate_cameras() if _CV2_AVAIL else []
        self._known_cameras = usb_cams + ([PHONE_CAMERA_ENTRY] if _PPS_AVAIL else [])
        self._acq.set_camera_list(self._known_cameras)
        if usb_cams:
            label = self._acq.cam_var.get()
            cam = next((c for c in usb_cams if c["label"] == label), usb_cams[0])
            self._camera.open(cam)
        else:
            self._acq.set_camera_live(False)
            self._acq.status_var.set(
                "No USB camera detected - check USB / close other apps, then Rescan, "
                "or select Phone Camera." if _PPS_AVAIL else
                "No camera detected - check USB / close other apps, then Rescan.")

    def on_camera_selected(self, label: str) -> None:
        if self._state == "recording":
            return
        cam = next((c for c in self._known_cameras if c["label"] == label), None)
        if cam is None:
            return
        if cam.get("kind") == "phone":
            self._switch_to_phone_camera()
        else:
            self._switch_to_usb_camera(cam)

    def _switch_to_usb_camera(self, cam: dict) -> None:
        if self._camera is not None and self._camera.active is not None \
                and self._camera.active.get("label") == cam["label"] \
                and isinstance(self._camera, CameraSession):
            return   # already using this camera
        if not isinstance(self._camera, CameraSession):
            if self._camera is not None:
                self._camera.close()
            self._acq.hide_phone_pairing_panel()
            self._camera = CameraSession(
                on_frame=self._on_camera_frame, on_status=self._on_camera_status)
        self._camera.open(cam)

    def _switch_to_phone_camera(self) -> None:
        if self._camera is not None:
            self._camera.close()
        self._camera = PhoneCameraSession(
            on_frame=self._on_camera_frame, on_status=self._on_camera_status)
        self._camera.open(PHONE_CAMERA_ENTRY)
        ips = _pps.get_all_local_ips() if _PPS_AVAIL else ["127.0.0.1"]
        primary_ip = ips[0]
        port = getattr(_pps, "PORT_STREAM_HTTPS", 8880)
        url = f"https://{primary_ip}:{port}/"
        self._acq.show_phone_pairing_panel(url)

    def on_camera_disabled(self) -> None:
        if self._camera is not None:
            self._camera.close()
        self._acq.set_camera_live(False)
        self._acq.hide_phone_pairing_panel()
```

`enumerate_cameras` must be importable at module scope in `pendulastic_app.py` for `on_rescan_cameras` to call it directly — check the existing guarded `cv2`/`camera_utils` import block and add `enumerate_cameras` alongside `CameraSession`/`PhoneCameraSession` in the same `from camera_utils import ...` line from Step 3 above (i.e. `from camera_utils import CameraSession, PhoneCameraSession, enumerate_cameras`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_app.py -v -k "phone or swaps"`
Expected: PASS

- [ ] **Step 6: Run the full app test file to check for regressions**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_app.py -v`
Expected: PASS (all tests, including the pre-existing `test_on_rescan_cameras_populates_dropdown_and_opens_first` etc. from the `camera-selection` branch — these must keep passing since `_switch_to_usb_camera` preserves the "already using this camera -> no-op" and "open the given cam" behaviors)

- [ ] **Step 7: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: wire phone-camera dropdown entry into App's camera swap logic"
```

---

### Task 10: Recording-time phone-specific behavior — timestamp sidecar + gap flagging

**Files:**
- Modify: `pendulastic_app.py` (`App._start_rgb_recording`, `_stop_rgb_recording`, `_on_camera_status`, `_handle_camera_lost_during_recording`)
- Test: `tests/test_app.py` (extend)

**Interfaces:**
- Consumes: `PhoneCameraSession.attach_timestamp_sink`/`detach_timestamp_sink` (Task 7).
- Produces: a `<video_path>.timestamps.csv` sidecar file (columns: `frame_index,desktop_ts_ms`) written only while `self._camera` is a `PhoneCameraSession` and RGB recording is active.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_start_rgb_recording_writes_timestamp_sidecar_for_phone_camera(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import camera_utils
    app = _m.App()
    try:
        monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
        app._camera = camera_utils.PhoneCameraSession(
            on_frame=app._on_camera_frame, on_status=app._on_camera_status,
            server_module=object())   # never actually opened in this test
        app._camera.active = _m.PHONE_CAMERA_ENTRY
        app._camera._frame_size = (640, 480)

        sinks = []
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "attach_timestamp_sink",
            lambda self, cb: sinks.append(cb))
        monkeypatch.setattr(camera_utils.PhoneCameraSession, "attach_writer", lambda self, w: None)
        monkeypatch.setattr(_m._cv2, "VideoWriter",
                             lambda *a, **k: type("W", (), {"isOpened": lambda s: True,
                                                             "write": lambda s, f: None,
                                                             "release": lambda s: None})())

        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._active_sources = ["rgb"]
        app._start_rgb_recording(meta)

        assert len(sinks) == 1
        sinks[0](3, 456789)
        sinks[0](4, 456800)
        assert os.path.exists(app._rgb_ts_path)

        monkeypatch.setattr(camera_utils.PhoneCameraSession, "detach_timestamp_sink",
                             lambda self: sinks.clear())
        monkeypatch.setattr(camera_utils.PhoneCameraSession, "detach_writer", lambda self: None)
        app._stop_rgb_recording()

        with open(app._rgb_ts_path, newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0] == ["frame_index", "desktop_ts_ms"]
        assert rows[1:] == [["3", "456789"], ["4", "456800"]]
    finally:
        app.destroy()


def test_start_rgb_recording_skips_sidecar_for_usb_camera(tmp_path, monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
        # app._camera is already a CameraSession (default from __init__)
        monkeypatch.setattr(app._camera, "active", {"index": 0, "label": "Camera 0"})
        monkeypatch.setattr(app._camera, "frame_size", (640, 480))
        monkeypatch.setattr(app._camera, "attach_writer", lambda w: None)
        monkeypatch.setattr(_m._cv2, "VideoWriter",
                             lambda *a, **k: type("W", (), {"isOpened": lambda s: True,
                                                             "write": lambda s, f: None,
                                                             "release": lambda s: None})())
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._active_sources = ["rgb"]
        app._start_rgb_recording(meta)
        assert getattr(app, "_rgb_ts_path", None) is None
    finally:
        app.destroy()


def test_camera_lost_during_recording_flags_gap_for_phone_camera(monkeypatch):
    import pendulastic_app as _m
    import camera_utils
    app = _m.App()
    try:
        app._camera = camera_utils.PhoneCameraSession(
            on_frame=app._on_camera_frame, on_status=app._on_camera_status, server_module=object())
        app._camera.active = _m.PHONE_CAMERA_ENTRY
        monkeypatch.setattr(camera_utils.PhoneCameraSession, "detach_writer", lambda self: None)
        monkeypatch.setattr(camera_utils.PhoneCameraSession, "detach_timestamp_sink", lambda self: None)
        app._state = "recording"
        shown = []
        monkeypatch.setattr(_m.messagebox, "showerror", lambda title, msg: shown.append(msg))
        app._on_camera_status("lost")
        app.update()
        assert shown
        assert "phone" in shown[0].lower()
    finally:
        app.destroy()
```

Add `import csv` and `import os` at the top of `tests/test_app.py` if not already present (check first — `os` is already imported per the file header seen earlier; `csv` likely is not, add it).

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_app.py -v -k "sidecar or flags_gap"`
Expected: FAIL — `_rgb_ts_path` doesn't exist, gap message doesn't mention "phone".

- [ ] **Step 3: Implement the sidecar + gap-flagging behavior**

In `pendulastic_app.py`'s `_start_rgb_recording`, after the existing `self._camera.attach_writer(self._rgb_writer)` line, add:

```python
        self._rgb_ts_path = None
        if isinstance(self._camera, PhoneCameraSession):
            self._rgb_ts_path = self._video_path + ".timestamps.csv"
            self._rgb_ts_file = open(self._rgb_ts_path, "w", newline="", encoding="utf-8")
            self._rgb_ts_writer = csv.writer(self._rgb_ts_file)
            self._rgb_ts_writer.writerow(["frame_index", "desktop_ts_ms"])
            self._camera.attach_timestamp_sink(self._on_phone_frame_timestamp)
```

Add the callback method (near `_on_camera_frame`):

```python
    def _on_phone_frame_timestamp(self, frame_index: int, desktop_ts_ms: int) -> None:
        """Runs on PhoneCameraSession's background thread — plain file I/O
        only, never touches Tkinter."""
        try:
            self._rgb_ts_writer.writerow([frame_index, desktop_ts_ms])
        except Exception:
            pass
```

In `_stop_rgb_recording`, before `self._rgb_writer = None`, add:

```python
        if isinstance(self._camera, PhoneCameraSession):
            self._camera.detach_timestamp_sink()
        ts_file = getattr(self, "_rgb_ts_file", None)
        if ts_file is not None:
            try:
                ts_file.close()
            except Exception:
                pass
            self._rgb_ts_file = None
```

Add `import csv` at the top of `pendulastic_app.py` if not already present (check — it likely already is, given `DataManager` probably writes CSVs; if so, skip this).

In `_handle_camera_lost_during_recording`, change the writer-detach line and the error message to branch on session type:

```python
    def _handle_camera_lost_during_recording(self) -> None:
        """Mirrors master_app._on_camera_lost: finalize whatever RGB video was
        captured so far rather than silently continuing to claim a live
        recording, and tell the user immediately rather than only surfacing
        this as a mystifying error on the NEXT trial's start."""
        is_phone = isinstance(self._camera, PhoneCameraSession)
        if is_phone:
            self._camera.detach_timestamp_sink()
            ts_file = getattr(self, "_rgb_ts_file", None)
            if ts_file is not None:
                try:
                    ts_file.close()
                except Exception:
                    pass
                self._rgb_ts_file = None
        writer = self._camera.detach_writer() if self._camera is not None else None
        if writer is not None:
            try:
                writer.release()
            except Exception:
                pass
        if self._pose_estimator is not None:
            est, self._pose_estimator = self._pose_estimator, None
            try:
                est.close()
            except Exception:
                pass
        source_name = "phone" if is_phone else "camera"
        self._acq.status_var.set(
            f"{source_name.capitalize()} lost during recording - video was stopped and saved so far.")
        messagebox.showerror(
            f"{source_name.capitalize()} Lost",
            f"The {source_name} stopped returning frames during recording.\n\n"
            "The RGB video was stopped and saved up to that point.\n"
            "Click 'Rescan' after this trial to reconnect.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_app.py -v -k "sidecar or flags_gap"`
Expected: PASS

- [ ] **Step 5: Run the full app test file to check for regressions**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_app.py -v`
Expected: PASS (all tests — including the existing `test_handle_camera_lost_during_recording_finalizes_writer_and_shows_error` test, which exercises the USB path and must still see `"Camera Lost"`/`"camera stopped..."` wording unchanged)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: write per-frame timestamp sidecar and flag gaps for phone-camera recordings"
```

---

### Task 11: Full test suite regression check + final manual on-device QA

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/ -v`
Expected: PASS — every test from Tasks 1-10 plus every pre-existing test in the `camera-selection` branch (USB camera flow, IMU, post-processing, etc.) unaffected.

- [ ] **Step 2: Manual on-device QA (cannot be automated — requires real hardware)**

Per spec §9, run the app (`"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" pendulastic_app.py`), check "RGB", select "📱 Phone Camera" from the dropdown, and verify on a real phone:

- **iOS Safari**: scan the QR / open the URL, accept the cert warning, confirm the page shows "Live" and the desktop's `AcquisitionPanel` preview shows the phone's camera feed within a few seconds.
- **Android Chrome**: same check.
- Start a recording, let it run 2-3 minutes with the phone screen on (wake lock engaged) — confirm no unexpected disconnect, confirm the `.timestamps.csv` sidecar is written next to the `.avi` and has a plausible, mostly-monotonic `desktop_ts_ms` column.
- Manually lock/dim the phone screen mid-recording (simulating wake-lock failure) — confirm the desktop surfaces a "lost"/gap message rather than silently continuing, and that the partial video is saved and playable.
- Turn off phone WiFi briefly mid-recording and turn it back on — confirm the phone page reconnects (status returns to "Live") and the desktop's status recovers within a few seconds without restarting the trial.
- Leave the stream running 10+ minutes on a physically warm phone — watch for the `degraded: Nfps` status appearing in the desktop UI if thermal throttling kicks in, confirming it's visible rather than silent.

Record the outcome (pass/fail per item, and any deviations) — if any of these fail, treat it as a new bug to triage before considering this feature done, not something to silently work around.

- [ ] **Step 3: No commit for this task** — it's verification only. If manual QA surfaces a bug, fix it as a new, separate small commit with its own test where automatable.

---
