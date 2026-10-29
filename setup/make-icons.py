#!/usr/bin/env python3
"""Generate the Pad Deck icon library: white line icons for streaming scenes, gear and production.

    python3 setup/make-icons.py      writes icons/deck-*.svg (re-run after editing an icon here)

Style: a 48-unit grid, bold round-capped white strokes,
line-drawn throughout (screens are outlined displays with line content inside).
"""
import os
import re

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "icons")
W = 2.6  # stroke width; per-shape stroke-widths below were drawn for 3 and are scaled to match
THIN = W / 3

def svg(body):
    body = re.sub(r'stroke-width="([\d.]+)"', lambda m: f'stroke-width="{float(m.group(1)) * THIN:.2f}"', body)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" fill="none" stroke="#fff" '
            f'stroke-width="{W}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>\n')

# ---------- building blocks ----------

STAND = '<path d="M24 34v6M16 41h16"/>'

def monitor(content):
    """An outlined display (40×28, x 4–44, y 6–34) on a stand, with line-drawn content inside."""
    return svg('<rect x="4" y="6" width="40" height="28" rx="3"/>' + content + STAND)

def person(cx, s=1.0, base=34):
    """Line-drawn head and shoulders standing on `base` (the bottom of the screen by default)."""
    r, w = 4.4 * s, 8.6 * s
    head = base - 8 * s - 2.2 * s - r
    return (f'<circle cx="{cx}" cy="{head:.1f}" r="{r:.1f}"/>'
            f'<path d="M{cx-w:.1f} {base}v-1a{w:.1f} {7.5*s:.1f} 0 0 1 {2*w:.1f} 0v1"/>')

ICONS = {}

# ---------- screens: what's on stream ----------

ICONS["screen-person"] = monitor(person(24))
ICONS["screen-two-people"] = monitor(person(15, .8) + person(33, .8))
ICONS["screen-split"] = monitor('<path d="M24 6v28"/>' + person(14, .72) + person(34, .72))
ICONS["screen-pip"] = monitor(person(18, .9) + '<rect x="28" y="20" width="11" height="9" rx="1.5" stroke-width="2.5"/>')
ICONS["screen-pip-person"] = monitor(person(18, .9) + '<rect x="28" y="12" width="12" height="11" rx="1.5" stroke-width="2.5"/><circle cx="34" cy="16.5" r="1.8" stroke-width="2"/>')
ICONS["screen-website"] = monitor('<path d="M4 13h40"/><path d="M9.5 9.5h.1M13.5 9.5h.1M10 20h16M10 25h22M10 30h11" stroke-width="2.5"/>')
ICONS["screen-website-cams"] = monitor('<path d="M4 13h40"/><path d="M9.5 9.5h.1M13.5 9.5h.1M10 20h12M10 25h12M10 30h8" stroke-width="2.5"/><rect x="27" y="17" width="12" height="7" rx="1.5" stroke-width="2.2"/><rect x="27" y="26" width="12" height="5" rx="1.5" stroke-width="2.2"/>')
ICONS["screen-game"] = monitor('<path d="M17 14h14a4.5 4.5 0 0 1 4.3 3.3l1.5 6a3 3 0 0 1-5.3 2.5L29.5 23h-11l-2 2.8a3 3 0 0 1-5.3-2.5l1.5-6A4.5 4.5 0 0 1 17 14z" stroke-width="2.5"/>'
                               '<path d="M18.5 17v4M16.5 19h4M29.5 18h.1M31.5 20.5h.1" stroke-width="2.2"/>')
