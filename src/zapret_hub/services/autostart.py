from __future__ import annotations

import subprocess
import sys
import winreg
from pathlib import Path

from zapret_hub.services.logging_service import LoggingManager


class AutostartManager:
    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    APP_NAME = "ZapretHub"
    TASK_NAME = "ZapretHub"
    LEGACY_APP_NAMES = ("Zapret Hub", "zapret_hub")

    def __init__(self, logging: LoggingManager) -> None:
        self.logging = logging

    def is_enabled(self) -> bool:
        return self._task_exists()

    def set_enabled(self, enabled: bool) -> None:
        command = self._build_command()
        self._remove_legacy_run_entries()
        self._delete_task()
        if enabled:
            self._create_task(command)
        self.logging.log("info", "Windows autostart changed", enabled=enabled, command=command if enabled else "")

    def _build_command(self) -> str:
        executable = Path(sys.executable)
        if executable.suffix.lower() == ".exe" and executable.name.lower() != "python.exe":
            return f'"{executable}" --autostart-launch'
        main_module = Path(__file__).resolve().parents[1] / "main.py"
        return f'"{executable}" "{main_module}" --autostart-launch'

    def _task_exists(self) -> bool:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", self.TASK_NAME],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode == 0

    def _create_task(self, command: str) -> bool:
        proc = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/F",
                "/SC",
                "ONLOGON",
                "/RL",
                "HIGHEST",
                "/TN",
                self.TASK_NAME,
                "/TR",
                command,
            ],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            self.logging.log("warning", "Failed to create autostart task", error=(proc.stderr or proc.stdout or "").strip())
            return False
        return True

    def _delete_task(self) -> None:
        subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", self.TASK_NAME],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _remove_legacy_run_entries(self) -> None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                for name in (self.APP_NAME, *self.LEGACY_APP_NAMES):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            return
