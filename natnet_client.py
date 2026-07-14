"""
natnet_client.py
================
Lightweight NatNet 3.x/4.x UDP listener that extracts rigid body quaternions
from a Motive broadcast and saves them in our standard OptiTrack CSV format.

Motive → NatNet → UDP multicast 239.255.42.99:1511

Usage:
    client = NatNetClient()
    client.start()
    # ... recording ...
    client.stop()
    client.save_csv("trial_1_optitrack.csv")
"""

import socket
import struct
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np

# ─── NatNet network constants ─────────────────────────────────────────────────
MULTICAST_GROUP  = "239.255.42.99"
DATA_PORT        = 1511
COMMAND_PORT     = 1510
COMMAND_HOST     = "127.0.0.1"   # Motive is usually on localhost; override if remote
SOCKET_BUFSIZE   = 65536

# NatNet message IDs
NAT_PING               = 0
NAT_PINGRESPONSE       = 1
NAT_FRAMEOFDATA        = 7
NAT_SERVERINFO         = 5
NAT_MODELDEF           = 4

# CSV header matching our analysis pipeline
CSV_HEADER = (
    "Frame,Time (Seconds),"
    "thigh_qx,thigh_qy,thigh_qz,thigh_qw,"
    "thigh_tx,thigh_ty,thigh_tz,"
    "shank_qx,shank_qy,shank_qz,shank_qw,"
    "shank_tx,shank_ty,shank_tz\n"
)


# ─── Frame data container ─────────────────────────────────────────────────────

class RigidBodyFrame:
    __slots__ = ("rb_id", "x", "y", "z", "qx", "qy", "qz", "qw",
                 "mean_error", "tracking_valid")

    def __init__(self, rb_id, x, y, z, qx, qy, qz, qw, mean_error, tracking_valid):
        self.rb_id          = rb_id
        self.x, self.y, self.z = x, y, z
        self.qx, self.qy, self.qz, self.qw = qx, qy, qz, qw
        self.mean_error     = mean_error
        self.tracking_valid = tracking_valid


class MocapFrame:
    __slots__ = ("frame_number", "timestamp", "rigid_bodies")

    def __init__(self, frame_number: int, timestamp: float,
                 rigid_bodies: Dict[int, RigidBodyFrame]):
        self.frame_number = frame_number
        self.timestamp    = timestamp
        self.rigid_bodies = rigid_bodies  # {rb_id: RigidBodyFrame}


# ─── NatNet packet parser ─────────────────────────────────────────────────────

