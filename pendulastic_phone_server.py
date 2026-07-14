"""
pendulastic_phone_server.py
===========================
Lightweight phone-as-camera server for the Pendulastic viewer.

  HTTP  port 8877  — serves the mobile capture page (HTML/JS)
  WS    port 8878  — receives JPEG blobs from the phone's browser

Usage:
    import pendulastic_phone_server as _pps
    ip, http_port, ws_port = _pps.start()
    # decoded BGR frames arrive in _pps.frame_queue (maxsize=4)
    _pps.stop()
"""
from __future__ import annotations

import asyncio
import queue
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import socket as _socket

import cv2
import numpy as np

PORT_HTTP: int = 8877
PORT_WS:   int = 8878

# Decoded BGR frames. maxsize=4 so the viewer always sees near-live frames;
# oldest frame is dropped when the queue is full and a new one arrives.
frame_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=4)

_http_server: HTTPServer | None       = None
_http_thread: threading.Thread | None = None
_ws_thread:   threading.Thread | None = None
_ws_loop:     asyncio.AbstractEventLoop | None = None
_ws_server                            = None   # websockets.WebSocketServer
_running = False
_local_ip = "127.0.0.1"


# ─── helpers ──────────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    """Return the machine's primary LAN IP (not loopback)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't need to be reachable — just gets the routing source address.
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        # Fallback: enumerate interfaces and pick the first non-loopback IPv4
        try:
            hostname = socket.gethostname()
            candidates = socket.getaddrinfo(hostname, None, socket.AF_INET)
            for _, _, _, _, sockaddr in candidates:
                ip = sockaddr[0]
                if not ip.startswith("127."):
                    return ip
        except Exception:
            pass
        return "127.0.0.1"
    finally:
        s.close()


# ─── mobile page ──────────────────────────────────────────────────────────────

def _build_page(ws_host: str, ws_port: int) -> bytes:
    """Return the mobile HTML/JS page as UTF-8 bytes with WS URL embedded."""
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Pendulastic Camera</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#000;color:#fff;font-family:-apple-system,BlinkMacSystemFont,sans-serif;
     display:flex;flex-direction:column;height:100dvh;overflow:hidden}}
