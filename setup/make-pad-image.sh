#!/bin/bash
# Builds a ready-to-flash CB1 image for the Pad 7: Wi-Fi, Pad 7 screen support, hostname "pad-deck".
# Needs Docker running. Usage:  bash make-pad-image.sh [path/to/CB1_...img.xz]
set -euo pipefail

SRC="${1:-$HOME/Desktop/CB1_Debian13_minimal_kernel7.0_20260430.img.xz}"
OUT="$HOME/Desktop/pad-deck-cb1.img"
[ -f "$SRC" ] || { echo "Image not found: $SRC"; exit 1; }

read -r -p "Wi-Fi name (SSID): " SSID
read -r -s -p "Wi-Fi password: " PW; echo

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cp "$SRC" "$WORK/src.img.xz"

# system.cfg wants single-quoted values, with ' written as '\''
export ESSID="${SSID//\'/\'\\\'\'}" EPW="${PW//\'/\'\\\'\'}"

echo "Unpacking and editing the image (takes a minute)..."
docker run --rm -i -v "$WORK:/w" -e ESSID -e EPW debian:trixie bash -s <<'EOF'
set -euo pipefail
apt-get update -qq >/dev/null && apt-get install -y -qq mtools xz-utils >/dev/null 2>&1
cd /w
xz -d src.img.xz
BOOT="src.img@@$((8192 * 512))"   # BOOT (FAT) partition starts at sector 8192
mtype -i "$BOOT" ::/system.cfg > system.cfg
awk -v q="'" '
  /^#?WIFI_SSID=/   { print "WIFI_SSID="   q ENVIRON["ESSID"] q; next }
  /^#?WIFI_PASSWD=/ { print "WIFI_PASSWD=" q ENVIRON["EPW"]   q; next }
  /^#?hostname=/    { print "hostname="    q "pad-deck"      q; next }
  /^BTT_PAD7=/      { print "BTT_PAD7="    q "ON"            q; next }
  { print }' system.cfg > system.new
mcopy -o -i "$BOOT" system.new ::/system.cfg
echo "Settings written:"
mtype -i "$BOOT" ::/system.cfg | grep -E '^(WIFI_SSID|hostname|BTT_PAD7)='
mtype -i "$BOOT" ::/system.cfg | grep -q '^WIFI_PASSWD=' && echo "WIFI_PASSWD=(set)"
EOF

mv "$WORK/src.img" "$OUT"
echo "Done: $OUT  — flash this with balenaEtcher."
