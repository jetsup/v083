"""Pure-Python client for the V380 LAN protocol (port 8800).

Protocol knowledge derived from reading prsyahmi/v380 (MIT). Yields raw H.264
Annex-B access units; pair with ffmpeg/PyAV/OpenCV to decode.
"""
import random
import socket
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

STATIC_KEY = b"macrovideo+*#!^@"
_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz1234567890!@#$%^&*()_+-="


class V380Error(Exception):
    pass


def _ecb(key, data):
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return e.update(data) + e.finalize()


def make_password_field(password: str) -> bytes:
    pw = password.encode()
    pw += b"\0" * (16 - len(pw) % 16)          # zero padding, always adds 1..16 bytes
    key = "".join(random.choice(_CHARS) for _ in range(16)).encode()
    return key + _ecb(key, _ecb(STATIC_KEY, pw))


def _recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise V380Error("connection closed")
        buf += chunk
    return bytes(buf)


def _field(b: bytes, size: int) -> bytes:
    return b[:size].ljust(size, b"\0")


class V380Client:
    def __init__(self, addr, cam_id, password, user="admin", port=8800,
                 hires=True, fps=20, timeout=5):
        self.addr, self.cam_id, self.password = addr, int(cam_id), password
        self.user, self.port, self.hires, self.fps, self.timeout = user, port, hires, fps, timeout
        self.sock = None

    def verify(self):
        """Check credentials only. Returns True, or raises V380Error (e.g. 'invalid password')."""
        self._login()
        return True

    def _login(self):
        req = bytearray(256)
        struct.pack_into("<iIBII", req, 0, 1167, 1022, 2, 1, self.cam_id)
        req[49:81] = _field(self.user.encode(), 32)
        req[81:113] = _field(make_password_field(self.password), 32)
        s = socket.create_connection((self.addr, self.port), self.timeout)
        try:
            s.sendall(req)
            resp = s.recv(256)
        finally:
            s.close()
        if len(resp) < 17:
            raise V380Error(f"short login response ({len(resp)} bytes)")
        cmd, result = struct.unpack_from("<ii", resp, 0)
        if cmd != 1168:
            raise V380Error(f"unexpected login command {cmd}")
        errs = {1011: "invalid username", 1012: "invalid password", 1018: "invalid device id"}
        if result in errs:
            raise V380Error(errs[result])
        (ticket,) = struct.unpack_from("<I", resp, 13)
        return ticket

    def connect(self):
        ticket = self._login()
        s = socket.create_connection((self.addr, self.port), self.timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        req = bytearray(256)
        struct.pack_into("<iIIHIIII", req, 0, 301, self.cam_id, 0, self.fps,
                         ticket, 0, 4097, 1 if self.hires else 0)
        s.sendall(req)
        resp = bytearray(s.recv(412).ljust(412, b"\0"))
        cmd, v21 = struct.unpack_from("<ii", resp, 0)
        if cmd != 401:
            raise V380Error(f"stream login: expected 401, got {cmd}")
        if v21 not in (402, 1001, -11, -12):
            raise V380Error(f"stream login: unsupported {v21}")
        struct.pack_into("<i", resp, 0, 303)   # start streaming
        s.sendall(resp)
        self.sock = s
        return self

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def video(self):
        """Yield complete frames (bytes): HEVC Annex-B on current firmware."""
        s = self.sock
        s.settimeout(self.timeout)
        vframe = bytearray()
        synced = False          # camera starts mid-GOP: skip P-frames until an I-frame
        while True:
            hdr = _recv_exact(s, 12)
            kind = hdr[0]
            if kind == 0x7F:
                ftype = hdr[1]
                # bytes 3..8: frame-chunk total (u16), chunk index (u16 @5), length (u16 @7)
                (total,) = struct.unpack_from("<H", hdr, 3)
                (cur,) = struct.unpack_from("<H", hdr, 5)
                (length,) = struct.unpack_from("<H", hdr, 7)
                if length > 500 or not total or cur > total:
                    raise V380Error("sanity check failed")
                payload = _recv_exact(s, length)
                if ftype in (0x00, 0x01, 0x34, 0x35):   # 0x34/0x35 = HEVC I/P
                    vframe += payload
                    if cur == total - 1:
                        data = bytes(vframe)
                        vframe.clear()
                        synced = synced or ftype in (0x00, 0x34)
                        if not synced:
                            continue
                        if ftype >= 0x34 and len(data) > 16:
                            data = data[16:]       # 16-byte frame header
                        yield data
                # 0x16 = audio, ignored
            elif kind in (0x1F, 0x6F):
                continue
            else:
                raise V380Error(f"unknown packet 0x{kind:02x}")
