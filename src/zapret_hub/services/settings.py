from __future__ import annotations

import locale
import sys
from dataclasses import asdict

from zapret_hub.domain import AppSettings
from zapret_hub.services.storage import StorageManager

if sys.platform.startswith("win"):
    import winreg


class SettingsManager:
    def __init__(self, storage: StorageManager) -> None:
        self.storage = storage
        self._settings_path = self.storage.paths.data_dir / "settings.json"
        self._settings = self.load()

    def load(self) -> AppSettings:
        raw = self.storage.read_json(self._settings_path, default={}) or {}
        settings = AppSettings(**raw)
        changed = False

        # One-time migration: preserve defaults from components.json on first load only.
        if not bool(raw.get("component_selection_initialized", False)):
            components_raw = self.storage.read_json(self.storage.paths.data_dir / "components.json", default=[]) or []
            if isinstance(components_raw, list):
                enabled_defaults: list[str] = []
                autostart_defaults: list[str] = []
                for item in components_raw:
                    if not isinstance(item, dict):
                        continue
                    cid = str(item.get("id", "")).strip()
                    if not cid:
                        continue
                    if bool(item.get("enabled", False)):
                        enabled_defaults.append(cid)
                    if bool(item.get("autostart", False)):
                        autostart_defaults.append(cid)
                settings.enabled_component_ids = enabled_defaults
                settings.autostart_component_ids = autostart_defaults
            settings.component_selection_initialized = True
            changed = True

        if not raw.get("language"):
            settings.language = self._detect_system_language()
            changed = True

        if raw.get("theme") == "midnight":
            settings.theme = "night"
            changed = True

        if raw.get("theme") not in ("night", "midnight", "dark", "oled", "light", "light blue"):
            settings.theme = self._detect_system_theme()
            changed = True

        if raw.get("zapret_ipset_mode") not in {"loaded", "none", "any"}:
            settings.zapret_ipset_mode = "loaded"
            changed = True

        if raw.get("zapret_game_filter_mode") not in {"auto", "disabled", "all", "tcp", "udp"}:
            settings.zapret_game_filter_mode = "disabled"
            changed = True

        if changed:
            self.storage.write_json(self._settings_path, asdict(settings))
        return settings

    def get(self) -> AppSettings:
        return self._settings

    def reload(self) -> AppSettings:
        self._settings = self.load()
        return self._settings

    def update(self, **changes: object) -> AppSettings:
        for key, value in changes.items():
            setattr(self._settings, key, value)
        self.save()
        return self._settings

    def save(self) -> None:
        self.storage.write_json(self._settings_path, asdict(self._settings))

    def _detect_system_language(self) -> str:
        try:
            locale_name = (locale.getdefaultlocale()[0] or "").lower()  # type: ignore[call-arg]
        except Exception:
            locale_name = ""
        return "ru" if locale_name.startswith("ru") else "en"

    def _detect_system_theme(self) -> str:
        if not sys.platform.startswith("win"):
            return "dark"
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                0,
                winreg.KEY_READ,
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return "light" if int(value) == 1 else "dark"
        except Exception:
            return "dark"