ICONS["screen-video"] = monitor('<path d="M20 13v14l11-7z"/>')
ICONS["screen-code"] = monitor('<path d="M17 14l-6 6 6 6M31 14l6 6-6 6M26.5 12.5l-5 15"/>')
ICONS["screen-slides"] = monitor('<path d="M12 29v-8M19 29V15M26 29v-6M33 29V12"/>')
ICONS["screen-chat"] = monitor('<path d="M12 11h24a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H22l-6 5v-5h-4a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2z" stroke-width="2.5"/>')
ICONS["screen-share"] = monitor('<path d="M13 29c1-7 6-11 14-11v-5l8 7-8 7v-5c-6 0-10 2-14 7z" stroke-width="2.5"/>')
ICONS["screen-desktop"] = monitor('<rect x="9" y="11" width="13" height="9" rx="1" stroke-width="2.5"/><rect x="25" y="15" width="14" height="14" rx="1" stroke-width="2.5"/><path d="M9 29h12" stroke-width="2.5"/>')
ICONS["screen-blank"] = monitor("")
ICONS["screen-lower-third"] = monitor(person(19, .8) + '<path d="M27 25h12M27 29.5h8" stroke-width="2.5"/>')
ICONS["screen-overlay"] = monitor('<path d="M9 11h6M9 11v5M39 11h-6M39 11v5M9 29h6M9 29v-5M39 29h-6M39 29v-5" stroke-width="2.5"/><circle cx="24" cy="20" r="3.5" stroke-width="2.5"/>')
ICONS["screen-camera"] = monitor('<rect x="12" y="13" width="17" height="13" rx="2.5" stroke-width="2.5"/><path d="M29 18l7-3.5v11L29 22" stroke-width="2.5"/>')
ICONS["screen-webcam"] = svg('<rect x="4" y="12" width="40" height="24" rx="3"/><path d="M24 36v5M16 42h16"/><rect x="17" y="3" width="14" height="8" rx="4"/><circle cx="24" cy="7" r="1.2" fill="#fff" stroke="none"/>' + person(24, .8, 36))

# ---------- devices and cameras ----------

