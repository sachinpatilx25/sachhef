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
        return False, "Please enter your mobile number and password."

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
        rec["mergedMp4"] = None

        if os.path.isdir(rec_folder):
            files = os.listdir(rec_folder)
            mp4_files = [f for f in files if f.endswith(".mp4") and os.path.getsize(os.path.join(rec_folder, f)) > 0]
            if mp4_files:
                rec["isDownloaded"] = True
                rec["mergedMp4"] = {
                    "name": mp4_files[0],
                    "size_mb": round(os.path.getsize(os.path.join(rec_folder, mp4_files[0])) / (1024*1024), 1),
                    "path": f"/downloads/{folder_name}/{mp4_files[0]}"
                }

        rec["activeDownload"] = state["active_downloads"].get(rec_id)

    return {
        "is_logged_in": True,
        "username": cfg.get("username", ""),
        "recordings": recordings,
        "count": len(recordings)
    }

def merge_streams(screen_path, audio_path, output_mp4):
    if not os.path.exists(FFMPEG_EXE):
        return False, "ffmpeg.exe not found"

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
        output_mp4
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=360)
        if proc.returncode == 0 and os.path.exists(output_mp4) and os.path.getsize(output_mp4) > 0:
            return True, "Success"
        return False, proc.stderr[-200:]
    except Exception as e:
        return False, str(e)

