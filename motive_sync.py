"""
==============================================================================
 motive_sync.py - Local Motive control via NatNet remote commands
==============================================================================
 The webcam capture app and Motive run on the SAME Windows PC. This module
 drives Motive's recording with the NatNet REMOTE COMMAND protocol over UDP
 loopback (no keyboard automation), which lets us set the session directory and
 take name programmatically and start/stop cleanly.

     import motive_sync
     motive_sync.start_local_motive(packet_string)   # on START
     motive_sync.stop_local_motive()                 # on STOP

 start_local_motive(packet_string):
   1. Parse the START packet (id / position / height / trial / relpath).
   2. Mirror the folder tree under OptiTrack_Recordings/ using 'relpath' (the
      post-processing pipeline expects the identical tree) and SANITIZE that path
      to forward slashes with no trailing separator - Motive can hard-freeze /
      drop the record buffer if SetCurrentSession receives Windows backslashes.
   3. Build a clean take name  P_{id}_Pos_{position}_H_{height}_T_{trial}.
   4. Send the strict NatNet command sequence:
        LiveMode  ->  SetCurrentSession,<unix_path>  ->
        SetRecordTakeName,<take>  ->  StartRecording
      (LiveMode first, with a settle delay, so the directory change lands while
       Motive is live rather than in Edit mode.)
 stop_local_motive():
   5. Send StopRecording, then wait ~0.5 s for Motive to flush the .take to disk
      before the socket is dropped.

 Motive setup: enable NatNet streaming with remote commands; the Command port
 must match MOTIVE_COMMAND_PORT below (Motive default 1510).

 No third-party dependencies (socket/struct/os/time are stdlib), so importing
 this module never crashes the host GUI app.
==============================================================================
"""

import os
import time
import socket
import struct


# -----------------------------------------------------------------------------
# CONFIGURATION  --  edit these to match your rig.
# -----------------------------------------------------------------------------
MOTIVE_IP = "127.0.0.1"          # Motive on the same PC -> NatNet loopback.
MOTIVE_COMMAND_PORT = 1510       # NatNet command port (Motive default).
NAT_REQUEST = 2                  # NatNet message id for a remote command.

# Mirror the laptop's folder tree under here using the packet's 'relpath', so the
# post-processing pipeline finds the identical Participant/Position/Height tree.
# Motive's SetCurrentSession is pointed at the sanitized version of this path.
LOCAL_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "OptiTrack_Recordings")

# Command sequencing delays (seconds). Bump up if Motive is slow to switch modes.
LIVEMODE_SETTLE_DELAY = 0.30     # after LiveMode, before changing the session dir
INTER_COMMAND_DELAY = 0.15       # between the remaining sequenced commands
STOP_FLUSH_DELAY = 0.50          # after StopRecording, to flush the .take to disk
RESPONSE_TIMEOUT = 0.20          # best-effort read of Motive's command response

# Characters illegal in Windows / Motive take names -> replaced with "_".
_ILLEGAL_NAME_CHARS = '\\/:*?"<>|'


# -----------------------------------------------------------------------------
# Parsing / naming / folder helpers
# -----------------------------------------------------------------------------
def parse_start_packet(packet_string):
    """
    Parse a packet string of the form:
        START|id=001|position=1|height=Joint-Level|trial=1|relpath=Participant_001\\...
    into a dict of key=value fields. Tokens without '=' (e.g. the leading
    'START') are ignored, so a string with or without the prefix both work.
    """
    fields = {}
    for part in str(packet_string).split("|"):
        if "=" in part:
            key, _, value = part.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def _sanitize(token):
    """Make a single token safe for a take name (no path/illegal characters)."""
    token = (token or "").strip()
    cleaned = "".join(
        "_" if (ch in _ILLEGAL_NAME_CHARS or ord(ch) < 32) else ch
        for ch in token
    )
    return cleaned or "Unknown"


def build_take_name(fields):
    """
    Build the Motive take name: trial_{N}_optitrack

    Matches the CSV filename the evaluation pipeline expects
    (trial_N_optitrack.csv) so no manual renaming is needed after export.
    Participant / position / height context lives in the session folder path
    (SetCurrentSession), not in the take name.
    """
    trial = _sanitize(fields.get("trial", "1"))
    return f"trial_{trial}_optitrack"


def sanitize_session_path(path):
    """
    Convert a session directory to the Unix-style absolute path Motive expects:
    forward slashes only, no trailing separator. Motive can crash / silently drop
    the record buffer if SetCurrentSession receives Windows backslashes.
    """
    if not path:
        return ""
    p = os.path.abspath(path).replace("\\", "/")
    return p.rstrip("/\\ ")