ICONS["camera"] = svg('<rect x="4" y="13" width="28" height="22" rx="4"/><path d="M32 21l12-6v18l-12-6z"/><circle cx="12" cy="20" r="1.5" fill="#fff" stroke="none"/>')
ICONS["camera-dslr"] = svg('<path d="M6 15h8l3-5h14l3 5h8a2 2 0 0 1 2 2v20a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V17a2 2 0 0 1 2-2z"/><circle cx="24" cy="26" r="7"/><circle cx="24" cy="26" r="2.5" fill="#fff" stroke="none"/><path d="M9 20h3"/>')
ICONS["webcam"] = svg('<circle cx="24" cy="19" r="13"/><circle cx="24" cy="19" r="5"/><path d="M16 36l-3 7h22l-3-7"/>')
ICONS["camera-closeup"] = svg('<circle cx="21" cy="21" r="14"/><path d="M31 31l11 11"/><circle cx="21" cy="17" r="3.5" stroke-width="2.5"/><path d="M14.5 28v-.5a6.5 6 0 0 1 13 0v.5" stroke-width="2.5"/>')
ICONS["phone"] = svg('<rect x="13" y="3" width="22" height="42" rx="5"/><path d="M21 8h6"/><circle cx="24" cy="39" r="1.5" fill="#fff" stroke="none"/>')
ICONS["phone-camera"] = svg('<rect x="13" y="3" width="22" height="42" rx="5"/><path d="M21 8h6"/><rect x="17.5" y="18" width="9" height="9" rx="1.5"/><path d="M26.5 21l4-2.2v7.4l-4-2.2"/>')
ICONS["phone-landscape"] = svg('<rect x="3" y="13" width="42" height="22" rx="5"/><path d="M8 21v6"/><circle cx="24" cy="24" r="4"/><circle cx="35" cy="20" r="1.5" fill="#fff" stroke="none"/>')
ICONS["tablet"] = svg('<rect x="7" y="4" width="34" height="40" rx="4"/><path d="M21 39h6"/>')
ICONS["laptop"] = svg('<rect x="9" y="9" width="30" height="21" rx="2"/><path d="M3 35h42l-2 4H5z"/>')
ICONS["laptop-game"] = svg('<rect x="9" y="9" width="30" height="21" rx="2"/><path d="M3 35h42l-2 4H5z"/><path d="M19 17v6M16 20h6"/><circle cx="28" cy="18" r="1.5" fill="#fff" stroke="none"/><circle cx="31" cy="22" r="1.5" fill="#fff" stroke="none"/>')
ICONS["desktop"] = svg('<rect x="4" y="6" width="30" height="22" rx="2"/><path d="M19 28v6M12 35h14"/><rect x="38" y="6" width="6" height="30" rx="1.5"/><path d="M41 11v.1"/>')
ICONS["controller"] = svg('<path d="M15 13h18a8 8 0 0 1 7.6 5.5l3 9.5a4.5 4.5 0 0 1-7.9 4.1L31 26H17l-4.7 6.1a4.5 4.5 0 0 1-7.9-4.1l3-9.5A8 8 0 0 1 15 13z"/><path d="M15 17.5v5M12.5 20h5"/><circle cx="31" cy="18" r="1.6" fill="#fff" stroke="none"/><circle cx="34.5" cy="21.5" r="1.6" fill="#fff" stroke="none"/>')
ICONS["handheld"] = svg('<rect x="4" y="12" width="40" height="24" rx="7"/><rect x="15" y="17" width="18" height="14" rx="1.5"/><path d="M9 22v4M7 24h4"/><circle cx="39" cy="22" r="1.4" fill="#fff" stroke="none"/><circle cx="39" cy="27" r="1.4" fill="#fff" stroke="none"/>')
ICONS["vr-headset"] = svg('<path d="M8 14h32a4 4 0 0 1 4 4v12a4 4 0 0 1-4 4h-8l-4-5h-8l-4 5H8a4 4 0 0 1-4-4V18a4 4 0 0 1 4-4z"/>')
ICONS["keyboard"] = svg('<rect x="3" y="13" width="42" height="22" rx="3"/><path d="M9 19h.1M15 19h.1M21 19h.1M27 19h.1M33 19h.1M39 19h.1M9 24h.1M15 24h.1M21 24h.1M27 24h.1M33 24h.1M39 24h.1M15 29h18"/>')
ICONS["drone"] = svg('<rect x="18" y="19" width="12" height="10" rx="3"/><path d="M18 21l-6-5M30 21l6-5M18 27l-6 5M30 27l6 5"/>'
                      '<circle cx="10" cy="14" r="6"/><circle cx="38" cy="14" r="6"/><circle cx="10" cy="34" r="6"/><circle cx="38" cy="34" r="6"/>')

# ---------- stream moments ----------

