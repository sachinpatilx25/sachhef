import os
import sys
import re
import json
import time
import shutil
import threading
import subprocess
from urllib.parse import urlparse, parse_qs, unquote
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(APP_DIR, "downloads")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
FFMPEG_EXE = os.path.join(APP_DIR, "ffmpeg.exe")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "base_api": "https://hefshinejavasms3-ibwfknlyra-el.a.run.app"
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

state = {
    "jwt": None,
    "user_info": None,
    "login_error": None,
    "active_downloads": {},
    "is_downloading_all": False
}

def authenticate():
    cfg = load_config()
    username = cfg.get("username", "").strip()
    password = cfg.get("password", "").strip()
    base_api = cfg.get("base_api", "https://hefshinejavasms3-ibwfknlyra-el.a.run.app")

    if not username or not password:
        state["jwt"] = None
        state["user_info"] = None
        state["login_error"] = "NOT_LOGGED_IN"
        return False, "Please enter your username and password to log in."

    url = f"{base_api}/"
    try:
        r = requests.post(
            url,
            json={"userName": username, "password": password},
            headers={"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"},
            timeout=15
        )
        r.raise_for_status()
        data = r.json()
        jwt = data.get("jwt")
        if jwt:
            state["jwt"] = jwt
            state["user_info"] = data
            state["login_error"] = None
            return True, "Login successful"
        else:
            state["jwt"] = None
            state["login_error"] = data.get("errorMessage") or "Invalid username or password. Please try again."
            return False, state["login_error"]
    except Exception as e:
        state["jwt"] = None
        state["login_error"] = f"Unable to reach HefShine server: {str(e)}"
        return False, state["login_error"]

def get_recordings():
    cfg = load_config()
    if not cfg.get("username") or not cfg.get("password"):
        return {
            "is_logged_in": False,
            "username": "",
            "recordings": [],
            "count": 0,
            "error": "NOT_LOGGED_IN"
        }

    if not state["jwt"]:
        ok, msg = authenticate()
        if not ok:
            return {
                "is_logged_in": False,
                "username": "",
                "recordings": [],
                "count": 0,
                "error": msg
            }

    base_api = cfg.get("base_api", "https://hefshinejavasms3-ibwfknlyra-el.a.run.app")
    headers = {
        "mytoken": state["jwt"],
        "Accept": "application/json, text/plain, */*"
    }

    recordings = []
    page = 0
    while True:
        try:
            url = f"{base_api}/student/bbb/recordings?page={page}"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code in (401, 403):
                ok, msg = authenticate()
                if ok:
                    headers["mytoken"] = state["jwt"]
                    r = requests.get(url, headers=headers, timeout=15)
                else:
                    return {
                        "is_logged_in": False,
                        "username": "",
                        "recordings": [],
                        "count": 0,
                        "error": "Session expired. Please log in again."
                    }
            r.raise_for_status()
            data = r.json()
            recs = data.get("recordings", [])
            recordings.extend(recs)
            if not data.get("hasNext"):
                break
            page += 1
        except Exception as e:
            return {
                "is_logged_in": True,
                "username": cfg.get("username", ""),
                "recordings": recordings,
                "count": len(recordings),
                "error": str(e)
            }

    for rec in recordings:
        rec_id = rec.get("id")
        title = re.sub(r'[\\/*?:"<>|]', "", rec.get("title", "Recording"))
        ready_at = rec.get("readyAt", "").replace(":", "-")[:19]
        folder_name = f"{title}_{ready_at}_{rec_id[:8]}"
        rec_folder = os.path.join(DOWNLOAD_DIR, folder_name)

        rec["folderName"] = folder_name
        rec["isDownloaded"] = False
        rec["mergedVideo"] = None
        rec["localFiles"] = []

        if os.path.isdir(rec_folder):
            files = os.listdir(rec_folder)
            valid_files = [f for f in files if os.path.isfile(os.path.join(rec_folder, f)) and os.path.getsize(os.path.join(rec_folder, f)) > 0]
            if len(valid_files) > 0:
                rec["localFiles"] = [
                    {
                        "name": f,
                        "size": os.path.getsize(os.path.join(rec_folder, f)),
                        "path": f"/downloads/{folder_name}/{f}"
                    }
                    for f in valid_files
                ]
                
                # Check for merged MP4
                merged = [f for f in valid_files if f.endswith(".mp4") or "merged" in f.lower()]
                if merged:
                    rec["mergedVideo"] = {
                        "name": merged[0],
                        "size": os.path.getsize(os.path.join(rec_folder, merged[0])),
                        "path": f"/downloads/{folder_name}/{merged[0]}"
                    }
                    rec["isDownloaded"] = True
                else:
                    has_media = any(f.endswith(".webm") for f in valid_files)
                    rec["isDownloaded"] = has_media

        rec["activeDownload"] = state["active_downloads"].get(rec_id)

    return {
        "is_logged_in": True,
        "username": cfg.get("username", ""),
        "recordings": recordings,
        "count": len(recordings),
        "is_downloading_all": state["is_downloading_all"]
    }

