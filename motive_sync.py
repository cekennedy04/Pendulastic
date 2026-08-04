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
   1. Parse the START packet (id / leg / characterization / trial / relpath).
   2. Mirror the folder tree under OptiTrack_Recordings/ using 'relpath' and
      SANITIZE that path to forward slashes — Motive hard-freezes on backslashes.
   3. Build the take name:  trial_{N}_optitrack
      (matches the CSV filename the evaluation pipeline expects).
   4. Send: LiveMode -> SetCurrentSession,<path> ->
            SetRecordTakeName,<take> -> StartRecording

 stop_local_motive():
   5. Send StopRecording + STOP_FLUSH_DELAY so Motive finishes writing the .tak.
   6. auto_export_csv() immediately opens the saved take and exports its rigid-body
      rotation quaternions as a CSV into the same session folder.

      NatNet command sequence for export:
        EditMode  -> OpenTake,<path>  -> ExportCSV,<path>  -> LiveMode
      - EditMode and OpenTake are documented NatNet remote commands (Motive 3+).
      - ExportCSV is a best-effort command; if Motive does not honour it, export
        manually: File > Export > CSV (enable Rotation, format Quaternion,
        disable Markers) from within Motive after the take is loaded.

 Motive setup: NatNet streaming + remote commands enabled; Command port must
 match MOTIVE_COMMAND_PORT (default 1510).

 No third-party dependencies — stdlib only (socket/struct/os/time).
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
# OptiTrack tree matches the acquisition tree (Participant/Leg/Characterization).
# Motive's SetCurrentSession is pointed at the sanitized version of this path.
# Note: analysis_pipeline.py and other downstream analysis scripts still expect
# the older Participant/Position/Height layout -- updating them is tracked as a
# separate follow-up task, not done here.
LOCAL_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "OptiTrack_Recordings")

# Command sequencing delays (seconds). Bump up if Motive is slow to switch modes.
LIVEMODE_SETTLE_DELAY = 0.30     # after LiveMode, before changing the session dir
INTER_COMMAND_DELAY = 0.15       # between the remaining sequenced commands
STOP_FLUSH_DELAY = 0.50          # after StopRecording, to flush the .take to disk
RESPONSE_TIMEOUT = 0.20          # best-effort read of Motive's command response

# Delays for the post-recording export sequence.
EDIT_MODE_SETTLE = 0.50          # after EditMode, before OpenTake
OPEN_TAKE_SETTLE = 3.00          # after OpenTake, Motive needs time to fully load the take
EXPORT_SETTLE = 2.00             # after ExportCSV, before returning to LiveMode

# Characters illegal in Windows / Motive take names -> replaced with "_".
_ILLEGAL_NAME_CHARS = '\\/:*?"<>|'

# Session state cached by start_local_motive() so stop_local_motive() /
# auto_export_csv() can reconstruct the .tak path without extra arguments.
_last_session_path = None   # forward-slash absolute path sent to SetCurrentSession
_last_take_name = None      # e.g. "trial_1_optitrack"


# -----------------------------------------------------------------------------
# Parsing / naming / folder helpers
# -----------------------------------------------------------------------------
def parse_start_packet(packet_string):
    """
    Parse a packet string of the form:
        START|id=001|leg=Right|characterization=pre|trial=1|relpath=Participant_001\\...
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
    Participant / leg / characterization context lives in the session folder
    path (SetCurrentSession), not in the take name.
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
    (e.g. Participant_001\\Right\\pre). Returns the created absolute path, or
    None if no relpath was supplied. Errors are logged, not raised.
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
            "START|id=001|leg=Right|characterization=pre|trial=1|relpath=Participant_001\\Right\\pre"

    Returns:
        The take name that was set (e.g. "trial_1_optitrack").
    """
    global _last_session_path, _last_take_name

    fields = parse_start_packet(packet_string)
    target_dir = mirror_relpath(fields)
    take_name = build_take_name(fields)
    session_path = sanitize_session_path(target_dir) if target_dir else None

    # Cache for use by auto_export_csv() after recording stops.
    _last_session_path = session_path
    _last_take_name = take_name

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


def auto_export_csv(session_path=None, take_name=None):
    """
    After StopRecording: switch Motive to EditMode, open the saved .tak file,
    and export rigid-body rotation quaternions as a CSV into the same folder.

    NatNet command sequence:
        EditMode  ->  OpenTake,<tak_path>  ->  ExportCSV,<csv_path>  ->  LiveMode

    Notes:
        - EditMode and OpenTake are documented remote commands (Motive 3+).
        - ExportCSV is a best-effort command. If Motive does not honour it (no
          CSV appears after ~5 s), export manually from within Motive:
            File > Export > CSV
            Rotation data: ON, Format: Quaternion, Markers: OFF
        - session_path and take_name default to the values cached by the most
          recent call to start_local_motive().
    """
    if session_path is None:
        session_path = _last_session_path
    if take_name is None:
        take_name = _last_take_name

    if not session_path or not take_name:
        print("[motive_sync] auto_export_csv: session path or take name unknown — skipping.")
        print("[motive_sync]   Export manually: File > Export > CSV (Quaternion, no markers)")
        return

    # Forward-slash paths — same requirement as SetCurrentSession.
    tak_path = f"{session_path}/{take_name}.tak"
    csv_path = f"{session_path}/{take_name}.csv"

    print(f"[motive_sync] Opening take: {tak_path}")
    print(f"[motive_sync] Exporting quaternions to: {csv_path}")

    sequence = [
        ("EditMode",                  EDIT_MODE_SETTLE),
        (f"OpenTake,{tak_path}",      OPEN_TAKE_SETTLE),
        (f"ExportCSV,{csv_path}",     EXPORT_SETTLE),
        ("LiveMode",                  0.0),
    ]
    try:
        _send_command_sequence(sequence)
        print(f"[motive_sync] Export sequence sent — CSV expected at: {csv_path}")
        print("[motive_sync]   If the CSV is absent after ~5 s, export manually in Motive.")
    except OSError as e:
        print(f"[motive_sync] auto_export_csv error: {e}")
        print("[motive_sync]   Export manually: File > Export > CSV (Quaternion, no markers)")


def stop_local_motive():
    """
    Stop recording, flush the .tak to disk, then automatically open the take
    and export rigid-body quaternions as CSV via auto_export_csv().

    Sequence:
        StopRecording -> sleep(STOP_FLUSH_DELAY)
        -> EditMode -> OpenTake -> ExportCSV -> LiveMode
    """
    _send_command_sequence([("StopRecording", STOP_FLUSH_DELAY)])
    print("[motive_sync] Recording stopped + saved.")
    auto_export_csv()


if __name__ == "__main__":
    # Not a runnable app - this is a module imported by the master webcam script.
    print(__doc__)
    demo = "START|id=001|leg=Right|characterization=pre|trial=1|relpath=Participant_001\\Right\\pre"
    f = parse_start_packet(demo)
    print("example take name   :", build_take_name(f))
    print("example session path:", sanitize_session_path(os.path.join(LOCAL_ROOT,
          "Participant_001", "Right", "pre")))