ICONS["clapperboard"] = svg('<path d="M5 20h38v20a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3z"/><path d="M5 20l-1-6 36-7 1 6z"/><path d="M13 12l4 6M23 10l4 6M33 8l4 6"/>')
ICONS["live"] = svg('<rect x="3" y="12" width="42" height="24" rx="5"/><path d="M10 18v12h5M19 18v12M23 18l3.5 12L30 18M40 18h-6v12h6M34 24h5" stroke-width="2.6"/>')
ICONS["on-air"] = svg('<circle cx="24" cy="24" r="5" fill="#fff" stroke="none"/><path d="M15.5 15.5a12 12 0 0 0 0 17M32.5 15.5a12 12 0 0 1 0 17M9.5 9.5a20.5 20.5 0 0 0 0 29M38.5 9.5a20.5 20.5 0 0 1 0 29"/>')
ICONS["record"] = svg('<circle cx="24" cy="24" r="19"/><circle cx="24" cy="24" r="9"/><circle cx="24" cy="24" r="3" fill="#fff" stroke="none"/>')
ICONS["stop"] = svg('<circle cx="24" cy="24" r="19"/><rect x="17" y="17" width="14" height="14" rx="2"/>')
ICONS["pause"] = svg('<circle cx="24" cy="24" r="19"/><path d="M19.5 16v16M28.5 16v16" stroke-width="4"/>')
ICONS["play"] = svg('<circle cx="24" cy="24" r="19"/><path d="M19.5 15.5v17l14-8.5z"/>')
ICONS["replay"] = svg('<path d="M8 24a16 16 0 1 0 5-11.6"/><path d="M8 6v8h8"/><path d="M21 17v14l11-7z"/>')
ICONS["starting-soon"] = svg('<circle cx="24" cy="26" r="17"/><path d="M24 17v9l6 4"/><path d="M18 4h12M24 4v5"/>')
ICONS["timer"] = svg('<path d="M14 4h20M14 44h20M16 4c0 10 16 10 16 20S16 34 16 44M32 4c0 10-16 10-16 20s16 10 16 20"/><path d="M20 39h8l-4-5z" fill="#fff"/>')
ICONS["brb"] = svg('<path d="M8 18h26v13a9 9 0 0 1-9 9h-8a9 9 0 0 1-9-9z"/><path d="M34 21h3a5 5 0 0 1 0 10h-3"/><path d="M16 5c-2 2.5 2 4 0 7M24 5c-2 2.5 2 4 0 7"/>')
ICONS["ending"] = svg('<path d="M36 30A15 15 0 1 1 22 9a12 12 0 0 0 14 21z"/><path d="M36 9v6M33 12h6"/>')
ICONS["intermission"] = svg('<rect x="4" y="8" width="40" height="26" rx="3"/><path d="M20 15v12M28 15v12" stroke-width="4"/><path d="M16 41h16"/>')
ICONS["countdown-3"] = svg('<circle cx="24" cy="24" r="19"/><path d="M18 15h11l-6 8a6 6 0 1 1-5.5 8.5"/>')

# ---------- audio ----------

ICONS["mic"] = svg('<rect x="17" y="4" width="14" height="24" rx="7"/><path d="M10 22a14 14 0 0 0 28 0M24 36v7M17 43h14"/>')
ICONS["mic-off"] = svg('<path d="M31 21V11a7 7 0 0 0-13.7-2M17 17v4a7 7 0 0 0 11.6 5.3"/><path d="M10 22a14 14 0 0 0 23.5 10.2M38 22a14 14 0 0 1-.9 5M24 36v7M17 43h14M5 5l38 38"/>')
ICONS["podcast-mic"] = svg('<rect x="15" y="4" width="18" height="26" rx="9"/><path d="M15 13h6M15 19h6M27 13h6M27 19h6"/><path d="M24 30v8M14 44h20M24 38v6"/>')
ICONS["headphones"] = svg('<path d="M6 30v-6a18 18 0 0 1 36 0v6"/><rect x="5" y="27" width="9" height="15" rx="3"/><rect x="34" y="27" width="9" height="15" rx="3"/>')
ICONS["headset"] = svg('<path d="M7 28v-5a17 17 0 0 1 34 0v5"/><rect x="5" y="25" width="8" height="13" rx="3"/><rect x="35" y="25" width="8" height="13" rx="3"/><path d="M39 38c0 4-4 6-9 6h-4"/>')
ICONS["speaker"] = svg('<path d="M6 18h7l11-9v30l-11-9H6z"/><path d="M31 17a9 9 0 0 1 0 14M36 11a17 17 0 0 1 0 26"/>')
ICONS["speaker-off"] = svg('<path d="M6 18h7l11-9v30l-11-9H6z"/><path d="M32 18l10 12M42 18L32 30"/>')
ICONS["music"] = svg('<path d="M18 36V10l22-5v26"/><circle cx="12" cy="36" r="6"/><circle cx="34" cy="31" r="6"/><path d="M18 17l22-5"/>')
ICONS["waveform"] = svg('<path d="M4 24h4M10 18v12M16 10v28M22 15v18M28 6v36M34 14v20M40 19v10M44 24h.1"/>')
ICONS["mixer"] = svg('<path d="M10 5v38M24 5v38M38 5v38"/><rect x="5" y="26" width="10" height="7" rx="2"/><rect x="19" y="12" width="10" height="7" rx="2"/><rect x="33" y="21" width="10" height="7" rx="2"/>')
ICONS["soundboard"] = svg('<rect x="4" y="4" width="17" height="17" rx="3"/><rect x="27" y="4" width="17" height="17" rx="3"/><rect x="4" y="27" width="17" height="17" rx="3"/><circle cx="12.5" cy="12.5" r="2.5"/><circle cx="35.5" cy="35.5" r="2.5"/><rect x="27" y="27" width="17" height="17" rx="3"/>')
ICONS["volume-knob"] = svg('<circle cx="24" cy="24" r="13"/><path d="M24 24l-6-6"/><path d="M8.4 33A18 18 0 1 1 39.6 33"/><path d="M8 37l-2 2M40 37l2 2"/>')
ICONS["audio-sound-fx"] = svg('<path d="M6 18h7l11-9v30l-11-9H6z"/><path d="M35 10l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" stroke-width="2.5"/><path d="M40 30v6M37 33h6" stroke-width="2.5"/>')

