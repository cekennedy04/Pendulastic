"""
pendulastic_phone_server.py
===========================
Lightweight phone-as-camera server for the Pendulastic viewer.

  HTTP  port 8877  — serves the mobile capture page (HTML/JS)
  POST  /upload    — receives recorded video file from the phone
  WS    port 8878  — (future) real-time JPEG streaming via WebSocket

The primary workflow for iOS (Safari) uses the record-and-upload approach:
the phone records with the native camera via <input capture>, then POSTs
the file to this server over plain HTTP — no HTTPS/getUserMedia needed.

Usage:
    import pendulastic_phone_server as _pps
    ip, http_port, ws_port = _pps.start(upload_dir)
    # uploaded video paths arrive in _pps.upload_queue
    _pps.stop()
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import queue
import re
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse, unquote

import cv2
import numpy as np

PORT_HTTP: int = 8877
PORT_WS:   int = 8878

# Uploaded video paths — one entry per completed upload.
upload_queue: "queue.Queue[str]" = queue.Queue()

# Real-time frame queue (WebSocket streaming, future use)
frame_queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=4)

_http_server: HTTPServer | None               = None
_http_thread: threading.Thread | None         = None
_ws_thread:   threading.Thread | None         = None
_ws_loop:     asyncio.AbstractEventLoop | None = None
_tcp_server:  asyncio.AbstractServer | None   = None
_running  = False
_local_ip = "127.0.0.1"
_upload_dir: str = "uploads"

_public_url:   str | None = None  # ngrok HTTPS URL once tunnel is up
_ngrok_status: str        = "not_started"  # not_started|connecting|ready|error|unavailable
_ngrok_error:  str        = ""
_ngrok_tunnel             = None  # pyngrok tunnel object

_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ─── ngrok tunnel (optional HTTPS for any-network access) ─────────────────────

def get_public_url() -> str | None:
    """Return ngrok HTTPS URL when the tunnel is active, else None."""
    return _public_url


def get_ngrok_status() -> tuple[str, str]:
    """Return (status, error_message). Status: connecting|ready|error|unavailable."""
    return _ngrok_status, _ngrok_error


def _ngrok_worker(port: int) -> None:
    """Start ngrok tunnel in a background thread."""
    global _public_url, _ngrok_status, _ngrok_error, _ngrok_tunnel
    _ngrok_status = "connecting"
    try:
        import logging as _log
        # Silence pyngrok — its output goes to Python logging which clutters the terminal
        _null = _log.NullHandler()
        for _lg_name in ("pyngrok", "pyngrok.ngrok", "pyngrok.process"):
            _lgi = _log.getLogger(_lg_name)
            _lgi.handlers = [_null]
            _lgi.propagate = False

        from pyngrok import ngrok
        tunnel        = ngrok.connect(port, "http")
        raw           = tunnel.public_url
        _ngrok_tunnel = tunnel
        _public_url   = ("https://" + raw[7:]) if raw.startswith("http://") else raw
        _ngrok_status = "ready"
    except ImportError:
        _ngrok_status = "unavailable"
        _ngrok_error  = "pyngrok not installed"
    except Exception as exc:
        _ngrok_status = "error"
        _ngrok_error  = str(exc)[:160]


# ─── IP discovery ─────────────────────────────────────────────────────────────

def _score_ip(ip: str) -> int:
    """Score a secondary (hostname-enumerated) IP for tiebreaking.

    Socket-route IPs (from get_all_local_ips) are always preferred and never
    run through this function — it is only used to rank the supplemental list.
    Campus/enterprise networks commonly use 172.x.x.x for real WiFi, so we
    only lightly penalise that range rather than treating it as virtual.
    """
    if ip.startswith("192.168."):
        return 4
    if ip.startswith("10."):
        return 3
    if ip.startswith("172."):
        return 2   # real on campus nets; virtual adapters also land here
    if ip.startswith("169.254."):
        return 0   # APIPA — link-local, unusable
    return 1


def get_all_local_ips() -> list[str]:
    """Return all non-loopback IPv4 addresses, best candidate first.

    Priority order:
      1. Socket-route IPs — connecting a UDP socket to well-known DNS servers
         returns the interface the OS would actually use to reach the internet.
         This is the correct LAN IP regardless of prefix (172/10/192.168), so
         we preserve the discovery order and never re-score these.
      2. Hostname-enumerated IPs — may include virtual adapters; sorted by
         _score_ip as a secondary fallback.
    """
    seen:    set[str]            = set()
    primary: list[str]           = []
    secondary: list[tuple[int, str]] = []

    def _add_primary(ip: str) -> None:
        if ip and ip not in seen and not ip.startswith("127."):
            seen.add(ip)
            primary.append(ip)

    def _add_secondary(ip: str) -> None:
        if ip and ip not in seen and not ip.startswith("127."):
            seen.add(ip)
            secondary.append((_score_ip(ip), ip))

    for dest in ("8.8.8.8", "1.1.1.1", "208.67.222.222"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((dest, 80))
            _add_primary(s.getsockname()[0])
            s.close()
        except Exception:
            pass

    try:
        hostname = socket.gethostname()
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
            _add_secondary(sockaddr[0])
    except Exception:
        pass

    secondary.sort(key=lambda x: -x[0])
    return primary + [ip for _, ip in secondary] or ["127.0.0.1"]


def get_local_ip() -> str:
    return get_all_local_ips()[0]


# ─── mobile page ──────────────────────────────────────────────────────────────

# Plain string (not f-string) so JS { } and ${} don't need escaping.
_TRACKING_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no,viewport-fit=cover">
<title>Pendulastic</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{
  width:100%;height:100%;overflow:hidden;
  background:#0f172a;
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif;
  color:#e2e8f0;
  display:flex;flex-direction:column;
}

/* ── Camera section ──────────────────────────────────────────────────── */
#wrap{
  flex:1;min-height:0;
  position:relative;background:#000;overflow:hidden;
}
#vid{width:100%;height:100%;object-fit:contain;display:block}
#cvs{position:absolute;inset:0;width:100%;height:100%}
#badge-layer{position:absolute;inset:0;pointer-events:none}

/* ── Loading overlay ─────────────────────────────────────────────────── */
#overlay-init{
  position:absolute;inset:0;
  display:flex;flex-direction:column;
  align-items:center;justify-content:center;
  background:rgba(0,0,0,.8);gap:14px;z-index:40;
}
.spinner{
  width:40px;height:40px;
  border:3px solid rgba(255,255,255,.15);
  border-top-color:#0ea5e9;border-radius:50%;
  animation:spin .8s linear infinite;
}
@keyframes spin{to{transform:rotate(360deg)}}
#init-msg{font-size:13px;color:#94a3b8;text-align:center;max-width:240px}

/* ── Top banner (in-camera text) ─────────────────────────────────────── */
#top-banner{
  position:absolute;top:12px;left:50%;transform:translateX(-50%);
  background:rgba(0,0,0,.72);color:#e2e8f0;
  padding:6px 18px;border-radius:20px;
  font-size:14px;font-weight:500;
  z-index:20;white-space:nowrap;display:none;
}
/* ── Countdown overlay ───────────────────────────────────────────────── */
#overlay-cd{
  position:absolute;inset:0;
  display:none;align-items:center;justify-content:center;z-index:30;
}
#cd-bubble{
  width:100px;height:100px;border-radius:50%;
  border:4px solid #0ea5e9;background:rgba(0,0,0,.75);
  display:flex;align-items:center;justify-content:center;
  font-size:56px;font-weight:700;color:#e2e8f0;
}
/* ── REC indicator + angle ───────────────────────────────────────────── */
#rec-ind{
  position:absolute;top:10px;left:10px;
  background:rgba(0,0,0,.65);color:#ef4444;
  font-size:13px;font-weight:700;padding:4px 10px;
  border-radius:12px;display:none;z-index:20;
}
#angle-disp{
  position:absolute;top:10px;right:10px;
  background:rgba(0,0,0,.72);color:#0ea5e9;
  font-size:22px;font-weight:700;
  padding:4px 14px;border-radius:12px;
  display:none;z-index:20;
  font-variant-numeric:tabular-nums;
}
/* ── Correction banner ───────────────────────────────────────────────── */
#corr-banner{
  position:absolute;bottom:10px;left:50%;transform:translateX(-50%);
  background:rgba(0,0,0,.78);color:#cbd5e1;
  font-size:12px;padding:5px 16px;border-radius:12px;
  display:none;z-index:20;white-space:nowrap;
}
/* ── Person selection badges ─────────────────────────────────────────── */
.p-badge{
  position:absolute;
  width:44px;height:44px;border-radius:50%;
  border:3px solid;background:rgba(0,0,0,.65);
  display:flex;align-items:center;justify-content:center;
  font-size:20px;font-weight:700;
  transform:translate(-50%,-50%);
  pointer-events:all;cursor:pointer;z-index:15;
  transition:transform .12s;
}
.p-badge:active{transform:translate(-50%,-50%) scale(1.25)}
/* ── Correction handles ──────────────────────────────────────────────── */
.c-handle{
  position:absolute;
  width:56px;height:56px;border-radius:50%;
  border:3px solid;background:rgba(0,0,0,.6);
  display:none;flex-direction:column;
  align-items:center;justify-content:center;
  transform:translate(-50%,-50%);
  z-index:25;touch-action:none;cursor:grab;gap:2px;
}
.c-handle:active{cursor:grabbing;transform:translate(-50%,-50%) scale(1.1)}
.c-handle.hip  {border-color:#f97316;color:#f97316}
.c-handle.knee {border-color:#0ea5e9;color:#0ea5e9}
.c-handle.ankle{border-color:#22c55e;color:#22c55e}
.c-lbl{font-size:9px;font-weight:700;text-transform:uppercase;line-height:1}
.c-icon{font-size:18px;line-height:1}

/* ── Chart panel ─────────────────────────────────────────────────────── */
#chart-panel{
  flex-shrink:0;height:0;overflow:hidden;
  background:rgba(6,9,20,.98);
  transition:height .28s cubic-bezier(.4,0,.2,1);
}
#chart-cvs{width:100%;display:block}

/* ── Bottom bar ──────────────────────────────────────────────────────── */
#bottom{
  flex-shrink:0;height:118px;
  background:#0f172a;border-top:1px solid #1e293b;
  display:flex;flex-direction:column;
  align-items:center;justify-content:center;
  padding:8px 16px;padding-bottom:max(10px,env(safe-area-inset-bottom));
  gap:7px;
}
.s-panel{display:none;flex-direction:column;align-items:center;width:100%;max-width:340px;gap:7px}
.s-panel.on{display:flex}
.s-label{font-size:13px;color:#94a3b8;text-align:center}
/* rows */
.btn-row{display:flex;width:100%;gap:8px}
/* buttons */
button{
  border:none;border-radius:12px;
  font-size:15px;font-weight:600;
  padding:0 18px;height:46px;
  cursor:pointer;transition:opacity .12s;
}
button:active{opacity:.65}
.b-ok    {background:#14532d;color:#86efac;flex:1}
.b-bad   {background:#7f1d1d;color:#fca5a5;flex:1}
.b-blue  {background:#0ea5e9;color:#fff;flex:1}
.b-stop  {background:#991b1b;color:#fff;flex:2}
.b-corr  {background:#1e3a5f;color:#93c5fd;flex:1}
.b-done  {background:#14532d;color:#86efac;flex:1}
.b-ghost {background:#1e293b;color:#94a3b8;flex:1}
.b-send  {background:#0ea5e9;color:#fff;flex:2}

/* delay row */
.d-row{display:flex;width:100%;gap:6px}
.d-btn{
  background:#1e293b;color:#94a3b8;
  font-size:13px;font-weight:600;
  height:44px;flex:1;border-radius:10px;
}
.d-btn.now{background:#0ea5e9;color:#fff}

/* leg selector */
.leg-row{display:flex;align-items:center;gap:6px}
.leg-lbl{font-size:12px;color:#475569}
.leg-btn{
  background:#1e293b;color:#475569;
  font-size:12px;font-weight:600;
  padding:0 14px;height:32px;border-radius:8px;
  border:1.5px solid transparent;
}
.leg-btn.on{background:#1e3a5f;color:#93c5fd;border-color:#0ea5e9}

/* progress + feedback */
#prog-wrap{width:100%;height:6px;background:#1e293b;border-radius:3px;display:none}
#prog-fill{height:100%;background:#0ea5e9;border-radius:3px;width:0;transition:width .2s}
#fb{font-size:13px;font-weight:600;color:#22c55e;display:none}

/* CDN fallback */
#cdn-fail{
  display:none;flex-direction:column;
  align-items:center;gap:8px;
  padding:10px 16px;width:100%;
}
#cdn-fail .s-label{font-size:12px}
#cdn-fail-msg{font-size:10px;color:#475569;text-align:center;max-width:280px;word-break:break-all}
.fi-label{
  background:#0ea5e9;color:#fff;
  border-radius:12px;padding:10px 22px;
  font-weight:600;font-size:15px;cursor:pointer;
}
</style>
</head>
<body>

<!-- ══ Camera section ══════════════════════════════════════════════════ -->
<div id="wrap">
  <video id="vid" autoplay playsinline muted></video>
  <canvas id="cvs"></canvas>
  <div id="badge-layer"></div>

  <!-- Correction handles -->
  <div id="h-hip"   class="c-handle hip">  <div class="c-icon">+</div><div class="c-lbl">Hip</div>  </div>
  <div id="h-knee"  class="c-handle knee"> <div class="c-icon">+</div><div class="c-lbl">Knee</div> </div>
  <div id="h-ankle" class="c-handle ankle"><div class="c-icon">+</div><div class="c-lbl">Ankle</div></div>

  <!-- Overlays -->
  <div id="overlay-init"><div class="spinner"></div><div id="init-msg">Loading...</div></div>
  <div id="overlay-cd"><div id="cd-bubble"><span id="cd-val">3</span></div></div>
  <div id="top-banner"></div>
  <div id="rec-ind">REC</div>
  <div id="angle-disp">--.-</div>
  <div id="corr-banner">Drag handles to correct joints - tap Done when finished</div>
</div>

<!-- ══ Rolling angle chart ══════════════════════════════════════════════ -->
<div id="chart-panel"><canvas id="chart-cvs" height="148"></canvas></div>

<!-- ══ Bottom controls ══════════════════════════════════════════════════ -->
<div id="bottom">

  <!-- INIT -->
  <div id="p-init" class="s-panel on">
    <div class="s-label">Starting up...</div>
  </div>

  <!-- SELECT: tap person in camera -->
  <div id="p-select" class="s-panel">
    <div class="s-label">Tap the person you want to track</div>
    <div class="leg-row">
      <span class="leg-lbl">Leg:</span>
      <button id="leg-L" class="leg-btn on" onclick="setLeg('left')">Left</button>
      <button id="leg-R" class="leg-btn"    onclick="setLeg('right')">Right</button>
    </div>
  </div>

  <!-- VALIDATE: confirm skeleton -->
  <div id="p-validate" class="s-panel">
    <div class="s-label">Does the skeleton look correct?</div>
    <div class="btn-row">
      <button class="b-ok"  onclick="onLooksGood()">Looks Good</button>
      <button class="b-bad" onclick="onFixSkeleton()">Fix Skeleton</button>
    </div>
    <div class="btn-row">
      <button class="b-ghost" onclick="onRetap()">Select Again</button>
    </div>
  </div>

  <!-- CORRECT: drag handles to fix joints -->
  <div id="p-correct" class="s-panel">
    <div class="s-label">Orange=Hip  Blue=Knee  Green=Ankle</div>
    <div class="btn-row">
      <button class="b-ok"    onclick="onConfirmCorrections()">Confirm</button>
      <button class="b-ghost" onclick="onRetap()">Select Again</button>
    </div>
  </div>

  <!-- READY: delay picker -->
  <div id="p-ready" class="s-panel">
    <div class="s-label">Participant enrolled - choose recording delay</div>
    <div class="d-row">
      <button class="d-btn now" onclick="startWithDelay(0)">Now</button>
      <button class="d-btn"     onclick="startWithDelay(3)">3 s</button>
      <button class="d-btn"     onclick="startWithDelay(5)">5 s</button>
      <button class="d-btn"     onclick="startWithDelay(10)">10 s</button>
    </div>
  </div>

  <!-- COUNTDOWN -->
  <div id="p-countdown" class="s-panel">
    <div class="s-label">Recording starts in...</div>
    <button class="b-ghost" onclick="cancelCountdown()">Cancel</button>
  </div>

  <!-- RECORDING -->
  <div id="p-recording" class="s-panel">
    <div class="btn-row">
      <button class="b-stop" onclick="stopRecording()">Stop</button>
      <button class="b-corr" onclick="enterMidCorrection()">Correct</button>
    </div>
  </div>

  <!-- CORRECTING (mid-recording) -->
  <div id="p-correcting" class="s-panel">
    <div class="s-label">Correcting joints - still recording</div>
    <button class="b-done" onclick="exitMidCorrection()">Done Correcting</button>
  </div>

  <!-- DONE -->
  <div id="p-done" class="s-panel">
    <div class="btn-row">
      <button class="b-send"  onclick="upload()">Send to Desktop</button>
      <button class="b-ghost" onclick="goToReady()">Record Again</button>
    </div>
    <div id="prog-wrap"><div id="prog-fill"></div></div>
    <div id="fb"></div>
  </div>

  <!-- CDN fallback -->
  <div id="cdn-fail">
    <div class="s-label">CDN load failed - phone needs internet access.</div>
    <div id="cdn-fail-msg"></div>
    <label class="fi-label">Record &amp; Send Video
      <input id="fi" type="file" accept="video/*" capture="environment"
             style="display:none" onchange="uploadFile(this)">
    </label>
  </div>

</div><!-- #bottom -->

<script type="module">
/* =========================================================================
   Pendulastic Phone  -  enrollment pipeline
   -------------------------------------------------------------------------
   States:
     INIT -> SELECT (tap person) -> VALIDATE (confirm skeleton)
           -> [CORRECT (drag key joints)] -> READY
           -> COUNTDOWN -> RECORDING -> [CORRECTING] -> DONE

   "Model learns" = enrollment phase: user corrects hip/knee/ankle at rest,
   those normalised positions become the prior for tracking. During recording,
   the selected person index is locked; if MediaPipe loses the person it falls
   back to the nearest centroid to the enrolled position.
   ========================================================================= */

import { PoseLandmarker, FilesetResolver }
  from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22/+esm";

// -- DOM -----------------------------------------------------------------
const vid        = document.getElementById('vid');
const cvs        = document.getElementById('cvs');
const ctx        = cvs.getContext('2d');
const badgeLay   = document.getElementById('badge-layer');
const initOv     = document.getElementById('overlay-init');
const initMsg    = document.getElementById('init-msg');
const cdOv       = document.getElementById('overlay-cd');
const cdVal      = document.getElementById('cd-val');
const topBanner  = document.getElementById('top-banner');
const recInd     = document.getElementById('rec-ind');
const anglDisp   = document.getElementById('angle-disp');
const corrBanner = document.getElementById('corr-banner');
const chartPanel = document.getElementById('chart-panel');
const chartCvs   = document.getElementById('chart-cvs');
const chartCtx   = chartCvs.getContext('2d');
const progWrap   = document.getElementById('prog-wrap');
const progFill   = document.getElementById('prog-fill');
const fbEl       = document.getElementById('fb');
const handles    = {
  hip:   document.getElementById('h-hip'),
  knee:  document.getElementById('h-knee'),
  ankle: document.getElementById('h-ankle'),
};

// -- Colours -------------------------------------------------------------
const P_COL  = ['#f97316','#0ea5e9','#a855f7','#22c55e'];
const J_COL  = { hip:'#f97316', knee:'#0ea5e9', ankle:'#22c55e' };

// -- MediaPipe landmark indices ------------------------------------------
const LM = { LH:23, LK:25, LA:27, RH:24, RK:26, RA:28 };
const BODY_IDX = [11,12,13,14,15,16,23,24,25,26,27,28];

// -- Chart constants -----------------------------------------------------
const CHART_H = 148, CHART_WIN = 12000, Y_MIN = 80, Y_MAX = 200;

// -- App state -----------------------------------------------------------
let state = 'INIT';
let landmarker = null;
let allPoses   = [];      // fresh from each detectForVideo call
let selIdx     = null;    // index of enrolled person in allPoses
let legSide    = 'left';  // 'left' | 'right'

// Enrollment snapshot (set once after VALIDATE or CORRECT)
const enroll = { hip:null, knee:null, ankle:null, centroid:null };

// Per-joint overrides active during RECORDING / CORRECTING
const corr   = { hip:null, knee:null, ankle:null };

// Frozen snapshot used during CORRECT / CORRECTING (no new detection)
let frozenPoses = null;

// Recording
let recorder = null, chunks = [], isRec = false;
let cdHandle = null;
let angleHist = [], chartTick = null;
let lastDetMs = 0, rafId = null;

// -- Leg-side indices ----------------------------------------------------
function kIdx() {
  return legSide === 'left'
    ? { hip:LM.LH, knee:LM.LK, ankle:LM.LA }
    : { hip:LM.RH, knee:LM.RK, ankle:LM.RA };
}

// -- Coordinate helpers --------------------------------------------------
// MediaPipe normalised [0,1] -> canvas drawing pixels (letterbox-aware)
function px(nx, ny) {
  const vr = vid.videoWidth / vid.videoHeight;
  const cr = cvs.width / cvs.height;
  let ox, oy, w, h;
  if (vr > cr) { w=cvs.width;  h=w/vr; ox=0; oy=(cvs.height-h)/2; }
  else          { h=cvs.height; w=h*vr; oy=0; ox=(cvs.width-w)/2;  }
  return [ox + nx*w, oy + ny*h];
}
// Canvas drawing pixels -> MediaPipe normalised
function unpx(cx, cy) {
  const vr = vid.videoWidth / vid.videoHeight;
  const cr = cvs.width / cvs.height;
  let ox, oy, w, h;
  if (vr > cr) { w=cvs.width;  h=w/vr; ox=0; oy=(cvs.height-h)/2; }
  else          { h=cvs.height; w=h*vr; oy=0; ox=(cvs.width-w)/2;  }
  return [(cx-ox)/w, (cy-oy)/h];
}
// Screen client coords -> normalised (for pointer events on #wrap)
function wrapToNorm(clientX, clientY) {
  const r  = cvs.getBoundingClientRect();
  const sx = cvs.width  / r.width;
  const sy = cvs.height / r.height;
  return unpx((clientX - r.left)*sx, (clientY - r.top)*sy);
}
// Normalised -> CSS px in #wrap space (for handle left/top)
function normToWrap(nx, ny) {
  const r  = cvs.getBoundingClientRect();
  const [dx,dy] = px(nx, ny);
  return [dx * r.width / cvs.width, dy * r.height / cvs.height];
}

// -- Pose geometry -------------------------------------------------------
function centroid(lms) {
  let sx=0, sy=0, n=0;
  for (const i of BODY_IDX) {
    const lm = lms[i];
    if (lm && (lm.visibility||0) > 0.25) { sx+=lm.x; sy+=lm.y; n++; }
  }
  return n ? [sx/n, sy/n] : [0.5, 0.5];
}

function kneeAngle(lms, k) {
  const h=lms[k.hip], kn=lms[k.knee], a=lms[k.ankle];
  if (!h||!kn||!a) return null;
  if ((h.visibility||0)<0.3||(kn.visibility||0)<0.3||(a.visibility||0)<0.3) return null;
  const v1=[h.x-kn.x, h.y-kn.y], v2=[a.x-kn.x, a.y-kn.y];
  const dot = v1[0]*v2[0]+v1[1]*v2[1];
  const mag = Math.hypot(...v1)*Math.hypot(...v2);
  return mag < 1e-6 ? null : Math.acos(Math.max(-1,Math.min(1,dot/mag)))*180/Math.PI;
}

// -- Drawing -------------------------------------------------------------
const CONN = PoseLandmarker.POSE_CONNECTIONS;

function drawSkel(lms, col, alpha, hiKpts) {
  alpha  = alpha  === undefined ? 1     : alpha;
  hiKpts = hiKpts === undefined ? false : hiKpts;
  ctx.save(); ctx.globalAlpha = alpha;
  ctx.strokeStyle = col; ctx.lineWidth = 2.5;
  for (const {start,end} of CONN) {
    const s=lms[start], e=lms[end];
    if (!s||!e||(s.visibility||0)<0.25||(e.visibility||0)<0.25) continue;
    const [x1,y1]=px(s.x,s.y), [x2,y2]=px(e.x,e.y);
    ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
  }
  if (hiKpts) {
    const k = kIdx();
    for (const [nm,idx] of [['hip',k.hip],['knee',k.knee],['ankle',k.ankle]]) {
      const lm = lms[idx]; if (!lm) continue;
      const [cx,cy] = px(lm.x, lm.y);
      ctx.beginPath(); ctx.arc(cx,cy,10,0,Math.PI*2);
      ctx.fillStyle = J_COL[nm]; ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2.5; ctx.stroke();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 9px -apple-system,sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(nm[0].toUpperCase(), cx, cy);
    }
  }
  ctx.restore();
}

function drawCorrDots() {
  for (const [nm,pos] of Object.entries(corr)) {
    if (!pos) continue;
    const [cx,cy] = px(pos.x, pos.y);
    ctx.beginPath(); ctx.arc(cx,cy,12,0,Math.PI*2);
    ctx.fillStyle = J_COL[nm]; ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2.5; ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 10px -apple-system,sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(nm[0].toUpperCase(), cx, cy);
  }
}

function refreshBadges() {
  badgeLay.innerHTML = '';
  allPoses.forEach((lms,i) => {
    const [nx,ny] = centroid(lms);
    const [bx,by] = normToWrap(nx, ny);
    const d = document.createElement('div');
    d.className = 'p-badge';
    d.textContent = i+1;
    d.style.left = bx+'px'; d.style.top = by+'px';
    d.style.borderColor = P_COL[i%4]; d.style.color = P_COL[i%4];
    d.addEventListener('pointerdown', () => doSelectPerson(i));
    badgeLay.appendChild(d);
  });
}

// -- Active person -------------------------------------------------------
function getActivePose() {
  if (selIdx === null) return allPoses[0] || null;
  if (selIdx < allPoses.length) return allPoses[selIdx];
  // fallback: closest to enrolled centroid
  if (!enroll.centroid || !allPoses.length) return null;
  let best=null, bestD=Infinity;
  allPoses.forEach(lms => {
    const [cx,cy] = centroid(lms);
    const d = (cx-enroll.centroid[0])**2 + (cy-enroll.centroid[1])**2;
    if (d<bestD) { bestD=d; best=lms; }
  });
  return best;
}

function applyCorr(lms) {
  const k = kIdx(); const out = lms.slice();
  for (const [nm,idx] of [['hip',k.hip],['knee',k.knee],['ankle',k.ankle]]) {
    if (corr[nm]) out[idx] = {...out[idx], x:corr[nm].x, y:corr[nm].y, visibility:1};
  }
  return out;
}

// -- Main animation loop -------------------------------------------------
function loop(now) {
  rafId = requestAnimationFrame(loop);
  if (state==='INIT' || state==='DONE') return;

  // Throttle detection by state
  const ms = (state==='RECORDING' || state==='COUNTDOWN') ? 67 : 200;
  if (now - lastDetMs < ms) return;
  lastDetMs = now;
  if (!landmarker || vid.readyState < 2) return;

  // Frozen states: no detection, just redraw
  if (state==='CORRECTING' || state==='CORRECT') {
    ctx.clearRect(0,0,cvs.width,cvs.height);
    const fp = frozenPoses && frozenPoses[selIdx];
    if (fp) drawSkel(fp, P_COL[(selIdx||0)%4], 0.4);
    drawCorrDots();
    return;
  }

  const res = landmarker.detectForVideo(vid, now);
  allPoses = res.landmarks || [];
  ctx.clearRect(0,0,cvs.width,cvs.height);

  if (state==='SELECT') {
    allPoses.forEach((lms,i) => drawSkel(lms, P_COL[i%4], 0.85));
    refreshBadges();

  } else if (state==='VALIDATE') {
    const pose = getActivePose();
    if (pose) drawSkel(pose, P_COL[(selIdx||0)%4], 1, true);

  } else if (state==='READY' || state==='COUNTDOWN') {
    const pose = getActivePose();
    if (pose) drawSkel(pose, '#22c55e', 1, true);

  } else if (state==='RECORDING') {
    let pose = getActivePose();
    if (pose) {
      pose = applyCorr(pose);
      drawSkel(pose, '#22c55e', 1, true);
      const ang = kneeAngle(pose, kIdx());
      if (ang !== null) {
        const rounded = Math.round(ang*10)/10;
        angleHist.push({t:now, angle:rounded});
        anglDisp.textContent = rounded.toFixed(1) + 'deg';
      }
    }
  }
}

// -- Handle placement & drag ---------------------------------------------
function placeHandles(pose) {
  const k = kIdx();
  for (const [nm,idx] of [['hip',k.hip],['knee',k.knee],['ankle',k.ankle]]) {
    const lm = (pose && pose[idx]) || enroll[nm];
    if (!lm) continue;
    const [wx,wy] = normToWrap(lm.x, lm.y);
    const h = handles[nm];
    h.style.left = wx+'px'; h.style.top = wy+'px'; h.style.display = 'flex';
    corr[nm] = {x:lm.x, y:lm.y};
  }
}

Object.entries(handles).forEach(([nm,el]) => {
  let drag = false;
  el.addEventListener('pointerdown', e => {
    drag = true; el.setPointerCapture(e.pointerId);
    e.stopPropagation(); e.preventDefault();
  });
  el.addEventListener('pointermove', e => {
    if (!drag) return;
    const wr = document.getElementById('wrap').getBoundingClientRect();
    const wx = e.clientX - wr.left;
    const wy = e.clientY - wr.top;
    el.style.left = wx+'px'; el.style.top = wy+'px';
    // convert wrap CSS px -> canvas px -> normalised
    const cr = cvs.getBoundingClientRect();
    const scx = wx * (cvs.width  / cr.width);
    const scy = wy * (cvs.height / cr.height);
    const [nx,ny] = unpx(scx, scy);
    corr[nm] = {x:nx, y:ny};
  });
  el.addEventListener('pointerup',     () => { drag = false; });
  el.addEventListener('pointercancel', () => { drag = false; });
});

// -- State machine -------------------------------------------------------
function go(s) {
  state = s;
  document.querySelectorAll('.s-panel').forEach(el => el.classList.remove('on'));
  const p = document.getElementById('p-'+s.toLowerCase());
  if (p) p.classList.add('on');

  // Reset all overlays
  initOv.style.display   = 'none';
  cdOv.style.display     = 'none';
  topBanner.style.display= 'none';
  recInd.style.display   = 'none';
  anglDisp.style.display = 'none';
  corrBanner.style.display = 'none';
  badgeLay.innerHTML     = '';
  Object.values(handles).forEach(h => h.style.display = 'none');

  if (s==='SELECT') {
    topBanner.textContent = 'Tap the person you want to track';
    topBanner.style.display = 'block';
  }
  if (s==='CORRECT') {
    corrBanner.style.display = 'block';
    frozenPoses = allPoses.slice();
    placeHandles(allPoses[selIdx]);
  }
  if (s==='COUNTDOWN') { cdOv.style.display = 'flex'; }
  if (s==='RECORDING') {
    recInd.style.display  = 'block';
    anglDisp.style.display = 'block';
    openChart();
  }
  if (s==='CORRECTING') {
    recInd.style.display  = 'block';
    corrBanner.style.display = 'block';
    frozenPoses = allPoses.slice();
    placeHandles(allPoses[selIdx]);
  }
  // DONE: chart stays open
  if (s==='READY') { closeChart(); corr.hip=corr.knee=corr.ankle=null; }
}

// -- User actions (on window for onclick attrs) ---------------------------
function doSelectPerson(i) {
  selIdx = i;
  enroll.centroid = centroid(allPoses[i]);
  go('VALIDATE');
}

window.setLeg = function(side) {
  legSide = side;
  document.getElementById('leg-L').classList.toggle('on', side==='left');
  document.getElementById('leg-R').classList.toggle('on', side==='right');
};

window.onLooksGood = function() {
  const pose = getActivePose();
  if (pose) {
    const k = kIdx();
    enroll.hip   = {x:pose[k.hip].x,   y:pose[k.hip].y};
    enroll.knee  = {x:pose[k.knee].x,  y:pose[k.knee].y};
    enroll.ankle = {x:pose[k.ankle].x, y:pose[k.ankle].y};
  }
  go('READY');
};

window.onFixSkeleton = function() {
  corr.hip = corr.knee = corr.ankle = null;
  go('CORRECT');
};

window.onConfirmCorrections = function() {
  // Corrected positions become enrollment reference
  for (const nm of ['hip','knee','ankle']) {
    if (corr[nm]) enroll[nm] = {x:corr[nm].x, y:corr[nm].y};
  }
  corr.hip = corr.knee = corr.ankle = null;
  frozenPoses = null;
  go('READY');
};

window.onRetap = function() {
  selIdx = null; corr.hip = corr.knee = corr.ankle = null; frozenPoses = null;
  go('SELECT');
};

window.startWithDelay = function(sec) {
  if (sec===0) { startRec(); return; }
  go('COUNTDOWN'); cdVal.textContent = sec;
  let r = sec;
  cdHandle = setInterval(() => {
    r--; cdVal.textContent = r;
    if (r<=0) { clearInterval(cdHandle); startRec(); }
  }, 1000);
};

window.cancelCountdown = function() { clearInterval(cdHandle); go('READY'); };

function startRec() {
  chunks = []; angleHist = [];
  const stream = vid.srcObject;
  let mime = '';
  for (const t of ['video/mp4;codecs=h264','video/mp4','video/webm;codecs=vp9','video/webm']) {
    if (MediaRecorder.isTypeSupported(t)) { mime=t; break; }
  }
  recorder = new MediaRecorder(stream, mime ? {mimeType:mime} : {});
  recorder.ondataavailable = e => { if (e.data.size>0) chunks.push(e.data); };
  recorder.start(500);
  isRec = true;
  go('RECORDING');
}

window.stopRecording = function() {
  if (recorder && recorder.state!=='inactive') recorder.stop();
  isRec = false; go('DONE');
};

window.enterMidCorrection = function() {
  corr.hip = corr.knee = corr.ankle = null;
  go('CORRECTING');
};

window.exitMidCorrection = function() {
  frozenPoses = null; go('RECORDING');
};

window.goToReady = function() { closeChart(); go('READY'); };

// -- Chart ---------------------------------------------------------------
function openChart() {
  chartPanel.style.height = CHART_H+'px';
  chartTick = setInterval(drawChart, 125);
}
function closeChart() {
  chartPanel.style.height = '0';
  if (chartTick) { clearInterval(chartTick); chartTick=null; }
  angleHist = [];
}
function drawChart() {
  const W = chartCvs.clientWidth; if (!W) return;
  chartCvs.width = W; chartCvs.height = CHART_H;
  chartCtx.fillStyle = '#060914'; chartCtx.fillRect(0,0,W,CHART_H);
  const now = performance.now(), t0 = now - CHART_WIN;
  const pts = angleHist.filter(p => p.t >= t0);
  const yMap = a => CHART_H - (a-Y_MIN)/(Y_MAX-Y_MIN)*(CHART_H-24) - 12;
  // grid lines
  chartCtx.strokeStyle = '#1e293b'; chartCtx.lineWidth = 1;
  for (let a=100; a<=180; a+=20) {
    const y = yMap(a);
    chartCtx.beginPath(); chartCtx.moveTo(0,y); chartCtx.lineTo(W,y); chartCtx.stroke();
    chartCtx.fillStyle = '#334155'; chartCtx.font = '9px -apple-system,sans-serif';
    chartCtx.fillText(a+'deg', 3, y-2);
  }
  // neutral ref
  chartCtx.strokeStyle = 'rgba(148,163,184,.2)'; chartCtx.setLineDash([4,4]);
  const yR = yMap(127);
  chartCtx.beginPath(); chartCtx.moveTo(0,yR); chartCtx.lineTo(W,yR); chartCtx.stroke();
  chartCtx.setLineDash([]);
  if (pts.length < 2) return;
  // waveform
  chartCtx.strokeStyle = '#0ea5e9'; chartCtx.lineWidth = 2; chartCtx.beginPath();
  pts.forEach((p,i) => {
    const x = (p.t-t0)/CHART_WIN*W, y = yMap(p.angle);
    i===0 ? chartCtx.moveTo(x,y) : chartCtx.lineTo(x,y);
  });
  chartCtx.stroke();
  // current angle badge
  const last = pts[pts.length-1];
  const bx = Math.min(W-56, (last.t-t0)/CHART_WIN*W);
  chartCtx.fillStyle = '#0ea5e9'; chartCtx.font = 'bold 13px -apple-system,sans-serif';
  chartCtx.fillText(last.angle.toFixed(1)+'deg', bx, 16);
}

// -- Upload --------------------------------------------------------------
window.upload = function() {
  const blob = new Blob(chunks, {type: recorder?.mimeType||'video/mp4'});
  const ext  = blob.type.includes('mp4') ? '.mp4' : '.webm';
  const xhr  = new XMLHttpRequest();
  xhr.open('POST', '/upload?name='+encodeURIComponent('pendulum'+ext));
  xhr.setRequestHeader('Content-Type', blob.type);
  progWrap.style.display = 'block';
  xhr.upload.onprogress = e => { if(e.lengthComputable) progFill.style.width=(e.loaded/e.total*100)+'%'; };
  xhr.onload = () => { fbEl.textContent=xhr.status===200?'Sent!':'Error '+xhr.status; fbEl.style.display='block'; };
  xhr.send(blob);
};

window.uploadFile = function(inp) {
  const file = inp.files[0]; if (!file) return;
  const rawExt = file.name.split('.').pop().toLowerCase();
  const ext = ['mp4','mov','avi','m4v','mkv','webm'].includes(rawExt) ? rawExt : 'mp4';
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/upload?name='+encodeURIComponent('pendulum.'+ext));
  xhr.setRequestHeader('Content-Type', file.type||'video/mp4');
  progWrap.style.display = 'block';
  xhr.upload.onprogress = e => { if(e.lengthComputable) progFill.style.width=(e.loaded/e.total*100)+'%'; };
  xhr.onload = () => { fbEl.textContent=xhr.status===200?'Sent!':'Error '+xhr.status; fbEl.style.display='block'; };
  xhr.send(file);
};

// -- Tap on wrap to select nearest person --------------------------------
document.getElementById('wrap').addEventListener('pointerdown', e => {
  if (state !== 'SELECT' || !allPoses.length) return;
  const [nx,ny] = wrapToNorm(e.clientX, e.clientY);
  let best=null, bestD=Infinity;
  allPoses.forEach((lms,i) => {
    const [cx,cy] = centroid(lms);
    const d = (cx-nx)**2 + (cy-ny)**2;
    if (d<bestD) { bestD=d; best=i; }
  });
  if (best !== null) doSelectPerson(best);
});

// -- Canvas auto-resize --------------------------------------------------
new ResizeObserver(() => {
  cvs.width  = cvs.offsetWidth  || 1;
  cvs.height = cvs.offsetHeight || 1;
}).observe(cvs);

// -- Init ----------------------------------------------------------------
async function init() {
  try {
    initMsg.textContent = 'Connecting to MediaPipe CDN...';
    const vision = await FilesetResolver.forVisionTasks(
      "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.22/wasm"
    );
    initMsg.textContent = 'Loading pose model...';
    landmarker = await PoseLandmarker.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
        delegate: "GPU"
      },
      runningMode: "VIDEO",
      numPoses: 4,
      minPoseDetectionConfidence: 0.4,
      minPosePresenceConfidence:  0.4,
      minTrackingConfidence:      0.4
    });
    initMsg.textContent = 'Opening camera...';
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {facingMode:'environment', width:{ideal:1280}, height:{ideal:720}},
      audio: false
    });
    vid.srcObject = stream;
    await new Promise(res => { vid.onloadedmetadata = res; });
    vid.play();
    cvs.width  = cvs.offsetWidth  || vid.videoWidth;
    cvs.height = cvs.offsetHeight || vid.videoHeight;
    initOv.style.display = 'none';
    go('SELECT');
    rafId = requestAnimationFrame(loop);
  } catch(e) {
    initOv.style.display = 'none';
    document.getElementById('cdn-fail').style.display = 'flex';
    document.getElementById('cdn-fail-msg').textContent = String(e);
    document.getElementById('p-init').classList.remove('on');
  }
}

init();
</script>
</body>
</html>
"""


