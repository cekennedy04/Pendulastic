# 7-day storage eviction soak test

## Why

WebKit deletes all script-writable storage — IndexedDB, localStorage, Cache
API, service worker registrations — after 7 days without user interaction with
the site. Apple documents an exemption for sites added to the Home Screen.
**That exemption is the only thing standing between a clinician and losing a
month of participant records, and this project has never verified it.**

The design deliberately does not depend on it (IndexedDB is a cache, the
exported files are the record, and a session cannot close unexported). This
test measures how much the exemption actually buys.

## Before you start

This app has no built-in participant picker yet (that is a future unit).
Every trial you record is attributed automatically to a single fixed on-device
test participant (`clinic_patient_id: "TEST-PARTICIPANT"`) — there is nothing
to type in and no id to choose. You also need a way to get the app onto the
phone in the first place and, on day 8+, a way to look inside its storage,
since the app has no screen that lists past sessions or trials.

**You will need:**
- An iPhone.
- A laptop on the same Wi-Fi network as the phone, to run the dev server.
- A Mac (can be the same laptop, or a different one) with Safari, and a cable,
  to open Web Inspector against the phone on day 8+. There is no way to
  inspect IndexedDB from the phone alone.

## Protocol

1. **Get the app onto the phone.** On the laptop, from the repo root, run
   `npm run build:wasm` once if `webapp/src/wasm/` doesn't exist yet, then
   start the dev server:

   ```
   miniconda3/python.exe webapp/dev_server.py
   ```

   Scan the printed QR code with the phone's camera, or type the printed
   `https://<lan-ip>:8900/` URL into Safari. Safari will warn about the
   self-signed certificate: Show Details → visit this website → Visit
   Website (asked once per host:port).

2. **Install to the Home Screen.** Tap Share → Add to Home Screen, then close
   Safari and open Pendulastic from its new Home Screen icon (not from
   Safari's tab). Confirm the install gate is doing its job: the "Install
   before recording" panel must stay hidden and the **Start trial** button
   must be visible immediately — if the gate panel appears instead, the
   standalone check failed and nothing past this point is testing what it
   should.

3. **Record at least two trials.** Tap Start, hold the limb still until the
   gate reads READY, release, let it settle, and tap Stop — repeat once more.
   Both trials are saved automatically to IndexedDB as they score; no explicit
   save step exists or is needed.

4. **Export the session.** In the session bar at the bottom, tap **Export
   session**. This is the durability gate itself, not a convenience: it reads
   both trials back out of IndexedDB, hands two `.jsonl` files plus one
   manifest `.json` to the share sheet (or downloads them if the share sheet
   isn't available), and only marks the session exported — enabling **Close
   session** — once that hand-off actually succeeded. Watch for "Session
   exported." in the status line below the buttons; if it instead says a trial
   was recorded during export or that export failed, export again before
   continuing. Actually save the shared/downloaded files somewhere off the
   phone (AirDrop, Files, email) — they are the real record regardless of
   what the rest of this test finds.

5. **Note the date and iOS version** in the table below.

6. **Do not open the app for at least 8 days.** Using the phone normally is
   fine and is the point — the clock is site inactivity, not device
   inactivity. Do not rebuild or redeploy the app during this window; a
   changed `BUILD_ID` would create a fresh cache on the next visit and you'd
   no longer be able to tell whether the *old* cache survived.

7. **On day 8+, test the cache first, before touching IndexedDB.** Put the
   phone in Airplane Mode (or turn off both Wi-Fi and Cellular) — the dev
   server does not need to be running for this and should not be relied on to
   be. With the phone fully offline, open Pendulastic from its Home Screen
   icon. If the app's UI loads (banner, gates, Start button) with no network
   path available at all, the service worker cache demonstrably survived —
   this is the only way to tell "cache survived" apart from "reloaded from
   the network," since with Wi-Fi on you cannot distinguish the two by
   looking at the screen.

8. **Then check IndexedDB**, still without needing the dev server: connect the
   phone to a Mac by cable, enable Settings → Safari → Advanced → Web
   Inspector on the phone if not already on, and open the phone's Pendulastic
   tab from Safari's Develop menu on the Mac. In the inspector's Storage tab,
   open IndexedDB → `pendulastic` → `patients` / `sessions` / `trials`.

## Record the result

- Is the `TEST-PARTICIPANT` patient record present, with one session and both
  trial records under it?
- For one trial record, expand its `trajectory` field. It is a plain object
  (`{t, angle_deg, release_idx, peak_idx, trough_idx, neutral_deg}`, **not**
  an ArrayBuffer, despite what an earlier draft of this test assumed) — confirm
  `t` and `angle_deg` are still populated arrays of numbers, not empty or
  `null`. This checks that the full per-sample series survived, not just the
  20 scored parameter fields, which is a real distinction: they're written to
  IndexedDB in the same `put()` but a partial or corrupted large field would
  not necessarily show up if you only glanced at `params`.
- Did the app load from Airplane Mode in step 7 (cache survived), separately
  from whether the IndexedDB records survived? These can disagree — Apple's
  eviction is documented as clearing all of an origin's script-writable
  storage together, so in principle they shouldn't, but that assumption is
  exactly what this test exists to check.

| Date started | iOS version | Date reopened | Records survived | Cache survived (Airplane Mode load) |
|---|---|---|---|---|
| | | | | |

## If it fails

The exemption does not hold on this iOS version. That is a finding, not a
defect to fix in this app: raise the export gate from once-per-session to
once-per-trial, and say so in the clinician-facing instructions. Installing to
the Home Screen does not, on its own, promise that data is safe — the export
files are what makes it safe, and this test only measures how much of a
convenience the install exemption buys on top of that.
