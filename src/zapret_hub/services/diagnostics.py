from __future__ import annotations

import json
import time
from dataclasses import fields
from pathlib import Path

from zapret_hub.domain import DiagnosticResult
from zapret_hub.services.components import ProcessManager
from zapret_hub.services.logging_service import LoggingManager
from zapret_hub.services.merge import MergeEngine
from zapret_hub.services.mods import ModsManager
from zapret_hub.services.storage import StorageManager


class DiagnosticsManager:
    def __init__(
        self,
        storage: StorageManager,
        logging: LoggingManager,
        processes: ProcessManager,
        mods: ModsManager,
        merge: MergeEngine,
    ) -> None:
        self.storage = storage
        self.logging = logging
        self.processes = processes
        self.mods = mods
        self.merge = merge

    def run_all(self) -> list[DiagnosticResult]:
        results = [
            self._check_required_directories(),
            self._check_components(),
            self._check_mods(),
            self._check_merged_config(),
        ]
        self.logging.log("info", "Diagnostics executed", passed=sum(item.status == "ok" for item in results))
        return results

    def _check_required_directories(self) -> DiagnosticResult:
        missing = []
        for field_info in fields(self.storage.paths):
            name = field_info.name
            value = getattr(self.storage.paths, name)
            if isinstance(value, Path) and not value.exists():
                missing.append(name)
        if missing:
            return DiagnosticResult("Directories", "error", "Missing required directories", {"missing": missing})
        return DiagnosticResult("Directories", "ok", "All required directories exist")

    def _check_components(self) -> DiagnosticResult:
        components = self.processes.list_components()
        if not components:
            return DiagnosticResult("Components", "warning", "No components configured")
        return DiagnosticResult("Components", "ok", f"{len(components)} components configured")

    def _check_mods(self) -> DiagnosticResult:
        installed = self.mods.list_installed()
        enabled = [item.id for item in installed if item.enabled]
        return DiagnosticResult("Mods", "ok", f"Installed: {len(installed)}, enabled: {len(enabled)}", {"enabled": enabled})

    def _check_merged_config(self) -> DiagnosticResult:
        state = self.merge.get_state()
        if state is None:
            return DiagnosticResult("Merged runtime", "warning", "Merged runtime has not been built yet")
        merged_path = Path(state.merged_path)
        if not merged_path.exists():
            return DiagnosticResult("Merged runtime", "error", "Merged config file is missing", {"path": state.merged_path})
        return DiagnosticResult("Merged runtime", "ok", "Merged runtime is available", {"path": state.merged_path})
