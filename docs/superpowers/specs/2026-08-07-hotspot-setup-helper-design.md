# Hotspot Setup Helper — Design Spec

## 1. Goal

Reduce the friction of getting an iPhone IMU sensor paired to a recording PC when both devices are
on a network that blocks device-to-device traffic (e.g. hospital/clinic guest wifi with AP
isolation), by detecting the stuck state and guiding the operator — who may be a non-technical
clinician, not a developer — to a working hotspot setup, instead of leaving them staring at a
"waiting for phones" label with no next step.

## 2. Background

Today, `master_app.py` and `pendulastic_app.py` both embed `pendulastic_imu_server` as an optional
WebSocket server (guarded import, `_IMU_AVAIL`). The operator is told to enter
`{get_local_ip()}:{PORT}` into the iPhone's Sensor Stream app (`master_app.py`'s
`_poll_imu_status`, mirrored by a live status update inside `pendulastic_app.py`'s `App._tick`).
This works when the PC and phone share an unrestricted LAN, but silently fails — connection just
never happens — on networks with client isolation (common on hospital/clinic guest wifi). There is
currently no guidance for this failure mode; the operator only sees "waiting for phones."

**Scope decisions from brainstorming:**
- This spec covers only the **per-recording device-connection** flow, not first-time
  install/onboarding (separate future spec).
- The pain point is specifically **network/phone pairing** — camera selection and OptiTrack sync
  are not in scope.
- The target user is a **non-technical clinician**, so guidance must be self-explanatory.
- The app **recommends both hotspot options (PC-hosts or phone-hosts) and lets the user pick** —
  it does not prescribe one.
- **`master_app.py` must not be disturbed** — this feature must not displace, resize, or restyle
  any existing widget. The same constraint is applied to `pendulastic_app.py` for consistency.
- **No hotspot automation in this pass.** Both paths are guidance/deep-link only. One-click
  toggle via the WinRT `NetworkOperatorTetheringManager` API is an explicit, separate follow-up
  spec once this guidance flow is validated in real clinic use.

**Revision note:** this design went through a Codex review round. The original draft proposed (a)
a startup network heuristic that probed local broadcast/multicast traffic to guess "isolated
network," and (b) a new `Frame` gridded into both apps' layouts. Codex's review found both
unworkable: Sensor Stream phones are WebSocket *clients* with no discovery responder, so there is
nothing for a broadcast probe to detect — "no peers reply" is the normal, healthy-network case, not
a signal of isolation. And `master_app.py` is a fixed `480x900`, non-resizable window, so a gridded
instruction panel would clip or displace existing controls, directly violating "must not disturb
master_app.py." Both are redesigned below: the heuristic is dropped in favor of a single,
symptom-based signal (sustained zero-phone pairing), and the trigger surface reuses an existing,
already-dynamic status label instead of adding new grid real estate. Codex also flagged that the
original spec understated integration effort ("two lines") and left several state transitions
undefined; both are addressed in §5–§6 below with concrete hook points and an explicit state
contract.

## 3. Architecture

Two new files, both at the repo root alongside `pendulastic_imu_server.py`:

- **`network_setup.py`** — pure Python, no Tkinter import, no dependency on either app module.
  Holds `PairingWatcher`, the only piece of stateful detection logic (see §4). Fully unit-testable
  with plain dicts and injected timestamps.
- **`network_setup_ui.py`** — Tkinter-dependent. Holds `HotspotHelperDialog(tk.Toplevel)` (the
  guidance popup, see §5) and `bind_status_hint(label, watcher, get_state_fn)`, a small helper
  each app calls once at startup to wire a `PairingWatcher` to one of its existing status labels.

This resolves the earlier draft's self-contradiction (a "no Tkinter dependency" module that then
defined a `tk.Frame`) by keeping the two concerns in separate files.

Neither app needs a new polling loop. `master_app.py` already polls IMU state every 500ms in
`_poll_imu_status` (writing to `self.lbl_imu`); `pendulastic_app.py` already polls it inside
`App._tick`'s IMU flex-axis block (writing to `self._acq.lbl_method_status`, `pendulastic_app.py`
lines 3027–3049). `bind_status_hint` is called from inside those *existing* call sites, not as a
new independent loop — so there is no new `.after()` lifecycle to manage and no risk of a
polling loop outliving the widget it targets.

## 4. Detection Logic (revised — heuristic dropped)

