#!/usr/bin/env python3
"""Pad Deck Helper: lets Pad Deck keys do things on this computer (open a website or an app, run a Shortcut,
press a key combination). Runs in the background on the computer that runs OBS; the Pad (or Nebula) sends it
requests over the network, and every request must carry the pairing code shown on first run.

    python3 pad-deck-helper.py            run it (helper/install-helper.sh sets it to start at login)
    python3 pad-deck-helper.py --code     show the pairing code

GET  /ping        is the helper there, and is the pairing code right?
GET  /apps        installed app names (for the key editor)
GET  /shortcuts   Shortcuts names (macOS)
POST /run         {"type": "url" | "app" | "shortcut" | "hotkey", ...}
"""
import hmac
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8098
MAC = platform.system() == "Darwin"
CONFIG_DIR = os.path.expanduser("~/Library/Application Support/Pad Deck Helper" if MAC else "~/.config/pad-deck-helper")
CONFIG = os.path.join(CONFIG_DIR, "config.json")


def load_code():
    """The pairing code, made up once and kept."""
    try:
        with open(CONFIG) as f:
            return json.load(f)["code"]
    except (OSError, ValueError, KeyError):
        code = "%06d" % secrets.randbelow(10 ** 6)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG, "w") as f:
            json.dump({"code": code}, f)
        os.chmod(CONFIG, 0o600)
        return code


# ---------- What a key can do ----------

# macOS key codes for keys that aren't a single character
KEY_CODES = {"return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
             "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
             **{"f%d" % n: c for n, c in zip(range(1, 13), (122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111))}}
MODIFIERS = {"cmd": "command", "command": "command", "⌘": "command", "shift": "shift", "⇧": "shift",
             "alt": "option", "option": "option", "opt": "option", "⌥": "option", "ctrl": "control", "control": "control", "⌃": "control"}


def press_keys(combo):
    """Press a key combination like "cmd+shift+4" or "ctrl+f5" (macOS: needs Accessibility permission)."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("No keys given")
    *mods, key = parts
    unknown = [m for m in mods if m not in MODIFIERS]
    if unknown:
        raise ValueError("Unknown modifier: %s (use cmd, shift, alt/option, ctrl)" % ", ".join(unknown))
    using = " using {%s}" % ", ".join("%s down" % MODIFIERS[m] for m in mods) if mods else ""
    if key in KEY_CODES:
        action = "key code %d%s" % (KEY_CODES[key], using)
    elif len(key) == 1:
        action = "keystroke %s%s" % (json.dumps(key), using)
    else:
        raise ValueError("Unknown key: %s" % key)
    r = subprocess.run(["osascript", "-e", 'tell application "System Events" to ' + action], capture_output=True, text=True)
    if r.returncode != 0:
        if "not allowed" in r.stderr or "1002" in r.stderr or "-1719" in r.stderr:
            raise ValueError("Allow Pad Deck Helper to control the keyboard: System Settings → Privacy & Security "
                             "→ Accessibility (turn on the entry for python3 or Terminal)")
        raise ValueError(r.stderr.strip() or "Couldn't press the keys")


def run(a):
    t = a.get("type")
    if t == "url":
        url = str(a.get("url", "")).strip()
        if "://" not in url:
            url = "https://" + url
        if MAC:
            subprocess.run(["open", url], check=True)
        else:
            webbrowser.open(url)
        return "Opened %s" % url.split("://", 1)[1].split("/", 1)[0]
    if not MAC:
        raise ValueError("This needs a Mac (for now)")
    if t == "app":
        name = str(a.get("app", "")).strip()
        r = subprocess.run(["open", "-a", name], capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError("Couldn't find the app “%s”" % name)
        return "Opened %s" % name
    if t == "shortcut":
        name = str(a.get("shortcut", "")).strip()
        if name not in shortcut_names():
            raise ValueError("There's no Shortcut called “%s”" % name)
        subprocess.Popen(["shortcuts", "run", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "Running %s" % name
    if t == "hotkey":
        press_keys(str(a.get("keys", "")))
        return "Pressed %s" % a.get("keys")
    raise ValueError("Unknown action %r" % t)


def app_names():
    names = set()
    for d in ("/Applications", "/Applications/Utilities", "/System/Applications", "/System/Applications/Utilities",
              os.path.expanduser("~/Applications")):
        try:
            names.update(n[:-4] for n in os.listdir(d) if n.endswith(".app") and not n.startswith("."))
        except OSError:
            pass
    return sorted(names, key=str.lower)


def shortcut_names():
    if not MAC:
        return []
    r = subprocess.run(["shortcuts", "list"], capture_output=True, text=True, timeout=10)
    return [l for l in r.stdout.splitlines() if l.strip()]


# ---------- Web server ----------

class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def paired(self):
        if hmac.compare_digest(self.headers.get("X-Pad-Deck-Code", ""), CODE):
            return True
        self.send_json({"error": "Wrong pairing code"}, 401)
        return False

    def do_GET(self):
        if not self.paired():
            return
        if self.path == "/ping":
            return self.send_json({"ok": True, "computer": socket.gethostname(), "mac": MAC})
        if self.path == "/apps":
            return self.send_json({"apps": app_names()})
        if self.path == "/shortcuts":
            return self.send_json({"shortcuts": shortcut_names()})
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if not self.paired():
            return
        if self.path != "/run":
            return self.send_json({"error": "Not found"}, 404)
        try:
            a = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            self.send_json({"ok": True, "message": run(a)})
        except (ValueError, OSError, subprocess.SubprocessError) as e:
            self.send_json({"error": str(e)}, 400)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    CODE = load_code()
    if "--code" in sys.argv:
        print(CODE)
        sys.exit()
    print("Pad Deck Helper on port %d. Pairing code: %s" % (PORT, CODE), flush=True)
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
