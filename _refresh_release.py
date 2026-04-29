from __future__ import annotations

import shutil
from pathlib import Path

from scripts.prepare_nuitka_release import main as prepare_nuitka_release_main


ROOT = Path(__file__).resolve().parent
INSTALLER_SRC = ROOT / "dist_installer" / "install_zaprethub_1.4.2_universal.exe"
RELEASE_DIR = ROOT / "release_1.4.2"


def main() -> None:
    prepare_nuitka_release_main()
    if INSTALLER_SRC.exists():
        RELEASE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(INSTALLER_SRC, RELEASE_DIR / INSTALLER_SRC.name)
    print("ok")


if __name__ == "__main__":
    main()
