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


def test_stream_page_is_well_formed_utf8_html():
    # _STREAM_PAGE is a str (same convention as the existing _TRACKING_PAGE),
    # encoded to bytes at the point of use — not stored as bytes itself.
    assert isinstance(pps._STREAM_PAGE, str)
    assert pps._STREAM_PAGE.strip().startswith("<!DOCTYPE html>")
    pps._STREAM_PAGE.encode("utf-8")   # must not raise


def test_stream_page_requests_capped_resolution_and_quality():
    text = pps._STREAM_PAGE
    assert "1280" in text and "720" in text
    assert "0.7" in text   # JPEG quality passed to canvas.toBlob


def test_stream_page_uses_wake_lock_and_reconnects():
    text = pps._STREAM_PAGE
    assert "wakeLock" in text
    assert "visibilitychange" in text
    assert "new WebSocket" in text


def test_stream_page_has_no_mediapipe_dependency():
    # This page must stay a minimal camera-only page (design decision) —
    # it must not grow the _TRACKING_PAGE's MediaPipe/pose dependency.
    assert "mediapipe" not in pps._STREAM_PAGE.lower()


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


def test_forward_imu_batch_preserves_real_intersample_spacing(monkeypatch):
    """Regression test for the 2026-08-17 browser-IMU instability
    investigation: previously every sample in a batch got Timestamp =
    time.time() evaluated fresh per iteration, which collapses onto the
    same millisecond in a tight loop and corrupts on_gyro()'s dt into a
    fixed 0.01s fallback for nearly every sample. Each sample's forwarded
    Timestamp must instead differ from its neighbours by its own real
    event.timeStamp delta, anchored to one receipt-time call -- not by
    however long the server's own processing loop happens to take."""
    calls = []
    monkeypatch.setattr(pps.imu_server, "_dispatch",
                        lambda path, message, ip: calls.append(json.loads(message)))
    monkeypatch.setattr(pps.time, "time", lambda: 1000.0)

    # Three samples 20ms apart per the browser's own clock.
    batch = {"batch": [
        {"ts": 100.0, "accel": {"x": 0, "y": 0, "z": 0}, "gyro": {"x": 0, "y": 0, "z": 0}},
        {"ts": 120.0, "accel": {"x": 0, "y": 0, "z": 0}, "gyro": {"x": 0, "y": 0, "z": 0}},
        {"ts": 140.0, "accel": {"x": 0, "y": 0, "z": 0}, "gyro": {"x": 0, "y": 0, "z": 0}},
    ]}
    n = pps._forward_imu_batch(batch, "10.0.0.5")

    assert n == 3
    accel_ts = [c["Timestamp"] for c in calls if "Timestamp" in c][::2]  # accel calls only
    assert accel_ts[1] - accel_ts[0] == 20
    assert accel_ts[2] - accel_ts[1] == 20
    # Anchored to the LAST sample's browser timestamp == server receipt time.
    assert accel_ts[2] == 1000000


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
        _send_binary_frame(sock, 2, 1_000_000 + 10_000_000, jpeg)
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