class _Parser:
    """Parses NatNet 3.x / 4.x frame-of-data packets."""

    def __init__(self, major: int = 3, minor: int = 0):
        self.major = major
        self.minor = minor

    def parse_frame(self, data: bytes) -> Optional[MocapFrame]:
        try:
            return self._parse(data)
        except Exception:
            return None

    def _parse(self, data: bytes) -> Optional[MocapFrame]:
        offset = 0

        # Message header: id (uint16) + size (uint16)
        msg_id, _ = struct.unpack_from("HH", data, offset)
        offset += 4
        if msg_id != NAT_FRAMEOFDATA:
            return None

        frame_number = struct.unpack_from("I", data, offset)[0]; offset += 4

        # ── Marker sets ───────────────────────────────────────────────────────
        n_sets = struct.unpack_from("I", data, offset)[0]; offset += 4
        for _ in range(n_sets):
            # null-terminated name
            end = data.index(b"\x00", offset)
            offset = end + 1
            n_markers = struct.unpack_from("I", data, offset)[0]; offset += 4
            offset += n_markers * 12  # 3 floats × 4 bytes

        # ── Unlabeled markers ─────────────────────────────────────────────────
        n_unlabeled = struct.unpack_from("I", data, offset)[0]; offset += 4
        offset += n_unlabeled * 12

        # ── Rigid bodies ──────────────────────────────────────────────────────
        n_rigid = struct.unpack_from("I", data, offset)[0]; offset += 4
        rigid_bodies: Dict[int, RigidBodyFrame] = {}

        for _ in range(n_rigid):
            rb_id = struct.unpack_from("i", data, offset)[0]; offset += 4
            x, y, z = struct.unpack_from("fff", data, offset); offset += 12
            qx, qy, qz, qw = struct.unpack_from("ffff", data, offset); offset += 16
            mean_error = struct.unpack_from("f", data, offset)[0]; offset += 4
            params = struct.unpack_from("h", data, offset)[0]; offset += 2
            tracking_valid = bool(params & 0x01)

            # Per-rigid-body marker data (if present in older format)
            if self.major < 3:
                n_rb_markers = struct.unpack_from("I", data, offset)[0]; offset += 4
                offset += n_rb_markers * 12  # positions
                offset += n_rb_markers * 4   # sizes
                offset += n_rb_markers * 4   # residuals

            rigid_bodies[rb_id] = RigidBodyFrame(
                rb_id, x, y, z, qx, qy, qz, qw, mean_error, tracking_valid)

        # ── Skip skeletons, labeled markers, force plates, devices ───────────
        # They appear here but we don't need them — just grab timestamp below
        # by scanning for the double (8-byte) timestamp near the end.
        # This is fragile for unknown future formats but sufficient for 3.x/4.x.

        # ── Timestamp ─────────────────────────────────────────────────────────
        timestamp = time.monotonic()   # fallback
        try:
            # Scan remaining data for the timestamp double
            # It typically appears after all optional sections.
            # We parse skeletons, assets, etc. to find it properly.
            offset = self._skip_skeletons(data, offset)
            offset = self._skip_labeled_markers(data, offset)
            offset = self._skip_force_plates(data, offset)
            offset = self._skip_devices(data, offset)
            # Frame suffix: timecode (2×uint32) + timestamp (double) + ...
            if offset + 8 + 8 <= len(data):
                offset += 8   # timecode
                timestamp = struct.unpack_from("d", data, offset)[0]
        except Exception:
            pass

        return MocapFrame(frame_number, timestamp, rigid_bodies)

    def _skip_skeletons(self, data: bytes, offset: int) -> int:
        n = struct.unpack_from("I", data, offset)[0]; offset += 4
        for _ in range(n):
            offset += 4  # skeleton ID
            n_rb = struct.unpack_from("I", data, offset)[0]; offset += 4
            for _ in range(n_rb):
                offset += 4 + 12 + 16 + 4 + 2  # id + pos + quat + error + params
        return offset

    def _skip_labeled_markers(self, data: bytes, offset: int) -> int:
        n = struct.unpack_from("I", data, offset)[0]; offset += 4
        for _ in range(n):
            offset += 4 + 12 + 4 + 2  # id + pos + size + params
            if self.major >= 3:
                offset += 4             # residual
        return offset

    def _skip_force_plates(self, data: bytes, offset: int) -> int:
        n = struct.unpack_from("I", data, offset)[0]; offset += 4
        for _ in range(n):
            offset += 4   # ID
            n_channels = struct.unpack_from("I", data, offset)[0]; offset += 4
            for _ in range(n_channels):
                n_frames = struct.unpack_from("I", data, offset)[0]; offset += 4
                offset += n_frames * 4
        return offset

    def _skip_devices(self, data: bytes, offset: int) -> int:
        n = struct.unpack_from("I", data, offset)[0]; offset += 4
        for _ in range(n):
            offset += 4   # ID
            n_channels = struct.unpack_from("I", data, offset)[0]; offset += 4
            for _ in range(n_channels):
                n_frames = struct.unpack_from("I", data, offset)[0]; offset += 4
                offset += n_frames * 4
        return offset


# ─── Main client ─────────────────────────────────────────────────────────────

