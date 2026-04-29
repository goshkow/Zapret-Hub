from __future__ import annotations

import sys
from pathlib import Path


_PYTHON_EXECUTABLE_NAMES = {
    "python.exe",
    "pythonw.exe",
    "py.exe",
    "pypy.exe",
    "pypy3.exe",
}


def is_packaged_runtime() -> bool:
    if getattr(sys, "frozen", False):
        return True
    if "__compiled__" in globals():
        return True
    if getattr(sys, "nuitka_version", None):
        return True
    exe_path = Path(sys.executable)
    exe_name = exe_path.name.lower()
    if exe_path.suffix.lower() == ".exe" and exe_name not in _PYTHON_EXECUTABLE_NAMES:
        return True
    return False


def development_install_root(anchor: str | Path) -> Path:
    return Path(anchor).resolve().parents[2]


def packaged_install_root() -> Path:
    return Path(sys.executable).resolve().parent


def packaged_resource_root() -> Path:
    install_root = packaged_install_root()
    return Path(getattr(sys, "_MEIPASS", install_root))

