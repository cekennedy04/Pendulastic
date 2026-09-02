# webapp

Self-contained iOS Safari capture app for the Wartenberg pendulum test. The
phone captures motion, a worker fuses and scores it through the
`mobile-imu-core` Rust engine compiled to WebAssembly, and the page displays
all 20 parameters.

Not validated. The banner saying so is required by the spec and has no
dismiss control.

## Views

The app is five sections toggled by `src/router.js`: **home** (tiles),
**capture**, **trials** (this session's history), **mas** (assessment entry),
and **session** (participant, leg, export, close).

Views use `.view` / `.view.active`, never the `hidden` attribute -- see the
note above the `.view` rule in `src/app.css` for why.

Leaving the capture view is refused while a trial is recording: a live
capture owns a `devicemotion` listener, a flush interval, and a screen wake
lock, and navigating away would orphan all three.

Recording requires a participant and a leg. Both are entered in **session**
and persist in the `settings` store across a reload and a relaunch; there is
no longer a hardcoded test participant.

## MAS export

A session with assessments exports `<base>-mas.csv` alongside the trial
`.jsonl` files, with exactly `mas_validation.DEFAULT_MAS_FIELDS` as its
header, so `append_mas_score()` ingests it unchanged. `mas_grade` may be `-1`
("not yet assessed"); it may never be blank. The optional grades take the
inverse rule -- blank is valid, `-1` is not. See
`docs/superpowers/specs/2026-08-31-mobile-webapp-workbench-restyle-design.md`.

A half-filled MAS form is saved as a draft in the `settings` store (not
`sessionStorage`, which is cleared exactly when a standalone app is
terminated). Drafts are keyed per participant and leg, and the most recently
saved one is resumed.

## Build step (required before serving or testing)

```
npm run build:wasm
```

This must be run once after cloning, and again after any change under
`mobile-imu-core/src/`. It:

1. builds `mobile-imu-core` for `wasm32-unknown-unknown` in release mode, and
2. runs `wasm-bindgen --target web` to generate `src/wasm/`.

Prerequisites: `cargo`, the `wasm32-unknown-unknown` target
(`rustup target add wasm32-unknown-unknown`), and a `wasm-bindgen` CLI whose
version matches the `wasm-bindgen` crate resolved in
`mobile-imu-core/Cargo.lock`. The script reads that pinned version, checks the
CLI against it, and prints the exact `cargo install` command if it is missing
or mismatched — a mismatched CLI does not fail loudly, it emits bindings the
compiled `.wasm` does not agree with.

Without this step, `npm test` fails immediately with a message pointing back
here rather than an opaque module-not-found stack.

## Why `src/wasm/` is not committed

It is generated output, and it is not reproducible across machines. The
`.wasm` binary embeds the building machine's cargo registry path (including
its username), source paths using that operating system's separators, and the
`rustc` version and commit hash. Two developers on two machines building the
identical Rust source get byte-different artifacts.

That makes a committed copy actively misleading in two directions:

- Any byte-exact check against it fails for reasons that have nothing to do
  with the code, so it gets switched off — after which the repo looks
  protected while nothing is checking.
- A committed binary that nobody rebuilds can silently lag
  `mobile-imu-core/src/`, leaving the browser running different maths than
  `cargo test` verifies. On a clinical measurement that failure mode is not a
  crash; it is a plausible, clean-looking wrong number.

So the artifact is built, never stored. CI builds it from source before
running the webapp tests, exactly as `npm run build:wasm` does locally, which
means what CI tests is necessarily what ships.

## Tests

```
npm test
```

Runs `node --test` over `tests/`. `tests/worker.test.js` drives the real
shipping `.wasm` — the fixture-replay seam exists because no driver can
synthesise a `DeviceMotionEvent` in Safari, so the worker protocol and the
scoring path are testable but the live sensor plumbing is not. On-device
behaviour is checked by a human operator, not by this suite.

## Serving

