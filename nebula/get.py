"""Installs or updates Pad Deck Lite on a rooted Creality Nebula Pad. One line, from your computer
(the Nebula's password is "creality"):

    ssh root@NEBULA-IP 'python3 -c "import urllib.request as u; exec(u.urlopen(\"https://raw.githubusercontent.com/FedorSosnin/pad-deck/main/nebula/get.py\").read())"'

It downloads the latest Pad Deck from GitHub into /usr/data/pad-deck, keeps your keys, settings, saved
layouts and uploaded icons, switches Creality's screen UI off and starts Pad Deck Lite (nebula/install.sh).
Undo with:  ssh root@NEBULA-IP sh /usr/data/pad-deck/uninstall.sh
"""
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

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
subprocess.run(["sh", os.path.join(D, "install.sh")], check=True)
print("Done. Set up OBS and your keys from a browser at http://<this Nebula's IP>:8099", flush=True)
