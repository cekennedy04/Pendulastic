# Hotspot Setup Helper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when an iPhone IMU sensor has failed to pair with the recording PC (both sensors
disconnected past a threshold, on a running/healthy server) and surface hotspot-setup guidance from
a click on the app's existing IMU status label, without adding any new widget to either app's
layout.

**Architecture:** Two new repo-root modules — `network_setup.py` (pure `PairingWatcher`, no
Tkinter) and `network_setup_ui.py` (`apply_stalled_hint()` + `HotspotHelperDialog`, a
`tk.Toplevel`) — get wired into two *existing* polling call sites: `master_app.py`'s
`_poll_imu_status` and `pendulastic_app.py`'s `App._tick` IMU block. Both integrations are guarded
by a `_HOTSPOT_HELPER_AVAIL` import flag so a missing/broken module leaves both apps' existing IMU
status behavior byte-for-byte unchanged.

**Tech Stack:** Python 3.13, Tkinter, pytest. No new third-party dependencies.

## Global Constraints

- Scope is the per-recording IMU phone-pairing flow only — not first-time install/onboarding, not
  camera selection, not OptiTrack sync.
- `master_app.py` must not be disturbed: no widget displaced, resized, or restyled, no layout
  change. `pendulastic_app.py` is held to the same constraint.
- No hotspot automation — both hotspot paths are guidance/deep-link only (one-click toggle is
  explicitly out of scope, a future spec).
- No proactive "network looks isolated" heuristic — the only trigger is sustained zero-phone
  pairing (`PairingWatcher`, see spec §4). This was cut after Codex review found the original
  broadcast/multicast heuristic invalid.
- `master_app.py` imports the IMU server module as `imu_server`; `pendulastic_app.py` imports it as
  `_imu`. Do not conflate the two aliases across files.
- Both apps already have their own IMU-status polling loop running; this feature adds **no new
  `.after()` loop** to either app — the only new `.after()` loop is scoped entirely to
  `HotspotHelperDialog`'s own lifetime.
