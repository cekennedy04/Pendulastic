"""
Exercises natnet_client's frame-of-data parser against synthetic packets.

Why this file exists: `NatNetClient` was dead code until the marker-coverage
check started using it -- a complete binary parser listed in
.vulture_whitelist.py as an unused class, referenced nowhere outside its own
docstring. The coverage panel now reads `tracking_valid` out of it during a
live session, so the parser sits on the path between "the assessor is blocking
the cameras" and the operator being told so.

None of that can be verified against a real Motive server here, and
`parse_frame` swallows every exception and returns None, so a parser that is
subtly wrong would look exactly like a quiet stream. These tests build the
packets by hand instead: byte layout taken from the parser's own field order,
so a change to one without the other fails here rather than in a session.
"""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import natnet_client as nc


def _rigid_body(rb_id, pos=(0.1, 0.2, 0.3), quat=(0.0, 0.0, 0.0, 1.0),
                mean_error=0.001, tracking_valid=True):
    return (struct.pack("i", rb_id)
            + struct.pack("fff", *pos)
            + struct.pack("ffff", *quat)
            + struct.pack("f", mean_error)
            + struct.pack("h", 0x01 if tracking_valid else 0x00))


def _frame(rigid_bodies=(), marker_sets=(), n_unlabeled=0, timestamp=12.5,
           msg_id=nc.NAT_FRAMEOFDATA, frame_number=42):
    """A NatNet 3.x frame-of-data packet with the sections the parser reads."""
    body = struct.pack("I", frame_number)

    body += struct.pack("I", len(marker_sets))
    for name, n_markers in marker_sets:
        body += name.encode("ascii") + b"\x00"
        body += struct.pack("I", n_markers)
        body += b"\x00" * (n_markers * 12)

    body += struct.pack("I", n_unlabeled) + b"\x00" * (n_unlabeled * 12)

    body += struct.pack("I", len(rigid_bodies))
    for rb in rigid_bodies:
        body += rb

    body += struct.pack("I", 0)      # skeletons
    body += struct.pack("I", 0)      # labeled markers
    body += struct.pack("I", 0)      # force plates
    body += struct.pack("I", 0)      # devices
    body += struct.pack("II", 0, 0)  # timecode
    body += struct.pack("d", timestamp)

    return struct.pack("HH", msg_id, len(body)) + body


# ── the happy path ───────────────────────────────────────────────────────────

def test_a_two_body_frame_parses():
    """The shape the coverage check actually consumes: a thigh and a shank."""
    pkt = _frame(rigid_bodies=[_rigid_body(1), _rigid_body(2)])
    frame = nc._Parser(major=3).parse_frame(pkt)
    assert frame is not None, "a well-formed packet must not parse to None"
    assert set(frame.rigid_bodies) == {1, 2}
    assert frame.frame_number == 42


def test_positions_and_quaternions_round_trip():
    pkt = _frame(rigid_bodies=[_rigid_body(1, pos=(1.5, -2.25, 3.125),
                                           quat=(0.1, 0.2, 0.3, 0.9))])
    rb = nc._Parser(major=3).parse_frame(pkt).rigid_bodies[1]
    assert rb.x == pytest.approx(1.5)
    assert rb.y == pytest.approx(-2.25)
    assert rb.z == pytest.approx(3.125)
    assert rb.qw == pytest.approx(0.9)


def test_tracking_valid_is_read_from_the_params_bit():
    """The one field the coverage check depends on. If this bit were read
    wrongly, every trial would look perfectly tracked or never tracked, and the
    warning would be useless in either direction."""
    parser = nc._Parser(major=3)
    good = parser.parse_frame(_frame(rigid_bodies=[_rigid_body(1, tracking_valid=True)]))
    bad = parser.parse_frame(_frame(rigid_bodies=[_rigid_body(1, tracking_valid=False)]))
    assert good.rigid_bodies[1].tracking_valid is True
    assert bad.rigid_bodies[1].tracking_valid is False


