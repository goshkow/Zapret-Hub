from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
from typing import Any

from zapret_hub.domain import AppPaths
from zapret_hub.services.autostart import AutostartManager
from zapret_hub.services.components import ProcessManager
from zapret_hub.services.diagnostics import DiagnosticsManager
from zapret_hub.services.files import FilesManager
from zapret_hub.services.logging_service import LoggingManager
from zapret_hub.services.merge import MergeEngine
from zapret_hub.services.mods import ModsManager
from zapret_hub.services.profiles import ProfilesManager
from zapret_hub.services.settings import SettingsManager
from zapret_hub.services.storage import StorageManager
from zapret_hub.services.updates import UpdatesManager


@dataclass(slots=True)
class ApplicationContext:
    paths: AppPaths
    storage: StorageManager
    settings: SettingsManager
    logging: LoggingManager
    autostart: AutostartManager
    processes: ProcessManager
    mods: ModsManager
    merge: MergeEngine
    diagnostics: DiagnosticsManager
    updates: UpdatesManager
    profiles: ProfilesManager
    files: FilesManager
    backend: Any | None = None


def bootstrap_application() -> ApplicationContext:
    if getattr(sys, "frozen", False):
        install_root = Path(sys.executable).resolve().parent
        resource_root = Path(getattr(sys, "_MEIPASS", install_root))
    else:
        install_root = Path.cwd()
        resource_root = install_root

    runtime_dir = install_root / "runtime"
    ui_assets_dir = install_root / "ui_assets"
    sample_data_dir = install_root / "sample_data"
    _hydrate_bundled_assets(
        resource_root=resource_root,
        install_root=install_root,
        runtime_dir=runtime_dir,
        ui_assets_dir=ui_assets_dir,
        sample_data_dir=sample_data_dir,
    )

    paths = AppPaths(
        install_root=install_root,
        core_dir=install_root / "core",
        runtime_dir=runtime_dir,
        configs_dir=install_root / "configs",
        default_packs_dir=install_root / "default_packs",
        mods_dir=install_root / "mods",
        merged_runtime_dir=install_root / "merged_runtime",
        backups_dir=install_root / "backups",
        cache_dir=install_root / "cache",
        logs_dir=install_root / "logs",
        data_dir=install_root / "data",
        ui_assets_dir=ui_assets_dir,
    )
    storage = StorageManager(paths)
    storage.ensure_layout()

    settings = SettingsManager(storage)
    logging = LoggingManager(storage)
    autostart = AutostartManager(logging)
    processes = ProcessManager(storage, logging, settings)
    merge = MergeEngine(storage, logging, settings)
    mods = ModsManager(storage, logging, merge, settings)
    diagnostics = DiagnosticsManager(storage, logging, processes, mods, merge)
    updates = UpdatesManager(storage, logging)
    profiles = ProfilesManager(storage)
    files = FilesManager(storage, settings)

    return ApplicationContext(
        paths=paths,
        storage=storage,
        settings=settings,
        logging=logging,
        autostart=autostart,
        processes=processes,
        mods=mods,
        merge=merge,
        diagnostics=diagnostics,
        updates=updates,
        profiles=profiles,
        files=files,
        backend=None,
    )


def _hydrate_bundled_assets(
    resource_root: Path,
    install_root: Path,
    runtime_dir: Path,
    ui_assets_dir: Path,
    sample_data_dir: Path,
) -> None:
    bundled_runtime = resource_root / "runtime"
    bundled_ui_assets = resource_root / "ui_assets"
    bundled_sample_data = resource_root / "sample_data"

    if bundled_runtime.exists() and not runtime_dir.exists():
        shutil.copytree(bundled_runtime, runtime_dir, dirs_exist_ok=True)

    if bundled_ui_assets.exists() and not ui_assets_dir.exists():
        shutil.copytree(bundled_ui_assets, ui_assets_dir, dirs_exist_ok=True)

    if bundled_sample_data.exists() and not sample_data_dir.exists():
        shutil.copytree(bundled_sample_data, sample_data_dir, dirs_exist_ok=True)