Any static file server over the repo's `webapp/` directory works; ES modules
and `new Worker(..., {type:'module'})` both require HTTP, not `file://`. iOS
requires HTTPS (or `localhost`) before `DeviceMotionEvent.requestPermission()`
will resolve.

## Testing on a real iPhone

`DeviceMotionEvent.requestPermission()` is only granted in a secure context,
so the page must be served over HTTPS — opening it from the filesystem or over
plain http:// fails at the permission call, and fails unhelpfully.

```
miniconda3/python.exe webapp/dev_server.py
```

Prints a URL and a QR code for the machine's LAN address, using the same
self-signed certificate helper as `pendulastic_phone_server.py`. Safari will
show a certificate interstitial once per host: Show Details → visit this
website → Visit Website.

Requires `npm run build:wasm` to have been run first; the server checks and
tells you if not.

## Deploying to a static host

`dev_server.py` is deliberately dev-only (see its module docstring), and its
origin is the laptop's LAN IP — a DHCP lease that can move at any time. To
Safari, a changed IP is a changed origin, which orphans everything scoped to
the old one: the installed Home Screen app, the service worker and its
offline cache, and the entire IndexedDB — every trial stored on the phone. A
static host with a fixed hostname (Netlify, Cloudflare Pages, GitHub Pages,
etc.) gives the app a stable, laptop-independent origin instead.

```
npm run build:dist
```

Builds `src/wasm/` from scratch (`build:wasm`, so the deployed wasm is always
current — see above) and assembles `webapp/dist/`, the directory to upload.
It contains exactly what the app needs at runtime — `index.html`, `sw.js`,
`manifest.json`, every file `sw.js`'s offline shell lists, the compiled wasm
pair, and a `_headers` file — and nothing else: no `tests/`, `scripts/`,
`captures/` (participant-adjacent capture data), `dev_server.py`, `README.md`,
or `docs/`.

Deploy `webapp/dist/`'s contents with whatever CLI your host provides (e.g.
`netlify deploy` or `wrangler pages deploy`), pointed at that directory. This
is a **local build plus a manual upload, not a git-triggered build** — a
host's own build step would need a Rust toolchain (`cargo` + `wasm-bindgen`)
just to run `build:wasm`, which most static hosts don't offer and none of them
need to if the build already happened locally.

**A rebuild requires a redeploy.** The wasm is not committed (see above), so
whatever is live on the host is exactly whatever `webapp/dist/` contained the
last time someone ran the upload — there is no CI or git hook keeping it in
sync with the repo. After any change under `mobile-imu-core/src/` or
`webapp/src/`, `npm run build:dist` and re-upload, or the phone keeps scoring
with old maths, or serving old JS, indefinitely.

`_headers` (the Netlify/Cloudflare Pages format) pins two things a real host's
defaults would otherwise get wrong: `sw.js` and `src/build-id.js` are served
`Cache-Control: no-cache`, because they carry the cache key the whole offline
design is keyed on — if the browser's HTTP cache serves either of them stale,
an installed phone never notices a new build exists (see `sw.js`'s own
comments); and `.wasm` is served as `application/wasm`, because Safari's
`instantiateStreaming` rejects `application/octet-stream` with a content-type
error that names neither the file nor the reason. **GitHub Pages ignores
`_headers` entirely** — it has no equivalent header-configuration mechanism —
so deploying there means either accepting the staleness/content-type risk
above or fronting it with something that can add headers (e.g. Cloudflare in
front of Pages). Netlify and Cloudflare Pages both honor `_headers` natively.

**Changing the origin orphans everything.** Moving from `dev_server.py`'s LAN
IP to a static host's hostname is itself an origin change, with the same
consequence described above: the installed app, its service-worker cache, and
every trial in IndexedDB are tied to the origin they were created under and do
not carry over. Before switching an in-use phone from the dev server to a
real host — or between two different static hosts — **export any trials you
care about first** (the in-app export, not a copy of the database file), then
reinstall against the new origin.
