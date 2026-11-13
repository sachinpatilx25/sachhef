from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import re
import requests

BASE_API = "https://hefshinejavasms3-ibwfknlyra-el.a.run.app"

class handler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, mytoken')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        url_p = urlparse(self.path)
        path = url_p.path
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else "{}"

        if path.endswith("/login"):
            try:
                data = json.loads(body)
                u = data.get("username", "").strip()
                p = data.get("password", "").strip()
                r = requests.post(
                    f"{BASE_API}/",
                    json={"userName": u, "password": p},
                    headers={"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"},
                    timeout=15
                )
                r.raise_for_status()
                res_data = r.json()
                jwt = res_data.get("jwt")
                if jwt:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"jwt": jwt, "username": u}).encode("utf-8"))
                    return
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": res_data.get("errorMessage") or "Invalid credentials"}).encode("utf-8"))
                    return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        url_p = urlparse(self.path)
        path = url_p.path
        qs = parse_qs(url_p.query)

        auth_header = self.headers.get('Authorization', '')
        jwt = auth_header.replace('Bearer ', '').strip() if auth_header else self.headers.get('mytoken', '')

        if path.endswith("/recordings"):
            if not jwt:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Unauthorized"}).encode("utf-8"))
                return

            recordings = []
            page = 0
            headers = {"mytoken": jwt, "Accept": "application/json, text/plain, */*"}

            while True:
                try:
                    r = requests.get(f"{BASE_API}/student/bbb/recordings?page={page}", headers=headers, timeout=15)
                    if r.status_code in (401, 403):
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": "Session expired"}).encode("utf-8"))
                        return
                    r.raise_for_status()
                    data = r.json()
                    recs = data.get("recordings", [])
                    recordings.extend(recs)
                    if not data.get("hasNext"):
                        break
                    page += 1
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e), "recordings": recordings}).encode("utf-8"))
                    return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"recordings": recordings, "count": len(recordings)}).encode("utf-8"))
            return

        elif path.endswith("/play"):
            rec_id = qs.get("id", [""])[0]
            if not rec_id or not jwt:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Missing parameters"}).encode("utf-8"))
                return

            try:
                play_url = f"{BASE_API}/student/bbb/recordings/{rec_id}/play"
                r = requests.post(play_url, json={}, headers={"mytoken": jwt}, timeout=15)
                r.raise_for_status()
                playback_url = r.json().get("playbackUrl")
                if not playback_url:
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Playback URL not found"}).encode("utf-8"))
                    return

                p = urlparse(playback_url)
                origin = f"{p.scheme}://{p.netloc}"
                if "/playback/presentation/" in p.path:
                    prefix_part = p.path.split("/playback/presentation/")[0]
                    pres_prefix = f"{prefix_part}/presentation"
                else:
                    match = re.match(r'(/bbb/playback/[^/]+)', p.path)
                    prefix_part = match.group(1) if match else ""
                    pres_prefix = f"{prefix_part}/presentation"

                base_media_url = f"{origin}{pres_prefix}/{rec_id}"

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "playback_url": playback_url,
                    "screenshare_url": f"{base_media_url}/deskshare/deskshare.webm",
                    "webcam_url": f"{base_media_url}/video/webcams.webm",
                    "metadata_url": f"{base_media_url}/metadata.xml"
                }).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        self.send_response(404)
        self.end_headers()

