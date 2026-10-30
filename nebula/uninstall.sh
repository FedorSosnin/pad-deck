#!/bin/sh
# Removes Pad Deck Lite from the Nebula Pad's startup and brings Creality's own screen UI back.
# (Leaves /usr/data/pad-deck in place; delete it to remove the files too.)
[ -x /etc/init.d/S99zpad_deck ] && /etc/init.d/S99zpad_deck stop
rm -f /etc/init.d/S99zpad_deck
chmod 755 /etc/init.d/S99start_app
/etc/init.d/S99start_app start
echo "Creality's screen UI is back."