def _build_page(ws_host: str, ws_port: int) -> bytes:
    return _TRACKING_PAGE.encode("utf-8")


# ─── HTTP server ──────────────────────────────────────────────────────────────

class _PageHandler(BaseHTTPRequestHandler):
    """Route HTTP requests: serve the browser page, handle API calls and uploads."""
    _page: bytes = b""

    # ── helpers ───────────────────────────────────────────────────────────────

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Ngrok-Skip-Browser-Warning", "true")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200) -> None:
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Ngrok-Skip-Browser-Warning", "true")
        self.end_headers()
        self.wfile.write(body)

    def _extract_multipart_video(self) -> tuple[str | None, bytes | None]:
        """Parse multipart/form-data body and return (filename, video_bytes)."""
        ct = self.headers.get("Content-Type", "")
        cl = int(self.headers.get("Content-Length", 0))
        boundary = None
        for seg in ct.split(";"):
            seg = seg.strip()
            if seg.startswith("boundary="):
                boundary = seg[9:].strip('"\'')
        if not boundary:
            return None, None

        body = self.rfile.read(cl)
        sep  = b"--" + boundary.encode()

        for raw_part in body.split(sep)[1:]:
            if raw_part.strip() in (b"--", b"--\r\n", b""):
                break
            if raw_part.startswith(b"\r\n"):
                raw_part = raw_part[2:]
            if b"\r\n\r\n" not in raw_part:
                continue
            hdr_raw, data = raw_part.split(b"\r\n\r\n", 1)
            if data.endswith(b"\r\n"):
                data = data[:-2]
            hdr = hdr_raw.decode("utf-8", errors="replace")
            if 'name="video"' not in hdr and "name='video'" not in hdr:
                continue
            m = re.search(r'filename=["\']([^"\']+)["\']', hdr)
            filename = m.group(1) if m else "pendulum.mp4"
            return filename, data

        return None, None

    def _save_video(self, filename: str, data: bytes) -> str:
        """Sanitise filename, write bytes, return saved path."""
        safe = "".join(c for c in os.path.basename(filename) if c.isalnum() or c in "._- ")
        if not safe:
            safe = "pendulum"
        root, ext = os.path.splitext(safe)
        if ext.lower() not in (".mp4", ".mov", ".avi", ".mkv", ".m4v"):
            ext = ".mp4"
        os.makedirs(_upload_dir, exist_ok=True)
        ts       = int(time.time())
        out_path = os.path.join(_upload_dir, f"{root}_{ts}{ext}")
        with open(out_path, "wb") as f:
            f.write(data)
        try:
            upload_queue.put_nowait(out_path)
        except Exception:
            pass
        return out_path

    # ── GET routing ───────────────────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")

        if path == "/api/participants":
            # Return a single beta/dev participant so the app's participant
            # screen is populated and the Record tab's CTA becomes enabled.
            self._send_json([{
                "id":         "beta",
                "first_name": "Beta",
                "last_name":  "Tester",
                "created_at": "2025-01-01T00:00:00",
            }])

        elif re.match(r"^/api/trials/[^/]+$", path):
            # Return a minimal trial status so the Review/Analysis screens
            # don't error out while polling.
            tid = path.split("/")[-1]
            self._send_json({
                "id":             tid,
                "participant_id": "beta",
                "status":         "pending_review",
                "leg_side":       "right",
                "hpe_model":      "mediapipe",
                "created_at":     str(int(time.time())),
            })

        elif path == "/model/pose_landmarker.task":
            # Serve the local MediaPipe pose model file to the phone browser.
            _base = os.path.dirname(os.path.abspath(__file__))
            _candidates = [
                os.path.join(_base, "models", "mediapipe", "pose_landmarker_full.task"),
                os.path.join(_base, "models", "mediapipe", "pose_landmarker_lite.task"),
                os.path.join(_base, "models", "mediapipe", "pose_landmarker_heavy.task"),
                os.path.join(_base, "models", "pose_landmarker_full.task"),
                os.path.join(_base, "models", "pose_landmarker_lite.task"),
                os.path.join(_base, "models", "pose_landmarker_heavy.task"),
                os.path.join(_base, "models", "pose_landmarker.task"),
            ]
            model_path = next((p for p in _candidates if os.path.exists(p)), None)
            if model_path is None:
                self._send_text("pose model not found", 404)
                return
            try:
                with open(model_path, "rb") as _f:
                    data = _f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.send_header("Ngrok-Skip-Browser-Warning", "true")
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                self._send_text(str(exc), 500)

        else:
            # Default: serve the browser-based capture/upload page.
            # no-cache so the phone always gets the latest version after an app update.
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_PageHandler._page)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Ngrok-Skip-Browser-Warning", "true")
            self.end_headers()
            self.wfile.write(_PageHandler._page)

    # ── POST routing ──────────────────────────────────────────────────────────

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")

        if path == "/api/trials/upload":
            # Mobile app multipart upload (expo-image-picker → FormData)
            filename, data = self._extract_multipart_video()
            if data is None:
                self._send_text("No video field in multipart body", 400)
                return
            ts       = int(time.time())
            out_path = self._save_video(filename or "pendulum.mp4", data)
            self._send_json({
                "id":             f"trial_{ts}",
                "participant_id": "beta",
                "status":         "pending_review",
                "leg_side":       "right",
                "hpe_model":      "mediapipe",
                "video_path":     out_path,
                "created_at":     str(ts),
            })

        elif path == "/api/trials/record":
            # Live camera mode — return a mock trial so the app proceeds to
            # the camera view (live tracking still needs the full HPE backend).
            ts = int(time.time())
            self._send_json({
                "id":             f"trial_{ts}",
                "participant_id": "beta",
                "status":         "recording",
                "leg_side":       "right",
                "hpe_model":      "mediapipe",
                "created_at":     str(ts),
            })

        elif path == "/upload":
            # Browser-based raw upload (the phone-browser HTML page)
            parsed   = urlparse(self.path)
            params   = parse_qs(parsed.query)
            raw_name = unquote(params.get("name", ["pendulum.mp4"])[0])
            cl       = int(self.headers.get("Content-Length", 0))
            CHUNK    = 256 * 1024
            safe     = "".join(c for c in os.path.basename(raw_name)
                               if c.isalnum() or c in "._- ") or "pendulum"
            root, ext = os.path.splitext(safe)
            if ext.lower() not in (".mp4", ".mov", ".avi", ".mkv", ".m4v"):
                ext = ".mp4"
            os.makedirs(_upload_dir, exist_ok=True)
            ts       = int(time.time())
            out_path = os.path.join(_upload_dir, f"{root}_{ts}{ext}")
            try:
                with open(out_path, "wb") as f:
                    remaining = cl
                    while remaining > 0:
                        chunk = self.rfile.read(min(CHUNK, remaining))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)
            except Exception as exc:
                self._send_text(str(exc), 500)
                return
            try:
                upload_queue.put_nowait(out_path)
            except Exception:
                pass
            self._send_text("ok")

        else:
            self._send_text("Not found", 404)

    def log_message(self, *_):
        pass