# ---------- video production ----------

ICONS["film"] = svg('<rect x="6" y="4" width="36" height="40" rx="3"/><path d="M14 4v40M34 4v40M6 14h8M6 24h8M6 34h8M34 14h8M34 24h8M34 34h8"/>')
ICONS["film-reel"] = svg('<circle cx="22" cy="24" r="18"/><circle cx="22" cy="13" r="4"/><circle cx="22" cy="35" r="4"/><circle cx="11" cy="24" r="4"/><circle cx="33" cy="24" r="4"/><path d="M40 24v18h6"/>')
ICONS["ring-light"] = svg('<circle cx="24" cy="18" r="14"/><circle cx="24" cy="18" r="7"/><path d="M24 32v12M16 44h16"/>')
ICONS["softbox"] = svg('<path d="M6 6h24l10 8v8l-10 8H6z"/><path d="M30 6v24"/><path d="M18 30v8M12 44l6-6 6 6"/>')
ICONS["light"] = svg('<path d="M17 33a12 12 0 1 1 14 0v4H17z"/><path d="M19 42h10"/><path d="M24 2v3M41 10l-2 2M7 10l2 2M45 24h-3M3 24h3"/>')
ICONS["scissors"] = svg('<circle cx="11" cy="12" r="6"/><circle cx="11" cy="36" r="6"/><path d="M16 15l26 20M16 33L42 13"/>')
ICONS["clip"] = svg('<rect x="4" y="10" width="40" height="28" rx="3"/><path d="M20 17v14l11-7z"/><path d="M4 30l8-6M44 18l-8 6" stroke-dasharray="3 4"/>')
ICONS["layers"] = svg('<path d="M24 5L4 15l20 10 20-10z"/><path d="M4 24l20 10 20-10M4 33l20 10 20-10"/>')
ICONS["transition"] = svg('<rect x="4" y="8" width="24" height="20" rx="3"/><rect x="20" y="20" width="24" height="20" rx="3"/><path d="M10 40l4-4-4-4M38 8l-4 4 4 4" stroke-width="2.5"/>')
ICONS["scene-switch"] = svg('<rect x="4" y="6" width="17" height="13" rx="2"/><rect x="27" y="29" width="17" height="13" rx="2"/><path d="M30 12h8a4 4 0 0 1 4 4v7M18 36h-8a4 4 0 0 1-4-4v-7"/><path d="M38 19l4 4 4-4M10 29l-4-4-4 4"/>')
ICONS["zoom-in"] = svg('<circle cx="21" cy="21" r="14"/><path d="M31 31l12 12M21 15v12M15 21h12"/>')
ICONS["crop"] = svg('<path d="M12 4v28a4 4 0 0 0 4 4h28"/><path d="M4 12h28a4 4 0 0 1 4 4v28"/>')
ICONS["eye"] = svg('<path d="M3 24s8-14 21-14 21 14 21 14-8 14-21 14S3 24 3 24z"/><circle cx="24" cy="24" r="6"/>')
ICONS["eye-off"] = svg('<path d="M18.6 11A20 20 0 0 1 24 10c13 0 21 14 21 14a32 32 0 0 1-5.4 7M12 14.5C6.4 18.3 3 24 3 24s8 14 21 14a20 20 0 0 0 9-2"/><path d="M5 5l38 38M20 20a6 6 0 0 0 8.5 8.5"/>')
ICONS["green-screen"] = svg('<rect x="4" y="4" width="40" height="34" rx="3"/><circle cx="24" cy="17" r="5"/><path d="M14 38v-1a10 9.5 0 0 1 20 0v1"/><path d="M14 44h20"/>')
ICONS["palette"] = svg('<path d="M24 4a20 20 0 0 0 0 40c3 0 4-2 3-4.5s0-5.5 3.5-5.5H36a8 8 0 0 0 8-8C44 13 35 4 24 4z"/><circle cx="14" cy="22" r="2.5" fill="#fff" stroke="none"/><circle cx="19" cy="13" r="2.5" fill="#fff" stroke="none"/><circle cx="29" cy="12" r="2.5" fill="#fff" stroke="none"/><circle cx="36" cy="19" r="2.5" fill="#fff" stroke="none"/>')
ICONS["effects"] = svg('<path d="M20 6l3.5 9.5L33 19l-9.5 3.5L20 32l-3.5-9.5L7 19l9.5-3.5z"/><path d="M36 28l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" stroke-width="2.5"/><path d="M37 6v8M33 10h8"/>')
ICONS["screenshot"] = svg('<path d="M4 14V8a4 4 0 0 1 4-4h6M34 4h6a4 4 0 0 1 4 4v6M44 34v6a4 4 0 0 1-4 4h-6M14 44H8a4 4 0 0 1-4-4v-6"/><circle cx="24" cy="25" r="6"/><path d="M16 17h3l2-3h6l2 3h3v16H16z"/>')
ICONS["broadcast"] = svg('<path d="M24 22v22M17 44h14"/><circle cx="24" cy="18" r="4" fill="#fff" stroke="none"/><path d="M16 10a11 11 0 0 0 0 16M32 10a11 11 0 0 1 0 16M10 4a19 19 0 0 0 0 28M38 4a19 19 0 0 1 0 28"/>')