class NatNetClient:
    """
    Thread-safe NatNet data receiver.

    Typical usage:
        client = NatNetClient(thigh_id=1, shank_id=2)
        client.start()
        ...
        client.stop()
        client.save_csv(path)
    """

    def __init__(self,
                 thigh_id: int = 1,
                 shank_id: int = 2,
                 multicast_group: str = MULTICAST_GROUP,
                 data_port: int = DATA_PORT,
                 server_host: str = COMMAND_HOST):
        self.thigh_id       = thigh_id
        self.shank_id       = shank_id
        self.multicast_group = multicast_group
        self.data_port      = data_port
        self.server_host    = server_host

        self._frames: List[Tuple] = []   # (frame_no, t, thigh_rb, shank_rb)
        self._lock   = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._parser  = _Parser(major=3, minor=0)
        self._socket: Optional[socket.socket] = None

        self.on_frame = None   # optional callback(frame_no, t, thigh, shank)
        self.n_frames_received = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._frames.clear()
        self.n_frames_received = 0
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._socket:
            try: self._socket.close()
            except Exception: pass
        if self._thread:
            self._thread.join(timeout=2.0)

    def save_csv(self, path: str) -> int:
        """Write captured frames to CSV. Returns number of rows written."""
        with self._lock:
            frames = list(self._frames)
        if not frames:
            return 0
        t0 = frames[0][1]
        with open(path, "w", newline="") as fh:
            fh.write(CSV_HEADER)
            for i, (fn, t, thigh, shank) in enumerate(frames):
                if thigh is None or shank is None:
                    continue
                fh.write(
                    f"{fn},{t - t0:.6f},"
                    f"{thigh.qx:.8f},{thigh.qy:.8f},{thigh.qz:.8f},{thigh.qw:.8f},"
                    f"{thigh.x:.6f},{thigh.y:.6f},{thigh.z:.6f},"
                    f"{shank.qx:.8f},{shank.qy:.8f},{shank.qz:.8f},{shank.qw:.8f},"
                    f"{shank.x:.6f},{shank.y:.6f},{shank.z:.6f}\n"
                )
        return len(frames)

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Network loop ───────────────────────────────────────────────────────────

    def _listen_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.data_port))
        mreq = struct.pack("4sL",
                           socket.inet_aton(self.multicast_group),
                           socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(0.5)
        self._socket = sock

        while self._running:
            try:
                data, _ = sock.recvfrom(SOCKET_BUFSIZE)
            except socket.timeout:
                continue
            except OSError:
                break

            mocap_frame = self._parser.parse_frame(data)
            if mocap_frame is None:
                continue

            self.n_frames_received += 1
            rbs   = mocap_frame.rigid_bodies
            thigh = rbs.get(self.thigh_id)
            shank = rbs.get(self.shank_id)

            with self._lock:
                self._frames.append((
                    mocap_frame.frame_number,
                    mocap_frame.timestamp,
                    thigh, shank,
                ))

            if self.on_frame:
                try:
                    self.on_frame(mocap_frame.frame_number,
                                  mocap_frame.timestamp, thigh, shank)
                except Exception:
                    pass

        try: sock.close()
        except Exception: pass

    # ── Knee angle computation (for live preview) ──────────────────────────────

    @staticmethod
    def compute_knee_angle(thigh: RigidBodyFrame, shank: RigidBodyFrame) -> float:
        """Returns knee angle in degrees using the same quaternion formula as the evaluator."""
        try:
            from scipy.spatial.transform import Rotation as R
            r_thigh = R.from_quat([thigh.qx, thigh.qy, thigh.qz, thigh.qw])
            r_shank = R.from_quat([shank.qx, shank.qy, shank.qz, shank.qw])
            return float(np.degrees((r_thigh.inv() * r_shank).magnitude()))
        except Exception:
            return float("nan")

    def live_knee_angle(self) -> float:
        """Returns the most recently computed knee angle, or nan if unavailable."""
        with self._lock:
            if not self._frames:
                return float("nan")
            _, _, thigh, shank = self._frames[-1]
        if thigh is None or shank is None:
            return float("nan")
        return self.compute_knee_angle(thigh, shank)