The only signal is **sustained zero-phone pairing**, tracked by `PairingWatcher`, a small stateful
pure-Python class (no I/O, no Tkinter):

```python
class PairingWatcher:
    def __init__(self, threshold_s: float = 45.0):
        self._threshold_s = threshold_s
        self._first_zero_ts: float | None = None

    def update(self, imu_state: dict, now: float) -> bool:
        """Call on every existing IMU-status poll. Returns True once both
        sensors have been disconnected for threshold_s, given the server is
        actually up. Resets the instant either sensor connects or the
        server isn't in a healthy running state."""
        healthy_but_disconnected = (
            imu_state.get("running") and not imu_state.get("bind_error")
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

This directly resolves the review's specific gaps:
- **"Waited 45 seconds" from when?** From the first poll that observes both sensors disconnected
  while the server is running and unbound-error — i.e. from whenever the host app's own existing
  gate (`_IMU_AVAIL and self.var_record_imu.get()` in `master_app.py`; `_IMU_AVAIL and "imu" in
  self._active_sources` in `pendulastic_app.py`) is already polling. No new "session start" concept
  is introduced.
- **Server not running / bind error** is explicitly excluded (`imu_state.get("running")` /
  `not imu_state.get("bind_error")`), so a not-yet-started or failed-to-bind server does not read as
  "network is blocking the phone."
- **One phone connected is not a stalled state.** `_first_zero_ts` resets the instant either
  sensor connects. A single connected phone is proof the network *isn't* blocking device-to-device
  traffic — whether that's sufficient for a usable recording is a separate, already-existing
  concern (`master_app.py`'s `_imu_ready()` already handles that with its own "no phone streaming"
  message; this feature doesn't touch that check).
- **Retry / reconnect cycles** are handled for free by the reset-on-connect behavior — no separate
  timer-reset logic is needed.
- **Unit-testable as stated**: `PairingWatcher().update(fake_state, fake_now)` needs no mocks,
  no sockets, no display.

The heuristic-based "soft banner on startup" from the original draft is cut entirely — there is no
reliable signal available before a pairing attempt actually stalls, and Codex's review confirmed
the proposed one was invalid on its own terms.

## 5. Trigger Placement & Dialog UX (revised — no new grid widget)

**Trigger:** `bind_status_hint` does not add any widget. When `PairingWatcher.update()` returns
`True`, the existing status label's text gets a fixed suffix appended
(`" — click here for hotspot help"`) and a `<Button-1>` binding is (re)attached that opens
`HotspotHelperDialog`. When it returns `False`, the suffix is absent and the binding is removed
(`label.unbind("<Button-1>")`) — a no-op if nothing was bound. This is the same idempotent
config-every-poll pattern both apps already use for their status labels, so it introduces no new
UI state to track. `master_app.py`'s window geometry, widget count, and layout are byte-for-byte
unchanged; the only difference the operator sees is conditional label text and, only when
relevant, a clickable cursor.

**Dialog (`HotspotHelperDialog`, a `tk.Toplevel`):** opened on click. Presents two options, both
funneling back to the existing IP-entry step — nothing about the pairing mechanism itself changes:

- **"Use this PC's hotspot"** — numbered instructions (Settings → Network & internet → Mobile
  hotspot → On) plus a button that calls `os.startfile("ms-settings:network-mobilehotspot")`
  (a documented Settings URI). The button's own label and adjacent text are explicit that this
  only *opens Settings* — it does not claim the PC is capable of hosting a hotspot, since that
  depends on Windows edition/hardware/policy the app cannot detect. On any exception, the button's
  helper text swaps to "couldn't open Settings automatically — open it yourself: Settings →
  Network & internet → Mobile hotspot."
- **"Use the iPhone's hotspot"** — instructions to enable Personal Hotspot on the phone and join it
  from the PC's normal Windows Wi-Fi picker (taskbar Wi-Fi icon). No deep-link is used for this
  path — the original draft's `ms-availablenetworks:` URI is not a documented `ms-settings:`
  scheme and Codex confirmed `os.startfile` can't verify a picker actually appeared, so it's
  dropped rather than shipped as a broken automation.
- **"Retry now"** — re-reads `pendulastic_imu_server.get_state()` immediately and updates the
  dialog's own text; if a phone is now connected, the dialog closes itself. This only affects the
  dialog's own display — it does not need to reach into the host app's `PairingWatcher`, since that
  watcher's state resets naturally the moment the next host-app poll observes a connection.

The dialog owns a `.after(1000, self._refresh)` loop scoped entirely to its own lifetime: started
in `__init__`, and explicitly cancelled (`self.after_cancel(...)`) in a `WM_DELETE_WINDOW` handler
before the window is destroyed. This is the only new `.after()` loop in the whole feature, and its
lifecycle is fully contained within the popup it belongs to — it cannot outlive or target a
destroyed widget, since closing the dialog is what cancels it.

## 6. Integration into master_app.py and pendulastic_app.py

Both integrations are small but not the "two lines" the original draft claimed. Concretely:

**`master_app.py`** — inside `_poll_imu_status` (lines 283–308), after the existing
`n = int(st["proximal"]["connected"]) + int(st["distal"]["connected"])` branch that produces
`txt, col` for the `n == 0` case, call `self._pairing_watcher.update(st, time.time())`; if `True`,
append the click-hint suffix to `txt` and call
`network_setup_ui.bind_status_hint(self.lbl_imu, open_dialog=self._open_hotspot_dialog)` (or
equivalent inline bind/unbind — the exact split between `network_setup_ui.py` helper vs. inline
code is an implementation-plan decision, not a design-level one). `self._pairing_watcher =
network_setup.PairingWatcher()` is constructed once in `App.__init__`, guarded by the same
`_HOTSPOT_HELPER_AVAIL` import flag as the rest of the feature.

**`pendulastic_app.py`** — inside `App._tick`'s existing IMU flex-axis `if/elif` chain (lines
3027–3049, the block that already branches on `slow` / `flex_axis_captured` / `flex_axis_armed`
to override `self._acq.lbl_method_status`), add a further branch checking
`self._pairing_watcher.update(st, time.time())`, mutually exclusive with the existing branches in
practice (they all require a connection; this one requires the absence of one). This reuses the
exact status-ownership pattern already established in that block rather than introducing a new one.
`self._pairing_watcher` is constructed once in `App.__init__`, same guard pattern.

Both apps import guarded:
```python
try:
    from network_setup import PairingWatcher
    import network_setup_ui
    _HOTSPOT_HELPER_AVAIL = True
