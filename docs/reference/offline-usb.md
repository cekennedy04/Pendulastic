# Running Pendulastic without internet (USB drive)

Status: design + prototype (`npm run build:usb`), 2026-11-16. Not tested on a
real phone yet. See "Not verified" below.

## What the app needs from the device

Read from `webapp/src/capture.js`, `app.js`, `worker.js`, `index.html`:

- **Motion sensors.** These come from `DeviceMotionEvent` on the main thread.
  The sensor is the phone's, so capture only works on a phone: iOS Safari
  (installed or not) or Android Chrome. Laptops have no motion sensor and can
  only review and export trials.
- **A secure context.** DeviceMotion is "available only in secure contexts"
  ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/DeviceMotionEvent);
  the [W3C spec](https://w3c.github.io/deviceorientation/) says "only
  available in secure contexts"). On iOS, `requestPermission()` also needs a
  user gesture ("requires transient activation",
  [MDN](https://developer.mozilla.org/en-US/docs/Web/API/DeviceMotionEvent/requestPermission_static)).
  The app already calls it from the Start tap.
- **Wasm**, fetched by wasm-bindgen's `init()` in the capture worker, and
  lazily on the main thread for Trends.
- **A module Worker** (`new Worker(new URL('./worker.js', import.meta.url), {type:'module'})`).
- **A service worker** for offline use. It caches the whole shell, wasm
  included (`SHELL` in `src/build-id.js`).
- **IndexedDB** for trials, sessions and MAS records. **localStorage** is used
  only for the research-mode flag.
- **Network.** The only call is `fetch('/upload')` behind "send to laptop", a
  dev-server feature. Without it, it fails with an on-screen message. Nothing
  else touches the network.

## Options considered

| Option | iPhone | Android phone | Laptop | Verdict |
|---|---|---|---|---|
| **(3) Install once online, then offline.** Open the URL once online and Add to Home Screen; the service worker serves everything afterwards | Works | Works | Works | **Primary.** Verified offline on desktop Chromium: SW cache holds all 32 shell files incl. the `.wasm`; after going offline a reload boots, shows the build line and initialises wasm. Not yet re-checked on a phone, but this is the path phones already use online |
| **(1) Single self-contained HTML on the drive** (JS, CSS, wasm inlined; no SW) | **No.** iOS cannot open a local HTML file in Safari. The Files app previews it without running scripts (widely reported, no official Apple source found) | Probably. Chrome opens it from the phone's storage. Capture is **unverified**: Chrome may open it as `content://`, and whether that counts as a secure context with IndexedDB was not confirmed | Works (review/export) | **Built as the USB fallback** for Android and laptops |
| **(2) Laptop on the drive running a local server plus a hotspot** | **No** | **No** | n/a | `http://192.168.x.x` is not a secure context (MDN lists only `https`, `wss`, `file`, `localhost` and `127.0.0.0/8`). So no DeviceMotion and no service worker. `localhost` helps only on the laptop itself. Self-signed HTTPS (see `spike_wss_selfsigned.py`) would mean installing and trusting a certificate on every phone. Rejected |
| **(4) Android APK/TWA sideloaded from USB** | No | Yes | No | Viable later for Android-only offline sites. A TWA needs a hosted origin and Digital Asset Links, and an APK means signing and an "unknown sources" install. Not built |

Why dist/ cannot just be copied to a drive: from `file://`, module scripts fail
("module requests use CORS, and `file://` URLs are never same-origin",
[MDN](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules/Modules_on_the_web)).
`fetch()` of the `.wasm` and the module worker fail for the same reason.
`serviceWorker.register()` accepts only `http(s)`
([MDN](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerContainer/register)).
Chromium does treat `file:///` as a secure context, which the build relies on
(`isSecureContext === true`, checked).

**Recommendation.** Install every phone once while it has internet: that
covers iOS and Android and is the only route for iPhones. Carry the USB file
for Android phones and laptops that were never installed, and treat it as
unverified for capture until it has been tried on a real Android phone.

## The prototype: `npm run build:usb`

`webapp/scripts/build-usb.mjs` writes `webapp/dist-usb/` (gitignored):

- `Pendulastic-offline.html` (~620 KB). The same modules as `dist/`, with only
  their import/export syntax rewritten into one inline script. `app.css` is
  inlined. The wasm is inlined as base64 and handed to `init()` in place of
  the fetch. The capture worker starts from a Blob URL, with a fallback that
  runs `worker.js`'s own handler on the main thread if a browser refuses the
  Blob URL. The manifest and service-worker registration are removed, and the
  install banner is replaced by an "Offline USB copy, export after every
  session" notice.
- `README-OFFLINE.txt`: per-device instructions for whoever holds the drive.

What does not change: the source files, the online build, and the maths.
`BUILD_ID` and `ALGORITHM_VERSION` come from the same `src/build-id.js`, so the
Session build line and each stored trial's `algorithm_version` identify the
build exactly as online. The bundler uses no dependencies. It throws on any
import/export form it does not understand, so it cannot emit a broken page.

### Verified (headless Chromium 1208 on Windows, page opened as `file:///`)

- The page loads with no errors and shows `build 9a32a8122408 · algorithm …`.
- Main-thread wasm initialises from the inlined bytes.
- End to end through the real UI: Start, then the swing fixture dispatched as
  synthetic `devicemotion` events at 60 Hz. The trial scored, the result and
  PT panel rendered, and the trial was saved to IndexedDB with
  `algorithm_version`.
- The Blob worker and the forced in-thread fallback give identical params on
  the fixture (f = 1.0 Hz, as simulated).
- `tests/usb-build.test.js` (in `npm test`) checks three things. The page has
  no external `src`/`href`. The IDs match `build-id.js`. The bundled worker's
  messages on the fixture are deep-equal to `src/worker.js`'s.

### Not verified

- Any real phone. In particular: Android Chrome opening the file from
  Downloads or USB-OTG (`content://` vs `file://`, secure context, whether
  DeviceMotion fires, whether IndexedDB persists), and Firefox and Samsung
  Internet.
- The install-once path on a real iPhone in airplane mode. It was checked
  only in desktop Chromium with the network cut.
- Storage durability. Trials live in the browser of the device that recorded
  them, never on the drive. Moving or renaming the HTML file may give it a
  new, empty store in some browsers. Export after every session.
