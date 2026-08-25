# webapp

Self-contained iOS Safari capture app for the Wartenberg pendulum test. The
phone captures motion, a worker fuses and scores it through the
`mobile-imu-core` Rust engine compiled to WebAssembly, and the page displays
all 20 parameters.

Not validated. The banner saying so is required by the spec and has no
dismiss control.

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