#vid{{width:100%;flex:1;object-fit:cover}}
#bar{{padding:14px 16px;background:#111;text-align:center;flex-shrink:0}}
#st{{font-size:18px;font-weight:700;margin-bottom:4px}}
#inf{{font-size:13px;color:#9ca3af}}
.ok{{color:#4ade80}}.err{{color:#f87171}}.wait{{color:#fbbf24}}
</style>
</head>
<body>
<video id="vid" autoplay playsinline muted></video>
<div id="bar">
  <div id="st" class="wait">Connecting to desktop…</div>
  <div id="inf">&nbsp;</div>
</div>
<canvas id="c" style="display:none"></canvas>
<script>
const WS_URL = 'ws://{ws_host}:{ws_port}';
const JPEG_Q = 0.65;   // JPEG quality (0-1)
const TARGET_FPS = 15; // frames/s to send

const vid = document.getElementById('vid');
const c   = document.getElementById('c');
const ctx = c.getContext('2d');
const st  = document.getElementById('st');
const inf = document.getElementById('inf');

let ws, animId, txFrames = 0, lastFpsTick = Date.now();

function setStatus(msg, cls) {{
  st.textContent = msg;
  st.className = cls || '';
}}

function connect() {{
  ws = new WebSocket(WS_URL);
  ws.binaryType = 'arraybuffer';
  ws.onopen  = () => {{ setStatus('Connected', 'ok'); startCamera(); }};
  ws.onclose = () => {{
    setStatus('Disconnected — reconnecting…', 'err');
    cancelAnimationFrame(animId);
    setTimeout(connect, 2000);
  }};
  ws.onerror = () => setStatus('Connection error', 'err');
}}

async function startCamera() {{
  try {{
    const stream = await navigator.mediaDevices.getUserMedia({{
      video: {{ facingMode: 'environment',
                width:  {{ ideal: 1280 }},
                height: {{ ideal: 720  }} }}
    }});
    vid.srcObject = stream;
    vid.onloadedmetadata = () => {{
      c.width  = vid.videoWidth;
      c.height = vid.videoHeight;
      inf.textContent = c.width + '×' + c.height;
      setStatus('● Streaming', 'ok');
      sendLoop();
    }};
  }} catch(e) {{
    setStatus('Camera error', 'err');
    inf.textContent = e.message;
  }}
}}

function sendLoop() {{
  const interval = 1000 / TARGET_FPS;
  let last = 0;
  function frame(ts) {{
    if (ts - last >= interval && ws && ws.readyState === WebSocket.OPEN) {{
      ctx.drawImage(vid, 0, 0);
      c.toBlob(blob => {{
        if (blob && ws.readyState === WebSocket.OPEN) {{
          ws.send(blob);
          txFrames++;
          const now = Date.now();
          if (now - lastFpsTick >= 1000) {{
            inf.textContent = c.width + '×' + c.height + '  ↑ ' + txFrames + ' fps';
            txFrames = 0;
            lastFpsTick = now;
          }}
        }}
      }}, 'image/jpeg', JPEG_Q);
      last = ts;
    }}
    animId = requestAnimationFrame(frame);
  }}
  animId = requestAnimationFrame(frame);
}}

connect();
</script>
</body>
</html>""".encode("utf-8")


# ─── HTTP server ──────────────────────────────────────────────────────────────

class _PageHandler(BaseHTTPRequestHandler):
    """Serve the single mobile capture page to any GET request."""
    _page: bytes = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PageHandler._page)))
        self.end_headers()
        self.wfile.write(_PageHandler._page)

    def log_message(self, *_):
        pass   # suppress HTTP access log noise in console


# ─── WebSocket handler ────────────────────────────────────────────────────────

async def _ws_handler(ws, path="/"):
    """Receive JPEG blobs from the phone, decode to BGR, drop into frame_queue."""
    async for msg in ws:
        if isinstance(msg, bytes):
            arr   = np.frombuffer(msg, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is not None:
                # Drop the oldest frame if the viewer hasn't consumed it yet
                # so the queue never falls more than 4 frames behind real-time.
                if frame_queue.full():
                    try:
                        frame_queue.get_nowait()
                    except Exception:
                        pass
                try:
                    frame_queue.put_nowait(frame)
                except Exception:
                    pass


# ─── public API ───────────────────────────────────────────────────────────────

class _ReuseHTTPServer(HTTPServer):
    """HTTPServer with SO_REUSEADDR so the port is released immediately on stop."""
    allow_reuse_address = True


def start() -> tuple[str, int, int]:
    """Start the HTTP and WebSocket servers in background daemon threads.

    Returns (local_ip, http_port, ws_port).  Idempotent — if already running,
    returns the cached values.  If a previous failed start left ports partially
    bound, stop() is called first to clean up before retrying.
    """
    global _http_server, _http_thread, _ws_thread, _ws_loop
    global _ws_server, _running, _local_ip

    if _running:
        return _local_ip, PORT_HTTP, PORT_WS

    # Clean up any half-started state from a previous failed attempt.
    stop()

    _local_ip = get_local_ip()
    _PageHandler._page = _build_page(_local_ip, PORT_WS)

    # ── HTTP server (SO_REUSEADDR via subclass) ──────────────────────────────
    _http_server = _ReuseHTTPServer(("0.0.0.0", PORT_HTTP), _PageHandler)
    _http_thread = threading.Thread(
        target=_http_server.serve_forever, daemon=True, name="pps-http")
    _http_thread.start()

    # ── WebSocket server (SO_REUSEADDR via reuse_address=True) ───────────────
    import websockets as _ws
    _ws_loop = asyncio.new_event_loop()

    async def _launch():
        global _ws_server
        _ws_server = await _ws.serve(
            _ws_handler, "0.0.0.0", PORT_WS,
            reuse_address=True,
        )

    try:
        _ws_loop.run_until_complete(_launch())
    except Exception:
        # WS bind failed — shut down the HTTP server we already started.
        try:
            _http_server.shutdown()
        except Exception:
            pass
        _http_server = None
        raise   # propagate so the viewer shows the real error

    _ws_thread = threading.Thread(
        target=_ws_loop.run_forever, daemon=True, name="pps-ws")
    _ws_thread.start()

    _running = True
    return _local_ip, PORT_HTTP, PORT_WS


def stop():
    """Shut down both servers and drain the frame queue."""
    global _http_server, _ws_server, _ws_loop, _running

    _running = False   # clear first so re-entrant calls are no-ops

    try:
        if _http_server:
            _http_server.shutdown()
    except Exception:
        pass
    _http_server = None

    try:
        if _ws_server and _ws_loop:
            _ws_loop.call_soon_threadsafe(_ws_server.close)
            _ws_loop.call_soon_threadsafe(_ws_loop.stop)
    except Exception:
        pass
    _ws_server = None

    # Drain any buffered frames so the next session starts clean.
    while not frame_queue.empty():
        try:
            frame_queue.get_nowait()
        except Exception:
            break

    _running = False
