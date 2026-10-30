#!/usr/bin/env python3
"""Pad Deck Lite: the Pad Deck stream deck for the Creality Nebula Pad (480×272 resistive touch, no browser).

Draws Pad Deck's keycaps straight to the framebuffer with PIL, reads the touch panel, and controls OBS over
obs-websocket v5, using only what the Nebula's Python 3.8 ships with (PIL, standard library).

It shares the main app's files: layout.json and settings.json in APP (edited from any browser through Pad
Deck's own web editor, which runs alongside on port 8099) and icons/ (drawn from pre-rendered PNGs in
ASSETS/icons for the library, or the uploaded file itself).

    python3 deck_lite.py         (normally started at boot by /etc/init.d/S99zpad_deck)
"""
import base64
import fcntl
import hashlib
import json
import mmap
import os
import queue
import select
import socket
import struct
import threading
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "..", "app")
ASSETS = os.path.join(ROOT, "assets")
W, H = 480, 272
BAR = 26                      # status bar height
FB = "/dev/fb0"
TOUCH = "/dev/input/event0"
BEEP = "/usr/bin/beep"
EDITOR_PORT = 8099

GREEN, RED, AMBER = (77, 255, 134), (255, 59, 78), (255, 194, 61)
KEY = (43, 45, 50)


def text_w(f, text):
    """Width of text in font f (PIL 7 on the Nebula has getsize; newer PIL only getbbox)."""
    return f.getbbox(text)[2] if hasattr(f, "getbbox") else f.getsize(text)[0]


def font(weight, size):
    return ImageFont.truetype(os.path.join(ASSETS, "fonts", "BarlowSemiCondensed-%s.ttf" % weight), size)


# ---------- Framebuffer ----------

class Screen:
    """The panel is 480×272 at 32 bpp with two buffers stacked vertically; draw into the one being shown."""

    def __init__(self):
        self.fd = os.open(FB, os.O_RDWR)
        var = bytearray(160)
        fcntl.ioctl(self.fd, 0x4600, var)                       # FBIOGET_VSCREENINFO
        xres, yres, xv, yv, xoff, yoff = struct.unpack_from("6I", var)
        self.stride = xv * 4
        self.offset = yoff * self.stride
        self.mem = mmap.mmap(self.fd, self.stride * yv)

    def show(self, img, box=None):
        """Copy img (480×272 RGB) to the screen, or just `box` (x0, y0, x1, y1) of it."""
        x0, y0, x1, y1 = box or (0, 0, W, H)
        raw = img.crop((x0, y0, x1, y1)).convert("RGBA").tobytes("raw", "BGRA")
        row = (x1 - x0) * 4
        for i, y in enumerate(range(y0, y1)):
            at = self.offset + y * self.stride + x0 * 4
            self.mem[at:at + row] = raw[i * row:(i + 1) * row]


# ---------- Touch (resistive, calibrated by tslib's /etc/pointercal) ----------

class Touch(threading.Thread):
    """Turns raw panel events into ("down"|"move"|"up", x, y) on `out`."""

    def __init__(self, out):
        super().__init__(daemon=True)
        self.out = out
        try:
            self.cal = [int(v) for v in open("/etc/pointercal").read().split()[:7]]
        except (OSError, ValueError):
            self.cal = [65536, 0, 0, 0, 65536, 0, 65536]

    def map(self, x, y):
        a = self.cal
        return (a[2] + a[0] * x + a[1] * y) / a[6], (a[5] + a[3] * x + a[4] * y) / a[6]

    def run(self):
        fd = os.open(TOUCH, os.O_RDONLY)
        rx = ry = 0
        down = was = False
        while True:
            data = os.read(fd, 16 * 64)
            for i in range(0, len(data) - 15, 16):
                _, _, typ, code, val = struct.unpack("<iiHHi", data[i:i + 16])
                if typ == 3 and code == 0:
                    rx = val
                elif typ == 3 and code == 1:
                    ry = val
                elif typ == 1 and code == 330:
                    down = bool(val)
                elif typ == 0:                                  # SYN: one complete report
                    x, y = self.map(rx, ry)
                    if down and not was:
                        self.out.put(("down", x, y))
                    elif down:
                        self.out.put(("move", x, y))
                    elif was:
                        self.out.put(("up", x, y))
                    was = down