def merge_streams(out_dir, screen_path, audio_path, final_mp4_path):
    """Combine instructor screen video and audio track into a single MP4 with synchronized sound."""
    if not os.path.exists(FFMPEG_EXE):
        return False, "ffmpeg not found"

    # Screen has the video, audio_path (webcams) has the audio
    cmd = [
        FFMPEG_EXE,
        "-y",
        "-i", screen_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-movflags", "+faststart",
        final_mp4_path
    ]

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
        if proc.returncode == 0 and os.path.exists(final_mp4_path) and os.path.getsize(final_mp4_path) > 0:
            return True, "Merged successfully"
        else:
            # Fallback if stream copying failed (e.g., codec mismatch, re-encode audio only)
            cmd_fallback = [
                FFMPEG_EXE,
                "-y",
                "-i", screen_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "copy",
                "-map", "0:v:0",
                "-map", "1:a:0",
                final_mp4_path.replace(".mp4", ".mkv")
            ]
            proc2 = subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
            if proc2.returncode == 0:
                return True, "Merged as MKV"
            return False, proc.stderr[-300:] if proc.stderr else "FFmpeg merge error"
    except Exception as e:
        return False, str(e)

def perform_download(rec_id, rec_title, ready_at):
    try:
        state["active_downloads"][rec_id] = {
            "status": "starting",
            "percent": 0,
            "current_file": "Connecting to server...",
            "error": None
        }

        if not state["jwt"]:
            ok, _ = authenticate()
            if not ok:
                raise Exception("Authentication failed")

        cfg = load_config()
        base_api = cfg.get("base_api", "https://hefshinejavasms3-ibwfknlyra-el.a.run.app")

        play_url = f"{base_api}/student/bbb/recordings/{rec_id}/play"
        r = requests.post(play_url, json={}, headers={"mytoken": state["jwt"]}, timeout=20)
        r.raise_for_status()
        playback_url = r.json().get("playbackUrl")
        if not playback_url:
            raise Exception("No playback URL available for this class.")

        parsed = urlparse(playback_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        if "/playback/presentation/" in parsed.path:
            prefix_part = parsed.path.split("/playback/presentation/")[0]
            pres_prefix = f"{prefix_part}/presentation"
        else:
            match = re.match(r'(/bbb/playback/[^/]+)', parsed.path)
            prefix_part = match.group(1) if match else ""
            pres_prefix = f"{prefix_part}/presentation"

        base_media_url = f"{origin}{pres_prefix}/{rec_id}"

        clean_title = re.sub(r'[\\/*?:"<>|]', "", rec_title or "Recording")
        clean_ready_at = (ready_at or "").replace(":", "-")[:19]
        folder_name = f"{clean_title}_{clean_ready_at}_{rec_id[:8]}"
        out_dir = os.path.join(DOWNLOAD_DIR, folder_name)
        os.makedirs(out_dir, exist_ok=True)

        screen_file = os.path.join(out_dir, "screenshare_deskshare.webm")
        audio_file = os.path.join(out_dir, "teacher_webcam_and_audio.webm")
        merged_mp4_file = os.path.join(out_dir, f"{clean_title}_Full_Lecture.mp4")

        # 1. Download Screen Share (Video)
        state["active_downloads"][rec_id]["current_file"] = "Downloading Screen Video..."
        state["active_downloads"][rec_id]["status"] = "downloading"
        if not (os.path.exists(screen_file) and os.path.getsize(screen_file) > 0):
            t_url = f"{base_media_url}/deskshare/deskshare.webm"
            head = requests.head(t_url, timeout=15)
            file_size = int(head.headers.get("content-length", 0))
            with requests.get(t_url, stream=True, timeout=180) as stream_resp:
                stream_resp.raise_for_status()
                downloaded = 0
                with open(screen_file, "wb") as f_out:
                    for chunk in stream_resp.iter_content(chunk_size=1024*1024):
                        if chunk:
                            f_out.write(chunk)
                            downloaded += len(chunk)
                            if file_size > 0:
                                state["active_downloads"][rec_id]["percent"] = round((downloaded / file_size) * 45, 1)

        # 2. Download Teacher & Audio
        state["active_downloads"][rec_id]["current_file"] = "Downloading Teacher Audio..."
        state["active_downloads"][rec_id]["percent"] = 50
        if not (os.path.exists(audio_file) and os.path.getsize(audio_file) > 0):
            t_url = f"{base_media_url}/video/webcams.webm"
            head = requests.head(t_url, timeout=15)
            file_size = int(head.headers.get("content-length", 0))
            with requests.get(t_url, stream=True, timeout=180) as stream_resp:
                stream_resp.raise_for_status()
                downloaded = 0
                with open(audio_file, "wb") as f_out:
                    for chunk in stream_resp.iter_content(chunk_size=1024*1024):
                        if chunk:
                            f_out.write(chunk)
                            downloaded += len(chunk)
                            if file_size > 0:
                                state["active_downloads"][rec_id]["percent"] = round(50 + (downloaded / file_size) * 35, 1)

        # 3. Download Metadata
        try:
            r_meta = requests.get(f"{base_media_url}/metadata.xml", timeout=10)
            if r_meta.status_code == 200:
                with open(os.path.join(out_dir, "metadata.xml"), "w", encoding="utf-8") as f_meta:
                    f_meta.write(r_meta.text)
        except Exception:
            pass

        # 4. Merge Screen & Audio into a single complete MP4 video
        state["active_downloads"][rec_id]["current_file"] = "Merging Video & Audio together..."
        state["active_downloads"][rec_id]["percent"] = 90

        if os.path.exists(screen_file) and os.path.exists(audio_file):
            ok_merge, merge_msg = merge_streams(out_dir, screen_file, audio_file, merged_mp4_file)
            if not ok_merge:
                print("Merge note:", merge_msg)

        state["active_downloads"][rec_id]["status"] = "completed"
        state["active_downloads"][rec_id]["percent"] = 100
        state["active_downloads"][rec_id]["current_file"] = "Completed"

    except Exception as e:
        state["active_downloads"][rec_id] = {
            "status": "failed",
            "percent": 0,
            "current_file": "Failed",
            "error": str(e)
        }

def download_all_thread():
    state["is_downloading_all"] = True
    try:
        data = get_recordings()
        recs = data.get("recordings", [])
        for r in recs:
            if not r.get("isDownloaded"):
                perform_download(r.get("id"), r.get("title"), r.get("readyAt"))
    finally:
        state["is_downloading_all"] = False

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HefShine Recordings Portal</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(20, 27, 45, 0.75);
      --border: rgba(255, 255, 255, 0.08);
      --primary: #3b82f6;
      --primary-hover: #1d4ed8;
      --accent: #10b981;
      --text: #f8fafc;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Plus Jakarta Sans', sans-serif;
      background: radial-gradient(circle at 10% 20%, #1e1b4b 0%, #090d16 90%);
      color: var(--text);
      min-height: 100vh;
      padding: 30px 16px;
    }
    .container {
      max-width: 1050px;
      margin: 0 auto;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 25px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
      flex-wrap: wrap;
      gap: 15px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .brand-icon {
      width: 50px;
      height: 50px;
      background: linear-gradient(135deg, #3b82f6, #6366f1);
      border-radius: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 8px 24px rgba(59, 130, 246, 0.35);
    }
    .brand-icon svg { width: 28px; height: 28px; fill: white; }
    h1 { font-size: 1.65rem; font-weight: 800; letter-spacing: -0.5px; }
    .subtitle { color: var(--text-muted); font-size: 0.85rem; margin-top: 3px; }
    .header-controls {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    .btn {
      padding: 10px 18px;
      border-radius: 10px;
      font-size: 0.88rem;
      font-weight: 600;
      cursor: pointer;
      border: none;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      display: inline-flex;
      align-items: center;
      gap: 8px;
      text-decoration: none;
    }
    .btn:active { transform: scale(0.98); }
    .btn-primary {
      background: linear-gradient(135deg, #3b82f6, #2563eb);
      color: white;
      box-shadow: 0 4px 15px rgba(37, 99, 235, 0.35);
    }
    .btn-primary:hover {
      box-shadow: 0 6px 22px rgba(37, 99, 235, 0.5);
      background: #1d4ed8;
    }
    .btn-download-all {
      background: linear-gradient(135deg, #10b981, #059669);
      color: white;
      box-shadow: 0 4px 15px rgba(16, 185, 129, 0.35);
    }
    .btn-download-all:hover {
      box-shadow: 0 6px 22px rgba(16, 185, 129, 0.5);
      background: #047857;
    }
    .btn-secondary {
      background: rgba(255, 255, 255, 0.06);
      color: var(--text);
      border: 1px solid var(--border);
    }
    .btn-secondary:hover {
      background: rgba(255, 255, 255, 0.12);
    }
    .user-pill {
      background: rgba(59, 130, 246, 0.12);
      border: 1px solid rgba(59, 130, 246, 0.3);
      padding: 8px 14px;
      border-radius: 30px;
      font-size: 0.85rem;
      color: #93c5fd;
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
    }
    .user-pill:hover { background: rgba(59, 130, 246, 0.2); }
    .status-dot {
      width: 8px;
      height: 8px;
      background: #10b981;
      border-radius: 50%;
      box-shadow: 0 0 10px #10b981;
    }
    .card {
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 24px;
      margin-bottom: 24px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
    }
    .search-bar {
      display: flex;
      gap: 12px;
      margin-bottom: 20px;
    }
    .search-input {
      flex: 1;
      background: rgba(15, 23, 42, 0.7);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 16px;
      color: #fff;
      font-size: 0.9rem;
      outline: none;
    }
    .search-input:focus { border-color: var(--primary); }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 20px;
    }
    .rec-card {
      background: rgba(15, 23, 42, 0.65);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: relative;
      transition: all 0.25s ease;
    }
    .rec-card:hover {
      border-color: rgba(59, 130, 246, 0.4);
      transform: translateY(-2px);
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.35);
    }
    .badge {
      position: absolute;
      top: 14px;
      right: 14px;
      font-size: 0.72rem;
      padding: 3px 10px;
      border-radius: 20px;
      font-weight: 600;
    }
    .badge-done { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-cloud { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
    .rec-title {
      font-size: 1.15rem;
      font-weight: 700;
      color: #fff;
      margin-bottom: 8px;
      padding-right: 75px;
    }
    .rec-date {
      font-size: 0.82rem;
      color: var(--text-muted);
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .video-ready-box {
      background: rgba(16, 185, 129, 0.08);
      border: 1px solid rgba(16, 185, 129, 0.25);
      border-radius: 10px;
      padding: 12px;
      margin-top: 10px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .video-ready-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.82rem;
      color: #34d399;
      font-weight: 600;
    }
    .btn-play-full {
      background: linear-gradient(135deg, #10b981, #059669);
      color: white;
      padding: 10px;
      border-radius: 8px;
      font-weight: 700;
      cursor: pointer;
      border: none;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      text-decoration: none;
    }
    .btn-play-full:hover { background: #047857; }
    .progress-wrap { margin-top: 12px; }
    .progress-bar {
      height: 8px;
      background: rgba(255, 255, 255, 0.08);
      border-radius: 8px;
      overflow: hidden;
      margin-bottom: 6px;
    }
    .progress-fill {
      height: 100%;
      background: linear-gradient(90deg, #3b82f6, #10b981);
      width: 0%;
      transition: width 0.3s;
    }
    .progress-info {
      display: flex;
      justify-content: space-between;
      font-size: 0.75rem;
      color: #93c5fd;
    }
    .btn-block { width: 100%; justify-content: center; margin-top: 12px; }
    
    .welcome-container {
      max-width: 440px;
      margin: 40px auto;
      text-align: center;
      padding: 36px 30px;
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border);
      border-radius: 20px;
      box-shadow: 0 15px 40px rgba(0, 0, 0, 0.5);
    }
    .welcome-icon {
      width: 64px;
      height: 64px;
      background: linear-gradient(135deg, #3b82f6, #6366f1);
      border-radius: 18px;
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 0 auto 20px auto;
      box-shadow: 0 10px 25px rgba(59, 130, 246, 0.35);
    }
    .welcome-icon svg { width: 34px; height: 34px; fill: white; }
    .form-group { margin-bottom: 16px; text-align: left; }
    .form-group label { display: block; font-size: 0.82rem; margin-bottom: 6px; color: var(--text-muted); font-weight: 500; }
    .modal-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.85);
      backdrop-filter: blur(8px);
      z-index: 1000;
      justify-content: center;
      align-items: center;
      padding: 20px;
    }
    .modal-box {
      background: #111827;
      border: 1px solid var(--border);
      border-radius: 16px;
      max-width: 960px;
      width: 100%;
      overflow: hidden;
      box-shadow: 0 25px 60px rgba(0,0,0,0.8);
    }
    .modal-header {
      padding: 16px 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
    }
    .modal-close {
      background: none;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 1.5rem;
    }
    .modal-close:hover { color: #fff; }
    video { width: 100%; max-height: 72vh; background: black; outline: none; }
    .empty-state { text-align: center; padding: 60px 20px; color: var(--text-muted); }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="brand">
        <div class="brand-icon">
          <svg viewBox="0 0 24 24"><path d="M4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-8 12.5v-9l6 4.5-6 4.5z"/></svg>
        </div>
        <div>
          <h1>HefShine Recordings Portal</h1>
          <div class="subtitle">Download & Watch Combined Video with Sound Offline</div>
        </div>
      </div>
      <div class="header-controls" id="header-user-controls" style="display: none;">
        <div class="user-pill" onclick="logout()" title="Click to log out">
          <div class="status-dot"></div>
          <span id="user-display">Student</span>
          <span style="font-size: 0.72rem; color: #60a5fa; margin-left: 4px;">(Sign Out)</span>
        </div>
        <button class="btn btn-secondary" onclick="loadRecordings()">🔄 Check for Lectures</button>
        <button class="btn btn-download-all" id="btn-dl-all" onclick="downloadAll()">⬇ Download All Classes</button>
        <button class="btn btn-secondary" onclick="openFolder()">📂 Open Saved Videos</button>
      </div>
    </header>

    <!-- Fresh Welcome & Login Form -->
    <div id="welcome-view" class="welcome-container" style="display: none;">
      <div class="welcome-icon">
        <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z"/></svg>
      </div>
      <h2 style="font-size: 1.4rem; font-weight: 700; margin-bottom: 6px;">Student Login</h2>
      <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 24px;">Enter your institute mobile number & password to view and download your class recordings.</p>

      <div id="login-error-msg" style="display: none; background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; padding: 10px 14px; border-radius: 10px; font-size: 0.85rem; margin-bottom: 16px; text-align: left;"></div>

      <form onsubmit="handleMainLogin(event)">
        <div class="form-group">
          <label>Mobile Number / Username</label>
          <input type="text" id="main-username" class="search-input" style="width: 100%;" placeholder="Enter Mobile Number" required autofocus>
        </div>
        <div class="form-group">
          <label>Password</label>
          <input type="password" id="main-password" class="search-input" style="width: 100%;" placeholder="Enter password" required>
        </div>
        <button type="submit" class="btn btn-primary btn-block" id="btn-login-submit" style="padding: 12px; font-size: 0.95rem;">
          Sign In & View Lectures
        </button>
      </form>
    </div>

    <!-- Recordings Dashboard -->
    <div id="dashboard-view" class="card" style="display: none;">
      <div class="search-bar">
        <input type="text" id="search-box" class="search-input" placeholder="🔍 Search recordings by topic name or date..." oninput="filterRecordings()">
      </div>
      <div id="grid-container" class="grid">
        <div class="empty-state">Loading your recordings...</div>
      </div>
    </div>
  </div>

  <!-- Video Player Modal -->
  <div id="player-modal" class="modal-overlay" onclick="closePlayerModal(event)">
    <div class="modal-box" onclick="event.stopPropagation()">
      <div class="modal-header">
        <span id="player-title" style="font-weight: 600;">Class Video (Screen + Sound)</span>
        <button class="modal-close" onclick="closePlayerModal()">&times;</button>
      </div>
      <video id="video-elem" controls autoplay></video>
    </div>
  </div>

  <script>
    let allRecordings = [];
    let pollTimer = null;

    async function loadRecordings() {
      const grid = document.getElementById('grid-container');
      const welcomeView = document.getElementById('welcome-view');
      const dashView = document.getElementById('dashboard-view');
      const headerControls = document.getElementById('header-user-controls');

      try {
        const res = await fetch('/api/recordings');
        const data = await res.json();

        if (!data.is_logged_in || data.error === 'NOT_LOGGED_IN') {
          welcomeView.style.display = 'block';
          dashView.style.display = 'none';
          headerControls.style.display = 'none';
          if (data.error && data.error !== 'NOT_LOGGED_IN') {
            const errBox = document.getElementById('login-error-msg');
            errBox.innerText = data.error;
            errBox.style.display = 'block';
          }
          return;
        }

        welcomeView.style.display = 'none';
        dashView.style.display = 'block';
        headerControls.style.display = 'flex';
        document.getElementById('user-display').innerText = data.username ? `Account: ${data.username}` : 'Student';

        allRecordings = data.recordings || [];
        renderRecordings(allRecordings);

        const isAnyDownloading = allRecordings.some(r => r.activeDownload && (r.activeDownload.status === 'downloading' || r.activeDownload.status === 'starting'));
        if (isAnyDownloading && !pollTimer) {
          pollTimer = setInterval(loadRecordings, 2000);
        } else if (!isAnyDownloading && pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }

      } catch (e) {
        grid.innerHTML = `<div class="empty-state" style="color: #ef4444; grid-column: 1/-1;">Could not reach server: ${e}</div>`;
      }
    }

    async function handleMainLogin(e) {
      e.preventDefault();
      const u = document.getElementById('main-username').value.trim();
      const p = document.getElementById('main-password').value.trim();
      const btn = document.getElementById('btn-login-submit');
      const errBox = document.getElementById('login-error-msg');

      btn.innerText = 'Connecting...';
      btn.disabled = true;
      errBox.style.display = 'none';

      try {
        const res = await fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: u, password: p })
        });
        const resp = await res.json();
        if (resp.error) {
          errBox.innerText = resp.error;
          errBox.style.display = 'block';
        } else {
          loadRecordings();
        }
      } catch (err) {
        errBox.innerText = "Connection failed: " + err;
        errBox.style.display = 'block';
      } finally {
        btn.innerText = 'Sign In & View Lectures';
        btn.disabled = false;
      }
    }

    function renderRecordings(recs) {
      const grid = document.getElementById('grid-container');
      if (recs.length === 0) {
        grid.innerHTML = '<div class="empty-state" style="grid-column: 1/-1;">No class recordings found on your student portal.</div>';
        return;
      }

      grid.innerHTML = recs.map(r => {
        const isDone = r.isDownloaded;
        const dl = r.activeDownload;
        const isDownloading = dl && (dl.status === 'downloading' || dl.status === 'starting');
        const dateStr = r.readyAt ? new Date(r.readyAt).toLocaleString() : 'N/A';

        let bottomContent = '';
        if (isDownloading) {
          bottomContent = `
            <div class="progress-wrap">
              <div class="progress-bar">
                <div class="progress-fill" style="width: ${dl.percent}%;"></div>
              </div>
              <div class="progress-info">
                <span>${dl.current_file}</span>
                <span>${dl.percent}%</span>
              </div>
            </div>
            <button class="btn btn-secondary btn-block" disabled>Processing Video...</button>
          `;
        } else if (isDone) {
          // If merged video exists, show unified player
          const playVideoPath = r.mergedVideo ? r.mergedVideo.path : (r.localFiles.find(f => f.name.includes('screenshare')) || r.localFiles[0]).path;
          const videoSize = r.mergedVideo ? (r.mergedVideo.size / 1024 / 1024).toFixed(1) + ' MB' : '';

          bottomContent = `
            <div class="video-ready-box">
              <div class="video-ready-header">
                <span>🎬 Full Lecture Video & Audio Synced</span>
                <span>${videoSize}</span>
              </div>
              <button class="btn-play-full" onclick="play('${playVideoPath}', '${r.title}')">
                ▶ Play Complete Class Video
              </button>
            </div>
            <button class="btn btn-secondary btn-block" onclick="startDl('${r.id}', '${r.title}', '${r.readyAt}')" style="margin-top: 10px; font-size: 0.8rem;">
              ✓ Re-download / Repair
            </button>
          `;
        } else {
          bottomContent = `
            <button class="btn btn-primary btn-block" onclick="startDl('${r.id}', '${r.title}', '${r.readyAt}')">
              ⬇ Download Video With Audio
            </button>
          `;
        }

        return `
          <div class="rec-card">
            <span class="badge ${isDone ? 'badge-done' : 'badge-cloud'}">
              ${isDone ? 'Ready (Audio + Video)' : 'Available Online'}
            </span>
            <div>
              <div class="rec-title">${r.title}</div>
              <div class="rec-date">📅 ${dateStr}</div>
            </div>
            <div>${bottomContent}</div>
          </div>
        `;
      }).join('');
    }

    function filterRecordings() {
      const q = document.getElementById('search-box').value.toLowerCase().trim();
      if (!q) {
        renderRecordings(allRecordings);
        return;
      }
      const filtered = allRecordings.filter(r => 
        (r.title && r.title.toLowerCase().includes(q)) || 
        (r.readyAt && r.readyAt.toLowerCase().includes(q))
      );
      renderRecordings(filtered);
    }

    async function startDl(id, title, readyAt) {
      await fetch('/api/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, title, readyAt })
      });
      loadRecordings();
    }

    async function downloadAll() {
      const btn = document.getElementById('btn-dl-all');
      btn.innerText = 'Starting Downloads...';
      btn.disabled = true;
      await fetch('/api/download-all', { method: 'POST' });
      setTimeout(() => {
        btn.innerText = '⬇ Download All Classes';
        btn.disabled = false;
        loadRecordings();
      }, 1000);
    }

    function play(path, title) {
      const modal = document.getElementById('player-modal');
      const v = document.getElementById('video-elem');
      document.getElementById('player-title').innerText = title + " (Screen & Audio)";
      v.src = path;
      modal.style.display = 'flex';
      v.play();
    }

    function closePlayerModal(e) {
      if (!e || e.target.id === 'player-modal' || e.target.classList.contains('modal-close')) {
        const modal = document.getElementById('player-modal');
        const v = document.getElementById('video-elem');
        v.pause();
        v.removeAttribute('src');
        modal.style.display = 'none';
      }
    }

    function openFolder() {
      fetch('/api/open-folder', { method: 'POST' });
    }

    async function logout() {
      if (confirm("Are you sure you want to sign out?")) {
        await fetch('/api/logout', { method: 'POST' });
        loadRecordings();
      }
    }

    window.onload = loadRecordings;
  </script>
</body>
</html>
"""

class HefShineServerHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def do_GET(self):
        url_p = urlparse(self.path)
        path = url_p.path

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            return

        elif path == "/api/recordings":
            data = get_recordings()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
            return

        elif path == "/api/config":
            cfg = load_config()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"username": cfg.get("username", "")}).encode("utf-8"))
            return

        elif path.startswith("/downloads/"):
            relative_path = unquote(path[len("/downloads/"):])
            full_file_path = os.path.abspath(os.path.join(DOWNLOAD_DIR, relative_path))

            if not full_file_path.startswith(DOWNLOAD_DIR) or not os.path.isfile(full_file_path):
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"File not found")
                return

            file_size = os.path.getsize(full_file_path)
            range_header = self.headers.get("Range")

            mime_type = "video/mp4" if full_file_path.endswith(".mp4") else ("video/webm" if full_file_path.endswith(".webm") else "application/octet-stream")
            if full_file_path.endswith(".xml"):
                mime_type = "application/xml"

            if range_header:
                match = re.match(r"bytes=(\d+)-(\d*)", range_header)
                if match:
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else file_size - 1
                    content_length = (end - start) + 1

                    self.send_response(206)
                    self.send_header("Content-Type", mime_type)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                    self.send_header("Content-Length", str(content_length))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()

                    with open(full_file_path, "rb") as f:
                        f.seek(start)
                        remaining = content_length
                        while remaining > 0:
                            chunk_size = min(remaining, 64 * 1024)
                            data = f.read(chunk_size)
                            if not data:
                                break
                            self.wfile.write(data)
                            remaining -= len(data)
                    return

            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with open(full_file_path, "rb") as f:
                while chunk := f.read(64 * 1024):
                    self.wfile.write(chunk)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        url_p = urlparse(self.path)
        path = url_p.path
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else "{}"

        if path == "/api/download":
            try:
                data = json.loads(body)
                rec_id = data.get("id")
                rec_title = data.get("title")
                ready_at = data.get("readyAt")
                if rec_id:
                    t = threading.Thread(target=perform_download, args=(rec_id, rec_title, ready_at), daemon=True)
                    t.start()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                    return
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        elif path == "/api/download-all":
            t = threading.Thread(target=download_all_thread, daemon=True)
            t.start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return

        elif path == "/api/logout":
            save_config({"username": "", "password": "", "base_api": "https://hefshinejavasms3-ibwfknlyra-el.a.run.app"})
            state["jwt"] = None
            state["user_info"] = None
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return

        elif path == "/api/config":
            try:
                data = json.loads(body)
                cfg = load_config()
                if "username" in data:
                    cfg["username"] = data["username"].strip()
                if "password" in data:
                    cfg["password"] = data["password"].strip()
                save_config(cfg)
                state["jwt"] = None
                ok, msg = authenticate()
                if not ok:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "error": msg}).encode("utf-8"))
                    return

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        elif path == "/api/open-folder":
            try:
                if sys.platform == "win32":
                    os.startfile(DOWNLOAD_DIR)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        self.send_response(404)
        self.end_headers()

def run(port=8899):
    for p in range(port, port + 50):
        try:
            server_address = ('', p)
            httpd = HTTPServer(server_address, HefShineServerHandler)
            print(f"[+] HefShine Web Portal running at: http://localhost:{p}")
            print(f"[+] Downloads folder: {DOWNLOAD_DIR}")
            httpd.serve_forever()
            break
        except OSError:
            continue

if __name__ == "__main__":
    run()