def test_a_lost_body_is_reported_as_invalid_not_omitted():
    """Motive keeps sending a body it has lost, with the valid bit clear. The
    coverage adapter treats absent and invalid alike, but only because the
    parser reports one of them -- this pins which."""
    pkt = _frame(rigid_bodies=[_rigid_body(1, tracking_valid=True),
                               _rigid_body(2, tracking_valid=False)])
    bodies = nc._Parser(major=3).parse_frame(pkt).rigid_bodies
    assert set(bodies) == {1, 2}
    assert bodies[2].tracking_valid is False


def test_marker_sets_and_unlabeled_markers_are_skipped_correctly():
    """Both sections sit BEFORE the rigid bodies, so mis-skipping either one
    shifts every field after it and the rigid bodies come out as noise."""
    pkt = _frame(marker_sets=[("Thigh", 3), ("Shank", 3)], n_unlabeled=7,
                 rigid_bodies=[_rigid_body(1, pos=(9.0, 9.0, 9.0)),
                               _rigid_body(2)])
    bodies = nc._Parser(major=3).parse_frame(pkt).rigid_bodies
    assert set(bodies) == {1, 2}
    assert bodies[1].x == pytest.approx(9.0), "offset drifted through the skips"


def test_the_timestamp_is_read_from_the_frame_suffix():
    frame = nc._Parser(major=3).parse_frame(
        _frame(rigid_bodies=[_rigid_body(1)], timestamp=987.5))
    assert frame.timestamp == pytest.approx(987.5)


# ── things that must not raise ───────────────────────────────────────────────

def test_a_non_frame_message_is_ignored():
    """Motive multicasts other message types on the same socket."""
    assert nc._Parser(major=3).parse_frame(
        _frame(msg_id=nc.NAT_SERVERINFO)) is None


def test_a_packet_truncated_before_the_bodies_returns_none_rather_than_raising():
    """parse_frame swallows exceptions by design; this pins that a short read
    on the wire cannot take down the receive thread."""
    pkt = _frame(rigid_bodies=[_rigid_body(1), _rigid_body(2)])
    for cut in (4, 12, 30):
        assert nc._Parser(major=3).parse_frame(pkt[:cut]) is None


def test_a_packet_truncated_only_in_the_suffix_still_yields_its_bodies():
    """Measured behaviour, and the right call: the rigid bodies are already
    complete, so only the timestamp is lost and it falls back to
    time.monotonic(). Pinned because it means a frame can carry a timestamp
    from a DIFFERENT CLOCK than its neighbours -- which is why
    CoverageSession refuses implausible gaps between frames."""
    pkt = _frame(rigid_bodies=[_rigid_body(1), _rigid_body(2)])
    frame = nc._Parser(major=3).parse_frame(pkt[:-3])
    assert frame is not None
    assert set(frame.rigid_bodies) == {1, 2}


def test_empty_and_garbage_input_return_none():
    parser = nc._Parser(major=3)
    assert parser.parse_frame(b"") is None
    assert parser.parse_frame(b"\xff" * 64) is None


def test_a_frame_with_no_rigid_bodies_parses_to_an_empty_mapping():
    """What arrives when Motive is streaming but the assets are not being
    solved -- distinct from no packets at all, and the coverage panel says so."""
    frame = nc._Parser(major=3).parse_frame(_frame())
    assert frame is not None
    assert frame.rigid_bodies == {}


# ── end to end into the coverage decision ────────────────────────────────────

def test_a_parsed_frame_feeds_the_coverage_monitor():
    """The whole chain the panel depends on, minus the socket: bytes in,
    verdict out."""
    import capture_coverage as cc
    parser = nc._Parser(major=3)
    monitor = cc.CoverageMonitor()
    for i in range(600):
        shank_ok = i < 300
        pkt = _frame(rigid_bodies=[_rigid_body(1, tracking_valid=True),
                                   _rigid_body(2, tracking_valid=shank_ok)])
        frame = parser.parse_frame(pkt)
        monitor.feed(i / 120.0, *cc.rigid_bodies_tracked(frame, 1, 2))
    stats = monitor.stats()
    assert stats.thigh_coverage == 1.0
    assert stats.shank_coverage == pytest.approx(0.5, abs=0.01)
    assert cc.verdict(stats).status == cc.FAIL
