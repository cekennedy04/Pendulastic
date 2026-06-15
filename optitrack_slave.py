"""
==============================================================================
 Part 2 - OPTITRACK SLAVE LISTENER (Remote Host)
==============================================================================
 Runs on the separate OptiTrack Windows desktop that runs Motive.

 It listens forever on UDP port 5005 for packets from the laptop master app.
   * On a "START" packet: parses the trial info, recreates the IDENTICAL folder
     tree under this machine's local root, and presses the Motive record hotkey.
   * On a "STOP" packet: presses the hotkey again to end the take.

 Run on Windows with:   python optitrack_slave.py

 Requires:   pip install pyautogui
 (socket, os are part of the standard library.)
==============================================================================
"""

import os
import socket

# pyautogui is the only third-party dependency. Guard the import so a missing
# package gives a clear instruction instead of a raw traceback.
try:
    import pyautogui
except ImportError:
    print("=" * 60)
    print("ERROR: pyautogui is not installed.")
    print("Open Command Prompt and run:\n\n    pip install pyautogui\n")
    print("=" * 60)
    raise SystemExit(1)


# -----------------------------------------------------------------------------
# CONFIGURATION  --  edit these to match your rig.
# -----------------------------------------------------------------------------
RECORD_HOTKEY = "f2"          # Motive's start/stop recording hotkey. Change as needed.
UDP_PORT = 5005               # Must match the master app.
LISTEN_IP = "0.0.0.0"         # Listen on all network interfaces.
LOCAL_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "OptiTrack_Recordings")
BUFFER_SIZE = 4096


def parse_start_packet(message):
    """
    Parse a START packet of the form:
        START|id=001|position=1|height=Joint-Level|trial=1|relpath=Participant_001\\Position_1\\Height_Joint-Level
    Returns a dict of the fields. Missing fields default to "".
    """
    fields = {}
    parts = message.split("|")
    for part in parts[1:]:               # skip the leading "START"
        if "=" in part:
            key, _, value = part.partition("=")
            fields[key.strip()] = value.strip()
    return fields


def main():
    print("=" * 60)
    print(" OptiTrack Slave Listener")
    print(f"   UDP port      : {UDP_PORT}")
    print(f"   Record hotkey : '{RECORD_HOTKEY}'")
    print(f"   Local root    : {LOCAL_ROOT}")
    print("=" * 60)

    # Make sure the local root exists.
    try:
        os.makedirs(LOCAL_ROOT, exist_ok=True)
    except OSError as e:
        print(f"[FATAL] Could not create local root folder '{LOCAL_ROOT}': {e}")
        return

    # Create and bind the UDP socket.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((LISTEN_IP, UDP_PORT))
    except OSError as e:
        print(f"[FATAL] Could not bind to UDP port {UDP_PORT}. "
              f"It may be blocked by the firewall or already in use.\nDetails: {e}")
        sock.close()
        return

    print(f"[LISTENING] Waiting for packets on UDP {UDP_PORT}...  (Ctrl+C to quit)\n")

    try:
        while True:
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
                message = data.decode("utf-8", errors="replace").strip()
                print(f"[SIGNAL RECEIVED] from {addr[0]}:{addr[1]}  ->  {message}")

                if message.upper().startswith("START"):
                    fields = parse_start_packet(message)
                    rel_path = fields.get("relpath", "")

                    if rel_path:
                        target_dir = os.path.join(LOCAL_ROOT, rel_path)
                        try:
                            os.makedirs(target_dir, exist_ok=True)
                            print(f"    Created folder: {target_dir}")
                        except OSError as e:
                            print(f"    [ERROR] Could not create folder "
                                  f"'{target_dir}': {e}")
                    else:
                        print("    [WARN] START packet had no 'relpath' field; "
                              "skipping folder creation.")

                    try:
                        pyautogui.press(RECORD_HOTKEY)
                        print(f"    >> Pressed '{RECORD_HOTKEY}' to START Motive recording.")
                    except Exception as e:
                        print(f"    [ERROR] Could not send record hotkey: {e}")

                elif message.upper().startswith("STOP"):
                    try:
                        pyautogui.press(RECORD_HOTKEY)
                        print(f"    >> Pressed '{RECORD_HOTKEY}' to STOP Motive recording.")
                    except Exception as e:
                        print(f"    [ERROR] Could not send record hotkey: {e}")

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
