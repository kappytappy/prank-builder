"""Prank Builder - web UI to customize the prank and download your prank kit.

Drag & drop images + a song, set the chaos level, hit build: you get a
zip with prank-popups.exe and prank-data.pak. Copy both to a USB stick,
double-click the exe on the target PC. ESC stops it.
"""
import io
import json
import base64
import os
import sys
import tempfile
import urllib.request
import zipfile

from flask import Flask, request, render_template, send_file, jsonify

try:
    import version
    APP_VERSION = version.APP_VERSION
except Exception:
    APP_VERSION = "dev"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300 MB

PRANK_REPO = "kappytappy/prank-popups"
EXE_NAME = "prank-popups.exe"
EXE_CACHE_DIR = os.path.join(tempfile.gettempdir(), "prank-builder-exe")

IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
MUSIC_EXTS = (".mp3", ".wav", ".ogg")


def _latest_release():
    """Newest release that actually contains the Windows exe asset."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{PRANK_REPO}/releases?per_page=20",
        headers={"User-Agent": "prank-builder", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        releases = json.load(r)
    for rel in releases:
        if rel.get("draft"):
            continue
        for a in rel.get("assets", []):
            if a.get("name") == EXE_NAME:
                return rel
    raise RuntimeError(f"{EXE_NAME} not found in any recent release yet.")


def get_exe():
    """Return (tag_name, exe_bytes), cached on disk."""
    os.makedirs(EXE_CACHE_DIR, exist_ok=True)
    meta_path = os.path.join(EXE_CACHE_DIR, "meta.json")
    exe_path = os.path.join(EXE_CACHE_DIR, EXE_NAME)
    tag = None
    if os.path.isfile(meta_path):
        try:
            tag = json.load(open(meta_path))["tag"]
        except Exception:
            tag = None
    try:
        rel = _latest_release()
    except Exception as e:
        if tag and os.path.isfile(exe_path):
            return tag, open(exe_path, "rb").read()
        raise RuntimeError(f"Could not reach GitHub releases: {e}")
    new_tag = rel.get("tag_name", "unknown")
    url = None
    for a in rel.get("assets", []):
        if a.get("name") == EXE_NAME:
            url = a.get("browser_download_url")
            break
    if not url:
        raise RuntimeError("prank-popups.exe not found in the latest release yet.")
    if tag == new_tag and os.path.isfile(exe_path):
        return tag, open(exe_path, "rb").read()
    req = urllib.request.Request(url, headers={"User-Agent": "prank-builder"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    with open(exe_path, "wb") as f:
        f.write(data)
    with open(meta_path, "w") as f:
        json.dump({"tag": new_tag}, f)
    return new_tag, data


def chaos_to_interval(chaos):
    """1 (chill) .. 10 (maximum chaos) -> (min_ms, max_ms) spawn interval."""
    c = max(1, min(10, int(chaos)))
    t = (c - 1) / 9.0
    min_ms = int(1200 + (120 - 1200) * t)
    max_ms = int(2000 + (300 - 2000) * t)
    return min_ms, max_ms


IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp"}
MUSIC_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg"}


def _collect_uploads():
    """Return (images [(mime, b64)], music (mime, b64) or None)."""
    images = []
    for f in request.files.getlist("images"):
        if not f or not f.filename:
            continue
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in IMG_EXTS:
            continue
        data = f.read()
        if data:
            images.append((IMG_MIME[ext],
                           base64.b64encode(data).decode("ascii")))
    music = None
    mf = request.files.get("music")
    if mf and mf.filename:
        ext = os.path.splitext(mf.filename)[1].lower()
        if ext in MUSIC_EXTS:
            data = mf.read()
            if data:
                music = (MUSIC_MIME[ext], base64.b64encode(data).decode("ascii"))
    return images, music


def _settings():
    chaos = request.form.get("chaos", "6")
    max_popups = max(5, min(60, int(request.form.get("max_popups", "25"))))
    music_on = request.form.get("music_on", "on") == "on"
    min_ms, max_ms = chaos_to_interval(chaos)
    return min_ms, max_ms, max_popups, music_on


@app.route("/")
def index():
    try:
        tag, _ = get_exe()
        exe_note = f"Bundling {EXE_NAME} {tag}"
    except Exception as e:
        exe_note = f"Prank exe not reachable right now ({e}). You can still build the data pack."
        tag = None
    return render_template("builder.html", app_version=APP_VERSION,
                           exe_note=exe_note, has_exe=tag is not None)


@app.route("/api/exe-info")
def exe_info():
    try:
        tag, _ = get_exe()
        return jsonify({"ok": True, "tag": tag})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/build", methods=["POST"])
def build():
    min_ms, max_ms, max_popups, music_on = _settings()
    images, music = _collect_uploads()

    # --- prank-data.pak ---
    pak = io.BytesIO()
    with zipfile.ZipFile(pak, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, (mime, b64) in enumerate(images):
            ext = [e for e, m in IMG_MIME.items() if m == mime][0]
            zf.writestr(f"images/img_{n:02d}{ext}", base64.b64decode(b64))
        if music:
            mime, b64 = music
            ext = [e for e, m in MUSIC_MIME.items() if m == mime][0]
            zf.writestr(f"music/song{ext}", base64.b64decode(b64))
        zf.writestr("config.json", json.dumps({
            "spawn_min_ms": min_ms,
            "spawn_max_ms": max_ms,
            "max_popups": max_popups,
            "music_on": music_on,
        }))
    pak_bytes = pak.getvalue()

    # --- kit zip: exe + pak ---
    tag, exe_bytes = get_exe()
    kit = io.BytesIO()
    with zipfile.ZipFile(kit, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(EXE_NAME, exe_bytes)
        zf.writestr("prank-data.pak", pak_bytes)
        zf.writestr("README.txt",
                    "PRANK KIT\n\n"
                    "1. Copy prank-popups.exe and prank-data.pak to a USB stick.\n"
                    "2. Double-click prank-popups.exe on the target PC.\n"
                    "3. Press ESC anywhere (or the STOP button) to end it.\n\n"
                    "Harmless: installs nothing, changes nothing.\n")
    kit.seek(0)
    return send_file(kit, as_attachment=True, download_name="prank-kit.zip",
                     mimetype="application/zip")


WEB_PRANK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>You have to see this</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 100%; height: 100%; overflow: hidden; background: #111;
               font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }
  #splash { position: fixed; inset: 0; display: flex; flex-direction: column;
            align-items: center; justify-content: center; background: #1c1e21;
            color: #fff; z-index: 9999; text-align: center; padding: 24px; }
  #splash h1 { font-size: 28px; margin-bottom: 12px; }
  #splash p { color: #b0b3b8; margin-bottom: 24px; }
  #go { background: #c81e1e; color: #fff; border: none; border-radius: 12px;
        padding: 18px 54px; font-size: 20px; font-weight: 700; cursor: pointer; }
  .pop { position: fixed; z-index: 100; border: 3px solid #fff; border-radius: 8px;
         box-shadow: 0 6px 24px rgba(0,0,0,.5); transition: left .8s, top .8s; }
  .pop span { display: flex; align-items: center; justify-content: center;
              width: 220px; height: 140px; background: #ffe14d; color: #111;
              font-size: 34px; font-weight: 800; }
  #stop { position: fixed; top: 14px; right: 14px; z-index: 10000; background: #c81e1e;
          color: #fff; border: none; border-radius: 10px; padding: 12px 26px;
          font-size: 16px; font-weight: 700; cursor: pointer; display: none; }
  #done { position: fixed; inset: 0; display: none; align-items: center;
          justify-content: center; z-index: 9999; background: #1c1e21; color: #fff;
          font-size: 30px; font-weight: 800; text-align: center; padding: 24px; }
</style>
</head>
<body>
<div id="splash">
  <h1>You HAVE to see this</h1>
  <p>Someone sent you something funny. Turn your sound on.</p>
  <button id="go">SHOW ME</button>
</div>
<button id="stop">STOP</button>
<div id="done">GOTCHA!<br><span style="font-size:18px;font-weight:400">Harmless prank &mdash; nothing was installed.</span></div>
<script>
var IMAGES = __IMAGES__;
var MUSIC = __MUSIC__;
var CFG = __CFG__;
var FALLBACK = ["GOTCHA!", "HI!", ":P", "BOO!", ";)", "LOL"];
var timers = [], popups = [], audio = null, running = false;

function rnd(a, b) { return a + Math.floor(Math.random() * (b - a + 1)); }

function move(el) {
  var w = el.offsetWidth || 220, h = el.offsetHeight || 140;
  el.style.left = rnd(0, Math.max(0, window.innerWidth - w)) + "px";
  el.style.top = rnd(0, Math.max(0, window.innerHeight - h)) + "px";
}

function spawn() {
  if (!running) return;
  var el = document.createElement("div");
  el.className = "pop";
  if (IMAGES.length) {
    var img = document.createElement("img");
    img.src = IMAGES[rnd(0, IMAGES.length - 1)];
    img.style.width = rnd(160, 320) + "px";
    img.style.display = "block";
    el.appendChild(img);
  } else {
    var s = document.createElement("span");
    s.textContent = FALLBACK[rnd(0, FALLBACK.length - 1)];
    el.appendChild(s);
  }
  document.body.appendChild(el);
  move(el);
  popups.push(el);
  while (popups.length > CFG.maxPopups) {
    var old = popups.shift();
    if (old.parentNode) old.parentNode.removeChild(old);
  }
  timers.push(setTimeout(function wander() {
    if (!running || !el.parentNode) return;
    move(el);
    timers.push(setTimeout(wander, rnd(900, 2200)));
  }, rnd(900, 2200)));
  timers.push(setTimeout(spawn, rnd(CFG.minMs, CFG.maxMs)));
}

function stopAll() {
  if (!running) return;
  running = false;
  timers.forEach(clearTimeout);
  timers = [];
  popups.forEach(function (el) { if (el.parentNode) el.parentNode.removeChild(el); });
  popups = [];
  if (audio) { audio.pause(); }
  document.getElementById("stop").style.display = "none";
  document.getElementById("done").style.display = "flex";
}

document.getElementById("go").addEventListener("click", function () {
  document.getElementById("splash").style.display = "none";
  document.getElementById("stop").style.display = "block";
  running = true;
  if (MUSIC && CFG.musicOn) {
    audio = new Audio(MUSIC);
    audio.loop = true;
    audio.play().catch(function () {});
  }
  spawn();
});
document.getElementById("stop").addEventListener("click", stopAll);
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") stopAll();
});
</script>
</body>
</html>
"""


