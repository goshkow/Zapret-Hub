from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QImage


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    png_path = root / "ui_assets" / "icons" / "app.png"
    ico_path = root / "ui_assets" / "icons" / "app.ico"
    shell_png_path = root / "ui_assets" / "icons" / "installer_runtime_icon.png"
    shell_ico_path = root / "ui_assets" / "icons" / "app_shell.ico"
    image = QImage(str(png_path))
    if image.isNull():
        raise RuntimeError(f"Failed to load PNG icon: {png_path}")
    try:
        if not image.save(str(ico_path), "ICO"):
            raise RuntimeError(f"Failed to save ICO icon: {ico_path}")
    except Exception:
        if not image.save(str(ico_path), "ICO"):
            raise RuntimeError(f"Failed to save ICO icon: {ico_path}")
    shell_source = shell_png_path if shell_png_path.exists() else png_path
    try:
        base = Image.open(shell_source).convert("RGBA")
        sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (96, 96), (128, 128), (256, 256)]
        base.save(str(shell_ico_path), format="ICO", sizes=sizes)
    except Exception:
        shell_image = QImage(str(shell_source))
        if shell_image.isNull() or not shell_image.save(str(shell_ico_path), "ICO"):
            raise RuntimeError(f"Failed to save shell ICO icon: {shell_ico_path}")
    print(f"Synchronized app icons: runtime={ico_path} shell={shell_ico_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
