# Pad Deck server API

The web app (`index.html`, `app.js`, `menu.js`, …) talks to whichever server served it. Three servers implement
this API and must answer the same way:

| Server | Where it runs | `device` |
|---|---|---|
| `server.py` | Pad 7, Nebula Pad (Pad Deck Lite) | `pad7`, `nebula` |
| `helper/PadDeckHelper.swift` | Pad Deck Helper on a Mac | `helper` |
| `helper/windows/PadDeckHelper.cs` | Pad Deck Helper on Windows | `helper` |

All of them listen on port **8099**. Check any of them against this page with:

```bash
python3 tools/check-server.py http://HOST:8099 [--code 123456]
```

## Pairing

Requests from the device itself (loopback: `127.0.0.1`, `::1`) are always allowed. Requests from other devices on the
network must carry the pairing code in an `X-Pad-Deck-Code` header:

- **Helper:** always (the code is the one shown in the Helper's menu).
- **Pad / Nebula:** only once a code is set in Menu → Helper (the same Helper code). With no code set, other devices
  can use the deck and editor as before, but can't run computer, plug or web-request keys.

A missing or wrong code is answered `401 {"error": "…", "pair": true}`. The web app then asks for the code, keeps
it in the browser, and sends it from then on. A link ending in `#code=123456` fills it in.

`GET /api/device` and the web app's own files never need the code.

## Routes

| Route | Body → answer | Notes |
|---|---|---|
| `GET /api/device` | → `{"device", "local": bool, "paired": bool, "os"?}` | `paired`: this request may use the API. `os` (`mac` / `windows`) on the Helper. |
| `GET /api/layout` | → layout | `{columns, rows, bg?, pages: [{buttons: [...]}]}` |
| `PUT /api/layout` | layout → `{"ok": true}` | 400 unless `pages` is a non-empty list of `{buttons: [...]}` |
| `GET /api/layouts` | → `[{name, saved, pages, columns, rows, buttons}]` | newest first |
| `POST /api/layouts` | `{name}` → `{"ok": true}` | saves the current layout under that name |
| `POST /api/layouts/load` | `{name}` → `{"ok": true}` | 404 `{"error"}` if gone; current one kept as "Before last load" |
| `POST /api/layouts/new` | → `{"ok": true}` | empty 4×3 layout; current one kept as "Before last load" |
| `DELETE /api/layouts?name=…` | → `{"ok": true}` | |
| `POST /api/layouts/import` | `{host}` → `{"ok": true, "icons": n}` | **Helper only.** Copies the layout (and its uploaded icons) from a Pad at `host` |
| `GET /api/icons` | → `["name.svg", ...]` | sorted |
| `POST /api/icons` | raw bytes, `X-Filename` → `{"name"}` | png, jpg, jpeg, svg, webp, gif |
| `GET /api/settings` | → settings | every key in the defaults below |
| `PUT /api/settings` | settings → `{"ok": true}` | unknown keys are dropped; values take the default's type |
| `POST /api/run` | action → `{"ok": true, "message"}` | 400 `{"error"}` on failure |
| `GET /api/helper?what=ping\|apps\|shortcuts` | → Helper answer | Pad: asks the Helper. Helper: answers itself |
| `GET /api/plugs` | → `{"plugs": [{name, ip, outlet}]}` | Kasa plugs found on the network |
| `POST /api/plugs/state` | `[{brand, ip, outlet}]` → `{"states": [true/false/null]}` | |
| `POST /api/buzz` | → `{"ok": true}` | Pad 7 vibration; does nothing elsewhere or for other devices |
| `GET /api/update[?check=1]` | → `{version, latest, available, busy, error, notes, checked, auto}` | `check=1` asks GitHub now. Pad 7: its own screen only |
| `POST /api/update` | → `{"ok": true, "message"}` | installs the newest release and restarts; 400 `{"error"}` if up to date |
| `POST /api/busy` | → `{"ok": true}` | OBS is streaming or recording: no automatic update for 5 minutes |
| `GET /api/connect` | → `{"addresses": [...], "port", "code", "computer"}` | **Helper only, loopback only.** For the phone set-up page |
| Pad-only | `/api/system`, `/api/wifi`, `/api/leds`, `/api/term` | the Helper answers 404 |

Default settings: `sound true, vibrate true, volume 0.6, leds true, obsHost "", obsPort 4455, obsPassword "",
brightness 180, helperHost "", helperPort 8098, helperCode "", autoUpdate true`.

## Updates

Each server checks GitHub's latest release a few times a day and, with `autoUpdate` on, installs it by itself
between 3 and 5 at night, unless a screen said OBS is live in the last 5 minutes (`POST /api/busy`). The `VERSION`
file is the version a Pad reports; the Helpers report their own (built from it). A release's tag must be
`v` + `VERSION`; the Helper apps workflow refuses to build a release that doesn't match.

### Actions (`POST /api/run`)

| `type` | Fields | Runs |
|---|---|---|
| `http` | `url`, `method?`, `body?` | a web request from the server |
| `plug` | `brand` (kasa, shelly, shelly2, tasmota), `ip`, `outlet?`, `do` (toggle, on, off) | the plug, from the server |
| `url` | `url` | opens on the computer (Pad: through the Helper) |
| `app` | `name` | opens on the computer |
| `shortcut` | `name` | Mac Shortcuts |
| `hotkey` | `keys` like `cmd+shift+4` | presses keys on the computer |

Errors use the same wording on every server (the web app shows them as they are).
