from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import logging.handlers
import socket as _socket
import struct
import sys
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path

# Force Nuitka to bundle the stdlib and crypto dependencies that are only
# imported by the external tg-ws-proxy runtime package at worker startup.
from cryptography.hazmat.primitives.ciphers import Cipher as _Cipher
from cryptography.hazmat.primitives.ciphers import algorithms as _algorithms
from cryptography.hazmat.primitives.ciphers import modes as _modes
from zapret_hub.runtime_env import development_install_root, is_packaged_runtime, packaged_install_root, packaged_resource_root


def run_tg_ws_proxy_worker(host: str, port: int, secret: str, verbose: bool = False) -> int:
    if is_packaged_runtime():
        install_root = packaged_install_root()
        resource_root = packaged_resource_root()
    else:
        install_root = development_install_root(__file__)
        resource_root = install_root
    tg_repo = install_root / "runtime" / "tg-ws-proxy"
    if not tg_repo.exists():
        bundled_repo = resource_root / "runtime" / "tg-ws-proxy"
        if bundled_repo.exists():
            tg_repo = bundled_repo
    if not tg_repo.exists():
        print(f"tg-ws-proxy runtime directory not found: {tg_repo}", file=sys.stderr)
        return 2

    proxy_pkg_root = str(tg_repo)
    if proxy_pkg_root not in sys.path:
        sys.path.insert(0, proxy_pkg_root)

    try:
        from proxy import tg_ws_proxy
    except Exception as error:
        _write_worker_error(
            install_root,
            f"Failed to import tg-ws-proxy worker: {error}\n{traceback.format_exc()}",
            "tg_worker_error.log",
        )
        return 3

    argv = ["tg-ws-proxy", "--host", host, "--port", str(port)]
    if secret:
        argv.extend(["--secret", secret])
    if verbose:
        argv.append("--verbose")

    prev_argv = sys.argv
    try:
        sys.argv = argv
        try:
            tg_ws_proxy.main()
        except Exception as error:
            _write_worker_error(
                install_root,
                f"Worker crashed: {error}\n{traceback.format_exc()}",
                "tg_worker_error.log",
            )
            return 4
    finally:
        sys.argv = prev_argv
    return 0
def _resolve_install_root() -> Path:
    if is_packaged_runtime():
        return packaged_install_root()
    return development_install_root(__file__)


def _write_worker_error(install_root: Path, message: str, filename: str) -> None:
    try:
        logs = install_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / filename
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.utcnow().isoformat()}] {message}\n")
    except Exception:
        pass
