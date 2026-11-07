"""Saved camera logins.

Credentials are tied to the camera's permanent ID (its serial), not its IP, so they survive
DHCP address changes. Passwords are encrypted (Fernet) with a random key kept in a separate
0600 file under ~/.config/v380cam. That protects against casual disclosure (cloud sync,
backups, screen-shares of the JSON); it is not a substitute for an OS keychain.
"""
import json
import os
import threading
import time
from pathlib import Path

from cryptography.fernet import Fernet

DIR = Path(os.environ.get("V380_CONFIG_DIR", Path.home() / ".config" / "v380cam"))


def _private(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


class CredentialStore:
    def __init__(self, directory=DIR):
        self.dir = Path(directory)
        self.file = self.dir / "cameras.json"
        self.keyfile = self.dir / "secret.key"
        self._lock = threading.Lock()
        self._fernet = None

    def _f(self):
        if self._fernet is None:
            if not self.keyfile.exists():
                _private(self.keyfile, Fernet.generate_key())
            self._fernet = Fernet(self.keyfile.read_bytes())
        return self._fernet

    def _load(self):
        try:
            return json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {}

    def _find(self, data, key=None, mac=None):
        if key and key in data:
            return key
        if mac:
            for k, v in data.items():
                if v.get("mac") and v["mac"] == mac:
                    return k
        return None

    # ---- API ----
    def save(self, key, user, password, *, name="", mac="", ip=""):
        with self._lock:
            data = self._load()
            old = data.get(key, {})
            data[key] = {"user": user, "password": self._f().encrypt(password.encode()).decode(),
                         "name": name or old.get("name", ""), "mac": mac or old.get("mac", ""),
                         "ip": ip or old.get("ip", ""), "saved_at": int(time.time())}
            _private(self.file, json.dumps(data, indent=2).encode())

    def get(self, key, mac=None):
        """-> (user, password) or None"""
        with self._lock:
            data = self._load()
            k = self._find(data, key, mac)
            if not k:
                return None
            try:
                return data[k]["user"], self._f().decrypt(data[k]["password"].encode()).decode()
            except Exception:
                return None

    def has(self, key, mac=None):
        return self._find(self._load(), key, mac) is not None

    def meta(self, key, mac=None):
        data = self._load()
        k = self._find(data, key, mac)
        return {k2: v for k2, v in data[k].items() if k2 != "password"} if k else {}

    def update_ip(self, key, ip, mac=""):
        with self._lock:
            data = self._load()
            k = self._find(data, key, mac)
            if k and data[k].get("ip") != ip:
                data[k]["ip"] = ip
                _private(self.file, json.dumps(data, indent=2).encode())

    def rename(self, key, name):
        with self._lock:
            data = self._load()
            if key in data:
                data[key]["name"] = name
                _private(self.file, json.dumps(data, indent=2).encode())

    def delete(self, key, mac=None):
        with self._lock:
            data = self._load()
            k = self._find(data, key, mac)
            if k:
                del data[k]
                _private(self.file, json.dumps(data, indent=2).encode())
            return bool(k)
