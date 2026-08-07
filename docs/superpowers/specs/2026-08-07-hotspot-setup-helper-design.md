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
`_poll_imu_status`, mirrored in `pendulastic_app.py`). This works when the PC and phone share an
unrestricted LAN, but silently fails — connection just never happens — on networks with
client-isolation (common on hospital/clinic guest wifi). There is currently no guidance for this
failure mode; the operator only sees "waiting for phones."

**Scope decisions from brainstorming:**
- This spec covers only the **per-recording device-connection** flow, not first-time
  install/onboarding (separate future spec).
- The pain point is specifically **network/phone pairing** — camera selection and OptiTrack sync
  are not in scope.
- The target user is a **non-technical clinician**, so guidance must be self-explanatory.
- The app **recommends both hotspot options (PC-hosts or phone-hosts) and lets the user pick** —
  it does not prescribe one.
- **`master_app.py` must not be disturbed** — this feature is purely additive there (new widget,
  no changes to existing code paths). The same constraint is applied to `pendulastic_app.py` for
  consistency, even though it's less critical there.
- **No hotspot automation in this pass.** The PC-hosts path deep-links to Windows' Mobile Hotspot
  settings page; it does not programmatically toggle the WinRT `NetworkOperatorTetheringManager`
  API. One-click toggle is an explicit, separate follow-up spec once this guidance flow is
  validated in real clinic use.

## 3. Architecture

A new standalone module, `network_setup.py` (repo root, alongside `pendulastic_imu_server.py`),
owns all detection logic. It has no Tkinter dependency and no dependency on either app module, so
it can be unit-tested in isolation and imported by both apps without coupling them together.

A companion `HotspotHelperPanel` (a `tk.Frame` subclass, defined in the same file) is the only
piece either app actually instantiates. Each app adds exactly two lines: construct the panel,
grid it below the existing IMU status widgets. The panel owns its own `.after()` polling loop
against `pendulastic_imu_server.get_state()` (already a public function) — it does not require
either app's existing `_poll_imu_status`/tick loop to push data into it or call anything on it.

```
network_setup.py
├── check_network_heuristic() -> (suspicious: bool, reason: str)   # pure-ish, catches its own errors
├── pairing_stalled(imu_state: dict, waited_s: float, threshold_s: float = 45) -> bool  # pure
└── HotspotHelperPanel(tk.Frame)                                   # UI, uses the two functions above
```

`master_app.py` / `pendulastic_app.py` changes are limited to:
```python
try:
    from network_setup import HotspotHelperPanel
    _HOTSPOT_HELPER_AVAIL = True
except Exception:
    _HOTSPOT_HELPER_AVAIL = False
...
if _HOTSPOT_HELPER_AVAIL:
    self.hotspot_panel = HotspotHelperPanel(self.root)  # or parent frame
    self.hotspot_panel.grid(row=<next row>, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
```
Both apps already use exactly this guarded-import + optional-widget pattern for other optional
features (`_IMU_AVAIL`, `_tuner`, etc.), so this introduces no new pattern.

## 4. Detection Logic

Two independent signals, either of which can surface the panel:

- **Heuristic hint** — `check_network_heuristic()` runs once at app startup and again whenever the
  panel is (re)shown. It compares default-gateway reachability against a local
  broadcast/multicast probe on the active interface: gateway reachable but no broadcast peers
  respond is treated as "possibly isolated network." This is a heuristic, not a certainty — any
  exception or ambiguous result returns `suspicious=False` (never shown), so it can only add a
  hint, never a false alarm that blocks anything.
- **Pairing-stalled (hard trigger)** — `pairing_stalled(imu_state, waited_s, threshold_s=45)` is a
  pure function: given the same `st["proximal"]["connected"]` / `st["distal"]["connected"]` shape
  `master_app.py` already reads from `imu_server.get_state()`, and how long the app has been
  waiting, it returns `True` once 0 phones have connected past 45 seconds. No changes to
  `pendulastic_imu_server.py` are needed — this reuses its existing public state shape.

**Panel visibility state machine:**
- Default: hidden.
- Heuristic hint at startup → soft, dismissible banner ("This network may block phone
  connections — need help?"). Dismissing it suppresses the banner for the rest of that app
  session (not persisted across restarts).
- `pairing_stalled() == True` → panel auto-expands to the full guidance view, regardless of
  whether the banner was dismissed, since this means recording is actually blocked right now.

## 5. Panel Content & UX

The expanded panel presents two options side by side, both funneling back into the existing
IP-entry step — nothing about the pairing mechanism itself changes:

- **"Use this PC's hotspot"** — numbered instructions (Settings → Network → Mobile hotspot → On)
  plus a button that runs `os.startfile("ms-settings:network-mobilehotspot")` to jump straight to
  that settings page, with a reminder to note the SSID/password shown there and join it from the
  phone's wifi list.
- **"Use the iPhone's hotspot"** — instructions to enable Personal Hotspot on the phone, plus a
  button that opens the PC's wifi picker (`os.startfile("ms-availablenetworks:")`, falling back to
  `"ms-settings:network-wifi"` if that URI scheme isn't handled) to join the phone's network from
  the PC side.
- **"Retry"** — re-runs `check_network_heuristic()` and re-reads `imu_server.get_state()`
  immediately, rather than waiting for the next poll tick, and collapses the panel back to hidden
  if a phone is now connected.

## 6. Error Handling & Testing

Every external call in `network_setup.py` fails soft — this must never crash or block a recording:
- Socket probes (heuristic) → any exception → `suspicious=False`.
- `os.startfile(...)` → any exception → caught, replaced inline with plain text ("couldn't open
  settings automatically — go to Settings → Network → Mobile hotspot manually") next to the
  button, no dialog, no crash.
- The panel itself is entirely optional per §3's guarded import — if `network_setup.py` fails to
  import for any reason, neither app is affected beyond the panel not appearing.

Testing:
- `pairing_stalled()` — plain unit tests with fake state dicts (0 connected/under threshold, 0
  connected/over threshold, 1+ connected at any time).
- `check_network_heuristic()` — unit tests with the socket layer mocked, covering both the
  "looks isolated" and "looks fine" branches, plus an exception-path test asserting
  `suspicious=False`.
- `HotspotHelperPanel` — headless Tkinter tests in the style of `tests/test_acquisition_panel.py`
  (construct, drive state via injected fakes, assert banner/panel visibility and button text) in
  a new `tests/test_network_setup.py`.
- One integration test per app confirming the guarded import truly no-ops when
  `network_setup` is patched to raise on import — i.e. the rest of the app still constructs
  successfully.

## 7. Out of Scope (explicitly deferred)

- One-click PC hotspot toggle via the WinRT `NetworkOperatorTetheringManager` API — follow-up spec
  once this guidance flow is validated.
- First-time install/onboarding (Python env, dependencies, OptiTrack/Motive setup) — separate spec.
- Any change to the broader pre-recording checklist (camera selection, OptiTrack sync) — those are
  not reported as pain points today.
