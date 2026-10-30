# Pad Deck

**A tactile, touch-first stream deck for OBS, built for the BigTreeTech Pad 7.**
Big chrome-and-carbon keycaps that switch scenes and mute your mic, live status lights, and everything
editable right on the screen. No phone, no extra app, no subscription. The Pad talks to OBS over your network.

![Pad Deck](docs/screenshots/deck.png)

## Features

- **One-tap OBS control:** keys switch scenes or toggle an audio input's mute. The live scene's key lights
  up green, and the bar shows OBS, Live and Rec status.
- **Physical keycaps:** a dished face, a weighted edge, a satisfying press, a plastic "click" through the
  Pad's speaker on press and release, and an optional buzz from the vibration motor.
- **Pages:** swipe up and down (the page follows your finger), or use the ▲/▼ rail. Swiping past the last
  page makes a new one; empty pages clean themselves up.
- **Edit on the device:** hold any key for 3 s or tap ✎. Keys wiggle iOS-style. Drag to rearrange,
  hold a key over ▲/▼ to carry it to another page, tap to edit, ✕ to delete (with a confirmation).
- **Button editor:** pick the scene or audio input, a label, a color (swatches or a full color picker) and
  an icon from a library of 114 matching line icons, or upload your own.
- **Any grid:** 1–8 columns × 1–8 rows. Shrink it and keys that no longer fit move to the next page.
- **Saved layouts:** save, overwrite, load and delete named layouts (grid included), or start fresh. Loading
  always keeps the previous layout as a backup.
- **Status lights:** the Pad's two top LEDs show OBS connected (green) and on air (red), dimmed to a glow.
- **Settings menu:** swipe left or tap ☰ for Wi-Fi (with an on-screen keyboard), a full terminal, sound
  and vibration, click volume (0–10) and system info and power.
- **Polish:** a chrome "Pad Deck" badge with a gleam on start-up, a hidden cursor, a 60 fps UI on a
  little ARM board.

| | |
|---|---|
| ![Start-up badge](docs/screenshots/badge.png) | ![Edit mode](docs/screenshots/edit-mode.png) |
| ![Button editor](docs/screenshots/button-editor.png) | ![Color picker](docs/screenshots/color-picker.png) |
| ![Saved layouts](docs/screenshots/layouts.png) | ![Sound & Feel](docs/screenshots/sound.png) |

## Hardware

- BigTreeTech **Pad 7** with a **CB1** compute module (1024×600 touch screen, speaker, vibration motor, RGB LEDs)
- OBS 28+ with the WebSocket server on (Tools → WebSocket Server Settings), on the same network

## Setup

1. **Image:** download BTT's CB1 Debian image, then run `bash setup/make-pad-image.sh <image.img.xz>`.
   It adds your Wi-Fi, the Pad 7 screen settings and the hostname `pad-deck`. Flash the result to an SD card.
2. **First boot:** create a `deck` user with sudo and your SSH key, and install
   `cage chromium libnss-mdns avahi-daemon sox libsox-fmt-mp3`.
3. **Point it at OBS:** set your computer's name or IP (and password, if any) in `config.js`.
4. **Install:** `bash setup/deploy.sh` copies the app over. Then run `setup/pad-setup.sh` on the Pad once and
   reboot. It sets up the kiosk, the invisible cursor, the speaker, the vibration motor and the LEDs.

`setup/pull-from-pad.sh` copies the Pad's layouts and settings back for safekeeping, and
`setup/make-icons.py` regenerates the icon library.

Run it locally with `python3 server.py` and open <http://localhost:8099>.

## Credits

Fonts: Barlow Semi Condensed and Yellowtail (SIL Open Font License). Terminal: xterm.js (MIT).
