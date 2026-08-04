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
