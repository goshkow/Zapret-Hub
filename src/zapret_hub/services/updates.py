from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
import json

from zapret_hub import __version__
from zapret_hub.domain import UpdateInfo
from zapret_hub.services.logging_service import LoggingManager
from zapret_hub.services.storage import StorageManager


class UpdatesManager:
    REPO_URL = "https://github.com/goshkow/Zapret-Hub"
    API_LATEST = "https://api.github.com/repos/goshkow/Zapret-Hub/releases/latest"

    def __init__(self, storage: StorageManager, logging: LoggingManager) -> None:
        self.storage = storage
        self.logging = logging

    def check_updates(self) -> list[UpdateInfo]:
        app_release = self.fetch_latest_application_release()
        app_status = UpdateInfo(
            target="application",
            current_version=__version__,
            latest_version=str(app_release.get("latest_version", __version__)),
            status=str(app_release.get("status", "error")),
            changelog=str(app_release.get("body", "")),
        )

        cache_file = self.storage.paths.cache_dir / "mods_index.json"
        cache_stamp = datetime.fromtimestamp(cache_file.stat().st_mtime).isoformat() if cache_file.exists() else "missing"
        updates = [
            app_status,
            UpdateInfo(
                target="mods-index",
                current_version=cache_stamp,
                latest_version=cache_stamp,
                status="ready",
                changelog="Local sample index loaded",
            ),
        ]
        self.logging.log("info", "Update check completed", items=len(updates), app_status=app_status.status)
        return updates

    def fetch_latest_application_release(self) -> dict[str, str]:
        try:
            request = Request(self.API_LATEST, headers={"User-Agent": f"ZapretHub/{__version__}"})
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as error:
            self.logging.log("warning", "Failed to fetch latest app release", error=str(error))
            return {
                "status": "error",
                "current_version": __version__,
                "latest_version": __version__,
                "error": str(error),
                "html_url": self.REPO_URL + "/releases",
            }

        latest_version = str(payload.get("tag_name") or payload.get("name") or "").strip().lstrip("v") or __version__
        html_url = str(payload.get("html_url") or (self.REPO_URL + "/releases")).strip()
        body = str(payload.get("body") or "").strip()
        asset = self._pick_release_asset(payload.get("assets") or [])
        status = "available" if self._version_key(latest_version) > self._version_key(__version__) else "up-to-date"
        return {
            "status": status,
            "current_version": __version__,
            "latest_version": latest_version,
            "html_url": html_url,
            "body": body,
            "asset_name": str(asset.get("name", "")) if asset else "",
            "asset_url": str(asset.get("browser_download_url", "")) if asset else "",
        }

    def prepare_update(self, release_info: dict[str, str]) -> dict[str, str]:
        asset_url = str(release_info.get("asset_url") or "").strip()
        asset_name = str(release_info.get("asset_name") or "").strip() or "update.zip"
        if not asset_url:
            raise ValueError("No downloadable asset was found for this platform.")

        temp_root = Path(tempfile.mkdtemp(prefix="zapret_hub_update_"))
        zip_path = temp_root / asset_name
        request = Request(asset_url, headers={"User-Agent": f"ZapretHub/{__version__}"})
        with urlopen(request, timeout=60) as response:
            zip_path.write_bytes(response.read())

        extract_root = temp_root / "payload"
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(extract_root)

        launch_exe = extract_root / "zapret_hub.exe"
        if not launch_exe.exists():
            raise FileNotFoundError("The downloaded update package does not contain zapret_hub.exe.")

        return {
            "temp_root": str(temp_root),
            "extract_root": str(extract_root),
            "launch_exe": str(launch_exe),
            "version": str(release_info.get("latest_version", "")),
        }

    def launch_update(self, prepared_update: dict[str, str]) -> None:
        extract_root = Path(prepared_update["extract_root"])
        install_root = self.storage.paths.install_root
        current_executable = Path(sys.executable).resolve()
        current_pid = os.getpid()
        script_root = Path(tempfile.gettempdir()) / "zapret_hub_updates"
        script_root.mkdir(parents=True, exist_ok=True)
        script_path = script_root / f"apply_update_{int(datetime.utcnow().timestamp() * 1000)}.ps1"
        launcher_path = script_root / f"apply_update_{int(datetime.utcnow().timestamp() * 1000)}.cmd"
        log_path = script_root / f"apply_update_{int(datetime.utcnow().timestamp() * 1000)}.log"

        script = textwrap.dedent(
            f"""
            $ErrorActionPreference = 'SilentlyContinue'
            $pidToWait = {current_pid}
            $src = '{str(extract_root).replace("'", "''")}'
            $dst = '{str(install_root).replace("'", "''")}'
            $launch = '{str(current_executable).replace("'", "''")}'
            $tempRoot = '{str(Path(prepared_update["temp_root"])).replace("'", "''")}'
            $logPath = '{str(log_path).replace("'", "''")}'
            $preserve = @('data', 'mods', 'configs', 'cache', 'logs', 'backups')
            $backupRoot = Join-Path '{str(script_root).replace("'", "''")}' ('preserve_' + [guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
            Add-Content -LiteralPath $logPath -Value ('[' + (Get-Date -Format s) + '] updater started')

            for ($i = 0; $i -lt 120; $i++) {{
              if (-not (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue)) {{ break }}
              Start-Sleep -Milliseconds 250
            }}

            if (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) {{
              Add-Content -LiteralPath $logPath -Value ('[' + (Get-Date -Format s) + '] forcing old process stop')
              Stop-Process -Id $pidToWait -Force -ErrorAction SilentlyContinue
              for ($i = 0; $i -lt 40; $i++) {{
                if (-not (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue)) {{ break }}
                Start-Sleep -Milliseconds 250
              }}
            }}

            New-Item -ItemType Directory -Path $dst -Force | Out-Null

            foreach ($item in $preserve) {{
              $dstItem = Join-Path $dst $item
              try {{
                if (Test-Path $dstItem) {{
                  Move-Item $dstItem (Join-Path $backupRoot $item) -Force
                }}
              }} catch {{}}
            }}
            Add-Content -LiteralPath $logPath -Value ('[' + (Get-Date -Format s) + '] preserved user dirs')

            Get-ChildItem -LiteralPath $dst -Force -ErrorAction SilentlyContinue | ForEach-Object {{
              try {{
                attrib -r -s -h $_.FullName /s /d *> $null
              }} catch {{}}
              try {{
                Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
              }} catch {{}}
            }}

            Get-ChildItem -LiteralPath $src -Force | ForEach-Object {{
              if ($preserve -contains $_.Name) {{ return }}
              $target = Join-Path $dst $_.Name
              if ($_.PSIsContainer) {{
                Copy-Item $_.FullName $target -Recurse -Force
              }} else {{
                Copy-Item $_.FullName $target -Force
              }}
            }}
            Add-Content -LiteralPath $logPath -Value ('[' + (Get-Date -Format s) + '] payload copied')

            foreach ($item in $preserve) {{
              $backupItem = Join-Path $backupRoot $item
              $target = Join-Path $dst $item
              if (Test-Path $backupItem) {{
                try {{
                  if (Test-Path $target) {{
                    Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
                  }}
                }} catch {{}}
                Move-Item $backupItem $target -Force
              }}
            }}
            Add-Content -LiteralPath $logPath -Value ('[' + (Get-Date -Format s) + '] user data restored')

            Start-Sleep -Milliseconds 400
            Start-Process -FilePath $launch
            Add-Content -LiteralPath $logPath -Value ('[' + (Get-Date -Format s) + '] relaunched app')
            Remove-Item $backupRoot -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 500
            Remove-Item '{str(script_path).replace("'", "''")}' -Force -ErrorAction SilentlyContinue
            """
        ).strip()
        script_path.write_text(script, encoding="utf-8")
        launcher = textwrap.dedent(
            f"""
            @echo off
            start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script_path}"
            exit /b 0
            """
        ).strip() + "\n"
        launcher_path.write_text(launcher, encoding="utf-8")

        startupinfo = None
        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        subprocess.Popen(
            [
                "cmd.exe",
                "/c",
                str(launcher_path),
            ],
            creationflags=creationflags,
            startupinfo=startupinfo,
            cwd=str(install_root),
        )
        self.logging.log("info", "App update launched", target_version=prepared_update.get("version", ""), source=str(extract_root))

    def _pick_release_asset(self, assets: list[dict[str, object]]) -> dict[str, object] | None:
        machine = platform.machine().lower()
        want_arm = "arm" in machine or "aarch64" in machine
        pattern = re.compile(r"portable.*win_arm64\.zip$", re.IGNORECASE) if want_arm else re.compile(r"portable.*win_x64\.zip$", re.IGNORECASE)
        for asset in assets:
            name = str(asset.get("name") or "")
            if pattern.search(name):
                return asset
        return None

    def _version_key(self, version: str) -> tuple[int, ...]:
        parts = re.findall(r"\d+", version)
        if not parts:
            return (0,)
        return tuple(int(part) for part in parts)
