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
        merged_root = self.storage.paths.merged_runtime_dir
        visible_runtime = merged_root / "zapret"
        visible_status = self._check_zapret_runtime_tree(visible_runtime, visible=True)
        if visible_status is not None:
            return visible_status

        active_candidates: list[Path] = []
        if merged_root.exists():
            active_candidates = sorted(
                (path for path in merged_root.glob("active_zapret*") if path.is_dir()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        for candidate in active_candidates:
            active_status = self._check_zapret_runtime_tree(candidate, visible=False)
            if active_status is not None:
                return active_status

        materialized_lists = merged_root / "_materialized_lists" / "lists"
        if materialized_lists.exists():
            list_files = [
                materialized_lists / "list-general.txt",
                materialized_lists / "list-exclude.txt",
                materialized_lists / "ipset-all.txt",
                materialized_lists / "ipset-exclude.txt",
            ]
            if any(path.exists() for path in list_files):
                return DiagnosticResult(
                    "Merged runtime",
                    "ok",
                    "Materialized lists are available",
                    {"path": str(materialized_lists)},
                )

        state = self.merge.get_state()
        if state is None:
            base_runtime = self.storage.paths.runtime_dir / "zapret-discord-youtube"
            if base_runtime.exists():
                return DiagnosticResult(
                    "Merged runtime",
                    "ok",
                    "Base Zapret runtime is available; merged runtime will be built on start",
                    {"path": str(base_runtime)},
                )
            return DiagnosticResult("Merged runtime", "warning", "Merged runtime has not been built yet")
        merged_path = Path(state.merged_path)
        if not merged_path.exists():
            return DiagnosticResult(
                "Merged runtime",
                "warning",
                "Merged runtime will be rebuilt when Zapret starts",
                {"path": state.merged_path},
            )
        return DiagnosticResult("Merged runtime", "ok", "Legacy merged config is available", {"path": state.merged_path})

    def _check_zapret_runtime_tree(self, root: Path, *, visible: bool) -> DiagnosticResult | None:
        if not root.exists():
            return None

        missing = []
        if not (root / "bin" / "winws.exe").exists():
            missing.append("bin/winws.exe")
        if not (root / "lists").exists():
            missing.append("lists")
        if not any(root.glob("general*.bat")):
            missing.append("general*.bat")

        if missing:
            return DiagnosticResult(
                "Merged runtime",
                "warning",
                f"Merged Zapret runtime is incomplete: {', '.join(missing)}",
                {"path": str(root), "missing": missing},
            )

        message = "Merged Zapret runtime is available" if visible else "Active Zapret runtime is available"
        return DiagnosticResult("Merged runtime", "ok", message, {"path": str(root)})
