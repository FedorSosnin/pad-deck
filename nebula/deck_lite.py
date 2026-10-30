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
import subprocess
import threading
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "..", "app")
ASSETS = os.path.join(ROOT, "assets")
W, H = 480, 272
BAR = 34                      # status bar height
RAIL_W = 38                   # ▲/▼ page buttons down the right edge
# The Nebula's bezel covers the outer few pixels of the panel: keep keys, rail and menu inside this.
SAFE_L, SAFE_R, SAFE_B = 14, 12, 14
M = 8                         # room around each keycap for its glow (neighbours' margins overlap)
MENU_HIT = (W - 84, 0, W, BAR + 14)   # generous touch area for the ☰ button (resistive panels aren't precise)
BACKLIGHT = "/sys/class/backlight/backlight_pwm0/brightness"   # 0–300
FB = "/dev/fb0"
TOUCH = "/dev/input/event0"
BEEP = "/usr/bin/beep"
EDITOR_PORT = 8099

GREEN, RED, AMBER = (77, 255, 134), (255, 59, 78), (255, 194, 61)
KEY = (43, 45, 50)


def text_w(f, text):
    """Width of text in font f (PIL 7 on the Nebula has getsize; newer PIL only getbbox)."""
    return f.getbbox(text)[2] if hasattr(f, "getbbox") else f.getsize(text)[0]


