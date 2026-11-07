"""Session: one live connection to one camera, shared by every viewer (GUI card, GUI viewer,
web clients). Reconnects automatically; stops retrying if the login is rejected.
"""
import json
import subprocess
import threading
import time

import numpy as np

from .client import V380Client, V380Error

AUTH_ERRORS = ("invalid password", "invalid username", "invalid device id")


def probe_size(first_frame: bytes):
    """Width/height of an HEVC keyframe via ffprobe."""
    r = subprocess.run(["ffprobe", "-v", "error", "-f", "hevc", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "json", "-"],
                       input=first_frame, capture_output=True, timeout=10)
    s = json.loads(r.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


class Session:
    def __init__(self, key, cam_id, addr, user, password, port=8800):
        self.key, self.cam_id, self.addr = key, cam_id, addr
        self.user, self.password, self.port = user, password, port
        self.status = "idle"          # idle | connecting | live | error | auth_failed | stopped
        self.error = ""
        self.size = (0, 0)
        self.fps = 0.0
        self.frame = None
        self.seq = 0
        self.refs = 0
        self.idle_since = time.time()
        self.cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = None
        self._client = None
        self._ff = None

    # ---- lifecycle ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"cam-{self.key}")
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._teardown()
        self.status = "stopped"
        with self.cond:
            self.cond.notify_all()

    def _teardown(self):
        for closer in (lambda: self._client and self._client.close(),
                       lambda: self._ff and self._ff.kill()):
            try:
                closer()
            except Exception:
                pass

    # ---- consumers ----
    def wait(self, last_seq, timeout=5.0):
        """Block until a frame newer than last_seq exists. -> (frame|None, seq)"""
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq or self._stop.is_set(), timeout)
            return self.frame, self.seq

    # ---- worker ----
    def _run(self):
        backoff = 1.0
        while not self._stop.is_set():
            self.status, self.error = "connecting", ""
            try:
                self._stream_once()
                backoff = 1.0
            except V380Error as e:
                self.error = str(e)
                if str(e) in AUTH_ERRORS:
                    self.status = "auth_failed"
                    return
                self.status = "error"
            except Exception as e:                      # network drop, timeout, ffmpeg gone...
                self.error, self.status = f"{type(e).__name__}: {e}", "error"
            self._teardown()
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 10)

    def _stream_once(self):
        c = self._client = V380Client(self.addr, self.cam_id, self.password, self.user, port=self.port)
        c.connect()
        video = c.video()
        first = next(video)
        w, h = self.size = probe_size(first)
        ff = self._ff = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-flags", "low_delay", "-probesize", "32768",
             "-f", "hevc", "-i", "-", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pump_err = []

        def pump():
            try:
                ff.stdin.write(first)
                for data in video:
                    ff.stdin.write(data)
                    ff.stdin.flush()
            except Exception as e:
                pump_err.append(e)
            finally:
                try:
                    ff.stdin.close()
                except Exception:
                    pass
        threading.Thread(target=pump, daemon=True).start()

        size, t0, n = w * h * 3, time.time(), 0
        while not self._stop.is_set():
            buf = ff.stdout.read(size)
            if len(buf) < size:
                raise V380Error(str(pump_err[0]) if pump_err else "stream ended")
            frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            n += 1
            now = time.time()
            if now - t0 >= 1.0:
                self.fps, t0, n = n / (now - t0), now, 0
            with self.cond:
                self.frame, self.seq, self.status = frame, self.seq + 1, "live"
                self.cond.notify_all()