def perform_download(rec_id, rec_title, ready_at):
    try:
        state["active_downloads"][rec_id] = {
            "status": "starting",
            "percent": 0,
            "message": "Connecting to HefShine server..."
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

        screen_file = os.path.join(out_dir, "temp_screen.webm")
        audio_file = os.path.join(out_dir, "temp_audio.webm")
        final_mp4 = os.path.join(out_dir, f"{clean_title}.mp4")

        # 1. Download Screen
        state["active_downloads"][rec_id]["message"] = "Downloading class screen (1/2)..."
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
                            state["active_downloads"][rec_id]["percent"] = round((downloaded / file_size) * 45)

        # 2. Download Audio
        state["active_downloads"][rec_id]["message"] = "Downloading teacher audio (2/2)..."
        state["active_downloads"][rec_id]["percent"] = 50
        t_url_aud = f"{base_media_url}/video/webcams.webm"
        head_aud = requests.head(t_url_aud, timeout=15)
        aud_size = int(head_aud.headers.get("content-length", 0))
        with requests.get(t_url_aud, stream=True, timeout=180) as stream_resp:
            stream_resp.raise_for_status()
            downloaded = 0
            with open(audio_file, "wb") as f_out:
                for chunk in stream_resp.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f_out.write(chunk)
                        downloaded += len(chunk)
                        if aud_size > 0:
                            state["active_downloads"][rec_id]["percent"] = round(50 + (downloaded / aud_size) * 35)

        # 3. Merge into clean single MP4
        state["active_downloads"][rec_id]["message"] = "Making single video with audio..."
        state["active_downloads"][rec_id]["percent"] = 90

        ok, msg = merge_streams(screen_file, audio_file, final_mp4)
        if ok:
            # Delete temporary webm files so student has only the clean single mp4
            try:
                if os.path.exists(screen_file): os.remove(screen_file)
                if os.path.exists(audio_file): os.remove(audio_file)
            except Exception:
                pass
            state["active_downloads"][rec_id]["status"] = "completed"
            state["active_downloads"][rec_id]["percent"] = 100
            state["active_downloads"][rec_id]["message"] = "Ready!"
        else:
            raise Exception("Merging error: " + msg)

    except Exception as e:
        state["active_downloads"][rec_id] = {
            "status": "failed",
            "percent": 0,
            "message": "Error: " + str(e)
        }

HTML_SIMPLE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>HefShine Lecture Downloader</title>
</head>
<body style="font-family: sans-serif; max-width: 800px; margin: 30px auto; padding: 0 15px; line-height: 1.5;">

  <h2>HefShine Recordings Portal</h2>
  <hr>

  <!-- Login Form -->
  <div id="login-box">
    <h3>Student Sign In</h3>
    <div id="login-err" style="color: red; margin-bottom: 10px; display: none;"></div>
    <form onsubmit="doLogin(event)">
      <p>
        Mobile Number:<br>
        <input type="text" id="u" style="padding: 7px; width: 280px;" required autofocus>
      </p>
      <p>
        Password:<br>
        <input type="password" id="p" style="padding: 7px; width: 280px;" required>
      </p>
      <p>
        <button type="submit" id="btn-login" style="padding: 8px 20px; font-weight: bold; cursor: pointer;">
          Sign In
        </button>
      </p>
    </form>
  </div>

  <!-- Recordings Dashboard -->
  <div id="dash-box" style="display: none;">
    <p>
      Account: <b id="user-name"></b> |
      <a href="javascript:void(0)" onclick="openFolder()">[📁 Open Video Folder]</a> |
      <a href="javascript:void(0)" onclick="loadClasses()">[🔄 Refresh]</a> |
      <a href="javascript:void(0)" onclick="doLogout()">[Sign Out]</a>
    </p>
    <hr>

    <h3>Your Classes:</h3>
    <div id="classes-list">Loading classes...</div>
  </div>

  <script>
    let pollTimer = null;

    async function doLogin(e) {
      e.preventDefault();
      const u = document.getElementById('u').value.trim();
      const p = document.getElementById('p').value.trim();
      const err = document.getElementById('login-err');
      const btn = document.getElementById('btn-login');

      btn.disabled = true;
      btn.innerText = 'Connecting...';
      err.style.display = 'none';

      try {
        const res = await fetch('/api/config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ username: u, password: p })
        });
        const data = await res.json();
        if (data.success) {
          loadClasses();
        } else {
          err.innerText = data.error || 'Invalid credentials';
          err.style.display = 'block';
        }
      } catch (ex) {
        err.innerText = 'Error: ' + ex;
        err.style.display = 'block';
      } finally {
        btn.disabled = false;
        btn.innerText = 'Sign In';
      }
    }

    async function loadClasses() {
      const list = document.getElementById('classes-list');
      try {
        const res = await fetch('/api/recordings');
        const data = await res.json();

        if (!data.is_logged_in) {
          document.getElementById('login-box').style.display = 'block';
          document.getElementById('dash-box').style.display = 'none';
          return;
        }

        document.getElementById('login-box').style.display = 'none';
        document.getElementById('dash-box').style.display = 'block';
        document.getElementById('user-name').innerText = data.username;

        const recs = data.recordings || [];
        if (recs.length === 0) {
          list.innerHTML = '<i>No recordings currently available on your portal.</i>';
          return;
        }

        let isBusy = false;
        list.innerHTML = '<ol>' + recs.map(r => {
          const dl = r.activeDownload;
          const isDownloading = dl && (dl.status === 'downloading' || dl.status === 'starting');
          if (isDownloading) isBusy = true;

          let actionHtml = '';
          if (isDownloading) {
            actionHtml = `<b>[Downloading: ${dl.percent}% - ${dl.message}]</b>`;
          } else if (r.isDownloaded && r.mergedMp4) {
            actionHtml = `
              <span style="color: green; font-weight: bold;">✔ Single MP4 Video Ready (${r.mergedMp4.size_mb} MB)</span><br>
              👉 <a href="${r.mergedMp4.path}" download style="font-size: 16px; font-weight: bold;">Download ${r.mergedMp4.name}</a> | 
              <a href="javascript:void(0)" onclick="openFolder()">Open in Folder</a>
            `;
          } else {
            actionHtml = `
              <button onclick="startDownload('${r.id}', '${r.title}', '${r.readyAt}')" style="padding: 6px 14px; font-size: 14px; cursor: pointer;">
                ⬇ Download Single Video with Audio (.mp4)
              </button>
            `;
          }

          return `
            <li style="margin-bottom: 22px;">
              <b>${r.title}</b><br>
              <small>Date: ${r.readyAt ? new Date(r.readyAt).toLocaleString() : 'N/A'}</small><br>
              <div style="margin-top: 6px;">${actionHtml}</div>
            </li>
          `;
        }).join('') + '</ol>';

        if (isBusy && !pollTimer) {
          pollTimer = setInterval(loadClasses, 2000);
        } else if (!isBusy && pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }

      } catch (e) {
        list.innerHTML = '<span style="color: red;">Error: ' + e + '</span>';
      }
    }

    async function startDownload(id, title, readyAt) {
      await fetch('/api/download', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id, title, readyAt })
      });
      loadClasses();
    }

    function openFolder() {
      fetch('/api/open-folder', { method: 'POST' });
    }

    async function doLogout() {
      if (confirm('Sign out of this account?')) {
        await fetch('/api/logout', { method: 'POST' });
        loadClasses();
      }
    }

    window.onload = loadClasses;
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
            self.wfile.write(HTML_SIMPLE.encode("utf-8"))
            return

        elif path == "/api/recordings":
            data = get_recordings()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
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
            mime_type = "video/mp4" if full_file_path.endswith(".mp4") else "application/octet-stream"

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
                    super().end_headers()

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
            super().end_headers()
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
            print(f"[+] Server started: http://localhost:{p}")
            httpd.serve_forever()
            break
        except OSError:
            continue

if __name__ == "__main__":
    run()
