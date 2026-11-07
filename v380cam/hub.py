"""Hub: the one object both front-ends (PySide6 app, web UI) talk to.

scan()            find devices / cameras on the LAN
login()           verify credentials and (optionally) remember them against the camera ID
acquire()/release live sessions from saved credentials, shared and reference counted
"""
import threading
import time

from . import discovery
from .client import V380Client, V380Error
from .session import Session
from .store import CredentialStore


class Hub:
    IDLE_SECONDS = 20

    def __init__(self, store=None):
        self.store = store or CredentialStore()
        self.devices = {}                 # key -> device dict (latest scan)
        self.scanning = False
        self.progress = (0.0, "")
        self._sessions = {}
        self._temp = {}                   # logins the user chose NOT to remember
        self._lock = threading.RLock()
        threading.Thread(target=self._reaper, daemon=True).start()

    # ---- discovery ----
    def scan(self, on_progress=None):
        with self._lock:
            if self.scanning:
                return list(self.devices.values())
            self.scanning, self.progress = True, (0.0, "Starting")

        def cb(frac, msg, devs):
            self.progress = (frac, msg)
            if on_progress:
                on_progress(frac, msg, self._decorate(devs))
        try:
            found = discovery.scan(cb)
            decorated = self._decorate([d.to_dict() for d in found])
            with self._lock:
                self.devices = {d["key"]: d for d in decorated}
            for d in decorated:                                  # keep saved IPs fresh (DHCP)
                if d["cam_id"]:
                    self.store.update_ip(d["cam_id"], d["ip"], d["mac"])
            return decorated
        finally:
            self.scanning = False

    def _decorate(self, devs):
        out = []
        for d in devs:
            d = dict(d)
            d["saved"] = bool(d["is_camera"] and self.store.has(d["cam_id"] or d["key"], d["mac"]))
            d["name"] = (self.store.meta(d["cam_id"] or d["key"], d["mac"]).get("name")
                         or (f"Camera {d['cam_id']}" if d["cam_id"] else d["ip"]))
            out.append(d)
        return out

    def device(self, key):
        return self.devices.get(key)

    # ---- credentials ----
    def login(self, key, user, password, remember=True, cam_id=None, ip=None):
        """Verify against the camera. Raises V380Error('invalid password') etc."""
        d = self.devices.get(key) or {}
        cam_id = cam_id or d.get("cam_id") or (key if str(key).isdigit() else "")
        ip = ip or d.get("ip")
        if not ip:
            raise V380Error("camera not found on the network - scan again")
        if not str(cam_id).isdigit():
            raise V380Error("camera ID required")
        V380Client(ip, cam_id, password, user, port=d.get("port", 8800)).verify()
        cam_id = str(cam_id)
        if remember:
            self.store.save(cam_id, user, password, mac=d.get("mac", ""), ip=ip)
            self._temp.pop(cam_id, None)
        else:
            self._temp[cam_id] = (user, password)
        if key in self.devices:                                  # promote to an ID-keyed entry
            self.devices[key]["cam_id"] = cam_id
        return cam_id

    def refresh(self):
        """Re-decorate the last scan (e.g. after saving/forgetting a login)."""
        with self._lock:
            self.devices = {d["key"]: d for d in self._decorate(self.devices.values())}
            return list(self.devices.values())

    def acquire_possible(self, d):
        """True if we hold usable credentials for this device dict."""
        cid = d.get("cam_id")
        return bool(str(cid).isdigit() and (self.store.has(str(cid), d.get("mac")) or str(cid) in self._temp))

    def forget(self, key):
        d = self.devices.get(key) or {}
        self._temp.pop(str(d.get("cam_id")), None)
        self.drop(key)
        return self.store.delete(d.get("cam_id") or key, d.get("mac"))

    # ---- live sessions ----
    def acquire(self, key):
        """Return a started Session using saved credentials (None if not saved/unknown)."""
        d = self.devices.get(key)
        if not d:
            return None
        cid = d["cam_id"] or key
        creds = self.store.get(cid, d["mac"]) or self._temp.get(str(d["cam_id"]))
        if not creds or not str(d["cam_id"]).isdigit():
            return None
        with self._lock:
            s = self._sessions.get(key)
            if s is None or s.status in ("stopped", "auth_failed") or s.addr != d["ip"]:
                if s:
                    s.stop()
                s = Session(key, d["cam_id"], d["ip"], creds[0], creds[1], d.get("port", 8800))
                self._sessions[key] = s
            s.refs += 1
            s.start()
            return s

    def release(self, session):
        with self._lock:
            session.refs = max(0, session.refs - 1)
            session.idle_since = time.time()

    def session_info(self, key):
        s = self._sessions.get(key)
        if not s:
            return {"status": "idle", "fps": 0, "width": 0, "height": 0, "error": ""}
        return {"status": s.status, "fps": round(s.fps, 1), "width": s.size[0], "height": s.size[1],
                "error": s.error}

    def drop(self, key):
        with self._lock:
            s = self._sessions.pop(key, None)
        if s:
            s.stop()

    def _reaper(self):
        while True:
            time.sleep(5)
            with self._lock:
                for k, s in list(self._sessions.items()):
                    if s.refs == 0 and time.time() - s.idle_since > self.IDLE_SECONDS:
                        s.stop()
                        del self._sessions[k]

    def shutdown(self):
        with self._lock:
            for s in self._sessions.values():
                s.stop()
            self._sessions.clear()
