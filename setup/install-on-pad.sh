#!/bin/bash
# Pad Deck first-start installer. build-image.sh puts it on the SD card's boot partition (/boot/pad-deck)
# and BTT's boot script runs it as root on every start until it has finished once. Progress shows on the
# Pad's screen; a full log goes to /boot/pad-deck/install.log (readable from any computer).
set -uo pipefail
HERE=/boot/pad-deck
[ -f "$HERE/.installed" ] && exit 0
exec >>"$HERE/install.log" 2>&1
echo "=== $(date) ==="

say() { echo "$*"; printf '\r\n   %s\r\n' "$*" > /dev/tty1 2>/dev/null; }
fail() {
  say "Setup couldn't finish: $*"
  tail -n 4 "$HERE/install.log" | grep -v '^$' | cut -c1-90 | while read -r l; do say "  $l"; done
  say "It will try again on the next start (full details: install.log on the SD card)."
  exit 1
}

# Take over the screen for progress messages (also skips Armbian's first-login wizard).
rm -f /root/.not_logged_in_yet
systemctl stop getty@tty1 serial-getty@ttyS0 2>/dev/null
setterm --cursor off --clear all > /dev/tty1 2>/dev/null
say "Pad Deck is setting itself up. This takes about 10 minutes, so leave it plugged in."

# shellcheck disable=SC1091
source "$HERE/install.conf"

# --- The deck user the app runs as (no password: log in with the SSH key, if one was given)
if ! id deck >/dev/null 2>&1; then
  groups=$(for g in sudo audio video render input; do getent group "$g" >/dev/null && printf '%s,' "$g"; done)
  useradd -m -s /bin/bash -G "${groups%,}" deck || fail "couldn't create the deck user"
  passwd -l deck >/dev/null
fi
echo "deck ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-pad-deck && chmod 440 /etc/sudoers.d/90-pad-deck
if [ -n "${SSH_KEY:-}" ]; then
  install -d -m 700 -o deck -g deck /home/deck/.ssh
  echo "$SSH_KEY" > /home/deck/.ssh/authorized_keys
  chown deck:deck /home/deck/.ssh/authorized_keys && chmod 600 /home/deck/.ssh/authorized_keys
fi

# --- Room for the software: BTT's image starts with its root filesystem the size of the image (~2.7 GB)
# and Armbian grows it to fill the card during the first start, so make sure that has happened.
free_mb() { df -m --output=avail / | tail -1 | tr -d ' '; }
if [ "$(free_mb)" -lt 1500 ]; then
  say "Making room on the SD card…"
  systemctl start armbian-resize-filesystem 2>/dev/null
  root=$(findmnt -n -o SOURCE /)
  resize2fs "$root" 2>/dev/null
  if [ "$(free_mb)" -lt 1500 ]; then
    [ -f "$HERE/.resized" ] && fail "the SD card is too small (use 8 GB or more)"
    touch "$HERE/.resized"
    say "Restarting once to finish making room (setup continues automatically)…"
    sleep 3; systemctl reboot; exit 0
  fi
fi

# --- Software
say "Waiting for Wi-Fi…"
for _ in $(seq 120); do getent hosts deb.debian.org >/dev/null && break; sleep 5; done
getent hosts deb.debian.org >/dev/null || fail "no internet. Check the Wi-Fi name and password the image was built with"
say "Installing software (the longest step)…"
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=600 update -q || fail "couldn't reach the software servers"
apt-get -o DPkg::Lock::Timeout=600 install -y -q --no-install-recommends \
  cage chromium libnss-mdns avahi-daemon sox libsox-fmt-mp3 device-tree-compiler python3 \
  || fail "software install failed"

# --- Pad Deck
say "Installing Pad Deck…"
rm -rf /home/deck/pad-deck
cp -r "$HERE/app" /home/deck/pad-deck && chown -R deck:deck /home/deck/pad-deck || fail "couldn't copy the app"
bash "$HERE/setup/pad-setup.sh" || fail "Pad setup failed"
systemctl set-default graphical.target

rm -f "$HERE/install.conf"
touch "$HERE/.installed"
say "All set! Restarting into Pad Deck…"
sleep 4
systemctl reboot
