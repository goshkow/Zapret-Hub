from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
import secrets
from typing import Any

from zapret_hub.domain import AppPaths
from zapret_hub.runtime_env import development_install_root, is_packaged_runtime, packaged_install_root, packaged_resource_root
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
    if is_packaged_runtime():
        install_root = packaged_install_root()
        resource_root = packaged_resource_root()
    else:
        install_root = development_install_root(__file__)
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
    _prime_first_run_state(settings, processes)

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


def build_startup_snapshot(context: ApplicationContext) -> dict[str, Any]:
    current = context.settings.get()
    general_options = list(context.processes.list_zapret_generals())
    if not str(current.selected_zapret_general or "").strip() and general_options:
        context.settings.update(selected_zapret_general=str(general_options[0]["id"]))
        current = context.settings.get()
    return {
        "components": [asdict(item) for item in context.processes.list_components()],
        "states": [asdict(item) for item in context.processes.list_states()],
        "settings": {
            "selected_zapret_general": current.selected_zapret_general,
            "favorite_zapret_generals": list(current.favorite_zapret_generals or []),
            "enabled_mod_ids": list(current.enabled_mod_ids or []),
        },
        "general_options": general_options,
    }


def _prime_first_run_state(settings: SettingsManager, processes: ProcessManager) -> None:
    current = settings.get()
    changes: dict[str, Any] = {}
    if not (current.tg_proxy_secret or "").strip():
        changes["tg_proxy_secret"] = secrets.token_hex(16)
    if str(current.zapret_ipset_mode or "").strip() not in {"loaded", "none", "any"}:
        changes["zapret_ipset_mode"] = "loaded"
    if str(current.zapret_game_filter_mode or "").strip() not in {"auto", "disabled", "all", "tcp", "udp"}:
        changes["zapret_game_filter_mode"] = "disabled"
    if changes:
        settings.update(**changes)


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
