#!/usr/bin/env python3
"""Checks a Pad Deck server (Pad, Nebula or Pad Deck Helper) against docs/server-api.md.

    python3 tools/check-server.py http://HOST:8099 [--code 123456]

Only harmless calls: the layout and settings are read and written back unchanged, a test layout is saved and
deleted, and the action checks use requests that fail on purpose. No plug is switched and nothing is opened.
"""
import json
import sys
import urllib.error
import urllib.request

args = sys.argv[1:]
if not args or args[0].startswith("-"):
    sys.exit(__doc__)
BASE = args[0].rstrip("/")
CODE = args[args.index("--code") + 1] if "--code" in args else None
LOOPBACK = any(h in BASE for h in ("//localhost", "//127.0.0.1", "//[::1]"))
failed = 0


def call(method, path, body=None, code=CODE, raw=None, headers=None):
    h = dict(headers or {})
    if code is not None:
        h["X-Pad-Deck-Code"] = code
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        text, status = e.read(), e.code
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def check(name, ok, detail=""):
    global failed
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else "  →  " + str(detail)[:200]))
    failed += not ok


print("Checking", BASE)
st, dev = call("GET", "/api/device", code=None)
check("GET /api/device", st == 200 and isinstance(dev, dict) and dev.get("device") in ("pad7", "nebula", "helper")
      and isinstance(dev.get("local"), bool) and isinstance(dev.get("paired"), bool), (st, dev))
helper = isinstance(dev, dict) and dev.get("device") == "helper"
if helper:
    check("helper reports its os", dev.get("os") in ("mac", "windows"), dev)

st, page = call("GET", "/", code=None)
check("GET / serves the web app", st == 200 and b"<html" in (page if isinstance(page, bytes) else b"").lower(), st)
st, _ = call("GET", "/app.js", code=None)
check("GET /app.js", st == 200, st)

if CODE and not LOOPBACK:
    st, j = call("GET", "/api/layout", code="")
    check("no code → 401 pair", st == 401 and isinstance(j, dict) and j.get("pair") is True, (st, j))
    st, j = call("GET", "/api/layout", code="000000" if CODE != "000000" else "111111")
    check("wrong code → 401 pair", st == 401 and isinstance(j, dict) and j.get("pair") is True, (st, j))
    st, dev2 = call("GET", "/api/device")
    check("device says paired with the code", st == 200 and dev2.get("paired") is True, dev2)

st, layout = call("GET", "/api/layout")
check("GET /api/layout", st == 200 and isinstance(layout, dict) and isinstance(layout.get("pages"), list), (st, layout))
if st == 200:
    st, j = call("PUT", "/api/layout", layout)
    check("PUT /api/layout (unchanged)", st == 200 and j == {"ok": True}, (st, j))
    st, _ = call("PUT", "/api/layout", {"pages": []})
    check("PUT /api/layout rejects an empty layout", st == 400, st)

st, s = call("GET", "/api/settings")
keys = {"sound", "vibrate", "volume", "leds", "obsHost", "obsPort", "obsPassword", "brightness", "helperHost",
        "helperPort", "helperCode"}
check("GET /api/settings has every key", st == 200 and isinstance(s, dict) and keys <= set(s), (st, s))
if st == 200:
    st, j = call("PUT", "/api/settings", s)
    check("PUT /api/settings (unchanged)", st == 200 and j == {"ok": True}, (st, j))
    st, s2 = call("GET", "/api/settings")
    check("settings read back the same", s2 == s, (s, s2))

st, icons = call("GET", "/api/icons")
check("GET /api/icons", st == 200 and isinstance(icons, list) and icons == sorted(icons) and len(icons) > 0, (st, icons))
if isinstance(icons, list) and icons:
    st, _ = call("GET", "/icons/" + urllib.request.quote(icons[0]), code=None)
    check("GET /icons/" + icons[0], st == 200, st)

NAME = "Check script test"
st, j = call("POST", "/api/layouts", {"name": NAME})
check("POST /api/layouts", st == 200 and j == {"ok": True}, (st, j))
st, lst = call("GET", "/api/layouts")
entry = next((e for e in lst if e.get("name") == NAME), None) if isinstance(lst, list) else None
check("GET /api/layouts lists it", entry is not None and {"saved", "pages", "columns", "rows", "buttons"} <= set(entry), lst)
st, j = call("DELETE", "/api/layouts?name=" + urllib.request.quote(NAME))
check("DELETE /api/layouts", st == 200 and j == {"ok": True}, (st, j))
st, lst = call("GET", "/api/layouts")
check("…and it's gone", isinstance(lst, list) and all(e.get("name") != NAME for e in lst), lst)
st, j = call("POST", "/api/layouts/load", {"name": "No such layout 12345"})
check("load a missing layout → 404", st == 404 and isinstance(j, dict) and "error" in j, (st, j))
st, j = call("POST", "/api/layouts", {"name": "  "})
check("save with no name → 400", st == 400 and isinstance(j, dict) and "error" in j, (st, j))

st, j = call("POST", "/api/plugs/state", [])
check("POST /api/plugs/state []", st == 200 and j == {"states": []}, (st, j))
st, j = call("POST", "/api/plugs/state", [{"brand": "shelly", "ip": "bad host!"}])
check("plug state for a bad address → null", st == 200 and j == {"states": [None]}, (st, j))

if not dev.get("local") and not CODE and not helper:
    st, j = call("POST", "/api/run", {"type": "nonsense"})
    check("run from another device with no code set → 403", st == 403, (st, j))
    a_run = False
else:
    a_run = True
st, j = call("POST", "/api/run", {"type": "nonsense"}) if a_run else (400, {"error": "skipped"})
check("run an unknown action → 400", st == 400 and isinstance(j, dict) and "error" in j, (st, j))
st, j = call("POST", "/api/run", {"type": "http", "url": "ftp://x"}) if a_run else (400, {"error": "http://"})
check("run http with a bad address → 400", st == 400 and "http://" in str(j.get("error", "")), (st, j))
st, j = call("POST", "/api/run", {"type": "plug", "brand": "kasa", "ip": "not an ip!", "do": "toggle"}) if a_run \
    else (400, {"error": "skipped"})
check("run plug with a bad address → 400", st == 400 and isinstance(j, dict) and "error" in j, (st, j))

st, j = call("POST", "/api/buzz")
check("POST /api/buzz", st == 200 and j == {"ok": True}, (st, j))

if helper:
    st, j = call("GET", "/api/helper?what=ping")
    check("GET /api/helper?what=ping", st == 200 and isinstance(j, dict) and j.get("ok") is True, (st, j))
    st, j = call("GET", "/api/helper?what=apps")
    check("GET /api/helper?what=apps", st == 200 and isinstance(j.get("apps"), list), (st, str(j)[:100]))
    st, _ = call("GET", "/api/wifi")
    check("Pad-only /api/wifi → 404 on the Helper", st == 404, st)
    if LOOPBACK:
        st, j = call("GET", "/api/connect")
        check("GET /api/connect", st == 200 and isinstance(j.get("addresses"), list) and j.get("code"), (st, j))
    else:
        st, j = call("GET", "/api/connect")
        check("/api/connect refused from the network", st == 403, st)

print("All good." if not failed else "%d check(s) failed." % failed)
sys.exit(1 if failed else 0)