except Exception:
    _HOTSPOT_HELPER_AVAIL = False
```
If import fails, `self._pairing_watcher` is never constructed and the existing `if _IMU_AVAIL:`
polling code runs exactly as it does today — the feature is absent, nothing else changes.

## 7. Error Handling & Testing

- `PairingWatcher.update()` does no I/O and cannot raise on well-formed input; callers already
  wrap their surrounding poll blocks in `try/except` (both apps do this today), so a malformed
  `imu_state` shape degrades the same way any other polling exception already does.
- `os.startfile(...)` in the dialog is wrapped; failure replaces the button's helper text rather
  than raising a dialog or crashing.
- The dialog's own refresh loop is guarded (`try/except` around `get_state()` inside `_refresh`) so
  a transient error doesn't kill the popup — it just skips that refresh tick.
- The whole feature is optional per §6's guarded import — if `network_setup.py` or
  `network_setup_ui.py` fails to import, neither app is affected beyond the hint never appearing.

Testing:
- `PairingWatcher` — plain unit tests: zero-connected-under-threshold, zero-connected-over-
  threshold, one-connected-resets, `running=False` never counts, `bind_error` set never counts,
  reconnect-then-disconnect starts a fresh timer.
- `HotspotHelperDialog` — headless Tkinter tests in the same style already used by
  `tests/test_acquisition_panel.py` in this suite (construct, drive state via injected fakes,
  assert button text/visibility). This repo's existing Tk tests already assume a real display is
  available in the environment they run in; this feature makes no new claim about headless/CI
  feasibility beyond what that existing suite already relies on.
- One test per app confirming `_HOTSPOT_HELPER_AVAIL = False` (patched import failure) leaves the
  existing IMU status behavior byte-for-byte unchanged — the actual verification of "purely
  additive."

## 8. Out of Scope (explicitly deferred)

- One-click PC hotspot toggle via the WinRT `NetworkOperatorTetheringManager` API — follow-up spec
  once this guidance flow is validated.
- First-time install/onboarding (Python env, dependencies, OptiTrack/Motive setup) — separate spec.
- Any change to the broader pre-recording checklist (camera selection, OptiTrack sync) — those are
  not reported as pain points today.
- Any proactive "this network looks isolated" signal before a pairing attempt actually stalls — no
  reliable signal was found; see the revision note in §2.
