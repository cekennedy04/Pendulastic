"""
==============================================================================
 Part 2 - OPTITRACK SLAVE LISTENER (Auto-Naming)
==============================================================================
 Runs on the OptiTrack Windows PC that runs Motive. Listens on UDP port 5005
 for triggers from the master laptop and drives Motive by keyboard automation.

 NETWORK
 -------
 The OptiTrack PC hosts a Windows Mobile Hotspot; the laptop connects to it and
 sends UDP packets to the host gateway (typically 192.168.137.1). Binding to
 0.0.0.0 listens on every interface, including the hotspot, so no IP edits are
 needed here. (This sidesteps University Guest-WiFi client isolation / dynamic
 IPs.)

 ON A "START" PACKET
   START|id=001|position=1|height=Joint-Level|trial=1|relpath=...
 the script:
   1. Parses id / position / height / trial / relpath.
   2. Mirrors the laptop's folder tree under OptiTrack_Recordings/ using
      'relpath', so the post-processing pipeline finds the identical
      Participant/Position/Height tree.
   3. Builds a clean Motive take name:  P_{id}_Pos_{position}_H_{height}_T_{trial}
      e.g.  P_001_Pos_1_H_Joint-Level_T_1
   4. Focuses Motive, opens the take-name field (Ctrl+Shift+N), types the name,
      presses Enter to commit, then fires the record hotkey (F2) to start.
 ON A "STOP" PACKET
   5. Presses the record hotkey (F2) again to stop + save the take.

 Run on Windows with:   python optitrack_slave.py
 Requires:   pip install pyautogui     (pygetwindow is optional but recommended)
==============================================================================
"""

import os
import socket
import time

# pyautogui is the only hard dependency. Guard the import so a missing package
# gives a clear instruction instead of a raw traceback.
try:
    import pyautogui
except ImportError:
    print("=" * 60)
    print("ERROR: pyautogui is not installed.")
    print("Open Command Prompt and run:\n\n    pip install pyautogui\n")
    print("=" * 60)
    raise SystemExit(1)

# pygetwindow lets us bring Motive to the foreground before typing. Optional:
# if it is missing, we assume Motive is already the active window.
try:
    import pygetwindow as gw
except Exception:
    gw = None


# -----------------------------------------------------------------------------
# CONFIGURATION  --  edit these to match your rig.
# -----------------------------------------------------------------------------
RECORD_HOTKEY = "f2"                 # Motive start/stop recording hotkey.
NAME_HOTKEY = ("ctrl", "shift", "n")  # Motive shortcut that focuses the take-name field.
UDP_PORT = 5005                      # Must match the master app.
LISTEN_IP = "0.0.0.0"                # All interfaces (incl. the hotspot).
BUFFER_SIZE = 4096
MOTIVE_WINDOW_TITLE = "Motive"       # Substring used to find/focus the Motive window.

# Mirror the laptop's folder tree under here using the packet's 'relpath', so the
# post-processing pipeline finds the identical Participant/Position/Height tree.
LOCAL_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "OptiTrack_Recordings")

# Keyboard-automation timing (seconds). Bump these up if Motive is slow to
# respond on the OptiTrack PC.
NAME_FIELD_DELAY = 0.30   # after Ctrl+Shift+N, wait for the name field to focus
COMMIT_DELAY = 0.20       # after Enter, before firing the record hotkey
TYPE_INTERVAL = 0.02      # per-character typing delay (avoids dropped chars)

# Unattended automation: disable pyautogui's mouse-corner fail-safe so a stray
# cursor position can't abort a trigger mid-session. Set True to re-enable.
PYAUTOGUI_FAILSAFE = False
pyautogui.FAILSAFE = PYAUTOGUI_FAILSAFE
pyautogui.PAUSE = 0.03

# Characters illegal in Windows / Motive take names -> replaced with "_".
_ILLEGAL_NAME_CHARS = '\\/:*?"<>|'


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def parse_start_packet(message):
    """
    Parse a START packet of the form:
        START|id=001|position=1|height=Joint-Level|trial=1|relpath=...
    Returns a dict of the key=value fields (the leading 'START' is skipped).
    """
    fields = {}
    for part in message.split("|")[1:]:
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
    path, or None if no relpath was supplied. Errors are logged, not fatal.
    """
    rel_path = fields.get("relpath", "").strip()
    if not rel_path:
        print("    [WARN] START packet had no 'relpath' - skipping folder mirror.")
        return None
    # Normalise separators so a Windows-style relpath also works if needed.
    rel_path = rel_path.replace("\\", os.sep).replace("/", os.sep)
    target_dir = os.path.join(LOCAL_ROOT, rel_path)
    try:
        os.makedirs(target_dir, exist_ok=True)
        print(f"    Mirrored folder: {target_dir}")
        return target_dir
    except OSError as e:
        print(f"    [ERROR] Could not create folder '{target_dir}': {e}")
        return None


def focus_motive():
    """
    Best-effort: bring the Motive window to the foreground so the keyboard
    automation lands in Motive. Returns True if a Motive window was activated.
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


