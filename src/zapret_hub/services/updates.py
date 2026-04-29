from __future__ import annotations

import os
import platform
import re
import ssl
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
import json
import certifi

from zapret_hub import __version__
from zapret_hub.domain import UpdateInfo
from zapret_hub.services.logging_service import LoggingManager
from zapret_hub.services.storage import StorageManager


class UpdatesManager:
    REPO_URL = "https://github.com/goshkow/Zapret-Hub"
    API_LATEST = "https://api.github.com/repos/goshkow/Zapret-Hub/releases/latest"
    API_RELEASES = "https://api.github.com/repos/goshkow/Zapret-Hub/releases"

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
            payload = self._request_json(self.API_RELEASES, timeout=10)
        except Exception as error:
            self.logging.log("warning", "Failed to fetch latest app release", error=str(error))
            friendly_error = str(error)
            if self._is_certificate_error(error):
                friendly_error = "Unable to verify GitHub certificates on this system. Please try again later."
            return {
                "status": "error",
                "current_version": __version__,
                "latest_version": __version__,
                "error": friendly_error,
                "html_url": self.REPO_URL + "/releases",
            }

        releases = self._normalize_release_entries(payload)
        if not releases:
            return {
                "status": "error",
                "current_version": __version__,
                "latest_version": __version__,
                "error": "No GitHub releases were found.",
                "html_url": self.REPO_URL + "/releases",
                "releases": [],
            }
        latest = releases[0]
        release_payload = latest["payload"]
        latest_version = str(latest["version"]).strip() or __version__
        html_url = str(latest["html_url"]).strip() or (self.REPO_URL + "/releases")
        body = str(latest["body"]).strip()
        asset = self._pick_release_asset(release_payload.get("assets") or [])
        status = "available" if self._version_key(latest_version) > self._version_key(__version__) else "up-to-date"
        newer_releases = [
            {
                "version": str(item["version"]),
                "body": str(item["body"]),
                "html_url": str(item["html_url"]),
                "is_latest": bool(idx == 0),
            }
            for idx, item in enumerate(releases)
            if self._version_key(str(item["version"])) > self._version_key(__version__)
        ]
        return {
            "status": status,
            "current_version": __version__,
            "latest_version": latest_version,
            "html_url": html_url,
            "body": body,
            "asset_name": str(asset.get("name", "")) if asset else "",
            "asset_url": str(asset.get("browser_download_url", "")) if asset else "",
            "releases": newer_releases,
        }

    def _request_json(self, url: str, *, timeout: int) -> object:
        payload = self._download_bytes(url, timeout=timeout)
        return json.loads(payload.decode("utf-8"))

    def _download_bytes(self, url: str, *, timeout: int) -> bytes:
        request = Request(url, headers={"User-Agent": f"ZapretHub/{__version__}"})
        errors: list[str] = []
        for label, context in self._ssl_context_chain():
            try:
                with urlopen(request, timeout=timeout, context=context) as response:
                    self.logging.log("info", "Update request succeeded", url=url, ssl_path=label)
                    return response.read()
            except URLError as error:
                errors.append(f"{label}: {error}")
                if not self._is_certificate_error(error):
                    break
                self.logging.log("warning", "Update request certificate fallback", url=url, ssl_path=label, error=str(error))
            except Exception as error:
                errors.append(f"{label}: {error}")
                if not self._is_certificate_error(error):
                    break
                self.logging.log("warning", "Update request certificate fallback", url=url, ssl_path=label, error=str(error))
        raise RuntimeError("; ".join(errors) or "Unknown update request failure")

    def _ssl_context_chain(self) -> list[tuple[str, ssl.SSLContext]]:
        chain = [("system", ssl.create_default_context())]
        certifi_context = ssl.create_default_context(cafile=certifi.where())
        chain.append(("certifi", certifi_context))
        return chain

    def _is_certificate_error(self, error: Exception) -> bool:
        if isinstance(error, ssl.SSLCertVerificationError):
            return True
        if isinstance(error, URLError):
            reason = getattr(error, "reason", None)
            if isinstance(reason, ssl.SSLCertVerificationError):
                return True
            if isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason).upper():
                return True
        return "CERTIFICATE_VERIFY_FAILED" in str(error).upper()

    def _normalize_release_entries(self, payload: object) -> list[dict[str, object]]:
        if not isinstance(payload, list):
            return []
        entries: list[dict[str, object]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if bool(item.get("draft")) or bool(item.get("prerelease")):
                continue
            version = str(item.get("tag_name") or item.get("name") or "").strip().lstrip("v")
            if not version:
                continue
            entries.append(
                {
                    "version": version,
                    "body": str(item.get("body") or ""),
                    "html_url": str(item.get("html_url") or self.REPO_URL + "/releases"),
                    "payload": item,
                }
            )
        entries.sort(key=lambda item: self._version_key(str(item["version"])), reverse=True)
        return entries

    def prepare_update(self, release_info: dict[str, str]) -> dict[str, str]:
        asset_url = str(release_info.get("asset_url") or "").strip()
        asset_name = str(release_info.get("asset_name") or "").strip() or "update.zip"
        if not asset_url:
            raise ValueError("No downloadable asset was found for this platform.")

        temp_root = Path(tempfile.mkdtemp(prefix="zapret_hub_update_"))
        zip_path = temp_root / asset_name
        zip_path.write_bytes(self._download_bytes(asset_url, timeout=60))

        extract_root = temp_root / "payload"
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(extract_root)

        payload_root = self._resolve_payload_root(extract_root)
        launch_exe = payload_root / "zapret_hub.exe"
        if not launch_exe.exists():
            raise FileNotFoundError("The downloaded update package does not contain zapret_hub.exe.")

        return {
            "temp_root": str(temp_root),
            "extract_root": str(payload_root),
            "launch_exe": str(launch_exe),
            "version": str(release_info.get("latest_version", "")),
        }

    def _resolve_payload_root(self, extract_root: Path) -> Path:
        direct_exe = extract_root / "zapret_hub.exe"
        if direct_exe.exists():
            return extract_root
        named_root = extract_root / "zapret_hub"
        if (named_root / "zapret_hub.exe").exists():
            return named_root
        for candidate in extract_root.iterdir():
            if candidate.is_dir() and (candidate / "zapret_hub.exe").exists():
                return candidate
        return extract_root

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

            function Remove-PathRobust([string]$targetPath) {{
              if (-not (Test-Path $targetPath)) {{ return $true }}
              for ($i = 0; $i -lt 6; $i++) {{
                try {{
                  attrib -r -s -h $targetPath /s /d *> $null
                }} catch {{}}
                try {{
                  Remove-Item $targetPath -Recurse -Force -ErrorAction Stop
                  return $true
                }} catch {{
                  Start-Sleep -Milliseconds 300
                }}
              }}
              $quarantineRoot = Join-Path $env:TEMP 'zapret_hub_update_quarantine'
              New-Item -ItemType Directory -Path $quarantineRoot -Force | Out-Null
              $moved = Join-Path $quarantineRoot ((Split-Path $targetPath -Leaf) + '_' + [guid]::NewGuid().ToString('N'))
              try {{
                Move-Item $targetPath $moved -Force -ErrorAction Stop
                return $true
              }} catch {{
                return $false
              }}
            }}

            function Add-UpdateLog([string]$message) {{
              try {{
                Add-Content -LiteralPath $logPath -Value ('[' + (Get-Date -Format s) + '] ' + $message)
              }} catch {{}}
            }}

            function Test-StandalonePayload([string]$sourceDir) {{
              return (Test-Path (Join-Path $sourceDir 'python311.dll')) -and
                     (Test-Path (Join-Path $sourceDir 'python3.dll')) -and
                     (Test-Path (Join-Path $sourceDir 'zapret_hub.exe'))
            }}

            function Test-InstalledStandalone([string]$targetDir) {{
              return (Test-Path (Join-Path $targetDir 'python311.dll')) -and
                     (Test-Path (Join-Path $targetDir 'python3.dll')) -and
                     (Test-Path (Join-Path $targetDir 'zapret_hub.exe'))
            }}

            function Overlay-Tree([string]$sourceDir, [string]$targetDir, [string[]]$preserveNames) {{
              New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
              $sourceItems = Get-ChildItem -LiteralPath $sourceDir -Force -ErrorAction SilentlyContinue
              $sourceNames = @{{}}
              foreach ($item in $sourceItems) {{
                $sourceNames[$item.Name] = $true
              }}
              Get-ChildItem -LiteralPath $targetDir -Force -ErrorAction SilentlyContinue | ForEach-Object {{
                if ($preserveNames -contains $_.Name) {{ return }}
                if (-not $sourceNames.ContainsKey($_.Name)) {{
                  [void](Remove-PathRobust $_.FullName)
                }}
              }}
              foreach ($item in $sourceItems) {{
                if ($preserveNames -contains $item.Name) {{ continue }}
                $dest = Join-Path $targetDir $item.Name
                if ($item.PSIsContainer) {{
                  Overlay-Tree $item.FullName $dest $preserveNames
                }} else {{
                  if (Test-Path $dest) {{
                    [void](Remove-PathRobust $dest)
                  }}
                  New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force | Out-Null
                  try {{
                    Copy-Item $item.FullName $dest -Force -ErrorAction Stop
                  }} catch {{
                    Add-UpdateLog ('copy failed: ' + $item.FullName + ' -> ' + $dest + ' | ' + $_.Exception.Message)
                  }}
                }}
              }}
            }}

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

            try {{ sc stop zapret *> $null }} catch {{}}
            try {{ sc delete zapret *> $null }} catch {{}}
            foreach ($image in @('zapret_hub.exe', 'TgWsProxy_windows.exe', 'winws.exe')) {{
              try {{ taskkill /F /T /IM $image *> $null }} catch {{}}
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

            $sourceIsStandalone = Test-StandalonePayload $src
            if ($sourceIsStandalone) {{
              Add-UpdateLog 'standalone payload detected'
              $oldInternal = Join-Path $dst '_internal'
              if (Test-Path $oldInternal) {{
                [void](Remove-PathRobust $oldInternal)
                Add-UpdateLog 'old _internal removed for standalone update'
              }}
            }}

            Overlay-Tree $src $dst $preserve
            Add-Content -LiteralPath $logPath -Value ('[' + (Get-Date -Format s) + '] payload copied')

            if ($sourceIsStandalone -and -not (Test-InstalledStandalone $dst)) {{
              Add-UpdateLog 'standalone validation failed after overlay, retrying top-level runtime files'
              foreach ($fileName in @('zapret_hub.exe', 'python311.dll', 'python3.dll')) {{
                $sourceFile = Join-Path $src $fileName
                $targetFile = Join-Path $dst $fileName
                if (Test-Path $sourceFile) {{
                  [void](Remove-PathRobust $targetFile)
                  try {{
                    Copy-Item $sourceFile $targetFile -Force -ErrorAction Stop
                    Add-UpdateLog ('runtime file copied: ' + $fileName)
                  }} catch {{
                    Add-UpdateLog ('runtime file copy failed: ' + $fileName + ' | ' + $_.Exception.Message)
                  }}
                }}
              }}
            }}

            foreach ($item in $preserve) {{
              $backupItem = Join-Path $backupRoot $item
              $target = Join-Path $dst $item
              if (Test-Path $backupItem) {{
                try {{
                  if (Test-Path $target) {{
                    [void](Remove-PathRobust $target)
                  }}
                }} catch {{}}
                Move-Item $backupItem $target -Force
              }}
            }}
            Add-Content -LiteralPath $logPath -Value ('[' + (Get-Date -Format s) + '] user data restored')

            if ($sourceIsStandalone -and -not (Test-InstalledStandalone $dst)) {{
              Add-UpdateLog 'standalone validation failed, aborting relaunch to avoid broken install'
              exit 2
            }}

            Start-Sleep -Milliseconds 400
            $launch = Join-Path $dst 'zapret_hub.exe'
            Start-Process -FilePath $launch -WorkingDirectory $dst
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