- Test harness convention already used throughout this suite (`tests/test_acquisition_panel.py`,
  `tests/test_master_app_paths.py`, `tests/test_app.py`): construct real Tkinter widgets headless
  (`tk.Tk(); r.withdraw()`), drive them directly by calling methods, no `mainloop()`, no
  computer-use/GUI-automation tool (none exists in this project's environment).
- Design spec: `docs/superpowers/specs/2026-08-07-hotspot-setup-helper-design.md`.

---

### Task 1: `network_setup.py` — `PairingWatcher`

**Files:**
- Create: `network_setup.py`
- Test: `tests/test_network_setup.py`

**Interfaces:**
- Produces: `PairingWatcher(threshold_s: float = 45.0)` with method
  `update(imu_state: dict, now: float) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_network_setup.py`:

```python
# tests/test_network_setup.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _state(proximal_connected, distal_connected, running=True, bind_error=None):
    return {
        "running": running,
        "bind_error": bind_error,
        "proximal": {"connected": proximal_connected},
        "distal":   {"connected": distal_connected},
    }


def test_zero_connected_under_threshold_not_stalled():
    from network_setup import PairingWatcher
    w = PairingWatcher(threshold_s=45.0)
    assert w.update(_state(False, False), now=1000.0) is False
    assert w.update(_state(False, False), now=1030.0) is False


def test_zero_connected_over_threshold_is_stalled():
    from network_setup import PairingWatcher
    w = PairingWatcher(threshold_s=45.0)
    assert w.update(_state(False, False), now=1000.0) is False
    assert w.update(_state(False, False), now=1046.0) is True


def test_one_connected_resets_timer():
    from network_setup import PairingWatcher
    w = PairingWatcher(threshold_s=45.0)
    assert w.update(_state(False, False), now=1000.0) is False
    # A phone connects before the threshold -- timer must reset.
    assert w.update(_state(True, False), now=1030.0) is False
    assert w.update(_state(False, False), now=1060.0) is False, (
        "must restart counting from the reconnect, not the original t0")
    assert w.update(_state(False, False), now=1076.0) is True


def test_running_false_never_stalled():
    from network_setup import PairingWatcher
    w = PairingWatcher(threshold_s=45.0)
    assert w.update(_state(False, False, running=False), now=1000.0) is False
    assert w.update(_state(False, False, running=False), now=1100.0) is False


def test_bind_error_never_stalled():
    from network_setup import PairingWatcher
    w = PairingWatcher(threshold_s=45.0)
    assert w.update(_state(False, False, bind_error="port in use"), now=1000.0) is False
    assert w.update(_state(False, False, bind_error="port in use"), now=1100.0) is False


def test_reconnect_then_disconnect_starts_fresh_timer():
    from network_setup import PairingWatcher
    w = PairingWatcher(threshold_s=45.0)
    assert w.update(_state(False, False), now=1000.0) is False
    assert w.update(_state(False, False), now=1046.0) is True   # stalled once
    assert w.update(_state(True, True), now=1050.0) is False    # both connect
    assert w.update(_state(False, False), now=1060.0) is False  # disconnect again
    assert w.update(_state(False, False), now=1106.0) is True   # re-stalls after a fresh 45s
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_network_setup.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'network_setup'`

- [ ] **Step 3: Implement `PairingWatcher`**

Create `network_setup.py`:

```python
"""
network_setup.py
=================
Pure detection logic for the hotspot-setup helper (see
docs/superpowers/specs/2026-08-07-hotspot-setup-helper-design.md). No
Tkinter dependency -- Tkinter-facing pieces live in network_setup_ui.py.
"""
from __future__ import annotations


class PairingWatcher:
    """Tracks how long an IMU server has had zero sensors connected while
    healthy (running, no bind error), and reports True once that's been
    sustained past threshold_s. Resets the instant either sensor connects
    or the server stops being healthy."""

    def __init__(self, threshold_s: float = 45.0):
        self._threshold_s = threshold_s
        self._first_zero_ts: float | None = None

    def update(self, imu_state: dict, now: float) -> bool:
        healthy_but_disconnected = (
            bool(imu_state.get("running"))
            and not imu_state.get("bind_error")
            and not imu_state["proximal"]["connected"]
            and not imu_state["distal"]["connected"]
        )
        if not healthy_but_disconnected:
            self._first_zero_ts = None
            return False
        if self._first_zero_ts is None:
            self._first_zero_ts = now
        return (now - self._first_zero_ts) >= self._threshold_s
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_network_setup.py -v
```

Expected: all 6 PASS

- [ ] **Step 5: Commit**

```bash
git add network_setup.py tests/test_network_setup.py
git commit -m "feat: add PairingWatcher for hotspot-setup detection"
```

---

### Task 2: `network_setup_ui.py` — `apply_stalled_hint` + `HotspotHelperDialog`

**Files:**
- Create: `network_setup_ui.py`
- Test: `tests/test_network_setup.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1 directly (the caller passes a `bool`, not a `PairingWatcher`)
- Produces:
  - `apply_stalled_hint(label: tk.Label, stalled: bool, open_dialog: Callable) -> None`
  - `HotspotHelperDialog(parent: tk.Widget, get_state_fn: Callable[[], dict])` — a `tk.Toplevel`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_network_setup.py`:

```python
import tkinter as tk


def _root():
    r = tk.Tk(); r.withdraw(); return r


def test_apply_stalled_hint_binds_suffix_and_cursor_when_stalled():
    from network_setup_ui import apply_stalled_hint, HINT_SUFFIX
    r = _root()
    try:
        lbl = tk.Label(r, text="waiting for phones")
        calls = []
        apply_stalled_hint(lbl, stalled=True, open_dialog=lambda e=None: calls.append(1))
        assert lbl.cget("text") == "waiting for phones" + HINT_SUFFIX
        assert lbl.cget("cursor") == "hand2"
        lbl.event_generate("<Button-1>", x=1, y=1)
        r.update()
        assert calls == [1]
    finally:
        r.destroy()


def test_apply_stalled_hint_is_idempotent_when_called_repeatedly():
    from network_setup_ui import apply_stalled_hint, HINT_SUFFIX
    r = _root()
    try:
        lbl = tk.Label(r, text="waiting for phones")
        apply_stalled_hint(lbl, stalled=True, open_dialog=lambda e=None: None)
        apply_stalled_hint(lbl, stalled=True, open_dialog=lambda e=None: None)
        assert lbl.cget("text") == "waiting for phones" + HINT_SUFFIX
        assert lbl.cget("text").count(HINT_SUFFIX) == 1
    finally:
        r.destroy()


def test_apply_stalled_hint_unbinds_when_resolved():
    from network_setup_ui import apply_stalled_hint, HINT_SUFFIX
    r = _root()
    try:
        lbl = tk.Label(r, text="2 phones connected")
        apply_stalled_hint(lbl, stalled=False, open_dialog=lambda e=None: None)
        assert lbl.cget("cursor") == ""
        assert lbl.cget("text") == "2 phones connected"
    finally:
        r.destroy()


def test_dialog_shows_both_hotspot_options():
    from network_setup_ui import HotspotHelperDialog
    r = _root()
    d = None
    try:
        d = HotspotHelperDialog(r, get_state_fn=lambda: {
            "proximal": {"connected": False}, "distal": {"connected": False}})
        assert d.winfo_exists()
        texts = [w.cget("text") for w in d.winfo_children()
                 if "text" in w.keys()]
        # Both LabelFrame titles appear somewhere in the dialog's widget tree.
        all_titles = [f.cget("text") for f in d.winfo_children()
                      if isinstance(f, tk.LabelFrame)]
        assert any("PC" in t for t in all_titles)
        assert any("iPhone" in t for t in all_titles)
    finally:
        if d is not None and d.winfo_exists():
            d._on_close()
        r.destroy()


def test_dialog_closes_itself_when_phone_connects():
    from network_setup_ui import HotspotHelperDialog
    r = _root()
    d = None
    try:
        d = HotspotHelperDialog(r, get_state_fn=lambda: {
            "proximal": {"connected": True}, "distal": {"connected": False}})
        d._check_and_maybe_close()
        assert not d.winfo_exists()
    finally:
        if d is not None and d.winfo_exists():
            d._on_close()
        r.destroy()


def test_dialog_stays_open_when_still_disconnected():
    from network_setup_ui import HotspotHelperDialog
    r = _root()
    d = None
    try:
        d = HotspotHelperDialog(r, get_state_fn=lambda: {
            "proximal": {"connected": False}, "distal": {"connected": False}})
        d._check_and_maybe_close()
        assert d.winfo_exists()
    finally:
        if d is not None and d.winfo_exists():
            d._on_close()
        r.destroy()


def test_dialog_pc_hotspot_button_falls_back_on_startfile_failure(monkeypatch):
    import network_setup_ui as _m
    r = _root()
    d = None
    try:
        d = _m.HotspotHelperDialog(r, get_state_fn=lambda: {
            "proximal": {"connected": False}, "distal": {"connected": False}})
        monkeypatch.setattr(_m.os, "startfile",
                             lambda *_: (_ for _ in ()).throw(OSError("no handler")))
        d._open_pc_hotspot_settings()
        assert "manually" in d._pc_helper_var.get().lower()
    finally:
        if d is not None and d.winfo_exists():
            d._on_close()
        r.destroy()


def test_dialog_close_cancels_pending_refresh():
    from network_setup_ui import HotspotHelperDialog
    r = _root()
    d = None
    try:
        d = HotspotHelperDialog(r, get_state_fn=lambda: {
            "proximal": {"connected": False}, "distal": {"connected": False}})
        assert d._after_id is not None
        d._on_close()
        assert d._after_id is None
    finally:
        if d is not None and d.winfo_exists():
            d._on_close()
        r.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_network_setup.py -v
```

Expected: the 6 `PairingWatcher` tests from Task 1 still PASS; the 8 new tests FAIL with
`ModuleNotFoundError: No module named 'network_setup_ui'`

- [ ] **Step 3: Implement `network_setup_ui.py`**

Create `network_setup_ui.py`:

```python
"""
network_setup_ui.py
====================
Tkinter-facing pieces of the hotspot-setup helper (see
docs/superpowers/specs/2026-08-07-hotspot-setup-helper-design.md).
Detection logic itself lives in network_setup.py (no Tkinter dependency).
"""
import os
import tkinter as tk

HINT_SUFFIX = " — click here for hotspot help"


def apply_stalled_hint(label: tk.Label, stalled: bool, open_dialog) -> None:
    """Toggle the click-for-help affordance on an existing status label.
    Safe to call on every poll tick regardless of the label's current text
    or prior binding state."""
    if stalled:
        current = label.cget("text")
        if not current.endswith(HINT_SUFFIX):
            label.config(text=current + HINT_SUFFIX)
        label.bind("<Button-1>", open_dialog)
        label.config(cursor="hand2")
    else:
        label.unbind("<Button-1>")
        label.config(cursor="")


class HotspotHelperDialog(tk.Toplevel):
    """Guidance popup shown when phone-to-PC IMU pairing has stalled.
    Offers PC-hosts-hotspot and phone-hosts-hotspot instructions, plus a
    manual Retry that re-checks pairing state and self-closes once a
    phone connects. Owns a self-contained refresh loop cancelled on
    close, so it cannot outlive itself or target a destroyed widget."""

    _REFRESH_MS = 1000

    def __init__(self, parent, get_state_fn):
        super().__init__(parent)
        self.title("Hotspot Setup Helper")
        self.resizable(False, False)
        self._get_state = get_state_fn
        self._after_id = None

        tk.Label(self, text="No phone has connected yet.",
                 font=("Segoe UI", 10, "bold")).pack(padx=12, pady=(12, 4))
        self._status_var = tk.StringVar(
            value="If you're on hospital/clinic wifi, it may be blocking "
                  "phone-to-PC connections. Pick a hotspot option below.")
        tk.Label(self, textvariable=self._status_var, wraplength=340,
                 justify="left", fg="gray").pack(padx=12, pady=(0, 10))

        pc_frame = tk.LabelFrame(self, text="Use this PC's hotspot")
        pc_frame.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(pc_frame, justify="left", wraplength=320,
                 text="1. Open Settings → Network & internet → Mobile hotspot\n"
                      "2. Turn it on and note the network name/password\n"
                      "3. Join that network from the phone").pack(
            anchor="w", padx=8, pady=(4, 2))
        self._pc_helper_var = tk.StringVar(value="")
        tk.Button(pc_frame, text="Open Hotspot Settings",
                  command=self._open_pc_hotspot_settings).pack(
            anchor="w", padx=8, pady=(0, 2))
        tk.Label(pc_frame, textvariable=self._pc_helper_var, fg="#B00020",
                 font=("Consolas", 8), wraplength=320,
                 justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        phone_frame = tk.LabelFrame(self, text="Use the iPhone's hotspot")
        phone_frame.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(phone_frame, justify="left", wraplength=320,
                 text="1. On the phone: Settings → Personal Hotspot → On\n"
                      "2. On this PC: click the Wi-Fi icon in the taskbar "
                      "and join the phone's network").pack(
            anchor="w", padx=8, pady=(4, 6))

        tk.Button(self, text="Retry now",
                  command=self._check_and_maybe_close).pack(pady=(0, 12))

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh()

    def _open_pc_hotspot_settings(self) -> None:
        try:
            os.startfile("ms-settings:network-mobilehotspot")
        except Exception:
            self._pc_helper_var.set(
                "Couldn't open Settings automatically — open it "
                "manually: Settings → Network & internet → Mobile hotspot.")

    def _check_and_maybe_close(self) -> None:
        try:
            st = self._get_state()
            connected = bool(st["proximal"]["connected"]
                              or st["distal"]["connected"])
        except Exception:
            connected = False
        if connected:
            self._status_var.set("Phone connected!")
            self._on_close()
        else:
            self._status_var.set(
                "Still no phone connected. Pick a hotspot option below, "
                "then retry.")

    def _refresh(self) -> None:
        self._check_and_maybe_close()
        # _check_and_maybe_close may have destroyed this window already
        # (phone connected) -- don't reschedule onto a dead widget.
        if self.winfo_exists():
            self._after_id = self.after(self._REFRESH_MS, self._refresh)

    def _on_close(self) -> None:
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        self.destroy()
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_network_setup.py -v
```

Expected: all 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add network_setup_ui.py tests/test_network_setup.py
git commit -m "feat: add HotspotHelperDialog and status-label hint toggle"
```

---

### Task 3: Integrate into `master_app.py`

**Files:**
- Modify: `master_app.py` lines 38–43 (guarded IMU import block), lines 106–115 (`__init__`
  IMU state block), lines 283–308 (`_poll_imu_status`)
- Test: `tests/test_master_app_paths.py` (append)

**Interfaces:**
- Consumes: `network_setup.PairingWatcher`, `network_setup_ui.apply_stalled_hint`,
  `network_setup_ui.HotspotHelperDialog` (both from Task 1/2)
- Produces: `MasterApp._pairing_watcher: PairingWatcher | None`,
  `MasterApp._open_hotspot_dialog(event=None) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_master_app_paths.py`:

```python
def test_poll_imu_status_shows_hotspot_hint_after_threshold(monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        monkeypatch.setattr(master_app.imu_server, "get_state", lambda: {
            "sync": {"state": "unsynced", "detail": ""},
            "running": True, "bind_error": None,
            "proximal": {"connected": False}, "distal": {"connected": False},
        })
        t = [1_000_000.0]
        monkeypatch.setattr(master_app.time, "time", lambda: t[0])

        app._poll_imu_status()
        assert "hotspot help" not in app.lbl_imu.cget("text")

        t[0] += 46
        app._poll_imu_status()
        assert "hotspot help" in app.lbl_imu.cget("text")
        assert app.lbl_imu.cget("cursor") == "hand2"
    finally:
        _teardown(app, r)


def test_poll_imu_status_clears_hotspot_hint_once_phone_connects(monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        t = [1_000_000.0]
        monkeypatch.setattr(master_app.time, "time", lambda: t[0])
        monkeypatch.setattr(master_app.imu_server, "get_state", lambda: {
            "sync": {"state": "unsynced", "detail": ""},
            "running": True, "bind_error": None,
            "proximal": {"connected": False}, "distal": {"connected": False},
        })
        app._poll_imu_status()
        t[0] += 46
        app._poll_imu_status()
        assert "hotspot help" in app.lbl_imu.cget("text")

        monkeypatch.setattr(master_app.imu_server, "get_state", lambda: {
            "sync": {"state": "synced", "detail": "ok", "offset_s": 0.0},
            "running": True, "bind_error": None,
            "proximal": {"connected": True}, "distal": {"connected": True},
        })
        app._poll_imu_status()
        assert "hotspot help" not in app.lbl_imu.cget("text")
        assert app.lbl_imu.cget("cursor") == ""
    finally:
        _teardown(app, r)


def test_hotspot_helper_absent_leaves_imu_status_unchanged(monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        monkeypatch.setattr(master_app, "_HOTSPOT_HELPER_AVAIL", False)
        t = [1_000_000.0]
        monkeypatch.setattr(master_app.time, "time", lambda: t[0])
        monkeypatch.setattr(master_app.imu_server, "get_state", lambda: {
            "sync": {"state": "unsynced", "detail": ""},
            "running": True, "bind_error": None,
            "proximal": {"connected": False}, "distal": {"connected": False},
        })
        app._poll_imu_status()
        t[0] += 46
        app._poll_imu_status()   # must not raise
        assert "hotspot help" not in app.lbl_imu.cget("text")
        assert "waiting for phones" in app.lbl_imu.cget("text")
    finally:
        _teardown(app, r)
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_master_app_paths.py -k hotspot -v
```

Expected: FAIL — `AttributeError: module 'master_app' has no attribute '_HOTSPOT_HELPER_AVAIL'`
(or the hint text never appears)

- [ ] **Step 3: Add the guarded import**

In `master_app.py`, immediately after the existing IMU guarded-import block (lines 38–43):

```python
# OLD (lines 38-43)
try:
    import pendulastic_imu_server as imu_server
    _IMU_AVAIL = True
except Exception:
    imu_server = None
    _IMU_AVAIL = False

# NEW -- add immediately after
try:
    import network_setup
    import network_setup_ui
    _HOTSPOT_HELPER_AVAIL = True
except Exception:
    network_setup = None
    network_setup_ui = None
    _HOTSPOT_HELPER_AVAIL = False
```

- [ ] **Step 4: Construct the watcher in `__init__`**

In `master_app.py`, in `MasterApp.__init__` (after line 115's IMU-start `try/except`):

```python
# OLD (lines 111-115)
        if _IMU_AVAIL:
            try:
                imu_server.start()
            except Exception:
                pass   # port busy / no network — surfaced in the status line

# NEW -- add right after
        self._pairing_watcher = (
            network_setup.PairingWatcher() if _HOTSPOT_HELPER_AVAIL else None)
```

- [ ] **Step 5: Add `_open_hotspot_dialog` and wire `_poll_imu_status`**

In `master_app.py`, add a new method near `_poll_imu_status` (e.g. immediately before it):

```python
    def _open_hotspot_dialog(self, event=None) -> None:
        network_setup_ui.HotspotHelperDialog(
            self.root, get_state_fn=imu_server.get_state)
```

Then modify `_poll_imu_status` (lines 283–308) — insert a `stalled` computation right after
`sy = st["sync"]`, and one call right after the existing `self.lbl_imu.config(text=txt, fg=col)`:

```python
    def _poll_imu_status(self):
        """Refresh the IMU status line ~2x/second while the app is open."""
        if not _IMU_AVAIL:
            return
        try:
            st = imu_server.get_state()
            sy = st["sync"]
            stalled = (self._pairing_watcher.update(st, time.time())
                       if _HOTSPOT_HELPER_AVAIL else False)
            if st.get("bind_error"):
                txt, col = f"server offline — {st['bind_error']}", "#B00020"
            else:
                n = int(st["proximal"]["connected"]) + int(st["distal"]["connected"])
                addr = f"{imu_server.get_local_ip()}:{imu_server.PORT}"
                if n == 0:
                    txt, col = f"waiting for phones — enter {addr} in app", "gray"
                elif n == 1:
                    txt, col = f"1 of 2 phones connected ({addr})", "#B36B00"
                elif sy["state"] == "synced":
                    txt, col = (f"2 phones · synced Δ{sy['offset_s']:+.3f}s "
                                f"({sy['detail']})", "#1B7F3B")
                else:
                    txt, col = (f"2 phones · {sy['state']} — {sy['detail']}",
                                "#B36B00")
            self.lbl_imu.config(text=txt, fg=col)
            if _HOTSPOT_HELPER_AVAIL:
                network_setup_ui.apply_stalled_hint(
                    self.lbl_imu, stalled, self._open_hotspot_dialog)
        except Exception:
            pass
        self._sync_after_id = self.root.after(500, self._poll_imu_status)
```

Every line inside the `if st.get("bind_error"): ... else: ...` block is unchanged from the current
file — only the `stalled` computation and the final `apply_stalled_hint` call are new.

- [ ] **Step 6: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_master_app_paths.py -k hotspot -v
```

Expected: all 3 new tests PASS

- [ ] **Step 7: Run the full master_app test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_master_app_paths.py tests\test_master_app_camera_utils.py -v
```

Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add master_app.py tests/test_master_app_paths.py
git commit -m "feat: surface hotspot-setup hint on master_app.py's IMU status label"
```

---

### Task 4: Integrate into `pendulastic_app.py`

**Files:**
- Modify: `pendulastic_app.py` lines 30–35 (guarded IMU import block), around line 2187 (`App.__init__`
  IMU-start block), lines 3027–3049 (`App._tick` IMU flex-axis block)
- Test: `tests/test_app.py` (append)

**Interfaces:**
- Consumes: same as Task 3
- Produces: `App._pairing_watcher: PairingWatcher | None`, `App._open_hotspot_dialog(event=None) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_tick_shows_hotspot_hint_after_stalled_threshold(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        t = [1_000_000.0]
        monkeypatch.setattr(_m.time, "time", lambda: t[0])
        monkeypatch.setattr(_m._imu, "get_state", lambda: {
            "proximal": {"connected": False, "hz": 0.0},
            "distal":   {"connected": False, "hz": 0.0},
            "flex_axis_captured": False, "flex_axis_armed": False,
            "running": True, "bind_error": None,
        })

        app._tick()
        assert "hotspot help" not in app._acq.lbl_method_status.cget("text")

        t[0] += 46
        app._tick()
        text = app._acq.lbl_method_status.cget("text")
        assert "no phone connected" in text
        assert "hotspot help" in text
        assert app._acq.lbl_method_status.cget("cursor") == "hand2"
    finally:
        app.destroy()


def test_tick_stalled_check_does_not_override_flex_axis_status(monkeypatch):
    """A connected sensor can never simultaneously be 'stalled' (zero
    connected) -- confirms the two code paths stay mutually exclusive."""
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        monkeypatch.setattr(_m.time, "time", lambda: 1_000_000.0)
        monkeypatch.setattr(_m._imu, "get_state", lambda: {
            "proximal": {"connected": False, "hz": 0.0},
            "distal":   {"connected": True, "hz": 100.0},
            "flex_axis_captured": True, "flex_axis_armed": False,
            "running": True, "bind_error": None,
        })
        app._tick()
        assert "Axis locked" in app._acq.lbl_method_status.cget("text")
        assert "hotspot help" not in app._acq.lbl_method_status.cget("text")
    finally:
        app.destroy()


def test_hotspot_helper_absent_does_not_break_tick(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        monkeypatch.setattr(_m, "_HOTSPOT_HELPER_AVAIL", False)
        app._active_sources = ["imu"]
        app._state = "idle"
        monkeypatch.setattr(_m._imu, "get_state", lambda: {
            "proximal": {"connected": False, "hz": 0.0},
            "distal":   {"connected": False, "hz": 0.0},
            "flex_axis_captured": False, "flex_axis_armed": False,
        })
        app._tick()   # must not raise
        assert "hotspot help" not in app._acq.lbl_method_status.cget("text")
    finally:
        app.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_app.py -k hotspot -v
```

Expected: FAIL — `AttributeError: module 'pendulastic_app' has no attribute '_HOTSPOT_HELPER_AVAIL'`

- [ ] **Step 3: Add the guarded import**

In `pendulastic_app.py`, immediately after the existing IMU guarded-import block (lines 30–35):

```python
# OLD (lines 30-35)
try:
    import pendulastic_imu_server as _imu
    _IMU_AVAIL = True
except Exception:
    _imu = None
    _IMU_AVAIL = False

# NEW -- add immediately after
try:
    import network_setup
    import network_setup_ui
    _HOTSPOT_HELPER_AVAIL = True
except Exception:
    network_setup = None
    network_setup_ui = None
    _HOTSPOT_HELPER_AVAIL = False
```

- [ ] **Step 4: Construct the watcher in `App.__init__`**

In `pendulastic_app.py`, in `App.__init__`, right after the existing IMU-start block:

```python
# OLD
        if _IMU_AVAIL:
            try:
                _imu.start()
            except Exception:
                pass

# NEW -- add right after
        self._pairing_watcher = (
            network_setup.PairingWatcher() if _HOTSPOT_HELPER_AVAIL else None)
```

- [ ] **Step 5: Add `_open_hotspot_dialog` and wire `_tick`**

Add a new method to `App` (e.g. near `_tick`):

```python
    def _open_hotspot_dialog(self, event=None) -> None:
        network_setup_ui.HotspotHelperDialog(self, get_state_fn=_imu.get_state)
```

Modify the IMU flex-axis block inside `_tick` (lines 3027–3049):

```python
        # Flip label when flex axis transitions from armed → captured
        if (_IMU_AVAIL and "imu" in self._active_sources
                and self._state in ("idle", "recording")):
            try:
                st = _imu.get_state()
                stalled = (self._pairing_watcher.update(st, time.time())
                           if _HOTSPOT_HELPER_AVAIL else False)
                # Low gyro rate makes AHRS integration unreliable regardless of
                # flex-axis state -- surface it first. Same threshold/message
                # pattern already used in pendulastic_viewer.py.
                slow = [d for d in (st["proximal"], st["distal"])
                        if d["connected"] and 0 < d.get("hz", 0) < _imu.MIN_USABLE_HZ]
                if stalled:
                    self._acq.lbl_method_status.config(
                        text="⚠ no phone connected", fg="#B00020")
                elif slow:
                    hz = min(d["hz"] for d in slow)
                    self._acq.lbl_method_status.config(
                        text=f"⚠ gyro only {hz:.0f} Hz — set the app's update "
                             f"interval to 10 ms (≥{_imu.MIN_USABLE_HZ:.0f} Hz needed)",
                        fg="#D97706")
                elif st.get("flex_axis_captured"):
                    self._acq.lbl_method_status.config(
                        text="● Axis locked — sagittal tracking", fg="green")
                elif st.get("flex_axis_armed"):
                    self._acq.lbl_method_status.config(
                        text="⚡ Flex once to capture axis...", fg="#B36B00")
                if _HOTSPOT_HELPER_AVAIL:
                    network_setup_ui.apply_stalled_hint(
                        self._acq.lbl_method_status, stalled,
                        self._open_hotspot_dialog)
            except Exception:
                pass
```

The `slow` / `flex_axis_captured` / `flex_axis_armed` branches and their bodies are byte-for-byte
unchanged from the current file — only the `stalled` computation, the new `if stalled:` branch
(placed first since it takes priority when true), and the final `apply_stalled_hint` call are new.

- [ ] **Step 6: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_app.py -k hotspot -v
```

Expected: all 3 new tests PASS

- [ ] **Step 7: Run the full app test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_app.py tests\test_acquisition_panel.py -v
```

Expected: all pass (tkinter singleton flake may appear if all files run together — run suites
individually if needed, per existing convention in this repo's other plans)

- [ ] **Step 8: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: surface hotspot-setup hint on pendulastic_app.py's IMU status label"
```

---

## Self-Review

**Spec coverage:**
- §3 Architecture (two files, no new `.after()` loop beyond the dialog's own) → Tasks 1, 2, 3, 4 ✓
- §4 Detection logic (`PairingWatcher`, all the resolved edge cases: one-connected-resets,
  running/bind_error gating) → Task 1 ✓
- §5 Trigger placement & dialog UX (reused label, `apply_stalled_hint`, both hotspot paths, Retry,
  self-contained refresh loop) → Task 2 ✓
- §6 Integration (exact hook points in both apps, guarded import) → Tasks 3, 4 ✓
- §7 Error handling & testing (fail-soft `os.startfile`, guarded-import-absent test per app) →
  Tasks 2, 3, 4 ✓
- §8 Out of scope — no task implements PC hotspot auto-toggle, onboarding, or a proactive
  heuristic; confirmed absent by design.

**Placeholder scan:** No TBDs; every step has complete, runnable code.

**Type consistency:**
- `PairingWatcher.update(imu_state, now) -> bool` (Task 1) used identically in Task 3
  (`master_app.py`) and Task 4 (`pendulastic_app.py`) ✓
- `apply_stalled_hint(label, stalled, open_dialog)` (Task 2) called identically in Tasks 3 and 4 ✓
- `HotspotHelperDialog(parent, get_state_fn)` (Task 2) constructed with `imu_server.get_state`
  (Task 3, module alias `imu_server`) and `_imu.get_state` (Task 4, module alias `_imu`) —
  correct per-file alias, not conflated ✓
- `_HOTSPOT_HELPER_AVAIL` guard name identical across both integration tasks ✓