def name_and_start_take(take_name):
    """Focus Motive, set the take name, and start recording."""
    if not focus_motive():
        print("    [WARN] Motive window not found/focused - make sure Motive is "
              "open and frontmost (install 'pygetwindow' for auto-focus).")

    # 1) Open the take-name field.
    pyautogui.hotkey(*NAME_HOTKEY)
    time.sleep(NAME_FIELD_DELAY)

    # 2) Overwrite any existing text, type the name, and commit it.
    pyautogui.hotkey("ctrl", "a")          # select existing field text (if any)
    pyautogui.write(take_name, interval=TYPE_INTERVAL)
    pyautogui.press("enter")
    time.sleep(COMMIT_DELAY)

    # 3) Start recording.
    pyautogui.press(RECORD_HOTKEY)
    print(f"    >> Recording started: {take_name}")


def stop_take():
    """Press the record hotkey again to stop + save the current take."""
    focus_motive()
    pyautogui.press(RECORD_HOTKEY)
    print("    >> Recording stopped + saved.")


def _print_host_ips():
    """Print this PC's IPs so you can confirm the laptop's target address."""
    ips = []
    try:
        ips = sorted(set(socket.gethostbyname_ex(socket.gethostname())[2]))
    except Exception:
        pass
    print(f"   Host IP(s)    : {', '.join(ips) if ips else '(unknown)'}")
    print("   Hotspot gw    : 192.168.137.1  (laptop should send packets here)")


# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------
def main():
    print("=" * 60)
    print(" OptiTrack Slave Listener (Auto-Naming Enabled)")
    print(f"   UDP port      : {UDP_PORT}")
    print(f"   Record hotkey : '{RECORD_HOTKEY}'")
    print(f"   Name shortcut : {'+'.join(NAME_HOTKEY)}")
    print(f"   Window focus  : {'pygetwindow' if gw else 'NOT available - keep Motive frontmost'}")
    print(f"   Mirror root   : {LOCAL_ROOT}")
    _print_host_ips()
    print("=" * 60)

    # Make sure the mirror root exists up front.
    try:
        os.makedirs(LOCAL_ROOT, exist_ok=True)
    except OSError as e:
        print(f"[FATAL] Could not create mirror root '{LOCAL_ROOT}': {e}")
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((LISTEN_IP, UDP_PORT))
    except OSError as e:
        print(f"[FATAL] Could not bind to UDP port {UDP_PORT}. It may be blocked "
              f"by the firewall or already in use.\nDetails: {e}")
        sock.close()
        return

    print(f"[LISTENING] Waiting for master laptop triggers on UDP {UDP_PORT}...  "
          "(Ctrl+C to quit)\n")

    try:
        while True:
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
                message = data.decode("utf-8", errors="replace").strip()
                print(f"[SIGNAL] from {addr[0]}:{addr[1]}  ->  {message}")

                if message.upper().startswith("START"):
                    fields = parse_start_packet(message)
                    # Mirror the folder tree the post-processing pipeline expects.
                    mirror_relpath(fields)
                    take_name = build_take_name(fields)
                    print(f"    -> Take name: {take_name}")
                    try:
                        name_and_start_take(take_name)
                    except Exception as e:
                        print(f"    [ERROR] Naming/record automation failed: {e}")

                elif message.upper().startswith("STOP"):
                    try:
                        stop_take()
                    except Exception as e:
                        print(f"    [ERROR] Could not send stop hotkey: {e}")

                else:
                    print(f"    [WARN] Unrecognized packet ignored: {message}")

            except UnicodeDecodeError as e:
                print(f"[NETWORK ERROR] Received undecodable packet: {e}")
            except OSError as e:
                print(f"[NETWORK ERROR] Socket error while receiving: {e}")

    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Ctrl+C detected. Closing listener cleanly.")
    finally:
        sock.close()
        print("[CLOSED] UDP socket released. Goodbye.")


if __name__ == "__main__":
    main()
