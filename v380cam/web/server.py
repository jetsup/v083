"""Web UI server: same logic as the desktop app (scan -> sign in -> view + dewarp).

    python3 -m v380cam web [--host 127.0.0.1] [--port 8081]

JSON API (all under /api):
    GET  /api/devices                    devices + scan state
    POST /api/scan                       start a scan
    POST /api/login   {key,user,password,remember,cam_id}   verify (+save) credentials
    POST /api/forget  {key}              delete saved credentials
    GET  /api/stream/<key>?dewarp=1&yaw=&pitch=&roll=&fov=&sid=   MJPEG stream
    POST /api/view    {sid,dewarp,yaw,pitch,roll,fov}      change a running stream's lens live
    GET  /api/snapshot/<key>?...         single JPEG
    GET  /api/status/<key>               live session state, fps, resolution
Passwords never leave the server: the browser only learns whether a login is saved.
"""
import argparse
import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2

from ..client import V380Error
from ..dewarp import Dewarper
from ..hub import Hub

STATIC = Path(__file__).parent / "static"
hub = None
views = {}                     # sid -> Dewarper (live-adjustable per open stream)
views_lock = threading.Lock()
scan_thread = None


def _is_ip(name):
    import ipaddress
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def start_scan():
    global scan_thread
    if hub.scanning or (scan_thread and scan_thread.is_alive()):
        return False
    scan_thread = threading.Thread(target=hub.scan, daemon=True)
    scan_thread.start()
    return True


def dewarper(q, sid=None):
    view = {k: float(q[k]) for k in ("yaw", "pitch", "roll", "fov") if k in q}
    dw = Dewarper(enabled=q.get("dewarp", "0") in ("1", "true"), **view)
    if sid:
        with views_lock:
            if len(views) > 64:
                views.pop(next(iter(views)))
            views[sid] = dw
    return dw


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- helpers ----
    def _host_ok(self):
        """Refuse DNS-rebinding / cross-site POSTs: only same-origin requests are served."""
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        name = host.rsplit(":", 1)[0].strip("[]") if not host.endswith("]") else host.strip("[]")
        if name != "localhost" and not _is_ip(name):          # DNS-rebinding guard
            self.send_error(403, "use the server's IP address or localhost")
            return False
        if origin and urlparse(origin).netloc != host:
            self.send_error(403, "cross-origin request refused")
            return False
        return True

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n < 65536 else {}
        except ValueError:
            return {}

    # ---- GET ----
    def do_GET(self):
        if not self._host_ok():
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = u.path.strip("/").split("/")
        if parts[0] == "api":
            return self.api_get(parts[1:], q)
        name = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = (STATIC / name).resolve()
        if STATIC not in f.parents or not f.is_file():
            return self.send_error(404)
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def api_get(self, parts, q):
        if parts == ["devices"]:
            frac, msg = hub.progress
            return self._json({"scanning": hub.scanning, "progress": frac, "message": msg,
                               "devices": hub.refresh() if not hub.scanning else list(hub.devices.values())})
        if len(parts) == 2 and parts[0] == "status":
            return self._json(hub.session_info(parts[1]))
        if len(parts) == 2 and parts[0] in ("stream", "snapshot"):
            return self.stream(parts[1], q) if parts[0] == "stream" else self.snapshot(parts[1], q)
        self.send_error(404)

    def _session(self, key):
        s = hub.acquire(key)
        if s is None:
            self._json({"error": "no saved login for this camera"}, 401)
        return s

    def snapshot(self, key, q):
        s = self._session(key)
        if not s:
            return
        try:
            frame, _ = s.wait(0, 10)
            if frame is None:
                return self._json({"error": s.error or "no frame yet"}, 503)
            jpg = cv2.imencode(".jpg", dewarper(q).apply(frame), [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
        finally:
            hub.release(s)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpg)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(jpg)

    def stream(self, key, q):
        s = self._session(key)
        if not s:
            return
        dw = dewarper(q, q.get("sid"))
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        last = 0
        try:
            while True:
                frame, last = s.wait(last, 8)
                if s.status == "auth_failed":
                    break
                if frame is None:
                    continue
                jpg = cv2.imencode(".jpg", dw.apply(frame), [cv2.IMWRITE_JPEG_QUALITY, 82])[1]
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n"
                                 % len(jpg) + jpg.tobytes() + b"\r\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            hub.release(s)
            sid = q.get("sid")
            if sid:
                with views_lock:
                    views.pop(sid, None)

    # ---- POST ----
    def do_POST(self):
        if not self._host_ok():
            return
        path = urlparse(self.path).path
        b = self._body()
        if path == "/api/scan":
            return self._json({"started": start_scan()})
        if path == "/api/login":
            try:
                cam_id = hub.login(b.get("key", ""), (b.get("user") or "admin").strip(), b.get("password", ""),
                                   bool(b.get("remember", True)), (b.get("cam_id") or "").strip() or None)
                return self._json({"ok": True, "cam_id": cam_id})
            except V380Error as e:
                return self._json({"ok": False, "error": str(e)}, 401)
            except OSError as e:
                return self._json({"ok": False, "error": f"can't reach camera ({e})"}, 502)
        if path == "/api/forget":
            return self._json({"ok": hub.forget(b.get("key", ""))})
        if path == "/api/view":
            with views_lock:
                dw = views.get(b.get("sid"))
            if not dw:
                return self._json({"ok": False}, 404)
            if "dewarp" in b:
                dw.enabled = bool(b["dewarp"])
            for k in ("yaw", "pitch", "roll", "fov"):
                if k in b:
                    setattr(dw, k, float(b[k]))
            return self._json({"ok": True})
        self.send_error(404)


def main():
    global hub
    ap = argparse.ArgumentParser(prog="v380cam web")
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 shares the UI on your LAN (no auth!)")
    ap.add_argument("--port", type=int, default=8081)
    a = ap.parse_args()
    hub = Hub()
    start_scan()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print(f"V380 Studio web UI  ->  http://{'localhost' if a.host == '127.0.0.1' else a.host}:{a.port}/")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hub.shutdown()


if __name__ == "__main__":
    main()