def text_top(f, text, middle):
    """The y to draw text at so its visible glyphs are centred on `middle`."""
    if hasattr(f, "getbbox"):
        _, t, _, b = f.getbbox(text)
    else:
        t = f.getoffset(text)[1]
        b = f.getsize(text)[1]
    return int(middle - (t + b) / 2)


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
    alpha = m.resize((w, h), Image.LANCZOS)
    if len(fill) == 4 and fill[3] < 255:                        # keep the fill's own transparency
        alpha = alpha.point(lambda v: v * fill[3] // 255)
    out.putalpha(alpha)
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
        try:                                                     # the Pad 7 keys' grain, tiled
            tile = Image.open(os.path.join(APP, "noise.png")).convert("RGBA")
            # At this resolution the Pad 7's grain lands one speck per pixel and reads as TV static, so soften it
            # into a fine matte texture: blur it a touch and keep it faint.
            tile = tile.filter(ImageFilter.GaussianBlur(0.6))
            tile.putalpha(tile.getchannel("A").point(lambda v: min(255, int(v * 1.6))))
            self.grain = Image.new("RGBA", (W, H))
            for gx in range(0, W, tile.width):
                for gy in range(0, H, tile.height):
                    self.grain.paste(tile, (gx, gy))
        except OSError:
            self.grain = None

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
        """One keycap for button b in state {live, muted, hot, pressed}: RGBA, (w + 2M) × (h + 8 + 2M), drawn
        M px in from the top-left so glows have room (paste it at slot x − M, y − M)."""
        ck = (json.dumps(b, sort_keys=True), w, h, tuple(sorted(state.items())))
        if ck in self.cache:
            return self.cache[ck]
        col = hexrgb(b.get("bg"))
        light = sum(c * k for c, k in zip(col, (.299, .587, .114))) > 150
        pressed = state.get("pressed")
        out = Image.new("RGBA", (w + 2 * M, h + 8 + 2 * M), (0, 0, 0, 0))
        top = M + (4 if pressed else 0)
        edge = 1 if pressed else 5
        r = max(6, min(w, h) // 9)
        # soft shadow under the key
        sh = Image.new("L", out.size, 0)
        ImageDraw.Draw(sh).rectangle([M + 3, top + 6, M + w - 3, top + h + edge], fill=170)
        out.paste((0, 0, 0, 255), mask=sh.filter(ImageFilter.GaussianBlur(3)))
        # live: green glow around the key
        if state.get("live"):
            glow = Image.new("L", out.size, 0)
            glow.paste(rounded((w, h), r, (255, 255, 255, 255)).getchannel("A"), (M, top))
            glow = glow.filter(ImageFilter.GaussianBlur(3)).point(lambda v: int(v * .55))
            out.paste(GREEN + (255,), mask=glow)
        # edge, face (with the same grain as the Pad 7 keys), dished plate, top light
        out.alpha_composite(rounded((w, h), r, mix(col, (0, 0, 0), .55) + (255,)), (M, top + edge))
        face = rounded((w, h), r, col + (255,))
        # gentle top-to-bottom shading: light catches the top of the cap, the bottom falls into shadow
        shade = Image.new("RGBA", (w, h))
        sd = ImageDraw.Draw(shade)
        for yy in range(h):
            t = yy / max(1, h - 1)
            sd.line([(0, yy), (w, yy)], fill=(255, 255, 255, int(22 * (1 - t) ** 2)) if t < .5 else (0, 0, 0, int(40 * ((t - .5) * 2) ** 1.5)))
        face.alpha_composite(Image.composite(shade, Image.new("RGBA", (w, h), (0, 0, 0, 0)), face.getchannel("A")))
        if self.grain:
            g = self.grain.crop((0, 0, w, h))
            face.alpha_composite(Image.composite(g, Image.new("RGBA", (w, h), (0, 0, 0, 0)), face.getchannel("A")))
        out.alpha_composite(face, (M, top))
        out.alpha_composite(rounded((w - 10, h - 10), max(4, r - 3), mix(col, (255, 255, 255), .04) + (40,)), (M + 5, top + 5))
        d = ImageDraw.Draw(out)
        d.line([(M + r, top + 1), (M + w - r, top + 1)], fill=mix(col, (255, 255, 255), .28) + (255,))
        if state.get("live"):
            out.alpha_composite(self.ring(w, h, r), (M, top))
        # status LED, glowing when lit
        led = GREEN if state.get("live") else RED if state.get("hot") else None
        lx, ly = M + 11, top + 11
        if led:
            halo = Image.new("L", out.size, 0)
            ImageDraw.Draw(halo).ellipse([lx - 6, ly - 6, lx + 6, ly + 6], fill=200)
            out.paste(led + (255,), mask=halo.filter(ImageFilter.GaussianBlur(3)))
        d.ellipse([lx - 4, ly - 4, lx + 4, ly + 4], fill=(led or (16, 17, 18)) + (255,))
        if led:
            d.ellipse([lx - 2, ly - 3, lx + 1, ly - 1], fill=mix(led, (255, 255, 255), .6) + (255,))
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
            out.alpha_composite(ic, (M + (w - ic.width) // 2, y + (ih - ic.height) // 2))
            if state.get("muted"):                              # slash through the icon, cut out in the key colour
                cx, cy, s = M + w // 2, y + ih // 2, ih * .42
                d.line([(cx - s, cy - s), (cx + s, cy + s)], fill=col + (255,), width=7)
                d.line([(cx - s, cy - s), (cx + s, cy + s)], fill=text_col + (255,), width=3)
            y += ih + 6
        for line in lines:
            tw = text_w(self.label, line)
            if not light:
                d.text((M + (w - tw) // 2, y + 1), line, font=self.label, fill=(0, 0, 0, 150))
            d.text((M + (w - tw) // 2, y), line, font=self.label, fill=text_col + (255,))
            y += 16
        if len(self.cache) > 200:
            self.cache.clear()
        self.cache[ck] = out
        return out

    def ring(self, w, h, r):
        ring = rounded((w, h), r, GREEN + (210,))
        inner = rounded((w - 4, h - 4), max(2, r - 2), (0, 0, 0, 255))
        hole = Image.new("L", (w, h), 0)
        hole.paste(inner.getchannel("A"), (2, 2))
        a = ring.getchannel("A")
        a.paste(0, mask=hole)
        ring.putalpha(a)
        return ring

    def chevron(self, w, h, up):
        """A ▲/▼ rail keycap (cached)."""
        ck = ("chevron", w, h, up)
        if ck not in self.cache:
            self.cache[ck] = self.draw_chevron(w, h, up)
        return self.cache[ck]

    def draw_chevron(self, w, h, up):
        out = Image.new("RGBA", (w + 2 * M, h + 8 + 2 * M), (0, 0, 0, 0))
        sh = Image.new("L", out.size, 0)
        ImageDraw.Draw(sh).rectangle([M + 3, M + 6, M + w - 3, M + h + 5], fill=170)
        out.paste((0, 0, 0, 255), mask=sh.filter(ImageFilter.GaussianBlur(3)))
        out.alpha_composite(rounded((w, h), 9, mix(KEY, (0, 0, 0), .55) + (255,)), (M, M + 5))
        face = rounded((w, h), 9, KEY + (255,))
        if self.grain:
            g = self.grain.crop((0, 0, w, h))
            face.alpha_composite(Image.composite(g, Image.new("RGBA", (w, h), (0, 0, 0, 0)), face.getchannel("A")))
        out.alpha_composite(face, (M, M))
        d = ImageDraw.Draw(out)
        cx, cy, s = M + w // 2, M + h // 2, 8
        pts = [(cx - s, cy + s // 2), (cx, cy - s // 2), (cx + s, cy + s // 2)] if up else \
              [(cx - s, cy - s // 2), (cx, cy + s // 2), (cx + s, cy - s // 2)]
        d.line(pts, fill=(214, 217, 223, 255), width=3)
        return out

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
        self.toast_msg, self.toast_until = None, 0
        self.menu_hits = []         # (x0, y0, x1, y1, action) for the buttons on the menu card
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
        kw = (W - SAFE_L - SAFE_R - RAIL_W - gap - (cols - 1) * gap) // cols
        kh = (H - BAR - pad - SAFE_B - 6 - (rows - 1) * gap) // rows
        return cols, rows, pad, gap, kw, kh

    def slot_box(self, i):
        cols, rows, pad, gap, kw, kh = self.grid()
        x = SAFE_L + (i % cols) * (kw + gap)
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
        f, cy = self.paint.small, BAR // 2
        x = 12
        for label, on, col in [(text, True, dot), ("LIVE", self.state["stream"], RED), ("REC", self.state["rec"], RED)]:
            d.ellipse([x, cy - 3, x + 6, cy + 3], fill=col if on else (55, 57, 62))
            d.text((x + 11, text_top(f, label, cy)), label, font=f, fill=(235, 236, 240) if on else (120, 124, 132))
            x += 22 + text_w(f, label)
        n = len(self.layout.get("pages", [])) or 1
        if self.toast_msg:                                       # a short message in the middle of the bar
            tw = text_w(f, self.toast_msg)
            d.text(((W - tw) // 2, text_top(f, self.toast_msg, cy)), self.toast_msg, font=f, fill=(255, 210, 120))
        elif n > 1:                                              # page dots, centred
            for p in range(n):
                cx = W // 2 - (n - 1) * 7 + p * 14
                d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(250, 250, 252) if p == self.page else (75, 78, 84))
        # ☰ menu button (✕ while the menu is open): a small keycap at the right end of the bar
        bx0, by0, bx1, by1 = W - 60, 4, W - 8, BAR - 4
        cap = rounded((bx1 - bx0, by1 - by0), 7, (52, 55, 61, 255))
        img.paste(cap, (bx0, by0), cap)
        mx, my = (bx0 + bx1) // 2, (by0 + by1) // 2
        if self.info:
            d.line([(mx - 7, my - 7), (mx + 7, my + 7)], fill=(225, 228, 234), width=3)
            d.line([(mx - 7, my + 7), (mx + 7, my - 7)], fill=(225, 228, 234), width=3)
        else:
            for k in (-6, 0, 6):
                d.line([(mx - 10, my + k), (mx + 10, my + k)], fill=(225, 228, 234), width=2)

    # ▲/▼ page buttons, the height of the first and last rows (hidden when there's no page that way)
    def rail_boxes(self):
        cols, rows, pad, gap, kw, kh = self.grid()
        x0 = W - SAFE_R - RAIL_W
        first = BAR + pad
        last = BAR + pad + (rows - 1) * (kh + gap)
        n = len(self.layout.get("pages", [])) or 1
        boxes = {}
        if self.page > 0:
            boxes[-1] = (x0, first, RAIL_W, kh)
        if self.page < n - 1:
            boxes[1] = (x0, last, RAIL_W, kh)
        return boxes

    def draw_rail(self, img):
        for direction, (x, y, w, h) in self.rail_boxes().items():
            cap = self.paint.chevron(w, h, direction < 0)
            img.paste(cap, (x - M, y - M), cap)

    def rail_at(self, x, y):
        for direction, (bx, by, w, h) in self.rail_boxes().items():
            if bx - 6 <= x <= bx + w + 8 and by - 6 <= y <= by + h + 10:
                return direction
        return None

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
                img.paste(k, (x - M, y - M), k)
            self.draw_rail(img)
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
        return (self.state["status"], self.state["stream"], self.state["rec"], self.page,
                len(self.layout.get("pages", [])), self.toast_msg)

    def toast(self, msg, seconds=1.8):
        self.toast_msg, self.toast_until = msg, time.time() + seconds
        self.refresh()

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
        box = (max(0, x - M), max(BAR, y - M), min(W, x + kw + M), min(H, y + kh + 8 + M))
        patch = self.paint.bg.crop(box)
        # Glow margins overlap, so repaint everything that reaches into this patch, in the same order as render().
        for j, b in enumerate(self.buttons()):
            if not b:
                continue
            jx, jy, jw, jh = self.slot_box(j)
            if jx - M < box[2] and jx + jw + M > box[0] and jy - M < box[3] and jy + jh + 8 + M > box[1]:
                k = self.paint.key(b, jw, jh, self.key_state(b, j))
                patch.paste(k, (jx - M - box[0], jy - M - box[1]), k)
        for direction, (rx, ry, rw, rh) in self.rail_boxes().items():
            if rx - M < box[2] and ry - M < box[3] and ry + rh + 8 + M > box[1]:
                cap = self.paint.chevron(rw, rh, direction < 0)
                patch.paste(cap, (rx - M - box[0], ry - M - box[1]), cap)
        self.frame.paste(patch, box[:2])
        self.screen.show(self.frame, box)

    def in_menu_button(self, x, y):
        x0, y0, x1, y1 = MENU_HIT
        return x0 <= x <= x1 and y0 <= y <= y1

    def editor_url(self):
        return "http://%s:%d" % (local_ip(), EDITOR_PORT)

    def draw_info(self, img):
        """The ☰ menu: where to edit, then rows with an icon on the left and controls on the right."""
        d = ImageDraw.Draw(img)
        cx0, cy0, cx1, cy1 = SAFE_L, BAR + 6, W - SAFE_R, H - SAFE_B
        card = rounded((cx1 - cx0, cy1 - cy0), 14, (20, 21, 24, 245))
        img.paste(card, (cx0, cy0), card)
        f, big = self.paint.label, self.paint.big
        d.text((cx0 + 16, cy0 + 10), "Edit keys and OBS from a browser:", font=f, fill=(150, 154, 162))
        d.text((cx0 + 16, cy0 + 28), self.editor_url(), font=big, fill=GREEN)
        self.menu_hits = []
        rows = [("deck-speaker", "Sound"), ("deck-light", "Brightness"), ("deck-wifi", "Wi-Fi: %s" % (self.wifi_ssid() or "not connected"))]
        y = cy0 + 64
        for icon, text in rows:
            d.line([(cx0 + 14, y), (cx1 - 14, y)], fill=(40, 42, 47))
            mid = y + 24
            ic = self.paint.icon(icon + ".svg", 24)
            if ic:
                img.paste(ic, (cx0 + 18, mid - 12), ic)
            d.text((cx0 + 54, text_top(big, text, mid)), text, font=big, fill=(238, 240, 244))
            if icon == "deck-speaker":                           # on/off switch
                on = self.settings.get("sound", True)
                sx0, sx1 = cx1 - 76, cx1 - 18
                pill = rounded((sx1 - sx0, 30), 15, (GREEN if on else (60, 63, 69)) + (255,))
                img.paste(pill, (sx0, mid - 15), pill)
                kx = sx1 - 28 if on else sx0 + 2
                d.ellipse([kx, mid - 13, kx + 26, mid + 13], fill=(245, 246, 248))
                self.menu_hits.append((cx0, y, cx1, y + 48, "sound"))
            elif icon == "deck-light":                           # − 60% +
                pct = "%d%%" % round(int(self.settings.get("brightness", 180)) / 3)
                for label, bx0, action in (("−", cx1 - 160, "dim"), ("+", cx1 - 62, "bright")):
                    cap = rounded((44, 34), 9, (52, 55, 61, 255))
                    img.paste(cap, (bx0, mid - 17), cap)
                    d.text((bx0 + (44 - text_w(big, label)) // 2, text_top(big, label, mid)), label, font=big, fill=(238, 240, 244))
                    self.menu_hits.append((bx0 - 10, y, bx0 + 54, y + 48, action))
                d.text((cx1 - 89 - text_w(f, pct) // 2, text_top(f, pct, mid)), pct, font=f, fill=(200, 204, 212))
            else:
                hint = "change in the editor"
                d.text((cx1 - 18 - text_w(f, hint), text_top(f, hint, mid)), hint, font=f, fill=(120, 124, 132))
            y += 48

    def wifi_ssid(self):
        try:
            out = subprocess.run(["wpa_cli", "-i", "wlan0", "status"], capture_output=True, text=True, timeout=3).stdout
            return dict(l.split("=", 1) for l in out.splitlines() if "=" in l).get("ssid")
        except (OSError, subprocess.SubprocessError):
            return None

    def menu_action(self, action):
        if action == "sound":
            self.save_setting("sound", not self.settings.get("sound", True))
        elif action in ("dim", "bright"):
            level = int(self.settings.get("brightness", 180)) + (30 if action == "bright" else -30)
            self.save_setting("brightness", max(30, min(300, level)))
            self.apply_brightness()
        self.render()

    def save_setting(self, key, value):
        """Change one setting in settings.json (shared with the web editor)."""
        self.settings[key] = value
        path = os.path.join(APP, "settings.json")
        try:
            data = json.load(open(path))
        except (OSError, ValueError):
            data = {}
        data[key] = value
        with open(path + ".tmp", "w") as f:
            json.dump(data, f, indent=2)
        os.replace(path + ".tmp", path)
        self.mtimes["settings.json"] = os.path.getmtime(path)

    def apply_brightness(self):
        try:
            with open(BACKLIGHT, "w") as f:
                f.write(str(int(self.settings.get("brightness", 180))))
        except (OSError, ValueError):
            pass

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
        self.apply_brightness()
        self.obs.start()
        self.touch = Touch(self.events)
        self.touch.start()
        self.render()
        start = None
        trail = []
        last_poll = 0
        while True:
            try:
                ev = self.events.get(timeout=0.02 if self.warm else 0.5)
            except queue.Empty:
                ev = None
                self.warm_one()
            if self.toast_msg and time.time() > self.toast_until:
                self.toast_msg = None
                self.refresh()
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
            elif kind == "move" and start:
                trail.append(ev[1:])
            elif kind == "down":
                _, x, y = ev
                start = (x, y, time.time())
                trail = [(x, y)]
                if y < BAR or self.info or self.in_menu_button(x, y):
                    if self.info or self.in_menu_button(x, y):
                        self.beep()
                    continue
                if self.rail_at(x, y):
                    self.beep()
                    continue
                i = self.slot_at(x, y)
                if i is not None and self.buttons()[i]:
                    self.pressed = i
                    self.redraw_key(i)
                    self.beep()
            elif kind == "up" and start:
                # Every touch is a tap on whatever the finger landed on (no swipes: the ▲/▼ buttons change pages).
                # Resistive panels report junk positions as the finger lifts, so the release point is ignored.
                sx, sy, _ = start
                start = None
                x, y = trail[-3] if len(trail) > 3 else (sx, sy)
                was, self.pressed = self.pressed, None
                dy, dx = y - sy, x - sx
                if self.in_menu_button(sx, sy) and abs(dx) < 40 and abs(dy) < 40:   # ☰ opens, ✕ closes
                    self.info = not self.info
                    self.render()
                elif self.info:                                  # a control on the menu card, or outside it
                    hit = next((a for x0, y0, x1, y1, a in self.menu_hits if x0 <= sx <= x1 and y0 <= sy <= y1), None)
                    if hit:
                        self.menu_action(hit)
                    elif not (SAFE_L <= sx <= W - SAFE_R and BAR + 6 <= sy <= H - SAFE_B):
                        self.info = False
                        self.render()
                elif self.rail_at(sx, sy):                       # ▲/▼ page buttons
                    self.page += self.rail_at(sx, sy)
                    self.render()
                elif was is not None:                          # a tap on a key (decided by where it landed)
                    self.redraw_key(was)
                    print("tap key %d (moved %d,%d over %d readings)" % (was, dx, dy, len(trail)), flush=True)
                    try:
                        self.press(self.buttons()[was])
                    except OSError as e:
                        print("press failed:", e, flush=True)
                    self.refresh()


if __name__ == "__main__":
    Deck().run()
