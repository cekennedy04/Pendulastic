"""Serve `webapp/` over HTTPS to a phone on the same network.

This exists because the app cannot be tested any other way. iOS Safari only
grants `DeviceMotionEvent.requestPermission()` in a secure context, so opening
the page from the filesystem or over plain http:// fails at the permission
call — and it fails without a useful error, which makes it look like a bug in
the app rather than a missing prerequisite.

Run from anywhere:

    miniconda3/python.exe webapp/dev_server.py

Then scan the printed QR code with the iPhone's camera, or type the URL into
Safari. The certificate is self-signed, so Safari shows an interstitial: tap
Show Details -> visit this website -> Visit Website. That trust decision is
per-host:port, so it is asked once per machine IP.

Development only. Nothing here is hardened for anything else: it serves a
directory to a local network with a certificate no one should trust.

--------------------------------------------------------------------------
Deliberately NOT set: Cross-Origin-Opener-Policy / Cross-Origin-Embedder-Policy

Those headers exist to unlock `SharedArrayBuffer` and unthrottled
high-resolution timers. This app uses neither, by design: the design spec
(2026-08-24-web-app-design.md, Section 2.1) rejected shared memory in favour of
transferable `postMessage`, because the sensor payload is ~3.4 KB/s and
cross-origin isolation is a permanent constraint to pay for an unmeasurable
gain. Setting the headers here would make the dev server quietly diverge from
what production needs, and cross-origin isolation has real side effects — it
blocks any cross-origin resource that does not opt in via CORP. If a future
change genuinely needs `SharedArrayBuffer`, that is a spec decision first, and
this server follows it rather than leading it.
"""
import http.server
import os
import socketserver
import ssl
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from pendulastic_phone_server import (  # noqa: E402
    get_local_ip,
    get_or_create_self_signed_cert,
)

PORT = 8900
WASM_DIR = os.path.join(HERE, "src", "wasm")
# The four files `npm run build:wasm` produces. Checked before binding a port,
# because a missing artifact otherwise surfaces on the phone as a blank page
# and a console error nobody is looking at.
REQUIRED_ARTIFACTS = (
    "mobile_imu_core.js",
    "mobile_imu_core_bg.wasm",
)


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves `webapp/` with the MIME types a module + wasm page needs."""

    # SimpleHTTPRequestHandler reads this table via `guess_type`. Python's
    # mimetypes database does not reliably know .wasm, and a wasm response
    # served as application/octet-stream fails `instantiateStreaming` in
    # Safari with a content-type error rather than anything about the module.
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".wasm": "application/wasm",
        ".json": "application/json",
        ".css": "text/css",
        ".html": "text/html; charset=utf-8",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def end_headers(self):
        # No caching, ever. During on-device testing the wasm and JS change
        # under the phone between runs, and a cached module means measuring a
        # build that no longer exists — a failure mode that looks like flaky
        # behaviour rather than a stale file.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        # One line per request, without the timestamp noise; the console is
        # the operator's instructions, not a log.
        sys.stderr.write(f"  {self.command} {self.path}\n")


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    # Deliberately NOT allow_reuse_address: on Windows a second launch would
    # otherwise bind alongside a server that is still running and serving the
    # OLD files, so the phone silently tests a stale build. Failing loudly is
    # the point.
    allow_reuse_address = False


def check_artifacts():
    missing = [f for f in REQUIRED_ARTIFACTS
               if not os.path.exists(os.path.join(WASM_DIR, f))]
    if missing:
        print("\n  webapp/src/wasm/ is missing: " + ", ".join(missing))
        print("\n  The generated WebAssembly is not committed (it embeds the")
        print("  building machine's paths and toolchain version). Build it:\n")
        print("      cd webapp && npm run build:wasm\n")
        sys.exit(1)


def main():
    # qrcode.print_ascii emits Unicode half-blocks, which Windows' default
    # cp1252 stdout cannot encode; without this the QR silently degrades to a
    # "type the URL" fallback on the one platform this is run from.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    check_artifacts()

    ip = get_local_ip()
    cert_dir = os.path.join(REPO_ROOT, ".certs")
    cert_path, key_path = get_or_create_self_signed_cert(cert_dir, ip)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)

    try:
        srv = Server(("0.0.0.0", PORT), Handler)
    except OSError as exc:
        print(f"\n  Port {PORT} is already in use — another dev server is")
        print("  probably still running and would serve stale files.")
        print(f"  Close that terminal and try again.\n\n  ({exc})\n")
        sys.exit(1)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)

    url = f"https://{ip}:{PORT}/"
    print("=" * 64)
    print("  Pendulastic webapp — dev server (HTTPS, self-signed)")
    print("=" * 64)
    print(f"\n  On the iPhone, open Safari at:\n\n      {url}\n")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        print("  (QR unavailable — type the URL above)")

    print("  Safari will warn about the certificate: Show Details ->")
    print("  visit this website -> Visit Website.\n")
    print("  Then, for one trial: tap Start, hold the limb still until the")
    print("  banner turns green and reads READY, release, let it settle,")
    print("  and tap Stop. Releasing before READY captures the zero pose at")
    print("  the wrong instant and the trial's angles are measured from a")
    print("  reference the leg had not reached.\n")
    print("  Ctrl-C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")


if __name__ == "__main__":
    main()