# ---------- OBS (obs-websocket v5 over a minimal stdlib WebSocket client) ----------

def local_ip():
    """This device's address on the network (no traffic is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        s.close()


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
    """Resolve host; for name.local fall back to a one-shot mDNS query (the Nebula has no nss-mdns)."""
    try:
        return socket.gethostbyname(host)
    except OSError:
        if not host.endswith(".local"):
            raise
    q = b"\0\0\0\0\0\1\0\0\0\0\0\0" + b"".join(bytes([len(p)]) + p.encode() for p in host.split(".")) + b"\0\0\1\0\1"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    # No default multicast route on the Nebula: send from the Wi-Fi address explicitly.
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip()))
    s.sendto(q, ("224.0.0.251", 5353))
    end = time.time() + 2
    while time.time() < end:
        try:
            data, _ = s.recvfrom(2048)
        except socket.timeout:
            break
        ip = mdns_a_record(data)
        if ip:
            return ip
    raise OSError("can't find " + host)


class OBS(threading.Thread):
    """Keeps a connection to OBS and reports state changes to `out` as ("obs", kind, value)."""

    def __init__(self, out):
        super().__init__(daemon=True)
        self.out = out
        self.target = None
        self.sock = None
        self.lock = threading.Lock()
        self.next_id = 1
        self.pending = {}                                       # requestId → what the response is for
        self.wake = threading.Event()

    def configure(self, host, port, password):
        target = (host or "", int(port or 4455), password or "")
        if target != self.target:
            self.target = target
            self.close()
            self.wake.set()

    def close(self):
        s, self.sock = self.sock, None
        if s:
            try:
                s.close()
            except OSError:
                pass

    # -- frames
    def send_json(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else struct.pack(">BH", 0x80 | 126, n) if n < 65536
                                 else struct.pack(">BQ", 0x80 | 127, n))
        with self.lock:
            if self.sock:
                self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise OSError("closed")
            buf += chunk
        return buf

    def recv_message(self):
        msg = b""
        while True:
            b0, b1 = self.recv_exact(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self.recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self.recv_exact(8))[0]
            payload = self.recv_exact(n)
            op = b0 & 0x0F
            if op == 8:
                raise OSError("closed by OBS")
            if op == 9:                                          # ping → pong
                with self.lock:
                    self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + b"\0\0\0\0" + payload)
                continue
            if op in (0, 1, 2):
                msg += payload
                if b0 & 0x80:
                    return json.loads(msg.decode())

    def request(self, kind, data=None):
        rid = str(self.next_id)
        self.next_id += 1
        self.send_json({"op": 6, "d": {"requestType": kind, "requestId": rid, "requestData": data or {}}})
        return rid

    # -- connection loop
    def run(self):
        while True:
            host, port, password = self.target or ("", 4455, "")
            if not host:
                self.out.put(("obs", "status", "unset"))
                self.wake.wait()
                self.wake.clear()
                continue
            self.out.put(("obs", "status", "connecting"))
            try:
                self.session(host, port, password)
            except (OSError, ValueError) as e:
                print("OBS: %s: %s" % (type(e).__name__, e), flush=True)
            self.close()
            self.out.put(("obs", "status", "offline"))
            self.wake.wait(3)
            self.wake.clear()

    def session(self, host, port, password):
        ip = resolve(host)
        print("OBS: connecting to %s (%s) port %d" % (host, ip, port), flush=True)
        s = socket.create_connection((ip, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall(("GET / HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: obswebsocket.json\r\n\r\n"
                   % (host, port, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = s.recv(1024)
            if not chunk:
                raise OSError("no handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise OSError("OBS refused the connection")
        s.settimeout(None)
        self.sock = s
        pending = self.pending = {}
        while self.sock is s:
            m = self.recv_message()
            op, d = m.get("op"), m.get("d", {})
            if op == 0:                                          # Hello → Identify (Scenes, Inputs, Outputs events)
                ident = {"rpcVersion": 1, "eventSubscriptions": 4 | 8 | 64}
                auth = d.get("authentication")
                if auth:
                    secret = base64.b64encode(hashlib.sha256((password + auth["salt"]).encode()).digest()).decode()
                    ident["authentication"] = base64.b64encode(
                        hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
                self.send_json({"op": 1, "d": ident})
            elif op == 2:                                        # Identified
                print("OBS: connected", flush=True)
                self.out.put(("obs", "status", "online"))
                pending[self.request("GetCurrentProgramScene")] = "scene"
                pending[self.request("GetStreamStatus")] = "stream"
                pending[self.request("GetRecordStatus")] = "rec"
                self.out.put(("obs", "need-mutes", None))
            elif op == 7:                                        # request response
                kind = pending.pop(d.get("requestId"), None)
                r = d.get("responseData") or {}
                if kind == "scene":
                    self.out.put(("obs", "scene", r.get("currentProgramSceneName") or r.get("sceneName")))
                elif kind in ("stream", "rec"):
                    self.out.put(("obs", kind, r.get("outputActive", False)))
                elif kind and kind.startswith("mute:"):
                    self.out.put(("obs", "mute", (kind[5:], r.get("inputMuted"))))
                elif not d.get("requestStatus", {}).get("result", True):
                    print("OBS request failed:", d.get("requestStatus"), flush=True)
            elif op == 5:                                        # events
                t, e = d.get("eventType"), d.get("eventData") or {}
                if t == "CurrentProgramSceneChanged":
                    self.out.put(("obs", "scene", e.get("sceneName")))
                elif t == "InputMuteStateChanged":
                    self.out.put(("obs", "mute", (e.get("inputName"), e.get("inputMuted"))))
                elif t == "StreamStateChanged":
                    self.out.put(("obs", "stream", e.get("outputActive")))
                elif t == "RecordStateChanged":
                    self.out.put(("obs", "rec", e.get("outputActive")))

    def ask_mute(self, name):
        try:
            self.pending[self.request("GetInputMute", {"inputName": name})] = "mute:" + name
        except (OSError, AttributeError):
            pass


# ---------- Drawing ----------

def rounded(size, r, fill, scale=3):
    """An anti-aliased rounded rectangle (PIL 7 has no rounded_rectangle): RGBA image of `size`."""
    w, h = size
    m = Image.new("L", (w * scale, h * scale), 0)
    d = ImageDraw.Draw(m)
    R = r * scale
    d.rectangle([R, 0, w * scale - R, h * scale], fill=255)
    d.rectangle([0, R, w * scale, h * scale - R], fill=255)
    for x, y in [(0, 0), (w * scale - 2 * R, 0), (0, h * scale - 2 * R), (w * scale - 2 * R, h * scale - 2 * R)]:
        d.ellipse([x, y, x + 2 * R, y + 2 * R], fill=255)
    out = Image.new("RGBA", (w, h), fill)
    out.putalpha(m.resize((w, h), Image.LANCZOS))
    return out


def hexrgb(h, default=KEY):
    try:
        h = (h or "").lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def mix(c, other, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c, other))


class Painter:
    def __init__(self):
        bg = Image.open(os.path.join(ASSETS, "carbon.jpg")).convert("RGB")
        scale = max(W / bg.width, H / bg.height)
        bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1), Image.LANCZOS)
        left, top = (bg.width - W) // 2, (bg.height - H) // 2
        self.bg = bg.crop((left, top, left + W, top + H))
        self.label = font("SemiBold", 15)
        self.small = font("SemiBold", 11)
        self.big = font("SemiBold", 19)
        self.cache = {}
        self.icons = {}

    def icon(self, name, size):
        key = (name, size)
        if key not in self.icons:
            img = None
            base = os.path.splitext(name or "")[0]
            for path in (os.path.join(ASSETS, "icons", base + ".png"), os.path.join(APP, "icons", name or "")):
                if name and os.path.isfile(path) and not path.endswith(".svg"):
                    try:
                        img = Image.open(path).convert("RGBA")
                        break
                    except OSError:
                        pass
            if img:
                img.thumbnail((size, size), Image.LANCZOS)
            self.icons[key] = img
        return self.icons[key]

    def key(self, b, w, h, state):
        """One keycap (RGBA, w×(h+6) incl. edge) for button b in state {live, muted, hot, pressed}."""
        ck = (json.dumps(b, sort_keys=True), w, h, tuple(sorted(state.items())))
        if ck in self.cache:
            return self.cache[ck]
        col = hexrgb(b.get("bg"))
        light = sum(c * k for c, k in zip(col, (.299, .587, .114))) > 150
        pressed = state.get("pressed")
        out = Image.new("RGBA", (w, h + 8), (0, 0, 0, 0))
        top = 4 if pressed else 0
        edge = 1 if pressed else 5
        # shadow, edge, face, dished plate, top light
        sh = Image.new("L", (w, h + 8), 0)
        ImageDraw.Draw(sh).rectangle([3, top + 6, w - 3, top + h + edge], fill=170)
        out.paste((0, 0, 0, 255), mask=sh.filter(ImageFilter.GaussianBlur(3)))
        r = max(6, min(w, h) // 9)
        out.alpha_composite(rounded((w, h), r, mix(col, (0, 0, 0), .55) + (255,)), (0, top + edge))
        out.alpha_composite(rounded((w, h), r, col + (255,)), (0, top))
        out.alpha_composite(rounded((w - 10, h - 10), max(4, r - 3), mix(col, (255, 255, 255), .04) + (255,)), (5, top + 5))
        d = ImageDraw.Draw(out)
        d.line([(r, top + 1), (w - r, top + 1)], fill=mix(col, (255, 255, 255), .28) + (255,))
        if state.get("live"):
            out.alpha_composite(self.ring(w, h, r), (0, top))
        # status LED
        led = GREEN if state.get("live") else RED if state.get("hot") else (16, 17, 18)
        d.ellipse([8, top + 8, 15, top + 15], fill=led + (255,))
        # icon and label
        text_col = (27, 28, 31) if light else (238, 240, 244)
        if state.get("muted"):
            text_col = mix(text_col, col, .38)
        label = b.get("label") or ""
        if state.get("muted") is not None and b.get("mute"):
            label = "%s %s" % (label, "Muted" if state["muted"] else "On")
        lines = self.wrap(label, w - 12)
        ih = int(h * .4) if b.get("icon") else 0
        block = ih + (6 if ih and lines else 0) + len(lines) * 16
        y = top + (h - block) // 2
        ic = self.icon(b.get("icon"), ih) if ih else None
        if ic:
            if light:
                ic = Image.merge("RGBA", (*[ic.getchannel("A").point(lambda v: 0)] * 3, ic.getchannel("A")))
            elif state.get("muted"):
                ic = Image.merge("RGBA", (*[c.point(lambda v: int(v * .7)) for c in ic.split()[:3]], ic.getchannel("A")))
            out.alpha_composite(ic, ((w - ic.width) // 2, y + (ih - ic.height) // 2))
            if state.get("muted"):                              # slash through the icon, cut out in the key colour
                cx, cy, s = w // 2, y + ih // 2, ih * .42
                d.line([(cx - s, cy - s), (cx + s, cy + s)], fill=col + (255,), width=7)
                d.line([(cx - s, cy - s), (cx + s, cy + s)], fill=text_col + (255,), width=3)
            y += ih + 6
        for line in lines:
            tw = text_w(self.label, line)
            if not light:
                d.text(((w - tw) // 2, y + 1), line, font=self.label, fill=(0, 0, 0, 150))
            d.text(((w - tw) // 2, y), line, font=self.label, fill=text_col + (255,))
            y += 16
        if len(self.cache) > 200:
            self.cache.clear()
        self.cache[ck] = out
        return out

    def ring(self, w, h, r):
        ring = rounded((w, h), r, GREEN + (150,))
        inner = rounded((w - 4, h - 4), max(2, r - 2), (0, 0, 0, 255))
        hole = Image.new("L", (w, h), 0)
        hole.paste(inner.getchannel("A"), (2, 2))
        a = ring.getchannel("A").point(lambda v: v)
        a.paste(0, mask=hole)
        ring.putalpha(a)
        return ring

    def wrap(self, text, width):
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if text_w(self.label, t) <= width or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) > 2:
            lines = lines[:2]
            while lines[1] and text_w(self.label, lines[1] + "…") > width:
                lines[1] = lines[1][:-1]
            lines[1] += "…"
        return lines


# ---------- The deck ----------

class Deck:
    def __init__(self):
        self.screen = Screen()
        self.paint = Painter()
        self.events = queue.Queue()
        self.layout, self.settings = {}, {}
        self.mtimes = {}
        self.page = 0
        self.state = {"status": "connecting", "scene": None, "muted": {}, "stream": False, "rec": False}
        self.obs = OBS(self.events)
        self.touch = None
        self.pressed = None
        self.info = False
        self.frame = None
        self.drawn = {}             # slot → state signature of the key currently on screen
        self.bar_drawn = None
        self.warm = []              # (button, w, h, state) keycaps to pre-draw while idle

    # -- files shared with the web editor
    def load(self):
        changed = False
        for name in ("layout.json", "settings.json"):
            path = os.path.join(APP, name)
            try:
                m = os.path.getmtime(path)
            except OSError:
                continue
            if self.mtimes.get(name) != m:
                self.mtimes[name] = m
                try:
                    data = json.load(open(path))
                except (OSError, ValueError):
                    continue
                if name == "layout.json":
                    self.layout = data
                else:
                    self.settings = data
                changed = True
        if changed:
            pages = self.layout.get("pages") or [{"buttons": self.layout.get("buttons", [])}]
            self.layout["pages"] = pages
            self.page = min(self.page, len(pages) - 1)
            s = self.settings
            self.obs.configure(s.get("obsHost"), s.get("obsPort"), s.get("obsPassword"))
            self.ask_mutes()
        return changed

    def buttons(self):
        return self.layout["pages"][self.page]["buttons"] if self.layout.get("pages") else []

    def ask_mutes(self):
        for name in {b["mute"] for p in self.layout.get("pages", []) for b in p["buttons"] if b and b.get("mute")}:
            self.obs.ask_mute(name)

    # -- geometry
    def grid(self):
        cols, rows = int(self.layout.get("columns", 4)), int(self.layout.get("rows", 2))
        pad, gap = 8, 8
        kw = (W - 2 * pad - (cols - 1) * gap) // cols
        kh = (H - BAR - 2 * pad - 6 - (rows - 1) * gap) // rows
        return cols, rows, pad, gap, kw, kh

    def slot_box(self, i):
        cols, rows, pad, gap, kw, kh = self.grid()
        x = pad + (i % cols) * (kw + gap)
        y = BAR + pad + (i // cols) * (kh + gap)
        return x, y, kw, kh

    def slot_at(self, x, y):
        for i in range(len(self.buttons())):
            sx, sy, kw, kh = self.slot_box(i)
            if sx <= x < sx + kw and sy <= y < sy + kh + 6:
                return i
        return None

    def key_state(self, b, i):
        st = {"pressed": self.pressed == i}
        if b.get("scene"):
            st["live"] = b["scene"] == self.state["scene"]
        if b.get("mute"):
            m = self.state["muted"].get(b["mute"])
            st["muted"] = m
            st["hot"] = m is False
        return st

    # -- drawing
    def draw_bar(self, img):
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, BAR - 1], fill=(10, 11, 13))
        st = self.state["status"]
        dot = {"online": GREEN, "connecting": AMBER, "offline": RED, "unset": AMBER}.get(st, AMBER)
        text = {"online": "OBS", "connecting": "Connecting", "offline": "OBS offline", "unset": "Set up OBS"}.get(st, "OBS")
        x = 10
        for label, on, col in [(text, True, dot), ("LIVE", self.state["stream"], RED), ("REC", self.state["rec"], RED)]:
            d.ellipse([x, 10, x + 6, 16], fill=col if on else (55, 57, 62))
            d.text((x + 11, 5), label, font=self.paint.small, fill=(235, 236, 240) if on else (120, 124, 132))
            x += 22 + text_w(self.paint.small, label)
        n = len(self.layout.get("pages", [])) or 1
        for p in range(n):                                       # page dots, right-aligned
            cx = W - 14 - (n - 1 - p) * 12
            d.ellipse([cx - 3, 10, cx + 3, 16], fill=(250, 250, 252) if p == self.page else (75, 78, 84))

    def render(self):
        img = self.paint.bg.copy()
        self.draw_bar(img)
        if self.info:
            self.draw_info(img)
        else:
            bs = self.buttons()
            for i, b in enumerate(bs):
                if not b:
                    continue
                x, y, kw, kh = self.slot_box(i)
                k = self.paint.key(b, kw, kh, self.key_state(b, i))
                img.paste(k, (x, y), k)
            if not any(bs):
                d = ImageDraw.Draw(img)
                msg = "Empty page · edit at %s" % self.editor_url()
                tw = text_w(self.paint.label, msg)
                d.text(((W - tw) // 2, H // 2), msg, font=self.paint.label, fill=(150, 154, 162))
        self.frame = img
        self.screen.show(img)
        self.drawn = {i: self.sig(i) for i in range(len(self.buttons()))}
        self.bar_drawn = self.bar_sig()
        self.queue_warm()

    # Partial updates: only redraw what changed (a full frame takes a while on the Nebula; one key is quick).
    def sig(self, i):
        b = self.buttons()[i] if i < len(self.buttons()) else None
        return json.dumps([b, self.key_state(b, i)], sort_keys=True) if b else None

    def bar_sig(self):
        return (self.state["status"], self.state["stream"], self.state["rec"], self.page, len(self.layout.get("pages", [])))

    def refresh(self):
        if self.info or self.frame is None:
            return self.render()
        if self.bar_sig() != self.bar_drawn:
            self.draw_bar(self.frame)
            self.screen.show(self.frame, (0, 0, W, BAR))
            self.bar_drawn = self.bar_sig()
        for i in range(len(self.buttons())):
            if self.sig(i) != self.drawn.get(i):
                self.redraw_key(i)

    def queue_warm(self):
        """Pre-draw every key on this page in its other looks, so the first press doesn't have to."""
        self.warm = []
        for i, b in enumerate(self.buttons()):
            if not b:
                continue
            _, _, kw, kh = self.slot_box(i)
            base = self.key_state(b, i)
            looks = [{"pressed": True}]
            if b.get("scene"):
                looks.append({"live": not base.get("live")})
            if b.get("mute"):
                looks.append({"muted": not base.get("muted"), "hot": bool(base.get("muted"))})
            for extra in looks:
                self.warm.append((b, kw, kh, dict(base, **extra)))

    def warm_one(self):
        if self.warm:
            b, kw, kh, st = self.warm.pop()
            self.paint.key(b, kw, kh, st)

    def redraw_key(self, i):
        if self.info or self.frame is None or i is None or i >= len(self.buttons()):
            return
        self.drawn[i] = self.sig(i)
        x, y, kw, kh = self.slot_box(i)
        box = (x, y, x + kw, min(H, y + kh + 8))
        patch = self.paint.bg.crop(box)
        b = self.buttons()[i]
        if b:
            k = self.paint.key(b, kw, kh, self.key_state(b, i))
            patch.paste(k, (0, 0), k)
        self.frame.paste(patch, box[:2])
        self.screen.show(self.frame, box)

    def editor_url(self):
        return "http://%s:%d" % (local_ip(), EDITOR_PORT)

    def draw_info(self, img):
        d = ImageDraw.Draw(img)
        card = rounded((W - 40, H - BAR - 30), 14, (20, 21, 24, 240))
        img.paste(card, (20, BAR + 15), card)
        host = self.settings.get("obsHost") or "not set"
        lines = [("Pad Deck Lite", self.paint.big, (255, 255, 255)),
                 ("Edit keys, pages and OBS from any browser:", self.paint.label, (150, 154, 162)),
                 (self.editor_url(), self.paint.big, GREEN),
                 ("OBS computer: %s (%s)" % (host, self.state["status"]), self.paint.label, (200, 204, 212)),
                 ("Swipe up/down for pages · tap to close", self.paint.label, (120, 124, 132))]
        y = BAR + 30
        for text, f, col in lines:
            d.text((40, y), text, font=f, fill=col)
            y += 34 if f is self.paint.big else 26

    # -- input
    def beep(self):
        if self.settings.get("sound", True) and os.access(BEEP, os.X_OK):
            try:
                os.posix_spawn(BEEP, [BEEP], os.environ)        # much cheaper than fork() for a big Python process
                os.waitpid(-1, os.WNOHANG)                       # reap earlier beeps
            except OSError:
                pass

    def press(self, b):
        if b.get("scene"):
            self.state["scene"] = b["scene"]                     # optimistic; the OBS event confirms
            self.obs.request("SetCurrentProgramScene", {"sceneName": b["scene"]})
        if b.get("mute"):
            self.obs.request("ToggleInputMute", {"inputName": b["mute"]})

    def run(self):
        os.system("killall boot_display jpeg_display_shell 2>/dev/null")
        self.load()
        self.obs.start()
        self.touch = Touch(self.events)
        self.touch.start()
        self.render()
        start = None
        last_poll = 0
        while True:
            try:
                ev = self.events.get(timeout=0.02 if self.warm else 0.5)
            except queue.Empty:
                ev = None
                self.warm_one()
            if time.time() - last_poll > 1:
                last_poll = time.time()
                if self.load():
                    self.render()
            if not ev:
                continue
            kind = ev[0]
            if kind == "obs":
                _, what, value = ev
                if what == "status":
                    self.state["status"] = value
                    if value != "online":
                        self.state["stream"] = self.state["rec"] = False
                elif what == "scene":
                    self.state["scene"] = value
                elif what == "mute":
                    self.state["muted"][value[0]] = value[1]
                elif what in ("stream", "rec"):
                    self.state[what] = bool(value)
                elif what == "need-mutes":
                    self.ask_mutes()
                    continue
                if self.events.empty():
                    self.refresh()
            elif kind == "down":
                _, x, y = ev
                start = (x, y, time.time())
                if y < BAR or self.info:
                    continue
                i = self.slot_at(x, y)
                if i is not None and self.buttons()[i]:
                    self.pressed = i
                    self.redraw_key(i)
                    self.beep()
            elif kind == "up" and start:
                _, x, y = ev
                sx, sy, _ = start
                start = None
                was, self.pressed = self.pressed, None
                dy, dx = y - sy, x - sx
                if self.info:
                    self.info = False
                    self.render()
                elif sy < BAR and abs(dx) < 30 and abs(dy) < 30:  # tap the status bar: info card
                    self.info = True
                    self.render()
                elif abs(dy) > 45 and abs(dy) > abs(dx):          # swipe: up = next page, down = previous
                    n = len(self.layout.get("pages", [])) or 1
                    nxt = self.page + (1 if dy < 0 else -1)
                    self.redraw_key(was)
                    if 0 <= nxt < n:
                        self.page = nxt
                        self.render()
                elif was is not None:
                    self.redraw_key(was)
                    if self.slot_at(x, y) == was:
                        try:
                            self.press(self.buttons()[was])
                        except OSError:
                            pass
                        self.refresh()


if __name__ == "__main__":
    Deck().run()
