#!/usr/bin/env python3
"""Pad Deck server: serves the app, saves the layout and runs the Pad's system features.

    python3 server.py [port]      (default 8099)

GET  /api/layout              current layout (layout.json)
PUT  /api/layout              save layout
GET  /api/layouts             saved layouts: [{name, saved, pages, buttons}], newest first
POST /api/layouts             {name} save the current layout under that name (replaces one with the same name)
POST /api/layouts/load        {name} make a saved layout current (the one it replaces is kept as "Before last load")
POST /api/layouts/new         start fresh: an empty 4×3 layout (the old one is kept as "Before last load")
DELETE /api/layouts?name=…    delete a saved layout
GET  /api/icons               list of icon filenames
POST /api/icons               upload an icon (raw body, X-Filename header)
GET  /api/settings            press feedback settings (settings.json)
PUT  /api/settings            save them
POST /api/buzz                pulse the Pad 7 vibration motor
POST /api/leds                set the Pad 7's top RGB LEDs, {"colors": ["#rrggbb", ...]}  (Pad only)

Pad-only (refused unless the request comes from the Pad itself):
GET  /api/system              hostname, address, uptime, temperature
POST /api/system/reboot|shutdown
GET  /api/wifi[?rescan=1]     current connection and nearby networks
POST /api/wifi                {ssid, password} connect, and make it stick across reboots
GET  /api/term                WebSocket: a shell on the Pad (binary = keystrokes, text = {"cols","rows"})
"""
import base64
import fcntl
import hashlib
import json
import os
import pty
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote_plus

ROOT = os.path.dirname(os.path.abspath(__file__))
LAYOUT = os.path.join(ROOT, "layout.json")
LAYOUTS = os.path.join(ROOT, "layouts")  # saved layouts, one JSON file each
SETTINGS = os.path.join(ROOT, "settings.json")
ICONS = os.path.join(ROOT, "icons")
ICON_TYPES = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif")
DEFAULT_SETTINGS = {"sound": True, "vibrate": True, "volume": 0.6, "leds": True,
                    "obsHost": "", "obsPort": 4455, "obsPassword": ""}  # OBS: Menu → OBS
VIBRATION = "/sys/class/leds/pad7_vibration/brightness"
BTT_CFG = "/boot/system.cfg"   # BTT's boot script reconnects to the Wi-Fi named here
LOCAL = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def write_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def run(*cmd, timeout=20):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def buzz():
    try:
        with open(VIBRATION, "w") as f:
            f.write("1")
        time.sleep(0.06)
        with open(VIBRATION, "w") as f:
            f.write("0")
    except OSError:
        pass


# The two RGB LEDs on top of the Pad 7 (ws2812 overlay, BTT's driver): one 32-bit int per LED.
LEDS = "/dev/ws2812-led"


LEDS_LOCK = threading.Lock()


def set_leds(colors):
    values = [int(c.lstrip("#"), 16) & 0xFFFFFF for c in colors[:8]]
    # One raw write, one at a time: the driver bit-bangs the LEDs and always returns 0 bytes written,
    # so a buffered file would retry forever (re-sending the colours, flickering and eating the CPU).
    with LEDS_LOCK:
        fd = os.open(LEDS, os.O_WRONLY)
        try:
            os.write(fd, struct.pack(f"<{len(values)}I", *values))
        finally:
            os.close(fd)


# ---------- Saved layouts ----------

BACKUP_NAME = "Before last load"