@app.route("/web", methods=["POST"])
def web():
    """Self-contained email-friendly prank page (single HTML file)."""
    min_ms, max_ms, max_popups, music_on = _settings()
    images, music = _collect_uploads()

    img_uris = ["data:%s;base64,%s" % (mime, b64) for mime, b64 in images]
    music_uri = ("data:%s;base64,%s" % (music[0], music[1])) if music else None
    html = WEB_PRANK_HTML.replace("__IMAGES__", json.dumps(img_uris)) \
                         .replace("__MUSIC__", json.dumps(music_uri)) \
                         .replace("__CFG__", json.dumps({
                             "minMs": min_ms, "maxMs": max_ms,
                             "maxPopups": max_popups, "musicOn": music_on,
                         }))
    return send_file(io.BytesIO(html.encode("utf-8")), as_attachment=True,
                     download_name="prank.html", mimetype="text/html")


@app.route("/exe")
def exe():
    tag, data = get_exe()
    return send_file(io.BytesIO(data), as_attachment=True,
                     download_name=EXE_NAME,
                     mimetype="application/octet-stream")


def main():
    import webbrowser
    from threading import Timer
    if getattr(sys, "frozen", False):
        Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5001")).start()
    app.run(host="127.0.0.1", port=5001, debug=False)


if __name__ == "__main__":
    main()
