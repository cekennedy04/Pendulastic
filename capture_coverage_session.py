"""
capture_coverage_session.py
===========================
Wires a live Motive stream into `capture_coverage`'s decision logic.

Kept apart from that module on purpose: the statistics and the pass/fail rules
are pure and fully testable, and everything that touches a socket lives here.
The NatNet client is injectable for the same reason -- the interesting failures
happen with real hardware in the room, so the parts that can be exercised
without it should be.

Note that `natnet_client.NatNetClient` was dead code before this: written,
complete, and referenced nowhere outside its own docstring (it is in
.vulture_whitelist.py as an unused class). The binary protocol work was already
done; nothing had asked it for anything.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import capture_coverage as cc

# Motive rigid-body ids for the two clusters. Match natnet_client's defaults.
DEFAULT_THIGH_ID = 1
DEFAULT_SHANK_ID = 2


class CoverageSession:
    """Owns a mocap stream and keeps two views of it.

    `live` is a rolling window for the during-recording indicator. `preflight`
    accumulates over a fixed watch and is only running while a check is in
    progress, so the two never interfere.
    """

    def __init__(self,
                 thigh_id: int = DEFAULT_THIGH_ID,
                 shank_id: int = DEFAULT_SHANK_ID,
                 window_s: float = cc.LIVE_WINDOW_S,
                 client_factory: Optional[Callable] = None):
        self.thigh_id = thigh_id
        self.shank_id = shank_id
        self._client_factory = client_factory or self._default_client
        self._client = None
        self._lock = threading.Lock()
        self.live = cc.CoverageMonitor(window_s=window_s)
        self._preflight: Optional[cc.CoverageMonitor] = None
        self._preflight_until = 0.0
        self._t0: Optional[float] = None

    def _default_client(self):
        import natnet_client
        return natnet_client.NatNetClient(thigh_id=self.thigh_id,
                                          shank_id=self.shank_id)

    # ── stream lifecycle ─────────────────────────────────────────────────────

    def start(self) -> bool:
        """Begin listening. False if the stream could not be opened, which is
        not an error worth raising on -- an operator with no Motive running
        should get a status line, not a traceback."""
        if self._client is not None:
            return True
        try:
            client = self._client_factory()
            client.on_frame = self._on_frame
            client.start()
        except Exception:
            self._client = None
            return False
        self._client = client
        self._t0 = None
        return True

    def stop(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.on_frame = None
            client.stop()
        except Exception:
            pass

    @property
    def running(self) -> bool:
        return self._client is not None

    # ── frame intake ─────────────────────────────────────────────────────────

    def _on_frame(self, frame_no, t, thigh, shank) -> None:
        """NatNetClient's callback. Deliberately cheap -- it runs on the
        receive thread, and anything slow here costs frames."""
        self.feed_frame(frame_no, t, thigh, shank)

    def feed_frame(self, frame_no, t, thigh, shank) -> None:
        """Record one frame. Separated from the callback so tests can drive it.

        `t` from NatNet is a stream timestamp whose origin is not meaningful, so
        it is rebased on the first frame seen; the statistics only ever use
        differences.
        """
        thigh_ok = bool(thigh is not None and getattr(thigh, "tracking_valid", False))
        shank_ok = bool(shank is not None and getattr(shank, "tracking_valid", False))
        with self._lock:
            if self._t0 is None:
                self._t0 = float(t)
            rel = float(t) - self._t0
            self.live.feed(rel, thigh_ok, shank_ok)
            if self._preflight is not None:
                self._preflight.feed(rel, thigh_ok, shank_ok)

    # ── the two views ────────────────────────────────────────────────────────

    def live_verdict(self) -> cc.Verdict:
        with self._lock:
            stats = self.live.stats()
        return cc.verdict(stats)

    def begin_preflight(self, duration_s: float = cc.PREFLIGHT_WATCH_S) -> None:
        with self._lock:
            self._preflight = cc.CoverageMonitor()
            self._preflight_until = time.monotonic() + duration_s

    def preflight_seconds_left(self) -> float:
        with self._lock:
            if self._preflight is None:
                return 0.0
            return max(0.0, self._preflight_until - time.monotonic())

    def preflight_done(self) -> bool:
        with self._lock:
            return self._preflight is not None and time.monotonic() >= self._preflight_until

    def finish_preflight(self) -> cc.Verdict:
        """Verdict for the completed watch, and clears it."""
        with self._lock:
            monitor, self._preflight = self._preflight, None
            stats = monitor.stats() if monitor is not None else None
        return cc.verdict(stats)
