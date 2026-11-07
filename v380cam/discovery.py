"""Network scan: find every device on the LAN, and pick out the cameras.

1. V380 UDP broadcast ("NVDEVSEARCH^100" -> port 10008) gives MAC, IP and camera ID.
2. A fast TCP sweep of the local subnet(s) finds every live host and its open ports.
3. Hosts are classified: v380 camera / other camera (RTSP, ONVIF) / other device.
"""
import ipaddress
import re
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict

PORTS = {8800: "V380", 9800: "V380 data", 554: "RTSP", 8554: "RTSP", 8899: "ONVIF", 80: "HTTP",
         443: "HTTPS", 8080: "HTTP", 22: "SSH", 53: "DNS", 445: "SMB", 5353: "mDNS", 8000: "HTTP"}


@dataclass
class Device:
    ip: str
    mac: str = ""
    cam_id: str = ""            # V380 serial / device ID: the permanent identity
    port: int = 8800            # V380 stream port
    kind: str = "other"         # "v380" | "camera" | "other"
    ports: list = field(default_factory=list)

    @property
    def is_camera(self):
        return self.kind in ("v380", "camera")

    @property
    def key(self):
        return self.cam_id or self.mac or self.ip

    def to_dict(self):
        d = asdict(self)
        d["key"] = self.key
        d["is_camera"] = self.is_camera
        d["services"] = [PORTS.get(p, str(p)) for p in self.ports]
        return d


def local_networks():
    """[(iface_ip, IPv4Network)] for real LAN interfaces."""
    nets = []
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True,
                             text=True, timeout=3).stdout
        for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", out):
            ip, plen = m.group(1), int(m.group(2))
            a = ipaddress.ip_address(ip)
            if a.is_loopback or plen >= 31 or plen < 16 or not a.is_private:
                continue                                   # skip lo, tailscale /32, public
            net = ipaddress.ip_network(f"{ip}/{max(plen, 24)}", strict=False)
            nets.append((ip, net))
    except Exception:
        pass
    if not nets:                                           # fallback: default-route address
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            s.close()
            nets.append((ip, ipaddress.ip_network(f"{ip}/24", strict=False)))
        except Exception:
            pass
    return nets


def _udp_discover(nets, seconds=1.8):
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", 10009))              # cameras reply to this fixed port
    except OSError:
        try:
            s.bind(("", 0))
        except OSError:
            return found
    targets = ["255.255.255.255"] + [str(n.broadcast_address) for _, n in nets]
    end = time.time() + seconds
    sent = 0
    s.settimeout(0.2)
    while time.time() < end:
        if sent < 4:
            for t in targets:
                try:
                    s.sendto(b"NVDEVSEARCH^100", (t, 10008))
                except OSError:
                    pass
            sent += 1
        try:
            data, _ = s.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        v = data.decode("latin1", "ignore").split("^")
        if len(v) >= 13 and v[0] == "NVDEVRESULT":
            try:
                port = int(v[9])
            except ValueError:
                port = 8800
            found[v[3]] = dict(mac=v[2].lower(), cam_id=v[12], port=port)
    s.close()
    return found


def _probe(ip, port, timeout=0.4):
    """-> 'open' | 'refused' | None. A refused connection still proves the host is up."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return "open"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return None


def _arp_table():
    table = {}
    try:
        for line in open("/proc/net/arp").read().splitlines()[1:]:
            f = line.split()
            if len(f) >= 4 and f[2] != "0x0" and f[3] != "00:00:00:00:00:00":
                table[f[0]] = f[3].lower()
    except OSError:
        pass
    return table


def scan(on_progress=None):
    """Scan the LAN. on_progress(fraction, message, devices_so_far) is optional."""
    def report(f, msg, devs=()):
        if on_progress:
            on_progress(f, msg, [d.to_dict() for d in devs])

    nets = local_networks()
    mine = {ip for ip, _ in nets}
    report(0.02, "Listening for V380 cameras")
    udp = _udp_discover(nets)
    devices = {ip: Device(ip, kind="v380", **info) for ip, info in udp.items()}
    report(0.18, f"{len(udp)} camera(s) answered", devices.values())

    hosts = [str(h) for _, n in nets for h in n.hosts() if str(h) not in mine]
    tasks = [(ip, p) for ip in hosts for p in PORTS]
    alive, opened, done = set(), {}, 0
    with ThreadPoolExecutor(max_workers=400) as ex:
        futs = {ex.submit(_probe, ip, p): (ip, p) for ip, p in tasks}
        for fut in as_completed(futs):
            ip, p = futs[fut]
            done += 1
            r = fut.result()
            if r:
                alive.add(ip)
                if r == "open":
                    opened.setdefault(ip, []).append(p)
            if done % 200 == 0 or done == len(tasks):
                report(0.18 + 0.75 * done / max(len(tasks), 1),
                       f"Probing {len(hosts)} addresses", devices.values())
    for ip in alive:
        devices.setdefault(ip, Device(ip)).ports = sorted(opened.get(ip, []))
    for ip, d in devices.items():
        d.ports = sorted(opened.get(ip, d.ports))

    arp = _arp_table()
    for ip, d in devices.items():
        d.mac = d.mac or arp.get(ip, "")
        if d.kind != "v380":
            if 8800 in d.ports and 9800 in d.ports:
                d.kind = "v380"                      # V380 protocol ports, but no UDP reply
            elif any(p in d.ports for p in (554, 8554, 8899)):
                d.kind = "camera"
    out = sorted(devices.values(), key=lambda d: (not d.is_camera, ipaddress.ip_address(d.ip)))
    report(1.0, f"Found {sum(d.is_camera for d in out)} camera(s) among {len(out)} devices", out)
    return out
