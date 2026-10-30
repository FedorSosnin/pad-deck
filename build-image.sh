#!/bin/bash
# Builds a ready-to-flash SD card image for Pad Deck. One line, with your Wi-Fi name and password:
#
#   curl -fsSL https://raw.githubusercontent.com/FedorSosnin/pad-deck/main/build-image.sh | bash -s -- "WiFi name" "WiFi password"
#
# (or, in a copy of this repo:  ./build-image.sh "WiFi name" "WiFi password")
# Then flash pad-deck.img to a microSD card (Raspberry Pi Imager or balenaEtcher), put it in the Pad 7 and
# power on. The first start installs everything (about 10 minutes) and opens Pad Deck, which asks for the
# computer running OBS right on the touchscreen.
#
# Needs Docker (Docker Desktop on a Mac). Downloads BigTreeTech's official CB1 image once (~470 MB).
# Everything Pad Deck adds goes on the card's boot partition (/boot/pad-deck) plus one line in BTT's own
# boot script, which runs the installer until it has finished.
# Optional: PAD_DECK_SSH_KEY="ssh-ed25519 AAAA…" to log in to the Pad as "deck" over SSH.
set -euo pipefail

WIFI_NAME="${1:-}"
WIFI_PASSWORD="${2:-}"
SSH_KEY="${PAD_DECK_SSH_KEY:-}"
OUT="$PWD/pad-deck.img"
REPO="FedorSosnin/pad-deck"
BTT_RELEASE="V3.1.0"
BTT_IMAGE="CB1_Debian13_minimal_kernel7.0_20260430.img"
BTT_URL="https://github.com/bigtreetech/CB1/releases/download/$BTT_RELEASE"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[ -n "$WIFI_NAME" ] || die 'Usage: build-image.sh "WiFi name" "WiFi password"'
command -v docker >/dev/null && docker info >/dev/null 2>&1 || die "Docker isn't running. Install/start Docker Desktop, then try again."

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# --- The app: this copy of the repo, or (when run from the one-line command) the latest from GitHub
SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ ! -f "$SRC/server.py" ]; then
  say "Downloading Pad Deck…"
  mkdir "$WORK/src"
  curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.tar.gz" | tar -xz -C "$WORK/src" --strip-components 1 \
    || die "Couldn't download Pad Deck from github.com/$REPO."
  SRC="$WORK/src"
fi
cd "$SRC"

# --- BTT's image (downloaded once, then reused)
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/pad-deck"
mkdir -p "$CACHE"
if [ ! -s "$CACHE/$BTT_IMAGE.xz" ]; then
  say "Downloading BigTreeTech's CB1 image ($BTT_RELEASE, about 470 MB)…"
  curl -fL --progress-bar -o "$CACHE/$BTT_IMAGE.xz.part" "$BTT_URL/$BTT_IMAGE.xz"
  mv "$CACHE/$BTT_IMAGE.xz.part" "$CACHE/$BTT_IMAGE.xz"
fi
[ -s "$CACHE/$BTT_IMAGE.sha256" ] || curl -fsSL -o "$CACHE/$BTT_IMAGE.sha256" "$BTT_URL/$BTT_IMAGE.sha256"

# --- What goes on the card's boot partition, under /pad-deck
P="$WORK/pad-deck"
mkdir -p "$P/app" "$P/setup"
rsync -a --exclude .git --exclude .gitignore --exclude .claude --exclude docs --exclude setup --exclude layouts \
  --exclude layout.json --exclude settings.json --exclude 'build-image.sh' \
  --exclude 'README.md' --exclude '__pycache__' --exclude '*.img' ./ "$P/app/"
# Uploaded icons stay on your own Pad; the image ships the generated library only.
find "$P/app/icons" -type f ! -name 'deck-*' -delete
echo '{"columns": 4, "rows": 3, "pages": [{"buttons": []}]}' > "$P/app/layout.json"
cp setup/pad-setup.sh setup/pad-deck-*.service setup/pad-deck-no-analog.dts "$P/setup/"
cp setup/install-on-pad.sh "$P/install.sh"
printf 'SSH_KEY=%q\n' "${SSH_KEY:-}" > "$P/install.conf"

# system.cfg wants single-quoted values, with ' written as '\''
q() { printf "'%s'" "${1//\'/\'\\\'\'}"; }
export WIFI_SSID_Q WIFI_PASS_Q
WIFI_SSID_Q=$(q "$WIFI_NAME"); WIFI_PASS_Q=$(q "${WIFI_PASSWORD:-}")

say "Building the image (a minute or two)…"
docker run --rm -i -v "$WORK:/w" -v "$CACHE:/cache:ro" -e BTT_IMAGE -e WIFI_SSID_Q -e WIFI_PASS_Q \
  --env BTT_IMAGE="$BTT_IMAGE" debian:trixie bash -s <<'EOF'
set -euo pipefail
apt-get update -qq >/dev/null && apt-get install -y -qq mtools xz-utils fdisk >/dev/null 2>&1
cd /w
xz -dc "/cache/$BTT_IMAGE.xz" > pad-deck.img
expected=$(cut -d' ' -f1 "/cache/$BTT_IMAGE.sha256")
[ "$(sha256sum pad-deck.img | cut -d' ' -f1)" = "$expected" ] || { echo "The downloaded BTT image is damaged; delete ~/.cache/pad-deck and try again." >&2; exit 1; }
start=$(sfdisk -d pad-deck.img | awk -F'[=,]' '/pad-deck.img1 /{gsub(/ /,"",$2); print $2}')
BOOT="pad-deck.img@@$((start * 512))"
export MTOOLS_SKIP_CHECK=1

# Wi-Fi, Pad 7 screen, hostname
mtype -i "$BOOT" ::/system.cfg > system.cfg
awk '
  /^#?WIFI_SSID=/   { print "WIFI_SSID="   ENVIRON["WIFI_SSID_Q"]; next }
  /^#?WIFI_PASSWD=/ { print "WIFI_PASSWD=" ENVIRON["WIFI_PASS_Q"]; next }
  /^#?hostname=/    { print "hostname='\''pad-deck'\''"; next }
  /^#?BTT_PAD7=/    { print "BTT_PAD7='\''ON'\''"; next }
  { print }' system.cfg > system.new
mcopy -o -i "$BOOT" system.new ::/system.cfg

# Pad Deck itself, and the hook that runs its installer at boot
mcopy -s -o -i "$BOOT" pad-deck ::/
mtype -i "$BOOT" ::/scripts/btt_init.sh > btt_init.sh
grep -q pad-deck btt_init.sh || printf '\n# Pad Deck first-start installer (does nothing once installed)\n[ -f /boot/pad-deck/install.sh ] && bash /boot/pad-deck/install.sh &\n' >> btt_init.sh
mcopy -o -i "$BOOT" btt_init.sh ::/scripts/btt_init.sh
mdir -i "$BOOT" ::/pad-deck | tail -n +4 | head -8
EOF

mv "$WORK/pad-deck.img" "$OUT"
say "Done: $OUT"
echo "Flash it to a microSD card with Raspberry Pi Imager (Choose OS → Use custom) or balenaEtcher,"
echo "put the card in the Pad 7 and power it on. The first start takes about 10 minutes, then Pad Deck"
echo "asks for the computer running OBS on the touchscreen."
