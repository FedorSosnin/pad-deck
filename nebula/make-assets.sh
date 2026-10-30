#!/bin/bash
# Renders what Pad Deck Lite (the Nebula Pad version) needs from the main app: the icon library as PNGs
# (Lite draws with PIL, which can't read SVG) and Barlow Semi Condensed as TTF. Needs Docker.
#   bash nebula/make-assets.sh       → nebula/assets/
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=nebula/assets
mkdir -p "$OUT/icons" "$OUT/fonts"
for w in Medium SemiBold; do
  [ -s "$OUT/fonts/BarlowSemiCondensed-$w.ttf" ] || curl -fsSL -o "$OUT/fonts/BarlowSemiCondensed-$w.ttf" \
    "https://raw.githubusercontent.com/google/fonts/main/ofl/barlowsemicondensed/BarlowSemiCondensed-$w.ttf"
done
cp carbon.jpg "$OUT/carbon.jpg"
docker run --rm -v "$PWD/icons:/in:ro" -v "$PWD/$OUT/icons:/out" debian:trixie bash -c '
  apt-get update -qq >/dev/null && apt-get install -y -qq librsvg2-bin >/dev/null 2>&1
  for f in /in/deck-*.svg; do n=$(basename "$f" .svg); rsvg-convert -w 96 -h 96 "$f" -o "/out/$n.png"; done
  ls /out | wc -l'
echo "Assets in $OUT"
