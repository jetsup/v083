# V380 Studio

A desktop app and a web UI for V380 (Macrovideo) cameras. Both scan your network, find the
cameras, ask you to sign in once, remember the login against the camera's permanent ID, and
show the live view with a switchable fisheye dewarp. No phone, cloud or V380 app needed.

## Run
```
pip install -r requirements.txt          # also needs ffmpeg + ffprobe on PATH
python3 -m v380cam gui                   # desktop app
python3 -m v380cam web                   # web UI -> http://localhost:8081/
python3 -m v380cam web --host 0.0.0.0    # share on your LAN (see Security)
```

## How it works
1. **Scan.** A V380 UDP broadcast returns each camera's ID, IP and MAC; a parallel TCP sweep of
   your subnet lists every other device and its open ports. Cameras are listed first. RTSP/ONVIF
   cameras are detected too, but only V380 cameras can be viewed for now.
2. **Sign in.** Click a camera. The app asks for username and password, verifies them against the
   camera, and (if "Remember" is ticked) saves them **against the camera ID** (its serial), with the
   MAC and last IP kept alongside. If the camera's IP changes, the login still matches. If the ID
   couldn't be discovered, the dialog asks you for it.
3. **View.** Live 20 fps HEVC stream. Switch between **Raw fisheye** and **Dewarped**; in dewarped mode
   drag to look around, scroll to zoom, or use the Pan / Tilt / Rotate / Field-of-view sliders.
   Snapshot, fullscreen, and "forget saved login" are in the top bar.

Cameras with a saved login show a live preview on their card. The desktop app and web UI share the
same core (`v380cam/hub.py`), so behaviour is identical. They also share one camera connection,
so run **either** the desktop app **or** the web server at one time unless you have verified your
camera accepts two sessions.

### Keys
- Web viewer: `d` toggle dewarp, arrows pan/tilt (dewarped), `+`/`-` zoom, `f` fullscreen, `Esc` back.
- Desktop viewer: `F11` fullscreen, `Esc` exit fullscreen; everything else is mouse (drag to pan, scroll to zoom).

## Where logins are stored
`~/.config/v380cam/` (override with `V380_CONFIG_DIR`):
- `cameras.json` - per camera ID: username, **encrypted** password, name, MAC, last IP
- `secret.key` - random encryption key

Both files are `chmod 600`. This keeps passwords out of plain text (backups, screen-shares), but
anyone who can read both files as your user can decrypt them; it is not an OS keychain. Delete a
login with the trash button in the viewer, or remove the camera's entry from `cameras.json`.

## Security
- The web server binds to `127.0.0.1` by default. `--host 0.0.0.0` exposes the camera to everyone on
  your network **without authentication**; only do that on a network you trust.
- Passwords never reach the browser: it only learns whether a login is saved. The server rejects
  non-IP/localhost `Host` headers (DNS rebinding) and cross-origin POSTs.
- Snapshots from the desktop app go to `~/Pictures/V380 Studio/`.

## Web API
`GET /api/devices` · `POST /api/scan` · `POST /api/login` · `POST /api/forget` ·
`GET /api/stream/<id>?dewarp=1&yaw=0&pitch=50&roll=0&fov=100` (MJPEG, works in `<img>`, VLC, OpenCV) ·
`GET /api/snapshot/<id>` · `GET /api/status/<id>` · `POST /api/view` (change a running stream's lens)

## Use frames in your own automation
```python
from v380cam.hub import Hub
from v380cam.dewarp import Dewarper

hub = Hub()
hub.scan()                                   # or hub.login("48228407", "admin", "pw") once
session = hub.acquire("48228407")            # uses the saved login
dw = Dewarper(enabled=True)
frame, seq = None, 0
while True:
    frame, seq = session.wait(seq)           # numpy BGR (e.g. 1080x1088x3)
    view = dw.apply(frame)
```

## Project layout
```
v380cam/
  client.py     V380 LAN protocol (login, H.265 stream)
  discovery.py  UDP discovery + subnet sweep
  store.py      encrypted credential store
  session.py    live connection, auto-reconnect, ffmpeg decode
  hub.py        scan / login / sessions: the one API both UIs use
  dewarp.py     fisheye -> flat virtual camera
  gui/          PySide6 desktop app (theme.py = palette + icons)
  web/          HTTP server + static single-page UI
```

## Notes
- The camera protocol was worked out from the MIT-licensed [prsyahmi/v380](https://github.com/prsyahmi/v380);
  this is an independent Python implementation. Current firmware streams H.265.
- Dewarp assumes a ~180 degree equidistant fisheye. The defaults (tilt 50) suit a ceiling-mounted unit;
  tune `lens_fov`, `cx`, `cy`, `radius` in `Dewarper(...)` if the picture looks stretched.
- Don't add `-fflags nobuffer` to the ffmpeg command: it makes the HEVC decoder drop references.
