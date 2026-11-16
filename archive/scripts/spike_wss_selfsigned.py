"""
spike_wss_selfsigned.py — throwaway spike to verify iOS Safari accepts a
wss:// connection over a self-signed cert on the SAME port as the page that
served the "Advanced -> Proceed" warning. Gates the phone-camera feature's
single-port HTTPS+WS design (see docs/superpowers/specs/2026-08-03-phone-
camera-recording-source-design.md section 3). Run this, then follow the
manual checklist printed at the bottom. Not part of the shipped feature.
"""
import base64
import datetime
import hashlib
import http.server
import ipaddress
import os
import socket
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

PORT = 8899
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _make_cert(ip: str, cert_path: str, key_path: str) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=7))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]),
            critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))


_PAGE = b"""<!DOCTYPE html><html><body style="font-family:sans-serif;padding:20px">
<h2>WSS Spike</h2>
<p id="status">connecting...</p>
<script>
const ws = new WebSocket("wss://" + location.host + "/ws");
ws.onopen    = () => { document.getElementById("status").textContent = "WS: CONNECTED"; };
ws.onerror   = () => { document.getElementById("status").textContent = "WS: ERROR (see console)"; };
ws.onclose   = (e) => { document.getElementById("status").textContent = "WS: CLOSED code=" + e.code; };
</script>
</body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            key = self.headers.get("Sec-WebSocket-Key", "")
            accept = base64.b64encode(
                hashlib.sha1((key + _WS_MAGIC).encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            print("[spike] WS upgrade completed from", self.client_address)
            try:
                while True:
                    hdr = self.rfile.read(2)
                    if not hdr:
                        break
            except Exception:
                pass
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ip = _local_ip()
    cert_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".certs")
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, "spike_cert.pem")
    key_path  = os.path.join(cert_dir, "spike_key.pem")
    _make_cert(ip, cert_path, key_path)

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    print(f"\nOpen this on your iPhone (Safari): https://{ip}:{PORT}/\n")
    print("MANUAL CHECKLIST:")
    print("  1. Open the URL above in iOS Safari.")
    print("  2. Tap Advanced -> Proceed to accept the self-signed cert warning.")
    print("  3. The page should show 'WS: CONNECTED' within ~1s.")
    print("     - If it shows 'WS: CONNECTED': single-port design is viable. Proceed to Task 2.")
    print("     - If it shows 'WS: ERROR' or hangs on 'connecting...': stop here and")
    print("       revisit the design with the user (spec section 3, mitigation options 2-3).")
    print("  4. Repeat on Android Chrome for confirmation (expected to be less strict).")
    print("Press Ctrl+C to stop.\n")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        while True:
            input()
    except KeyboardInterrupt:
        pass
