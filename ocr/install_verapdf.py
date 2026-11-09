"""Install the veraPDF command line validator (PDF/A) without user interaction.

    python install_verapdf.py TARGET_DIR

Downloads the pinned release, checks its SHA-256, runs the IzPack installer in
automated mode and runs veraPDF once, so its config directory exists – the
service later runs as a user without write access to TARGET_DIR. Needs Java.
Used by the Dockerfile and by CI (same version in both).

To update: change VERSION and SHA256 (sha256sum of the installer zip from
https://software.verapdf.org/releases/).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

VERSION = "1.30.3"
SHA256 = "b512bf24945503a33fc10738ecffc1f2dccaaf8593a448e94ee9ac86a9559a59"
URL = f"https://software.verapdf.org/releases/{VERSION.rsplit('.', 1)[0]}/verapdf-greenfield-{VERSION}-installer.zip"

AUTO_INSTALL = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.izforge.izpack.panels.htmlhello.HTMLHelloPanel id="welcome"/>
    <com.izforge.izpack.panels.target.TargetPanel id="install_dir">
        <installpath>{target}</installpath>
    </com.izforge.izpack.panels.target.TargetPanel>
    <com.izforge.izpack.panels.packs.PacksPanel id="sdk_pack_select">
        <pack index="0" name="veraPDF GUI" selected="true"/>
        <pack index="1" name="veraPDF Batch files" selected="true"/>
        <pack index="2" name="veraPDF Validation model" selected="false"/>
        <pack index="3" name="veraPDF Documentation" selected="false"/>
        <pack index="4" name="veraPDF Sample Plugins" selected="false"/>
    </com.izforge.izpack.panels.packs.PacksPanel>
    <com.izforge.izpack.panels.install.InstallPanel id="install"/>
    <com.izforge.izpack.panels.finish.FinishPanel id="finish"/>
</AutomatedInstallation>
"""


def main(target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "installer.zip"
        with urllib.request.urlopen(URL, timeout=120) as response:  # noqa: S310 - fixed https URL
            archive.write_bytes(response.read())
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != SHA256:
            sys.exit(f"veraPDF {VERSION}: checksum mismatch ({digest})")
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
        installer = next(Path(tmp).glob("*/verapdf-izpack-installer-*.jar"))
        config = Path(tmp) / "auto-install.xml"
        config.write_text(AUTO_INSTALL.format(target=escape(str(target.resolve()))), encoding="utf-8")
        subprocess.run(["java", "-jar", str(installer), str(config)], check=True)
    launcher = target / ("verapdf.bat" if sys.platform == "win32" else "verapdf")
    subprocess.run([str(launcher), "--version"], check=True)  # creates TARGET_DIR/config


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