def mirror_relpath(fields):
    """
    Recreate the laptop's folder tree under LOCAL_ROOT using the 'relpath' field
    (e.g. Participant_001\\Position_1\\Height_Joint-Level). Returns the created
    absolute path, or None if no relpath was supplied. Errors are logged, not raised.
    """
    rel_path = fields.get("relpath", "").strip()
    if not rel_path:
        print("[motive_sync] WARN: packet had no 'relpath' - skipping folder mirror.")
        return None
    rel_path = rel_path.replace("\\", os.sep).replace("/", os.sep)
    target_dir = os.path.join(LOCAL_ROOT, rel_path)
    try:
        os.makedirs(target_dir, exist_ok=True)
        print(f"[motive_sync] Mirrored folder: {target_dir}")
        return target_dir
    except OSError as e:
        print(f"[motive_sync] ERROR: could not create '{target_dir}': {e}")
        return None


# -----------------------------------------------------------------------------
# NatNet remote command transport
# -----------------------------------------------------------------------------
def _natnet_packet(command):
    """Frame a NatNet remote command: <uint16 NAT_REQUEST><uint16 size><cmd\\0>."""
    body = command.encode("utf-8") + b"\x00"
    return struct.pack("<HH", NAT_REQUEST, len(body)) + body


def _send_command_sequence(commands):
    """
    Send an ordered list of (command_string, post_delay) over one UDP socket to
    Motive's command port. Best-effort: reads (and logs) any response without
    blocking the sequence. Raises OSError only if the socket itself fails.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(RESPONSE_TIMEOUT)
    try:
        for command, delay in commands:
            sock.sendto(_natnet_packet(command), (MOTIVE_IP, MOTIVE_COMMAND_PORT))
            print(f"[motive_sync] -> {command}")
            try:
                resp, _ = sock.recvfrom(4096)
                if len(resp) > 4:
                    text = resp[4:].split(b"\x00", 1)[0].decode("utf-8", "replace")
                    if text.strip():
                        print(f"[motive_sync]    <- {text.strip()}")
            except socket.timeout:
                pass
            if delay:
                time.sleep(delay)
    finally:
        sock.close()


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def start_local_motive(packet_string):
    """
    Mirror the folder tree, point Motive at it, name the take, and start recording
    via the NatNet remote-command state machine.

    Sequence (strict): LiveMode -> SetCurrentSession,<unix_path> ->
                        SetRecordTakeName,<take> -> StartRecording.

    Args:
        packet_string: the START packet, e.g.
            "START|id=001|position=1|height=Joint-Level|trial=1|relpath=Participant_001\\Position_1\\Height_Joint-Level"

    Returns:
        The take name that was set (e.g. "P_001_Pos_1_H_Joint-Level_T_1").
    """
    fields = parse_start_packet(packet_string)
    target_dir = mirror_relpath(fields)
    take_name = build_take_name(fields)
    session_path = sanitize_session_path(target_dir) if target_dir else None
    print(f"[motive_sync] Take name: {take_name}")

    # a) LiveMode first (settle), so the session change lands while Motive is live.
    sequence = [("LiveMode", LIVEMODE_SETTLE_DELAY)]
    # b) Point Motive at the (sanitized, forward-slash) session directory.
    if session_path:
        print(f"[motive_sync] Session path: {session_path}")
        sequence.append((f"SetCurrentSession,{session_path}", INTER_COMMAND_DELAY))
    else:
        print("[motive_sync] WARN: no session path - leaving Motive's current session.")
    # c) Set the take/file asset name.
    sequence.append((f"SetRecordTakeName,{take_name}", INTER_COMMAND_DELAY))
    # d) Start recording.
    sequence.append(("StartRecording", 0.0))

    _send_command_sequence(sequence)
    print(f"[motive_sync] Recording started: {take_name}")
    return take_name


def stop_local_motive():
    """
    Stop recording and give Motive time to flush the .take to disk.

    Sequence: StopRecording -> sleep(STOP_FLUSH_DELAY) before the socket drops.
    """
    _send_command_sequence([("StopRecording", STOP_FLUSH_DELAY)])
    print("[motive_sync] Recording stopped + saved.")


if __name__ == "__main__":
    # Not a runnable app - this is a module imported by the master webcam script.
    print(__doc__)
    demo = "START|id=001|position=1|height=Joint-Level|trial=1|relpath=Participant_001\\Position_1\\Height_Joint-Level"
    f = parse_start_packet(demo)
    print("example take name   :", build_take_name(f))
    print("example session path:", sanitize_session_path(os.path.join(LOCAL_ROOT,
          "Participant_001", "Position_1", "Height_Joint-Level")))
