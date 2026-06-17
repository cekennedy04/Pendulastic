"""
==============================================================================
 motive_sync.py - Local Motive control (same-machine, no network)
==============================================================================
 The webcam capture app and Motive now run on the SAME Windows PC, so there is
 no UDP / listener anymore. Import this module directly from the master webcam
 recording script and call:

     import motive_sync
     motive_sync.start_local_motive(packet_string)   # on START
     motive_sync.stop_local_motive()                 # on STOP

 start_local_motive(packet_string):
   1. Parses the START packet string (id / position / height / trial / relpath).
   2. Mirrors the laptop folder tree under OptiTrack_Recordings/ using 'relpath'
      (the post-processing pipeline expects the identical tree).
   3. Builds a clean take name  P_{id}_Pos_{position}_H_{height}_T_{trial}.
   4. Focuses Motive, opens the take-name field (Ctrl+Shift+N), types the name,
      presses Enter to commit, then fires the record hotkey (F2).
 stop_local_motive():
   5. Focuses Motive and presses the record hotkey (F2) again to stop + save.

 Requires:   pip install pyautogui     (pygetwindow optional, for auto-focus)
 Both imports are soft: importing this module never crashes the host app; the
 functions raise a clear RuntimeError if pyautogui is missing.
==============================================================================
"""

import os
import time

# Soft imports: a missing pyautogui must NOT kill the importing GUI app. The
# start/stop functions raise a clear error instead.
try:
    import pyautogui
except Exception:
    pyautogui = None

try:
    import pygetwindow as gw
except Exception:
    gw = None


# -----------------------------------------------------------------------------
# CONFIGURATION  --  edit these to match your rig.
# -----------------------------------------------------------------------------
RECORD_HOTKEY = "f2"                  # Motive start/stop recording hotkey.
NAME_HOTKEY = ("ctrl", "shift", "n")  # Motive shortcut that focuses the take-name field.
MOTIVE_WINDOW_TITLE = "Motive"        # Substring used to find/focus the Motive window.

# Mirror the laptop's folder tree under here using the packet's 'relpath', so the
# post-processing pipeline finds the identical Participant/Position/Height tree.
LOCAL_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "OptiTrack_Recordings")

# Keyboard-automation timing (seconds). Bump up if Motive is slow to respond.
NAME_FIELD_DELAY = 0.30   # after Ctrl+Shift+N, wait for the name field to focus
COMMIT_DELAY = 0.20       # after Enter, before firing the record hotkey
TYPE_INTERVAL = 0.02      # per-character typing delay (avoids dropped chars)

# Unattended automation: disable pyautogui's mouse-corner fail-safe so a stray
# cursor can't abort a trigger. Set True to re-enable.
PYAUTOGUI_FAILSAFE = False

# Characters illegal in Windows / Motive take names -> replaced with "_".
_ILLEGAL_NAME_CHARS = '\\/:*?"<>|'

if pyautogui is not None:
    pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE
    pyautogui.PAUSE = 0.03


# -----------------------------------------------------------------------------
# Parsing / naming / folder helpers
# -----------------------------------------------------------------------------
def parse_start_packet(packet_string):
    """
    Parse a packet string of the form:
        START|id=001|position=1|height=Joint-Level|trial=1|relpath=Participant_001\\...
    into a dict of key=value fields. The leading 'START' token (and any token
    without '=') is ignored, so a string with or without the prefix both work.
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
    """Build  P_{id}_Pos_{position}_H_{height}_T_{trial}  from parsed fields."""
    p_id = _sanitize(fields.get("id", "Unknown"))
    pos = _sanitize(fields.get("position", "Unknown"))
    height = _sanitize(fields.get("height", "Unknown"))
    trial = _sanitize(fields.get("trial", "1"))
    return f"P_{p_id}_Pos_{pos}_H_{height}_T_{trial}"


def mirror_relpath(fields):
    """
    Recreate the laptop's folder tree under LOCAL_ROOT using the 'relpath' field
    (e.g. Participant_001\\Position_1\\Height_Joint-Level). Returns the created
    path, or None if no relpath was supplied. Errors are logged, not raised.
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
# Motive keyboard automation
# -----------------------------------------------------------------------------
def _require_pyautogui():
    if pyautogui is None:
        raise RuntimeError(
            "pyautogui is not installed - Motive automation unavailable. "
            "Run:  pip install pyautogui"
        )


def focus_motive():
    """
    Best-effort: bring the Motive window to the foreground so keystrokes land in
    Motive. Returns True if a Motive window was activated.
    """
    if gw is None:
        return False
    try:
        candidates = [
            w for w in gw.getAllWindows()
            if MOTIVE_WINDOW_TITLE.lower() in (getattr(w, "title", "") or "").lower()
        ]
    except Exception:
        return False
    for w in candidates:
        try:
            if getattr(w, "isMinimized", False):
                w.restore()
            w.activate()
            time.sleep(0.15)
            return True
        except Exception:
            continue
    return False


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def start_local_motive(packet_string):
    """
    Mirror the folder tree, name the Motive take, and start recording locally.

    Args:
        packet_string: the START packet, e.g.
            "START|id=001|position=1|height=Joint-Level|trial=1|relpath=Participant_001\\Position_1\\Height_Joint-Level"

    Returns:
        The take name that was set (e.g. "P_001_Pos_1_H_Joint-Level_T_1").

    Raises:
        RuntimeError: if pyautogui is unavailable.
    """
    _require_pyautogui()
    fields = parse_start_packet(packet_string)
    mirror_relpath(fields)
    take_name = build_take_name(fields)
    print(f"[motive_sync] Take name: {take_name}")

    if not focus_motive():
        print("[motive_sync] WARN: Motive window not found/focused - make sure "
              "Motive is open and frontmost (install 'pygetwindow' for auto-focus).")

    # 1) Open the take-name field.
    pyautogui.hotkey(*NAME_HOTKEY)
    time.sleep(NAME_FIELD_DELAY)
    # 2) Overwrite any existing text, type the name, commit it.
    pyautogui.hotkey("ctrl", "a")
    pyautogui.write(take_name, interval=TYPE_INTERVAL)
    pyautogui.press("enter")
    time.sleep(COMMIT_DELAY)
    # 3) Start recording.
    pyautogui.press(RECORD_HOTKEY)
    print(f"[motive_sync] Recording started: {take_name}")
    return take_name


def stop_local_motive():
    """
    Stop + save the current Motive take (presses the record hotkey again).

    Raises:
        RuntimeError: if pyautogui is unavailable.
    """
    _require_pyautogui()
    focus_motive()
    pyautogui.press(RECORD_HOTKEY)
    print("[motive_sync] Recording stopped + saved.")


if __name__ == "__main__":
    # Not a runnable app - this is a module imported by the master webcam script.
    print(__doc__)
    print("pyautogui available :", pyautogui is not None)
    print("pygetwindow available:", gw is not None)
    demo = "START|id=001|position=1|height=Joint-Level|trial=1|relpath=Participant_001\\Position_1\\Height_Joint-Level"
    print("example take name   :", build_take_name(parse_start_packet(demo)))
