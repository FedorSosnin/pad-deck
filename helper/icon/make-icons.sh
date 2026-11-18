#!/bin/bash
# Turns icon.svg into the app icons the builds use: AppIcon.icns (Mac) and PadDeckHelper.ico (Windows).
# Needs rsvg-convert and ImageMagick (brew install librsvg imagemagick). Run it again after changing icon.svg.
set -euo pipefail
cd "$(dirname "$0")"
TMP=$(mktemp -d); SET="$TMP/AppIcon.iconset"; mkdir "$SET"
for s in 16 32 128 256 512; do
  rsvg-convert -w $s icon.svg -o "$SET/icon_${s}x${s}.png"
  rsvg-convert -w $((s * 2)) icon.svg -o "$SET/icon_${s}x${s}@2x.png"
done
iconutil -c icns "$SET" -o AppIcon.icns
# Windows: the same drawing, but filling the square (Windows icons don't keep the Mac's margin).
for s in 16 24 32 48 64 256; do
  rsvg-convert -w $((s * 1024 / 824)) icon.svg -o "$TMP/big.png"
  magick "$TMP/big.png" -gravity center -crop ${s}x${s}+0+0 +repage "$TMP/win-$s.png"
done
magick "$TMP"/win-{16,24,32,48,64,256}.png PadDeckHelper.ico
rm -rf "$TMP"
echo "Made AppIcon.icns and PadDeckHelper.ico"

# Home screen icons for the web app (phones and tablets): full-bleed squares, like the Windows icon.
cd ..; cd ..
for s in 180 192 512; do
  rsvg-convert -w $((s * 1024 / 824)) helper/icon/icon.svg -o /tmp/pad-deck-icon-big.png
  magick /tmp/pad-deck-icon-big.png -gravity center -crop ${s}x${s}+0+0 +repage pwa/icon-$s.png
done
mv pwa/icon-180.png pwa/apple-touch-icon.png
rm -f /tmp/pad-deck-icon-big.png
echo "Made pwa/ home screen icons"
