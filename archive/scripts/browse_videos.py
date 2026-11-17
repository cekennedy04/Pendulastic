"""
browse_videos.py
================
Video browser for annotated pose-estimation outputs.

Run:
    .venv\\Scripts\\python.exe browse_videos.py
Open:
    http://localhost:5051
"""
from __future__ import annotations

import glob
import os
import re

from flask import Flask, Response, jsonify, render_template_string, request

BASE_DIR    = r"C:\Users\cladi\Pendulastic"
RECORD_ROOT = os.path.join(BASE_DIR, "Recordings")

_ANN_RE = re.compile(
    r"^P_(.+?)_Pos_(.+?)_H_(.+?)_T_(\d+)_([A-Za-z]\w*)_([\w.]+)_([\d.]+)_annotated\.mp4$",
    re.I,
)

app = Flask(__name__)


def _discover() -> list[dict]:
    results = []
    for path in glob.glob(os.path.join(RECORD_ROOT, "**", "*_annotated.mp4"), recursive=True):
        bn = os.path.basename(path)
        m = _ANN_RE.match(bn)
        if not m:
            continue
        pid, pos, height, trial, family, variant, thresh = m.groups()
        results.append({
            "pid":     pid,
            "pos":     pos,
            "trial":   trial,
            "family":  family,
            "variant": variant,
            "label":   f"{family}-{variant}",
            "rel":     os.path.relpath(path, RECORD_ROOT).replace("\\", "/"),
        })
    results.sort(key=lambda r: (r["pid"], r["pos"], int(r["trial"]), r["family"], r["variant"]))
    return results


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/videos")
def api_videos():
    return jsonify(_discover())