# ---------- community ----------

ICONS["chat"] = svg('<path d="M8 6h32a4 4 0 0 1 4 4v20a4 4 0 0 1-4 4H20l-10 9v-9H8a4 4 0 0 1-4-4V10a4 4 0 0 1 4-4z"/><path d="M13 16h22M13 24h14"/>')
ICONS["audience"] = svg('<circle cx="24" cy="15" r="6"/><path d="M13 40a11 11 0 0 1 22 0"/><circle cx="10" cy="19" r="4.5"/><path d="M3 38a8 8 0 0 1 8.5-8"/><circle cx="38" cy="19" r="4.5"/><path d="M45 38a8 8 0 0 0-8.5-8"/>')
ICONS["heart"] = svg('<path d="M24 42S5 30.5 5 17.5A10.5 10.5 0 0 1 24 11.5a10.5 10.5 0 0 1 19 6C43 30.5 24 42 24 42z"/>')
ICONS["star"] = svg('<path d="M24 4l6.2 12.6 13.8 2-10 9.8 2.4 13.8L24 35.7l-12.4 6.5L14 28.4 4 18.6l13.8-2z"/>')
ICONS["bell"] = svg('<path d="M12 34V21a12 12 0 0 1 24 0v13l4 4H8z"/><path d="M20 42a4 4 0 0 0 8 0M24 5v4"/>')
ICONS["gift"] = svg('<rect x="5" y="16" width="38" height="9" rx="2"/><path d="M8 25v17h32V25M24 16v26"/><path d="M24 16c-3-8-12-9-12-3s12 3 12 3zM24 16c3-8 12-9 12-3s-12 3-12 3z"/>')
ICONS["lightning"] = svg('<path d="M27 3L9 27h13l-3 18 18-24H24z"/>')
ICONS["trophy"] = svg('<path d="M14 6h20v10a10 10 0 0 1-20 0z"/><path d="M14 9H6v3a7 7 0 0 0 8 7M34 9h8v3a7 7 0 0 1-8 7M24 26v9M15 43h18l-2-8H17z"/>')
ICONS["poll"] = svg('<rect x="4" y="6" width="40" height="36" rx="4"/><path d="M12 16h12M12 24h22M12 32h8" stroke-width="4"/>')