def layout_file(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    if not slug:
        raise ValueError("Give the layout a name")
    return os.path.join(LAYOUTS, slug + ".json")


def save_layout_as(name, layout=None):
    os.makedirs(LAYOUTS, exist_ok=True)
    if layout is None:
        with open(LAYOUT) as f:
            layout = json.load(f)
    entry = {"name": name, "saved": time.strftime("%Y-%m-%dT%H:%M:%S"), "layout": layout}
    write_atomic(layout_file(name), json.dumps(entry, indent=2).encode())


def list_layouts():
    out = []
    for fn in os.listdir(LAYOUTS) if os.path.isdir(LAYOUTS) else []:
        try:
            with open(os.path.join(LAYOUTS, fn)) as f:
                e = json.load(f)
        except (OSError, ValueError):
            continue
        pages = e["layout"].get("pages") or [{"buttons": e["layout"].get("buttons", [])}]
        out.append({"name": e["name"], "saved": e["saved"], "pages": len(pages),
                    "columns": e["layout"].get("columns", 4), "rows": e["layout"].get("rows", 3),
                    "buttons": sum(1 for p in pages for b in p["buttons"] if b)})
    return sorted(out, key=lambda e: e["saved"], reverse=True)


def load_layout(name=None):
    """Make a saved layout current, grid size included (name=None: a fresh, empty 4×3 one)."""
    if name is None:
        layout = None
    else:
        with open(layout_file(name)) as f:
            layout = json.load(f)["layout"]
    with open(LAYOUT) as f:
        current = json.load(f)
    save_layout_as(BACKUP_NAME, current)
    if layout is None:
        layout = {"columns": 4, "rows": 3, "pages": [{"buttons": []}]}
    write_atomic(LAYOUT, json.dumps(layout, indent=2).encode())


# ---------- Wi-Fi (NetworkManager) ----------

def nm_fields(line):
    """Split one line of `nmcli -t` output (':' separated, '\\:' escaped)."""
    return [p.replace("\\:", ":").replace("\\\\", "\\") for p in re.split(r"(?<!\\):", line)]


def wifi_ip():
    out = run("ip", "-4", "-o", "addr", "show", "wlan0").stdout
    m = re.search(r"inet (\S+)/", out)
    return m.group(1) if m else None


def wifi_state(rescan):
    out = run("sudo", "nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
              "--rescan", "yes" if rescan else "auto", timeout=30).stdout
    nets = {}
    for line in out.splitlines():
        in_use, ssid, sig, sec = (nm_fields(line) + ["", "", "", ""])[:4]
        if not ssid:
            continue
        n = {"ssid": ssid, "signal": int(sig or 0), "secure": bool(sec.strip("- ")), "active": in_use == "*"}
        old = nets.get(ssid)
        if not old or n["active"] or (n["signal"] > old["signal"] and not old["active"]):
            nets[ssid] = n
    networks = sorted(nets.values(), key=lambda n: (not n["active"], -n["signal"]))
    active = next((n for n in networks if n["active"]), None)
    current = {"ssid": active["ssid"], "signal": active["signal"], "ip": wifi_ip()} if active else None
    return {"current": current, "networks": networks}


def cfg_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def wifi_connect(ssid, password):
    cmd = ["sudo", "nmcli", "dev", "wifi", "connect", ssid, "ifname", "wlan0"]
    if password:
        cmd[6:6] = ["password", password]  # after the SSID
    r = run(*cmd, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().replace("Error: ", "")
        if password:
            msg = msg.replace(password, "••••")
        return False, msg or "Couldn't connect"
    # Make it the network BTT's boot script keeps us on.
    cfg = run("sudo", "cat", BTT_CFG).stdout
    lines, seen = [], set()
    for line in cfg.splitlines():
        key = re.match(r"#?(WIFI_SSID|WIFI_PASSWD)=", line)
        if key:
            seen.add(key.group(1))
            line = f"{key.group(1)}={cfg_quote(ssid if key.group(1) == 'WIFI_SSID' else password)}"
        lines.append(line)
    for k in ("WIFI_SSID", "WIFI_PASSWD"):
        if k not in seen:
            lines.append(f"{k}={cfg_quote(ssid if k == 'WIFI_SSID' else password)}")
    subprocess.run(["sudo", "tee", BTT_CFG], input="\n".join(lines) + "\n", text=True, capture_output=True)
    return True, "Connected"


# ---------- System ----------

def system_info():
    up = int(float(open("/proc/uptime").read().split()[0]))
    try:
        temp = round(int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000)
    except OSError:
        temp = None
    ssid = next((nm_fields(l)[1] for l in run("nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi").stdout.splitlines()
                 if l.startswith("yes:")), None)
    return {"hostname": socket.gethostname(), "ip": wifi_ip(), "ssid": ssid, "temp": temp,
            "uptime": f"{up // 86400}d {up % 86400 // 3600}h {up % 3600 // 60}m" if up >= 86400 else f"{up // 3600}h {up % 3600 // 60}m"}


# ---------- Terminal over WebSocket ----------

def ws_send(sock, opcode, payload=b""):
    n = len(payload)
    head = bytes([0x80 | opcode])
    head += bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n)
    sock.sendall(head + payload)


def ws_frames(buf):
    """Yield complete (opcode, payload) frames from the front of buf, removing them."""
    while len(buf) >= 2:
        n, i = buf[1] & 0x7F, 2
        if n == 126:
            if len(buf) < 4:
                return
            n, i = struct.unpack(">H", buf[2:4])[0], 4
        elif n == 127:
            if len(buf) < 10:
                return
            n, i = struct.unpack(">Q", buf[2:10])[0], 10
        masked = buf[1] & 0x80
        mask = buf[i:i + 4] if masked else b"\0\0\0\0"
        i += 4 if masked else 0
        if len(buf) < i + n:
            return
        payload = bytes(b ^ mask[j % 4] for j, b in enumerate(buf[i:i + n]))
        opcode = buf[0] & 0x0F
        del buf[:i + n]
        yield opcode, payload


def run_terminal(sock):
    pid, fd = pty.fork()
    if pid == 0:
        home = os.path.expanduser("~")
        os.chdir(home)
        env = {"HOME": home, "USER": os.environ.get("USER", "deck"), "TERM": "xterm-256color", "LANG": "C.UTF-8",
               "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "SHELL": "/bin/bash"}
        os.execvpe("bash", ["bash", "-l"], env)
    buf = bytearray()
    try:
        while True:
            ready, _, _ = select.select([sock, fd], [], [])
            if fd in ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                ws_send(sock, 2, data)
            if sock in ready:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                for opcode, payload in ws_frames(buf):
                    if opcode == 8:
                        return
                    if opcode == 9:
                        ws_send(sock, 10, payload)
                    elif opcode == 1:
                        size = json.loads(payload)
                        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", int(size["rows"]), int(size["cols"]), 0, 0))
                    elif opcode == 2:
                        os.write(fd, payload)
    finally:
        try:
            os.kill(pid, signal.SIGHUP)
            os.waitpid(pid, 0)
        except OSError:
            pass
        os.close(fd)
        try:
            ws_send(sock, 8)
        except OSError:
            pass


# ---------- HTTP ----------

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def json_body(self):
        try:
            data = json.loads(self.body())
            return data if isinstance(data, dict) else None
        except ValueError:
            return None

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as e:
            # A failing system command (e.g. nmcli) becomes an error message rather than a dropped connection.
            try:
                self.send_json({"error": str(e)}, 500)
            except OSError:
                pass

    def local_only(self):
        if self.client_address[0] in LOCAL:
            return True
        self.send_json({"error": "Only available on the Pad itself"}, 403)
        return False

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/layout":
            with open(LAYOUT) as f:
                return self.send_json(json.load(f))
        if path == "/api/layouts":
            return self.send_json(list_layouts())
        if path == "/api/icons":
            return self.send_json(sorted(n for n in os.listdir(ICONS)
                                         if n.lower().endswith(ICON_TYPES) and not n.startswith(".")))
        if path == "/api/settings":
            try:
                with open(SETTINGS) as f:
                    return self.send_json({**DEFAULT_SETTINGS, **json.load(f)})
            except (OSError, ValueError):
                return self.send_json(DEFAULT_SETTINGS)
        if path == "/api/system":
            return self.local_only() and self.send_json(system_info())
        if path == "/api/wifi":
            return self.local_only() and self.send_json(wifi_state("rescan=1" in query))
        if path == "/api/term":
            if self.headers.get("Upgrade", "").lower() != "websocket" or not self.local_only():
                return
            accept = base64.b64encode(hashlib.sha1((self.headers["Sec-WebSocket-Key"] + WS_GUID).encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            return run_terminal(self.connection)
        super().do_GET()

    def do_PUT(self):
        data = self.json_body()
        if self.path == "/api/layout":
            pages = data.get("pages") if data else None
            if not isinstance(pages, list) or not pages or not all(isinstance(pg.get("buttons"), list) for pg in pages):
                return self.send_error(400, "invalid layout")
            write_atomic(LAYOUT, json.dumps(data, indent=2).encode())
            return self.send_json({"ok": True})
        if self.path == "/api/settings":
            if data is None:
                return self.send_error(400, "invalid settings")
            clean = {k: type(v)(data[k]) for k, v in DEFAULT_SETTINGS.items() if k in data}
            write_atomic(SETTINGS, json.dumps({**DEFAULT_SETTINGS, **clean}, indent=2).encode())
            return self.send_json({"ok": True})
        self.send_error(404)

    def do_DELETE(self):
        path, _, query = self.path.partition("?")
        if path == "/api/layouts":
            name = dict(re.findall(r"(\w+)=([^&]*)", query)).get("name", "")
            try:
                os.remove(layout_file(unquote_plus(name)))
            except FileNotFoundError:
                pass
            return self.send_json({"ok": True})
        self.send_error(404)

    def do_POST(self):
        if self.path in ("/api/layouts", "/api/layouts/load", "/api/layouts/new"):
            name = str((self.json_body() or {}).get("name", "")).strip()
            try:
                if self.path == "/api/layouts":
                    save_layout_as(name)
                else:
                    load_layout(name if self.path == "/api/layouts/load" else None)
            except FileNotFoundError:
                return self.send_json({"error": "That layout no longer exists"}, 404)
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            return self.send_json({"ok": True})
        if self.path == "/api/icons":
            name = re.sub(r"[^\w.-]", "_", os.path.basename(self.headers.get("X-Filename", "")))
            if not name.lower().endswith(ICON_TYPES):
                return self.send_error(400, "unsupported file type")
            write_atomic(os.path.join(ICONS, name), self.body())
            return self.send_json({"name": name})
        if self.path == "/api/buzz":
            threading.Thread(target=buzz, daemon=True).start()
            return self.send_json({"ok": True})
        if not self.path.startswith(("/api/wifi", "/api/system/", "/api/leds")):
            return self.send_error(404)
        if not self.local_only():
            return
        if self.path == "/api/leds":
            set_leds([str(c) for c in (self.json_body() or {}).get("colors", [])])
            return self.send_json({"ok": True})
        if self.path == "/api/wifi":
            data = self.json_body() or {}
            if not data.get("ssid"):
                return self.send_json({"error": "No network chosen"}, 400)
            ok, msg = wifi_connect(str(data["ssid"]), str(data.get("password", "")))
            return self.send_json({"ok": ok, "message": msg} if ok else {"error": msg}, 200 if ok else 400)
        action = {"/api/system/reboot": "reboot", "/api/system/shutdown": "poweroff"}.get(self.path)
        if not action:
            return self.send_error(404)
        self.send_json({"ok": True})
        subprocess.Popen(["sudo", "systemctl", action])


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    ThreadingHTTPServer.daemon_threads = True
    print(f"Pad Deck on http://0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