@app.route("/video")
def serve_video():
    from flask import Response
    rel = request.args.get("path", "")
    abs_path = os.path.normpath(os.path.join(RECORD_ROOT, rel.replace("/", os.sep)))
    if not abs_path.startswith(os.path.normpath(RECORD_ROOT)):
        return "Forbidden", 403
    if not os.path.isfile(abs_path):
        return "Not found", 404

    file_size = os.path.getsize(abs_path)
    range_hdr = request.headers.get("Range")

    if not range_hdr:
        # No Range header — serve the whole file with Accept-Ranges so the browser
        # knows it can seek (some browsers send a Range request after this).
        def _stream_full():
            with open(abs_path, "rb") as f:
                while True:
                    chunk = f.read(1 << 16)
                    if not chunk:
                        break
                    yield chunk
        return Response(_stream_full(), status=200, mimetype="video/mp4",
                        headers={"Content-Length": str(file_size),
                                 "Accept-Ranges": "bytes"})

    # Parse "bytes=start-end"
    m = re.search(r"bytes=(\d+)-(\d*)", range_hdr)
    byte1 = int(m.group(1)) if m else 0
    byte2 = int(m.group(2)) if m and m.group(2) else file_size - 1
    byte2 = min(byte2, file_size - 1)
    length = byte2 - byte1 + 1

    def _stream_range():
        with open(abs_path, "rb") as f:
            f.seek(byte1)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1 << 16, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return Response(_stream_range(), status=206, mimetype="video/mp4",
                    headers={
                        "Content-Range":  f"bytes {byte1}-{byte2}/{file_size}",
                        "Content-Length": str(length),
                        "Accept-Ranges":  "bytes",
                    })


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Pendulastic — Video Browser</title>
<style>
  :root {
    --bg:      #0f0f1a;
    --panel:   #1a1a2e;
    --border:  #2a2a4a;
    --accent:  #4f8ef7;
    --accent2: #00d4aa;
    --text:    #e0e0f0;
    --muted:   #888899;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text);
         font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }

  /* ── top bar ── */
  .topbar { background: var(--panel); border-bottom: 1px solid var(--border);
            padding: 14px 24px; display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
  .logo   { font-size: 18px; font-weight: 700; color: var(--accent); letter-spacing: 1px;
             white-space: nowrap; }
  .filters { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
  .field  { display: flex; flex-direction: column; gap: 4px; }
  label   { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .8px; }
  select  { background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
            padding: 7px 12px; color: var(--text); font-size: 13px; cursor: pointer;
            outline: none; min-width: 130px; }
  select:focus { border-color: var(--accent); }

  .btn { border: none; border-radius: 8px; padding: 8px 16px; font-size: 13px;
         font-weight: 600; cursor: pointer; transition: filter .12s; }
  .btn-play  { background: var(--accent2); color: #000; }
  .btn-pause { background: #e65100; color: #fff; }
  .btn-sync  { background: var(--accent); color: #fff; }
  .btn:hover { filter: brightness(1.2); }

  .count { margin-left: auto; color: var(--muted); font-size: 12px; white-space: nowrap; }

  /* ── grid ── */
  .grid { display: grid;
          grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
          gap: 16px; padding: 20px; }

  .card { background: var(--panel); border: 1px solid var(--border);
          border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; }
  .card-head { padding: 10px 14px; display: flex; align-items: center; gap: 10px;
               border-bottom: 1px solid var(--border); }
  .model-tag { background: var(--accent); color: #fff; font-size: 11px; font-weight: 700;
               border-radius: 6px; padding: 3px 8px; text-transform: uppercase;
               letter-spacing: .5px; }
  .trial-info { font-size: 12px; color: var(--muted); }
  .fullscreen-btn { margin-left: auto; background: none; border: 1px solid var(--border);
                    color: var(--muted); border-radius: 6px; padding: 3px 8px;
                    cursor: pointer; font-size: 12px; }
  .fullscreen-btn:hover { border-color: var(--accent); color: var(--accent); }

  video { width: 100%; display: block; background: #000; max-height: 300px; object-fit: contain; }

  .empty { padding: 60px 24px; text-align: center; color: var(--muted); }
  .empty p { margin-top: 8px; font-size: 13px; }
</style>
</head>
<body>

<div class="topbar">
  <span class="logo">PENDULASTIC &mdash; Videos</span>
  <div class="filters">
    <div class="field"><label>Participant</label>
      <select id="sel-pid" onchange="onFilter()"><option value="">All</option></select></div>
    <div class="field"><label>Position</label>
      <select id="sel-pos" onchange="onFilter()"><option value="">All</option></select></div>
    <div class="field"><label>Trial</label>
      <select id="sel-trial" onchange="onFilter()"><option value="">All</option></select></div>
    <div class="field"><label>Model</label>
      <select id="sel-model" onchange="onFilter()"><option value="">All models</option></select></div>
  </div>
  <button class="btn btn-play"  onclick="playAll()">&#9654; Play all</button>
  <button class="btn btn-pause" onclick="pauseAll()">&#9646;&#9646; Pause all</button>
  <button class="btn btn-sync"  onclick="syncAll()" title="Restart all from 0:00">&#8635; Sync</button>
  <span class="count" id="count">0 videos</span>
</div>

<div class="grid" id="grid"></div>

<script>
let allVideos = [];

async function init() {
  const resp = await fetch('/api/videos');
  allVideos = await resp.json();
  populateSelectors();
  render();
}

function unique(arr, key) {
  return [...new Set(arr.map(v => v[key]))].sort();
}

function populateSelectors() {
  function fill(id, vals, prefix) {
    const sel = document.getElementById(id);
    const cur = sel.value;
    sel.innerHTML = `<option value="">${prefix}</option>` +
      vals.map(v => `<option value="${v}">${v}</option>`).join('');
    if (vals.includes(cur)) sel.value = cur;
  }
  fill('sel-pid',   unique(allVideos, 'pid'),   'All participants');
  fill('sel-pos',   unique(allVideos, 'pos'),   'All positions');
  fill('sel-trial', unique(allVideos, 'trial'), 'All trials');
  fill('sel-model', unique(allVideos, 'label'), 'All models');
}

function filtered() {
  const pid   = document.getElementById('sel-pid').value;
  const pos   = document.getElementById('sel-pos').value;
  const trial = document.getElementById('sel-trial').value;
  const model = document.getElementById('sel-model').value;
  return allVideos.filter(v =>
    (!pid   || v.pid   === pid)   &&
    (!pos   || v.pos   === pos)   &&
    (!trial || v.trial === trial) &&
    (!model || v.label === model)
  );
}

function onFilter() { render(); }

function render() {
  const vids = filtered();
  document.getElementById('count').textContent = `${vids.length} video${vids.length !== 1 ? 's' : ''}`;
  const grid = document.getElementById('grid');

  if (!vids.length) {
    grid.innerHTML = `<div class="empty"><b>No videos match your filters.</b>
      <p>Run inference first: <code>.venv\\Scripts\\python.exe -u pendulastic_pipeline.py</code></p></div>`;
    return;
  }

  grid.innerHTML = vids.map(v => {
    const src  = `/video?path=${encodeURIComponent(v.rel)}`;
    const info = `P${v.pid} &nbsp;|&nbsp; Pos${v.pos} &nbsp;|&nbsp; T${v.trial}`;
    return `
      <div class="card">
        <div class="card-head">
          <span class="model-tag">${v.label}</span>
          <span class="trial-info">${info}</span>
          <button class="fullscreen-btn" onclick="goFull(this.closest('.card').querySelector('video'))">&#x26F6;</button>
        </div>
        <video src="${src}" controls preload="metadata" loop></video>
      </div>`;
  }).join('');
}

function allVideoEls() {
  return [...document.querySelectorAll('#grid video')];
}
function playAll()  { allVideoEls().forEach(v => v.play()); }
function pauseAll() { allVideoEls().forEach(v => v.pause()); }
function syncAll()  { allVideoEls().forEach(v => { v.currentTime = 0; v.play(); }); }
function goFull(vid) { if (vid.requestFullscreen) vid.requestFullscreen(); }

init();
</script>
</body>
</html>"""

if __name__ == "__main__":
    import webbrowser, threading
    def _open():
        import time; time.sleep(0.8)
        webbrowser.open("http://localhost:5051")
    threading.Thread(target=_open, daemon=True).start()
    print("Pendulastic video browser -> http://localhost:5051")
    app.run(host="0.0.0.0", port=5051, debug=False)
