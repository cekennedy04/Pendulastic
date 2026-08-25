"""Shutdown/restart behaviour of the IMU websocket server.

These use a REAL socket against the real server, because the bug they guard
was invisible to every mock: the module reported a healthy server
(`start() -> True`, `get_state()["running"] -> True`, `bind_error() -> None`)
while nothing was listening and every phone connection was refused. Only an
actual connection attempt distinguishes the two.
"""
import base64
import hashlib
import os
import socket
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

imu = pytest.importorskip("pendulastic_imu_server")

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
HOST = "127.0.0.1"


def _ws_connect(port, timeout=5.0):
    """Minimal RFC 6455 handshake. Returns the socket, or raises."""
    key = base64.b64encode(os.urandom(16)).decode()
    s = socket.create_connection((HOST, port), timeout=timeout)
    try:
        s.sendall(
            f"GET / HTTP/1.1\r\nHost: {HOST}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                raise RuntimeError("closed during handshake")
            resp += chunk
        if b"101" not in resp.split(b"\r\n")[0]:
            raise RuntimeError("handshake refused")
        expect = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()).decode()
        if expect.encode() not in resp:
            raise RuntimeError("Sec-WebSocket-Accept mismatch")
        return s
    except Exception:
        s.close()
        raise


def _can_connect(port):
    try:
        _ws_connect(port).close()
        return True
    except Exception:
        return False


@pytest.fixture
def server():
    """Start the server, guaranteeing it is stopped afterwards."""
    if not imu.start():
        pytest.skip(f"port {imu.PORT} unavailable: {imu.bind_error()}")
    yield imu
    try:
        imu.stop()
    except Exception:
        pass


def test_restart_after_stop_with_a_client_attached_actually_listens(server):
    """The regression this file exists for.

    stop() cannot join while a phone holds its socket -- close() does not close
    established connections, so the handler tasks keep the supervisor alive
    past the point its listening socket is gone. start() used to see that live
    thread, early-return the stale _running, and report a healthy server that
    refused every connection. In a session the UI showed IMU up, no phone could
    stream, and nothing surfaced an error.
    """
    client = _ws_connect(imu.PORT)
    try:
        time.sleep(0.3)
        imu.stop()
        assert imu._running is False, "stop() must stop claiming to be running"

        claimed = imu.start()
        state_running = imu.get_state().get("running")
        reachable = _can_connect(imu.PORT)

        # The point is not that start() succeeds -- it is that what the module
        # REPORTS matches what a phone would actually find.
        assert reachable, (
            f"start() reported running={claimed} "
            f"get_state()['running']={state_running} bind_error={imu.bind_error()!r}, "
            f"but no client can connect")
        assert claimed is True and state_running is True
    finally:
        client.close()


def test_stop_when_idle_fully_releases_the_thread(server):
    """With nothing attached the join must succeed and the handle clear --
    that leak is what the teardown work set out to fix."""
    time.sleep(0.2)
    imu.stop()
    assert imu._thread is None, "idle stop() must clear _thread"
    assert imu._running is False


def test_stop_with_a_client_does_not_stall(server):
    """The join can never succeed while a client is attached, so it must not
    be attempted: it used to burn the full 2 s timeout freezing the UI on
    every close-with-phone-connected, for an outcome identical to not waiting.
    """
    client = _ws_connect(imu.PORT)
    try:
        time.sleep(0.3)
        t0 = time.time()
        imu.stop()
        elapsed = time.time() - t0
        assert elapsed < 1.0, f"stop() stalled {elapsed:.2f}s with a client attached"
    finally:
        client.close()


def test_stop_leaves_the_port_bindable(server):
    """Whether or not the thread lingers, the listening socket must be gone so
    the next launch can bind."""
    client = _ws_connect(imu.PORT)
    try:
        time.sleep(0.3)
        imu.stop()
        time.sleep(0.2)
        probe = socket.socket()
        try:
            probe.bind(("0.0.0.0", imu.PORT))
        finally:
            probe.close()
    finally:
        client.close()
