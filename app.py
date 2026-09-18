"""Prank Builder - web UI to customize the prank and download your prank kit.

Drag & drop images + a song, set the chaos level, hit build: you get a
zip with prank-popups.exe and prank-data.pak. Copy both to a USB stick,
double-click the exe on the target PC. ESC stops it.
"""
import io
import json
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
    req = urllib.request.Request(
        f"https://api.github.com/repos/{PRANK_REPO}/releases/latest",
        headers={"User-Agent": "prank-builder", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


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
    chaos = request.form.get("chaos", "6")
    max_popups = max(5, min(60, int(request.form.get("max_popups", "25"))))
    music_on = request.form.get("music_on", "on") == "on"
    min_ms, max_ms = chaos_to_interval(chaos)

    # --- prank-data.pak ---
    pak = io.BytesIO()
    n_img = 0
    with zipfile.ZipFile(pak, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in request.files.getlist("images"):
            if not f or not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in IMG_EXTS:
                continue
            data = f.read()
            if not data:
                continue
            zf.writestr(f"images/img_{n_img:02d}{ext}", data)
            n_img += 1
        mf = request.files.get("music")
        if mf and mf.filename:
            ext = os.path.splitext(mf.filename)[1].lower()
            if ext in MUSIC_EXTS:
                data = mf.read()
                if data:
                    zf.writestr(f"music/song{ext}", data)
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
