"""Installs or updates Pad Deck Lite on a rooted Creality Nebula Pad. One line, from your computer
(the Nebula's password is "creality"), with the Wi-Fi it should use wherever it goes:

    ssh root@NEBULA-IP 'python3 -c "import urllib.request as u; exec(u.urlopen(\"https://raw.githubusercontent.com/FedorSosnin/pad-deck/main/nebula/get.py\").read())" "WiFi name" "WiFi password"'

More name/password pairs can follow (e.g. home and office). Pad Deck Lite has no keyboard, so this (or a
pad-deck-wifi.txt on a USB stick: name on line 1, password on line 2) is how the Nebula learns networks.

It downloads the latest Pad Deck from GitHub into /usr/data/pad-deck, keeps your keys, settings, saved
layouts and uploaded icons, switches Creality's screen UI off and starts Pad Deck Lite (nebula/install.sh).
Undo with:  ssh root@NEBULA-IP sh /usr/data/pad-deck/uninstall.sh
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


def add_wifi(ssid, password, connect=False):
    """Save a Wi-Fi network in wpa_supplicant (kept across restarts). Joins it now only if `connect`."""
    def wpa(*args):
        return subprocess.run(["wpa_cli", "-i", "wlan0", *args], capture_output=True, text=True, timeout=15).stdout
    ids = {l.split("\t")[1]: l.split("\t")[0] for l in wpa("list_networks").splitlines()[1:] if "\t" in l}
    nid = ids.get(ssid) or wpa("add_network").strip().splitlines()[-1]
    wpa("set_network", nid, "ssid", ssid.encode().hex())        # hex: any characters are safe
    if password:
        psk = hashlib.pbkdf2_hmac("sha1", password.encode(), ssid.encode(), 4096, 32).hex()
        wpa("set_network", nid, "psk", psk)
        wpa("set_network", nid, "key_mgmt", "WPA-PSK")
    else:
        wpa("set_network", nid, "key_mgmt", "NONE")
    wpa("set_network", nid, "scan_ssid", "1")
    wpa("enable_network", nid)
    wpa("save_config")
    if connect:
        wpa("select_network", nid)
        wpa("enable_network", "all")
        wpa("save_config")


SOURCE = os.environ.get("PAD_DECK_TARBALL", "https://github.com/FedorSosnin/pad-deck/archive/refs/heads/main.tar.gz")
D = "/usr/data/pad-deck"
# Not part of the app on the Nebula (or yours to keep): never copied over.
SKIP = {".git", ".github", ".gitignore", "docs", "setup", "nebula", "layouts", "layout.json", "settings.json",
        "build-image.sh", "README.md"}

print("Downloading Pad Deck…", flush=True)
data = urllib.request.urlopen(SOURCE, timeout=180).read()
tmp = tempfile.mkdtemp(dir="/usr/data")
try:
    tarfile.open(fileobj=io.BytesIO(data)).extractall(tmp)
    src = os.path.join(tmp, os.listdir(tmp)[0])
    print("Installing…", flush=True)
    app = os.path.join(D, "app")
    os.makedirs(app, exist_ok=True)
    for name in os.listdir(src):
        if name in SKIP:
            continue
        s, t = os.path.join(src, name), os.path.join(app, name)
        if os.path.isdir(s):
            shutil.copytree(s, t, dirs_exist_ok=True)     # merges, so icons you uploaded stay
        else:
            shutil.copy2(s, t)
    lite = os.path.join(D, "lite")
    shutil.rmtree(lite, ignore_errors=True)
    os.makedirs(lite)
    shutil.copy2(os.path.join(src, "nebula", "deck_lite.py"), lite)
    shutil.copytree(os.path.join(src, "nebula", "assets"), os.path.join(lite, "assets"))
    for name in ("S99zpad_deck", "install.sh", "uninstall.sh"):
        shutil.copy2(os.path.join(src, "nebula", name), D)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
# Wi-Fi networks from the install line: saved for later, without dropping the connection this runs over.
wifi = sys.argv[1:]
status = subprocess.run(["wpa_cli", "-i", "wlan0", "status"], capture_output=True, text=True).stdout
current = dict(l.split("=", 1) for l in status.splitlines() if "=" in l).get("ssid")
for i in range(0, len(wifi) - 1, 2):
    if wifi[i] == current:                                       # re-saving it could drop this very session
        print("Wi-Fi network %s: already connected" % wifi[i], flush=True)
        continue
    add_wifi(wifi[i], wifi[i + 1])
    print("Saved Wi-Fi network: %s" % wifi[i], flush=True)
subprocess.run(["sh", os.path.join(D, "install.sh")], check=True)
print("Done. Set up OBS and your keys from a browser at http://<this Nebula's IP>:8099", flush=True)
