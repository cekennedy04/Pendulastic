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

# Largest believable gap between two frames we actually received, in seconds.
#
# NatNet frame timestamps are not guaranteed to come from one clock. The parser
# reads the stream timestamp from the packet suffix, but falls back to
# time.monotonic() when that section is missing or truncated -- so a single
# short read on the wire can hand us a timestamp tens of thousands of seconds
# away from its neighbours. Rebasing on that would make the window look
# enormous and the longest unbroken stretch with it, which fails in the worst
# direction: a setup that cannot record would be reported as fine.
#
# 1 s is far above any real inter-frame interval at this rig's 120 Hz, so
# nothing legitimate trips it.
MAX_PLAUSIBLE_FRAME_GAP_S = 1.0

# What to assume elapsed when a gap is rejected. One frame at 120 Hz.
NOMINAL_FRAME_INTERVAL_S = 1.0 / 120.0


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
        self._last_raw: Optional[float] = None
        self._last_rel = 0.0
        self._clock_breaks = 0

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
        self._last_raw = None
        self._last_rel = 0.0
        self._clock_breaks = 0
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
    def clock_breaks(self) -> int:
        """How many frames arrived with an implausible timestamp gap. Non-zero
        means the stream is handing us mixed clocks; the statistics stay sane
        but it is worth knowing."""
        return self._clock_breaks

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
        elapsed time is accumulated from the first frame seen; the statistics
        only ever use differences.

        Gaps that cannot be real are refused rather than trusted -- see
        MAX_PLAUSIBLE_FRAME_GAP_S for why a single truncated packet can
        otherwise hand us a timestamp from a different clock, and why believing
        it would report a broken setup as fine.
        """
        thigh_ok = bool(thigh is not None and getattr(thigh, "tracking_valid", False))
        shank_ok = bool(shank is not None and getattr(shank, "tracking_valid", False))
        raw = float(t)
        with self._lock:
            if self._t0 is None:
                self._t0 = raw
                self._last_raw = raw
                rel = 0.0
            else:
                delta = raw - self._last_raw
                if delta < 0.0 or delta > MAX_PLAUSIBLE_FRAME_GAP_S:
                    # A clock discontinuity, not a real gap. Advance by one
                    # nominal frame instead of inheriting the jump; the next
                    # frame is then measured against this one's raw value, so
                    # the new clock continues contiguously from here.
                    self._clock_breaks += 1
                    delta = NOMINAL_FRAME_INTERVAL_S
                rel = self._last_rel + delta
                self._last_raw = raw
            self._last_rel = rel
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


def _main(argv=None) -> int:
    """Smoke-test the live path against a real Motive server.

    Everything above this line is covered by tests that fake the stream, so the
    one thing they cannot prove is that the socket path works: NatNetClient was
    dead code until this feature, and its parser had never run against real
    packets. This makes checking that a single command instead of a GUI session.

        python capture_coverage_session.py            # watch for 10 s
        python capture_coverage_session.py 30         # watch for 30 s
        python capture_coverage_session.py 10 3 4     # thigh id 3, shank id 4

    Prints a line a second, then the verdict. Exit code 0 if the setup would
    pass a pre-flight check, 1 if it would fail, 2 if nothing arrived.
    """
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    seconds = float(argv[0]) if argv else 10.0
    thigh_id = int(argv[1]) if len(argv) > 1 else DEFAULT_THIGH_ID
    shank_id = int(argv[2]) if len(argv) > 2 else DEFAULT_SHANK_ID

    session = CoverageSession(thigh_id=thigh_id, shank_id=shank_id)
    print(f"Listening for Motive (thigh id {thigh_id}, shank id {shank_id}) "
          f"for {seconds:.0f}s...")
    if not session.start():
        print("\nCould not open the mocap stream.")
        print("In Motive: View > Data Streaming, tick Broadcast Frame Data.")
        return 2

    session.begin_preflight(duration_s=seconds)
    try:
        while not session.preflight_done():
            time.sleep(1.0)
            stats = session.live.stats()
            if stats is None:
                print("  ...no frames yet")
                continue
            print(f"  both {stats.coverage * 100:5.1f}%   "
                  f"thigh {stats.thigh_coverage * 100:5.1f}%   "
                  f"shank {stats.shank_coverage * 100:5.1f}%   "
                  f"unbroken {stats.longest_continuous_s:.2f}s")
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        result = session.finish_preflight()
        session.stop()

    print(f"\n{result.headline}\n")
    print(result.detail)
    if session.clock_breaks:
        print(f"\nNote: {session.clock_breaks} frame(s) arrived with an "
              "implausible timestamp gap and were not trusted. That usually "
              "means truncated packets on the wire.")
    return {cc.PASS: 0, cc.FAIL: 1}.get(result.status, 2)


if __name__ == "__main__":
    raise SystemExit(_main())