# ─── WebSocket server (pure stdlib — future streaming support) ─────────────────

async def _ws_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Handle one WebSocket connection: HTTP upgrade → JPEG frame loop."""
    try:
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=10.0)
            if not chunk:
                return
            raw += chunk

        ws_key: str | None = None
        for line in raw.split(b"\r\n"):
            if line.lower().startswith(b"sec-websocket-key:"):
                ws_key = line.split(b":", 1)[1].strip().decode("ascii")
                break

        if not ws_key:
            writer.close()
            return

        accept = base64.b64encode(
            hashlib.sha1((ws_key + _WS_MAGIC).encode("ascii")).digest()
        ).decode("ascii")

        writer.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n\r\n"
        )
        await writer.drain()
    except Exception:
        try:
            writer.close()
        except Exception:
            pass
        return

    try:
        while True:
            hdr     = await asyncio.wait_for(reader.readexactly(2), timeout=60.0)
            opcode  = hdr[0] & 0x0F
            is_mask = bool(hdr[1] & 0x80)
            plen    = hdr[1] & 0x7F

            if plen == 126:
                ext  = await asyncio.wait_for(reader.readexactly(2), timeout=5.0)
                plen = struct.unpack(">H", ext)[0]
            elif plen == 127:
                ext  = await asyncio.wait_for(reader.readexactly(8), timeout=5.0)
                plen = struct.unpack(">Q", ext)[0]

            mask_key = b""
            if is_mask:
                mask_key = await asyncio.wait_for(reader.readexactly(4), timeout=5.0)

            payload = b""
            if plen > 0:
                payload = await asyncio.wait_for(reader.readexactly(plen), timeout=15.0)

            if is_mask and mask_key and payload:
                pa = np.frombuffer(payload, dtype=np.uint8).copy()
                mk = np.frombuffer(mask_key, dtype=np.uint8)
                full_mask = np.tile(mk, (len(pa) + 3) // 4)[: len(pa)]
                pa ^= full_mask
                payload = pa.tobytes()

            if opcode == 0x8:
                break
            elif opcode == 0x9:
                pong = payload[:125]
                writer.write(bytes([0x8A, len(pong)]) + pong)
                await writer.drain()
            elif opcode == 0x2 and payload:
                arr   = np.frombuffer(payload, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    if frame_queue.full():
                        try:
                            frame_queue.get_nowait()
                        except Exception:
                            pass
                    try:
                        frame_queue.put_nowait(frame)
                    except Exception:
                        pass
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


# ─── public API ───────────────────────────────────────────────────────────────

class _ReuseHTTPServer(HTTPServer):
    allow_reuse_address = True


def start(upload_dir: str = "uploads") -> tuple[str, int, int]:
    """Start HTTP and WebSocket servers in background daemon threads.

    Returns (local_ip, http_port, ws_port).  Idempotent if already running.
    """
    global _http_server, _http_thread, _ws_thread, _ws_loop
    global _tcp_server, _running, _local_ip, _upload_dir
    global _public_url, _ngrok_status, _ngrok_error, _ngrok_tunnel

    if _running:
        return _local_ip, PORT_HTTP, PORT_WS

    stop()

    _upload_dir = upload_dir
    _local_ip   = get_local_ip()
    _PageHandler._page = _build_page(_local_ip, PORT_WS)

    _http_server = _ReuseHTTPServer(("0.0.0.0", PORT_HTTP), _PageHandler)
    _http_thread = threading.Thread(
        target=_http_server.serve_forever, daemon=True, name="pps-http")
    _http_thread.start()

    _ws_loop = asyncio.new_event_loop()

    async def _launch():
        global _tcp_server
        _tcp_server = await asyncio.start_server(
            _ws_client, "0.0.0.0", PORT_WS, reuse_address=True)

    try:
        _ws_loop.run_until_complete(_launch())
    except Exception:
        try:
            _http_server.shutdown()
        except Exception:
            pass
        _http_server = None
        raise

    _ws_thread = threading.Thread(
        target=_ws_loop.run_forever, daemon=True, name="pps-ws")
    _ws_thread.start()

    _running = True

    # Reset ngrok state and start tunnel (provides HTTPS from any network)
    _public_url   = None
    _ngrok_status = "not_started"
    _ngrok_error  = ""
    _ngrok_tunnel = None
    threading.Thread(target=_ngrok_worker, args=(PORT_HTTP,),
                     daemon=True, name="pps-ngrok").start()

    return _local_ip, PORT_HTTP, PORT_WS


def stop():
    """Shut down both servers and drain queues."""
    global _http_server, _tcp_server, _ws_loop, _running
    global _public_url, _ngrok_status, _ngrok_tunnel

    _running = False

    # Disconnect ngrok tunnel if active
    try:
        if _ngrok_tunnel is not None:
            from pyngrok import ngrok
            ngrok.disconnect(_ngrok_tunnel.public_url)
    except Exception:
        pass
    _public_url   = None
    _ngrok_status = "not_started"
    _ngrok_tunnel = None

    try:
        if _http_server:
            _http_server.shutdown()
    except Exception:
        pass
    _http_server = None

    try:
        if _tcp_server and _ws_loop:
            _ws_loop.call_soon_threadsafe(_tcp_server.close)
    except Exception:
        pass
    _tcp_server = None

    for q in (frame_queue, upload_queue):
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break

    _running = False


# ─── standalone entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    import pathlib
    _here = pathlib.Path(__file__).parent
    _udir = str(_here / "uploads")

    print("Starting Pendulastic backend server…")
    ip, http_port, ws_port = start(upload_dir=_udir)

    all_ips = get_all_local_ips()
    print(f"\n  Local URL : http://{ip}:{http_port}")
    if len(all_ips) > 1:
        print("  Alternates:")
        for a in all_ips[1:]:
            print(f"    http://{a}:{http_port}")
    print("\n  Waiting for ngrok HTTPS tunnel (works from any network)…")

    # Wait up to 15 s for ngrok to connect, then print the result
    for _ in range(15):
        time.sleep(1)
        if _ngrok_status == "ready" and _public_url:
            print(f"\n  HTTPS URL : {_public_url}  ← open this on your phone")
            print(  "             (works on cellular too)\n")
            break
        if _ngrok_status == "error":
            print(f"\n  ngrok tunnel failed: {_ngrok_error}")
            print("  → Use the local URL above if phone is on the same WiFi.")
            print("  → For any-network access: ngrok.com → free account → run")
            print("      ngrok authtoken YOUR_TOKEN\n")
            break
    else:
        if _public_url:
            print(f"\n  HTTPS URL : {_public_url}\n")
        else:
            print("\n  (ngrok still connecting — check back shortly)\n")

    print("Uploaded videos are saved to:", _udir)
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            if _ngrok_status == "ready" and _public_url:
                pass  # already printed above
            try:
                path = upload_queue.get(timeout=1.0)
                print(f"[upload] Saved: {path}")
            except queue.Empty:
                pass
    except KeyboardInterrupt:
        print("\nStopping…")
        stop()
