#!/usr/bin/env python3
"""Pad Deck server: serves the app, saves the layout and runs the Pad's system features.

    python3 server.py [port]      (default 8099)

GET  /api/layout              current layout (layout.json)
PUT  /api/layout              save layout
GET  /api/device              {"device": "pad7" | "nebula"}, so the editor can hide what this device lacks
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
POST /api/run                 {"type": ..., ...} run a key's non-OBS action: a web request, or (through the
                              Pad Deck Helper on your computer) open a website/app, run a Shortcut, press keys
GET  /api/helper?what=ping|apps|shortcuts   ask the Pad Deck Helper (connection test, lists for the editor)
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
from urllib.parse import unquote_plus, urlsplit, urlunsplit
import urllib.error
import urllib.request
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
LAYOUT = os.path.join(ROOT, "layout.json")
LAYOUTS = os.path.join(ROOT, "layouts")  # saved layouts, one JSON file each
SETTINGS = os.path.join(ROOT, "settings.json")
ICONS = os.path.join(ROOT, "icons")
ICON_TYPES = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif")
DEFAULT_SETTINGS = {"sound": True, "vibrate": True, "volume": 0.6, "leds": True,
                    "obsHost": "", "obsPort": 4455, "obsPassword": "",  # OBS: Menu → OBS
                    "brightness": 180,  # screen backlight (Pad Deck Lite on the Nebula)
                    "helperHost": "", "helperPort": 8098, "helperCode": ""}  # Pad Deck Helper: Menu → Helper
VIBRATION = "/sys/class/leds/pad7_vibration/brightness"
BTT_CFG = "/boot/system.cfg"   # BTT's boot script reconnects to the Wi-Fi named here
LOCAL = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
# Devices with no browser on their own screen (Pad Deck Lite on the Nebula) manage Wi-Fi and power from the
# web editor instead, so those sections also answer other computers on the network. The terminal never does.
REMOTE_ADMIN = os.environ.get("PAD_DECK_REMOTE_ADMIN") == "1"
DEVICE = os.environ.get("PAD_DECK_DEVICE", "pad7")   # "nebula" for Pad Deck Lite: the editor hides what it lacks
NMCLI = shutil.which("nmcli") is not None   # the Pad 7 (NetworkManager); otherwise wpa_supplicant (the Nebula)
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


# ---------- Actions beyond OBS: web requests, and the Pad Deck Helper on your computer ----------

HELPER_TYPES = {"url", "app", "shortcut", "hotkey"}   # these run on the computer, via helper/pad-deck-helper.py


def load_settings():
    try:
        with open(SETTINGS) as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


def mdns_a_record(data):
    """First IPv4 address in the answer section of a DNS/mDNS response, or None."""
    def skip_name(i):
        while True:
            n = data[i]
            if n == 0:
                return i + 1
            if n & 0xC0 == 0xC0:                                 # compressed: pointer ends the name
                return i + 2
            i += n + 1
    try:
        qd, an = struct.unpack(">HH", data[4:8])
        i = 12
        for _ in range(qd):
            i = skip_name(i) + 4
        for _ in range(an):
            i = skip_name(i)
            typ, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
            i += 10
            if typ == 1 and rdlen == 4:
                return socket.inet_ntoa(data[i:i + 4])
            i += rdlen
    except (IndexError, struct.error):
        pass
    return None


def resolve(host):
    """Hostname → IP, with a one-shot mDNS query for name.local (the Nebula has no mDNS resolver)."""
    try:
        return socket.gethostbyname(host)
    except OSError:
        if not host.endswith(".local"):
            raise
    q = b"\0\0\0\0\0\1\0\0\0\0\0\0" + b"".join(bytes([len(p)]) + p.encode() for p in host.split(".")) + b"\0\0\1\0\1"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(q, ("224.0.0.251", 5353))
    end = time.time() + 2
    while time.time() < end:
        try:
            ip = mdns_a_record(s.recvfrom(2048)[0])
        except socket.timeout:
            break
        if ip:
            return ip
    raise OSError("can't find " + host)


def url_with_ip(url):
    """The same URL with a .local host resolved (so it also works on the Nebula)."""
    u = urlsplit(url)
    if u.hostname and u.hostname.endswith(".local"):
        return urlunsplit(u._replace(netloc=resolve(u.hostname) + (":%d" % u.port if u.port else "")))
    return url


def helper_call(path, payload=None):
    s = load_settings()
    host = s["helperHost"] or s["obsHost"]
    if not host:
        raise ValueError("Set up the Pad Deck Helper first (Menu → Helper)")
    try:
        req = urllib.request.Request(url_with_ip("http://%s:%d%s" % (host, int(s["helperPort"]), path)),
                                     data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "X-Pad-Deck-Code": s["helperCode"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ValueError("The Helper's pairing code doesn't match (Menu → Helper)")
        raise ValueError(json.loads(e.read() or b"{}").get("error") or "Helper error %d" % e.code)
    except (urllib.error.URLError, OSError):
        raise ValueError("Can't reach the Pad Deck Helper on %s. Is it running?" % host)


# Smart plugs that take plain local web requests (no cloud account): the path for each action.
PLUG_URLS = {
    "shelly": {"toggle": "/relay/0?turn=toggle", "on": "/relay/0?turn=on", "off": "/relay/0?turn=off"},
    "shelly2": {"toggle": "/rpc/Switch.Toggle?id=0", "on": "/rpc/Switch.Set?id=0&on=true", "off": "/rpc/Switch.Set?id=0&on=false"},
    "tasmota": {"toggle": "/cm?cmnd=Power%20Toggle", "on": "/cm?cmnd=Power%20On", "off": "/cm?cmnd=Power%20Off"},
}


def http_send(url, method=None, body=None):
    headers = {"Content-Type": "application/json"} if body and body.strip()[:1] in "{[" else {}
    try:
        req = urllib.request.Request(url_with_ip(url), data=body.encode() if body else None, headers=headers,
                                     method=(method or ("POST" if body else "GET")).upper())
        with urllib.request.urlopen(req, timeout=8) as r:
            return "Sent (%d)" % r.status
    except urllib.error.HTTPError as e:
        raise ValueError("The device answered %d %s" % (e.code, e.reason))
    except (urllib.error.URLError, OSError) as e:
        raise ValueError("Couldn't reach it: %s" % (getattr(e, "reason", None) or e))


def run_action(a):
    """Run one key's non-OBS action. Returns a short message for the screen."""
    t = a.get("type")
    if t == "http":                                              # Home Assistant, webhooks, other devices…
        url = str(a.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("The web address should start with http:// or https://")
        return http_send(url, a.get("method"), a.get("body") or None)
    if t == "plug":                                              # a smart plug on the local network
        ip, do = str(a.get("ip", "")).strip(), a.get("do", "toggle")
        if not re.fullmatch(r"[A-Za-z0-9.\-:]+", ip):
            raise ValueError("Enter the plug's IP address (from its app or your router)")
        if do not in ("toggle", "on", "off"):
            raise ValueError("Unknown plug action %r" % do)
        url = PLUG_URLS.get(a.get("brand"))
        if not url:
            raise ValueError("Unknown plug type %r" % a.get("brand"))
        http_send("http://" + ip + url[do])
        return "Plug: %s" % do
    if t in HELPER_TYPES:
        return helper_call("/run", a).get("message", "Done")
    raise ValueError("Unknown action %r" % t)


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
    try:
        out = run("ip", "-4", "-o", "addr", "show", "wlan0").stdout
    except OSError:
        return None
    m = re.search(r"inet (\S+)/", out)
    return m.group(1) if m else None


# Nebula Pad: wpa_supplicant via wpa_cli (its config has update_config=1, so save_config persists).

def wpa(*args):
    return run("wpa_cli", "-i", "wlan0", *args, timeout=15).stdout


def wpa_state(rescan):
    if rescan:
        wpa("scan")
        time.sleep(3)
    status = dict(l.split("=", 1) for l in wpa("status").splitlines() if "=" in l)
    nets = {}
    for line in wpa("scan_results").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 5 or not parts[4]:
            continue
        ssid, signal = parts[4], max(0, min(100, 2 * (int(parts[2]) + 100)))   # dBm → rough percent
        n = {"ssid": ssid, "signal": signal, "secure": "WPA" in parts[3] or "WEP" in parts[3],
             "active": ssid == status.get("ssid") and status.get("wpa_state") == "COMPLETED"}
        if ssid not in nets or n["signal"] > nets[ssid]["signal"]:
            nets[ssid] = n
    networks = sorted(nets.values(), key=lambda n: (not n["active"], -n["signal"]))
    active = next((n for n in networks if n["active"]), None)
    current = {"ssid": active["ssid"], "signal": active["signal"], "ip": status.get("ip_address")} if active else None
    return {"current": current, "networks": networks}


def wpa_connect(ssid, password):
    ids = {l.split("\t")[1]: l.split("\t")[0] for l in wpa("list_networks").splitlines()[1:] if "\t" in l}
    nid = ids.get(ssid) or wpa("add_network").strip().splitlines()[-1]
    wpa("set_network", nid, "ssid", ssid.encode().hex())        # hex: any characters are safe
    if password:
        # The WPA key derived from the passphrase (what wpa_passphrase does), so quotes etc. need no escaping.
        psk = hashlib.pbkdf2_hmac("sha1", password.encode(), ssid.encode(), 4096, 32).hex()
        wpa("set_network", nid, "psk", psk)
        wpa("set_network", nid, "key_mgmt", "WPA-PSK")
    else:
        wpa("set_network", nid, "key_mgmt", "NONE")
    wpa("set_network", nid, "scan_ssid", "1")
    wpa("select_network", nid)
    for _ in range(25):
        time.sleep(1)
        st = dict(l.split("=", 1) for l in wpa("status").splitlines() if "=" in l)
        if st.get("wpa_state") == "COMPLETED" and st.get("ssid") == ssid:
            wpa("enable_network", "all")                         # select_network disabled the others; keep them as fallbacks
            wpa("save_config")
            run("udhcpc", "-i", "wlan0", "-n", "-q", timeout=20)
            return True, "Connected"
    wpa("enable_network", "all")
    wpa("reconnect")
    return False, "Couldn't connect. Check the password."


def wifi_state(rescan):
    if not NMCLI:
        return wpa_state(rescan)
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
    if not NMCLI:
        return wpa_connect(ssid, password)
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
    try:
        up = int(float(open("/proc/uptime").read().split()[0]))
    except OSError:                                              # not Linux (e.g. the dev preview on a Mac)
        up = None
    try:
        temp = round(int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000)
    except OSError:
        temp = None
    try:
        if NMCLI:
            ssid = next((nm_fields(l)[1] for l in run("nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi").stdout.splitlines()
                         if l.startswith("yes:")), None)
        else:
            ssid = dict(l.split("=", 1) for l in wpa("status").splitlines() if "=" in l).get("ssid")
    except OSError:                                              # no Wi-Fi tools here (e.g. the dev preview on a Mac)
        ssid = None
    return {"hostname": socket.gethostname(), "ip": wifi_ip(), "ssid": ssid, "temp": temp,
            "uptime": None if up is None else f"{up // 86400}d {up % 86400 // 3600}h {up % 3600 // 60}m" if up >= 86400
            else f"{up // 3600}h {up % 3600 // 60}m"}


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

    def local_only(self, admin=True):
        """Pad-only features. `admin` ones (Wi-Fi, system) are also open to the network on REMOTE_ADMIN devices."""
        if self.client_address[0] in LOCAL or (admin and REMOTE_ADMIN):
            return True
        self.send_json({"error": "Only available on the Pad itself"}, 403)
        return False

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/layout":
            with open(LAYOUT) as f:
                return self.send_json(json.load(f))
        if path == "/api/helper":
            if not self.local_only():
                return
            what = dict(re.findall(r"(\w+)=([^&]*)", query)).get("what", "ping")
            if what not in ("ping", "apps", "shortcuts"):
                return self.send_error(404)
            try:
                return self.send_json(helper_call("/" + what))
            except ValueError as e:
                return self.send_json({"error": str(e)}, 502)
        if path == "/api/device":
            return self.send_json({"device": DEVICE})
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
            if self.headers.get("Upgrade", "").lower() != "websocket" or not self.local_only(admin=False):
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
        if self.path == "/api/run":
            if not self.local_only():
                return
            try:
                return self.send_json({"ok": True, "message": run_action(self.json_body() or {})})
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
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
        subprocess.Popen(["sudo", "systemctl", action] if shutil.which("systemctl") else [action])   # Nebula: plain reboot/poweroff


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    ThreadingHTTPServer.daemon_threads = True
    print(f"Pad Deck on http://0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
