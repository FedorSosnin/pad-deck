#!/bin/bash
# Copies the Pad's own data back to this Mac, so it's saved with the project (and the local preview matches):
# the live layout, saved layouts, press-feedback settings and any icons uploaded on the Pad.
# The library icons (deck-*.svg) come from setup/make-icons.py, so they aren't copied back.
set -euo pipefail
cd "$(dirname "$0")/.."
KEY="$HOME/.ssh/pad-deck"
PAD="deck@pad-deck.local"

rsync -a -e "ssh -i $KEY" "$PAD:pad-deck/layout.json" "$PAD:pad-deck/settings.json" ./
rsync -a --delete -e "ssh -i $KEY" "$PAD:pad-deck/layouts/" layouts/
rsync -a --exclude 'deck-*' -e "ssh -i $KEY" "$PAD:pad-deck/icons/" icons/
echo "Pulled layout, saved layouts, settings and uploaded icons from the Pad."