# ---------- more cameras ----------

ICONS["ptz-camera"] = svg('<path d="M14 42h20M24 42v-5"/><path d="M12 37h24l-3-6H15z"/><rect x="11" y="6" width="26" height="22" rx="11"/><circle cx="24" cy="17" r="5.5"/><circle cx="24" cy="17" r="1.5" fill="#fff" stroke="none"/>')
ICONS["webcam-clip"] = svg('<rect x="8" y="8" width="32" height="15" rx="7.5"/><circle cx="24" cy="15.5" r="4"/><circle cx="34" cy="15.5" r="1.3" fill="#fff" stroke="none"/><path d="M24 23v5M10 28h28M10 28v6h28"/>')
ICONS["camera-tripod"] = svg('<rect x="8" y="5" width="22" height="16" rx="3"/><path d="M30 10l9-4.5v15L30 16"/><path d="M19 21v6M19 27l-10 16M19 27l10 16M19 27v16"/>')
ICONS["phone-tripod"] = svg('<rect x="16" y="3" width="16" height="24" rx="3.5"/><path d="M22 6.5h4"/><path d="M24 27v5M24 32l-10 11M24 32l10 11M24 32v11"/>')
ICONS["multicam"] = svg('<rect x="3" y="7" width="20" height="14" rx="3"/><path d="M23 12l6-3v10l-6-3"/><rect x="19" y="27" width="20" height="14" rx="3"/><path d="M39 32l6-3v10l-6-3"/><path d="M8 27v6a4 4 0 0 0 4 4h3"/>')
ICONS["action-cam"] = svg('<rect x="5" y="11" width="38" height="28" rx="5"/><circle cx="17" cy="25" r="7.5"/><circle cx="17" cy="25" r="2.5"/><path d="M30 18h7M30 24h7M10 7h8"/>')

# ---------- smart home and power ----------

ICONS["smart-plug"] = svg('<rect x="9" y="6" width="30" height="36" rx="8"/><path d="M19 17v6M29 17v6M19 31a5 5 0 0 0 10 0"/><circle cx="34" cy="11.5" r="1.3" fill="#fff" stroke="none"/>')
ICONS["smart-bulb"] = svg('<path d="M17 33a12 12 0 1 1 14 0v4H17z"/><path d="M19 42h10"/><path d="M19.5 20.5a6.5 6.5 0 0 1 9 0M17 17a10 10 0 0 1 14 0M22.5 24a2 2 0 0 1 3 0"/>')
ICONS["lamp"] = svg('<path d="M16 4h16l6 16H10z"/><path d="M24 20v19M14 44h20M24 39v5"/><path d="M30 26l3 3" stroke-width="2.5"/>')
ICONS["power"] = svg('<path d="M24 4v18"/><path d="M14 10a17 17 0 1 0 20 0"/>')
ICONS["fan"] = svg('<circle cx="24" cy="22" r="18"/><circle cx="24" cy="22" r="3"/><path d="M24 19c0-6 2-9 6-9M27 23c5 3 7 6 5 9M21 23c-5 3-9 3-10 0"/><path d="M16 40l-2 4h20l-2-4"/>')
ICONS["wifi"] = svg('<path d="M4 18a28 28 0 0 1 40 0M10 25a19.5 19.5 0 0 1 28 0M16.5 32a10 10 0 0 1 15 0"/><circle cx="24" cy="39" r="2" fill="#fff" stroke="none"/>')

