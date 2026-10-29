#!/bin/bash
# One-time (re-runnable) Pad configuration. Run on the Pad as deck:  bash pad-setup.sh
# Expects the packages from the first install: cage chromium fonts-roboto-slab libnss-mdns avahi-daemon.
set -euo pipefail

# --- Invisible mouse cursor: the touchscreen never moves a pointer, so hide it at the compositor level.
THEME=/usr/share/icons/pad-invisible
sudo mkdir -p "$THEME/cursors"
sudo python3 - "$THEME/cursors/default" <<'EOF'
import struct, sys
# Xcursor file with a single 1x1 fully transparent image (nominal size 24).
img = struct.pack("<9I", 36, 0xFFFD0002, 24, 1, 1, 1, 0, 0, 0) + struct.pack("<I", 0)
data = b"Xcur" + struct.pack("<3I", 16, 0x10000, 1) + struct.pack("<3I", 0xFFFD0002, 24, 28) + img
open(sys.argv[1], "wb").write(data)
EOF
# Cover every cursor name any installed theme has, so nothing falls back to a visible arrow.
for name in $(ls /usr/share/icons/*/cursors/ 2>/dev/null | grep -v -e ':$' -e '^$' -e '^default$' | sort -u) \
            left_ptr arrow top_left_arrow pointer hand2 text xterm watch wait progress crosshair grab grabbing; do
  sudo ln -sfn default "$THEME/cursors/$name"
done
printf '[Icon Theme]\nName=pad-invisible\n' | sudo tee "$THEME/index.theme" >/dev/null
# Apps that ignore XCURSOR_THEME use the "default" theme: point that at the invisible one too.
printf '[Icon Theme]\nInherits=pad-invisible\n' | sudo tee /usr/share/icons/default/index.theme >/dev/null

# --- Vibration motor: enable the overlay and let the deck user pulse it.
if ! grep -qE "^overlays=.*pad7_vibration" /boot/armbianEnv.txt; then
  sudo sed -i -E 's/^overlays=(.*)$/overlays=\1 pad7_vibration/' /boot/armbianEnv.txt
fi
echo 'ACTION=="add", SUBSYSTEM=="leds", KERNEL=="pad7_vibration", RUN+="/bin/chmod 666 /sys%p/brightness"' \
  | sudo tee /etc/udev/rules.d/90-pad-deck-vibration.rules >/dev/null

# --- Top RGB LEDs (OBS connected / on air): BTT's ws2812 overlay on PC14, writable by the deck user.
if ! grep -qE "^overlays=.*ws2812" /boot/armbianEnv.txt; then
  sudo sed -i -E 's/^overlays=(.*)$/overlays=\1 ws2812/' /boot/armbianEnv.txt
fi
sudo sed -i -E 's/^#?param_gpio_ws2812_pin=.*/param_gpio_ws2812_pin=PC14/' /boot/armbianEnv.txt
echo 'KERNEL=="ws2812-led", MODE="0666"' | sudo tee /etc/udev/rules.d/90-pad-deck-leds.rules >/dev/null
[ -e /dev/ws2812-led ] && sudo chmod 666 /dev/ws2812-led

# --- Audio: turn off the analog codec, which would otherwise set the shared audio clock wrong for HDMI.
sudo mkdir -p /boot/overlay-user
sudo dtc -q -@ -I dts -O dtb -o /boot/overlay-user/pad-deck-no-analog.dtbo /tmp/pad-deck-no-analog.dts
if ! grep -q "^user_overlays=.*pad-deck-no-analog" /boot/armbianEnv.txt; then
  if grep -q "^user_overlays=" /boot/armbianEnv.txt; then
    sudo sed -i -E 's/^user_overlays=(.*)$/user_overlays=\1 pad-deck-no-analog/' /boot/armbianEnv.txt
  else
    echo "user_overlays=pad-deck-no-analog" | sudo tee -a /boot/armbianEnv.txt >/dev/null
  fi
fi

# --- Audio: send sound to HDMI (the screen board's speaker), like BTT's own click script.
# sox plays BTT's click.mp3 for the speaker test (Menu → Sound & Feel).
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends sox libsox-fmt-mp3 >/dev/null
# 44.1 kHz on purpose: this kernel's AHUB driver gets the 48 kHz clock wrong (plays ~25% slow, so
# sounds arrive late and bunched up); 44.1 kHz comes out exact.
# The screen's HDMI receiver mutes whenever the audio stream stops and takes a moment to unmute,
# which swallows short clicks. So the default device is a dmix on HDMI that a keepalive service
# feeds silence forever; Chromium's clicks mix into an already-running stream.
sudo tee /etc/asound.conf >/dev/null <<'EOF'
pcm.hdmimix {
  type dmix
  ipc_key 4455
  ipc_perm 0666
  slave { pcm "hw:HDMI,0" rate 44100 format S16_LE channels 2 period_size 1024 buffer_size 4096 }
}
pcm.!default { type plug slave.pcm "hdmimix" }
ctl.!default { type hw card HDMI }
EOF
sudo install -m 644 /tmp/pad-deck-audio.service /etc/systemd/system/

# --- Services
sudo install -m 644 /tmp/pad-deck-server.service /tmp/pad-deck-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pad-deck-server pad-deck-kiosk pad-deck-audio >/dev/null 2>&1
sudo systemctl restart pad-deck-audio
echo "Pad setup done. Reboot to apply the vibration and LED overlays."