# ---------- hype and community ----------

ICONS["megaphone"] = svg('<path d="M6 19h7l20-11v32L13 29H6a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2z"/><path d="M13 29l3 12h6l-3-11"/><path d="M39 18a7 7 0 0 1 0 12"/>')
ICONS["party"] = svg('<path d="M6 42l8-26 18 18z"/><path d="M11 28l9 9M14 20l12 12"/><path d="M26 6c-2 4 2 6 0 10M33 15c4-2 6 2 10 0M30 12l3-3M38 22h4M40 6v4M38 8h4" stroke-width="2.5"/>')
ICONS["confetti"] = svg('<path d="M10 8l3 4M38 6l-2 5M24 4v5M6 22h5M42 22h-5M14 34l-4 4M34 34l4 4M24 39v5" stroke-width="2.5"/><circle cx="24" cy="23" r="9"/><path d="M19 20.5h.1M29 20.5h.1M19 26a6 4 0 0 0 10 0" stroke-width="2.5"/>')
ICONS["balloon"] = svg('<path d="M24 32c-8 0-13-7-13-15a13 13 0 0 1 26 0c0 8-5 15-13 15z"/><path d="M22 32l-2 3h8l-2-3M24 35c0 4-4 5-4 9"/>')
ICONS["rocket"] = svg('<path d="M24 4c8 6 11 15 9 26H15c-2-11 1-20 9-26z"/><circle cx="24" cy="17" r="3.5"/><path d="M15 26l-6 6v6l7-3M33 26l6 6v6l-7-3M20 34l1 8h6l1-8"/>')
ICONS["crown"] = svg('<path d="M6 36l-2-22 11 9 9-14 9 14 11-9-2 22z"/><path d="M8 42h32"/>')
ICONS["shield"] = svg('<path d="M24 4l17 6v12c0 11-8 18-17 22C15 40 7 33 7 22V10z"/><path d="M17 24l5 5 9-10"/>')
ICONS["fire"] = svg('<path d="M24 44c-9 0-15-6-15-14 0-7 5-11 7-17 3 4 4 7 4 9 2-3 2-9 0-18 10 5 17 15 17 26 0 8-6 14-13 14z"/><path d="M24 44c-4 0-6-3-6-6 0-4 3-6 4-9 3 3 8 5 8 10 0 3-3 5-6 5z"/>')
ICONS["thumbs-up"] = svg('<path d="M14 21l8-15c3 0 5 2 5 5l-1 8h12a4 4 0 0 1 4 4.6l-2.5 14A4 4 0 0 1 35.6 41H14z"/><rect x="5" y="21" width="9" height="20" rx="2"/>')
ICONS["door-exit"] = svg('<path d="M28 6H10a2 2 0 0 0-2 2v32a2 2 0 0 0 2 2h18"/><path d="M20 24h22M35 17l7 7-7 7"/>')
ICONS["calendar"] = svg('<rect x="5" y="8" width="38" height="34" rx="4"/><path d="M5 18h38M15 4v8M33 4v8"/><path d="M13 26h.1M20 26h.1M27 26h.1M34 26h.1M13 33h.1M20 33h.1M27 33h.1" stroke-width="3.5"/>')
ICONS["captions"] = svg('<rect x="3" y="9" width="42" height="30" rx="5"/><path d="M20 20a5 5 0 1 0 0 8M34 20a5 5 0 1 0 0 8"/>')

def main():
    os.makedirs(OUT, exist_ok=True)
    for name, data in ICONS.items():
        with open(os.path.join(OUT, f"deck-{name}.svg"), "w") as f:
            f.write(data)
    print(f"Wrote {len(ICONS)} icons to {os.path.normpath(OUT)}")

if __name__ == "__main__":
    main()
