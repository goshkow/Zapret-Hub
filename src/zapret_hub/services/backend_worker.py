from __future__ import annotations

import multiprocessing as mp
import os
import queue
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from zapret_hub.domain import FileRecord


def _snapshot(context) -> dict[str, Any]:
    settings = context.settings.get()
    return {
        "components": [asdict(item) for item in context.processes.list_components()],
        "states": [asdict(item) for item in context.processes.list_states()],
        "settings": {
            "selected_zapret_general": settings.selected_zapret_general,
            "favorite_zapret_generals": list(settings.favorite_zapret_generals or []),
            "enabled_mod_ids": list(settings.enabled_mod_ids or []),
            "zapret_ipset_mode": settings.zapret_ipset_mode,
            "zapret_game_filter_mode": settings.zapret_game_filter_mode,
            "autostart_windows": bool(settings.autostart_windows),
            "apply_update_on_next_launch": bool(getattr(settings, "apply_update_on_next_launch", False)),
        },
    }


def _mods_payload(context) -> dict[str, Any]:
    return {
        "index": context.mods.fetch_index(),
        "installed": list(context.mods.list_installed()),
    }


def _general_file_records(context) -> list[FileRecord]:
    records: list[FileRecord] = []
    seen: set[str] = set()
    for option in context.processes.list_zapret_generals():
        path = Path(str(option.get("path", "") or ""))
        if not path.exists() or not path.is_file():
            continue
        resolved = str(path.resolve()).lower()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            relative = str(path.relative_to(context.paths.install_root))
        except ValueError:
            relative = str(path)
        bundle = str(option.get("bundle", "") or "").strip()
        label = f"{bundle}/{path.name}" if bundle else relative
        records.append(FileRecord(path=str(path), relative_path=label, size=path.stat().st_size))
    return sorted(records, key=lambda item: item.relative_path.lower())


def _host_file_records(context) -> list[FileRecord]:
    path = context.files.ensure_local_hosts_file()
    try:
        relative = str(path.relative_to(context.paths.install_root))
    except ValueError:
        relative = str(path)
    return [FileRecord(path=str(path), relative_path=relative, size=path.stat().st_size)]


def _restart_zapret_if_running(context) -> None:
    states = {item.component_id: item for item in context.processes.list_states()}
    if states.get("zapret") and states["zapret"].status == "running":
        context.processes.stop_component("zapret")
        context.processes.start_component("zapret")


def _worker_main(task_queue, result_queue) -> None:
    from zapret_hub.bootstrap import bootstrap_application

    def _emit_progress(task_id: str, action: str, payload: dict[str, Any]) -> None:
        result_queue.put({"id": task_id, "action": action, "ok": True, "kind": "progress", "payload": payload})

    context = bootstrap_application()
    while True:
        task = task_queue.get()
        if not isinstance(task, dict):
            continue
        action = str(task.get("action", ""))
        if action == "shutdown":
            try:
                context.processes.stop_all()
            except Exception:
                pass
            result_queue.put({"id": task.get("id", ""), "action": action, "ok": True, "payload": {}})
            break
        task_id = str(task.get("id", ""))
        payload = task.get("payload", {}) or {}
        try:
            result = _run_action(context, action, payload, lambda progress: _emit_progress(task_id, action, progress))
            result_queue.put({"id": task_id, "action": action, "ok": True, "payload": result or {}})
        except Exception as error:
            result_queue.put({"id": task_id, "action": action, "ok": False, "error": str(error)})


def _run_action(context, action: str, payload: dict[str, Any], emit_progress: callable | None = None) -> dict[str, Any]:
    payload = {key: value for key, value in payload.items() if not str(key).startswith("_")}
    context.settings.reload()

    if action == "toggle_master_runtime":
        components = context.processes.list_components()
        states = {item.component_id: item for item in context.processes.list_states()}
        active_ids = [c.id for c in components if c.enabled]
        running_ids = {cid for cid in active_ids if states.get(cid) and states[cid].status == "running"}
        if running_ids:
            for cid in list(running_ids):
                context.processes.stop_component(cid)
            mode = "disconnect"
        else:
            for cid in active_ids:
                context.processes.start_component(cid)
            mode = "connect"
        result = {"mode": mode}
        result.update(_snapshot(context))
        return result

    if action == "load_startup_snapshot":
        current = context.settings.get()
        if not str(current.selected_zapret_general or "").strip():
            options = context.processes.list_zapret_generals()
            if options:
                context.settings.update(selected_zapret_general=str(options[0]["id"]))
        result = _snapshot(context)
        result.update(_mods_payload(context))
        result["general_options"] = list(context.processes.list_zapret_generals())
        return result

    if action == "load_components_payload":
        current = context.settings.get()
        options = context.processes.list_zapret_generals()
        if not str(current.selected_zapret_general or "").strip() and options:
            context.settings.update(selected_zapret_general=str(options[0]["id"]))
        result = _snapshot(context)
        result["general_options"] = options
        return result

    if action == "start_enabled_components":
        context.processes.start_enabled_components()
        return _snapshot(context)

    if action == "start_component":
        component_id = str(payload.get("component_id", "")).strip()
        if component_id:
            context.processes.start_component(component_id)
        return _snapshot(context)

    if action == "stop_component":
        component_id = str(payload.get("component_id", "")).strip()
        if component_id:
            context.processes.stop_component(component_id)
        return _snapshot(context)

    if action == "apply_settings":
        before = context.settings.get()
        tg_before = (
            before.tg_proxy_host,
            int(before.tg_proxy_port),
            before.tg_proxy_secret,
            before.tg_proxy_dc_ip,
            bool(before.tg_proxy_cfproxy_enabled),
            bool(before.tg_proxy_cfproxy_priority),
            before.tg_proxy_cfproxy_domain,
            before.tg_proxy_fake_tls_domain,
            int(before.tg_proxy_buf_kb),
            int(before.tg_proxy_pool_size),
        )
        zapret_before = (before.zapret_ipset_mode, before.zapret_game_filter_mode, before.selected_zapret_general)
        theme_before = before.theme
        language_before = before.language
        autostart_before = bool(before.autostart_windows)
        context.settings.update(**payload)
        tg_after = (
            str(payload.get("tg_proxy_host", context.settings.get().tg_proxy_host)),
            int(payload.get("tg_proxy_port", context.settings.get().tg_proxy_port)),
            str(payload.get("tg_proxy_secret", context.settings.get().tg_proxy_secret)),
            str(payload.get("tg_proxy_dc_ip", context.settings.get().tg_proxy_dc_ip)),
            bool(payload.get("tg_proxy_cfproxy_enabled", context.settings.get().tg_proxy_cfproxy_enabled)),
            bool(payload.get("tg_proxy_cfproxy_priority", context.settings.get().tg_proxy_cfproxy_priority)),
            str(payload.get("tg_proxy_cfproxy_domain", context.settings.get().tg_proxy_cfproxy_domain)),
            str(payload.get("tg_proxy_fake_tls_domain", context.settings.get().tg_proxy_fake_tls_domain)),
            int(payload.get("tg_proxy_buf_kb", context.settings.get().tg_proxy_buf_kb)),
            int(payload.get("tg_proxy_pool_size", context.settings.get().tg_proxy_pool_size)),
        )
        current = context.settings.get()
        zapret_after = (
            current.zapret_ipset_mode,
            current.zapret_game_filter_mode,
            current.selected_zapret_general,
        )
        states = {item.component_id: item for item in context.processes.list_states()}
        if tg_before != tg_after and states.get("tg-ws-proxy") and states["tg-ws-proxy"].status == "running":
            context.processes.stop_component("tg-ws-proxy")
            context.processes.start_component("tg-ws-proxy")
        if zapret_before != zapret_after and states.get("zapret") and states["zapret"].status == "running":
            context.processes.stop_component("zapret")
            context.processes.start_component("zapret")
        result = {
            "theme_changed": theme_before != context.settings.get().theme,
            "language_changed": language_before != context.settings.get().language,
            "autostart_changed": autostart_before != bool(context.settings.get().autostart_windows),
        }
        result.update(_snapshot(context))
        return result

    if action == "select_general":
        selected = str(payload.get("selected", "")).strip()
        if not selected:
            return {}
        settings = context.settings.get()
        settings.selected_zapret_general = selected
        context.settings.save()
        states = {item.component_id: item for item in context.processes.list_states()}
        if states.get("zapret") and states["zapret"].status == "running":
            context.processes.stop_component("zapret")
            context.processes.start_component("zapret")
        result = {"selected": selected}
        result.update(_snapshot(context))
        return result

    if action == "toggle_component_enabled":
        component_id = str(payload.get("component_id", "")).strip()
        if component_id:
            component = context.processes.toggle_component_enabled(component_id)
            result = {"component": asdict(component)}
            result.update(_snapshot(context))
            return result
        return {}

    if action == "toggle_component_autostart":
        component_id = str(payload.get("component_id", "")).strip()
        if component_id:
            component = context.processes.toggle_component_autostart(component_id)
            result = {"component": asdict(component)}
            result.update(_snapshot(context))
            return result
        return {}

    if action == "toggle_mod":
        mod_id = str(payload.get("mod_id", "")).strip()
        if not mod_id:
            return {}
        states = {item.component_id: item for item in context.processes.list_states()}
        zapret_was_running = bool(states.get("zapret") and states["zapret"].status == "running")
        installed = {item.id: item for item in context.mods.list_installed()}
        if mod_id not in installed:
            context.mods.install(mod_id)
            installed = {item.id: item for item in context.mods.list_installed()}
        if mod_id in installed:
            context.mods.set_enabled(mod_id, not installed[mod_id].enabled)
        try:
            context.files._invalidate_collection_cache()
            context.files.rebuild_materialized_collections()
        except Exception:
            pass
        if zapret_was_running:
            try:
                context.processes.stop_component("zapret")
                context.processes.start_component("zapret")
            except Exception:
                pass
        result = {"mod_id": mod_id}
        result.update(_snapshot(context))
        result.update(_mods_payload(context))
        return result

    if action == "install_mod":
        mod_id = str(payload.get("mod_id", "")).strip()
        if mod_id:
            context.mods.install(mod_id)
        result = {"mod_id": mod_id}
        result.update(_snapshot(context))
        result.update(_mods_payload(context))
        return result

    if action == "remove_mod":
        mod_id = str(payload.get("mod_id", "")).strip()
        if mod_id:
            context.mods.remove(mod_id)
        result = {"mod_id": mod_id}
        result.update(_snapshot(context))
        result.update(_mods_payload(context))
        return result

    if action == "import_mod_from_github":
        repo_url = str(payload.get("repo_url", "")).strip()
        previous_selected_general = str(payload.get("previous_selected_general", "")).strip()
        if repo_url:
            context.mods.import_from_github(repo_url)
            if previous_selected_general:
                context.settings.update(selected_zapret_general=previous_selected_general)
        result = {"repo_url": repo_url}
        result.update(_snapshot(context))
        result.update(_mods_payload(context))
        result["general_options"] = list(context.processes.list_zapret_generals())
        return result

    if action == "import_mod_from_paths":
        raw_paths = payload.get("paths", []) or []
        paths = [str(item).strip() for item in raw_paths if str(item).strip()]
        previous_selected_general = str(payload.get("previous_selected_general", "")).strip()
        if paths:
            context.mods.import_from_paths(paths)
            if previous_selected_general:
                context.settings.update(selected_zapret_general=previous_selected_general)
        result = {"paths": paths}
        result.update(_snapshot(context))
        result.update(_mods_payload(context))
        result["general_options"] = list(context.processes.list_zapret_generals())
        return result

    if action == "import_mod_from_path":
        path = str(payload.get("path", "")).strip()
        previous_selected_general = str(payload.get("previous_selected_general", "")).strip()
        if path:
            context.mods.import_from_path(path)
            if previous_selected_general:
                context.settings.update(selected_zapret_general=previous_selected_general)
        result = {"path": path}
        result.update(_snapshot(context))
        result.update(_mods_payload(context))
        result["general_options"] = list(context.processes.list_zapret_generals())
        return result

    if action == "move_mod":
        mod_id = str(payload.get("mod_id", "")).strip()
        direction = int(payload.get("direction", 0) or 0)
        if mod_id and direction:
            context.mods.move(mod_id, direction)
            try:
                context.files._invalidate_collection_cache()
                context.files.rebuild_materialized_collections()
            except Exception:
                pass
        result = _snapshot(context)
        result.update(_mods_payload(context))
        return result

    if action == "set_mod_emoji":
        mod_id = str(payload.get("mod_id", "")).strip()
        emoji = str(payload.get("emoji", "")).strip()
        if mod_id and emoji:
            context.mods.set_emoji(mod_id, emoji)
        result = _snapshot(context)
        result.update(_mods_payload(context))
        return result

    if action == "restart_zapret_if_running":
        _restart_zapret_if_running(context)
        return _snapshot(context)

    if action == "add_collection_values":
        collection_id = str(payload.get("collection_id", "")).strip()
        raw = str(payload.get("raw", "") or "")
        values = context.files.add_collection_values(collection_id, raw)
        _restart_zapret_if_running(context)
        result = _snapshot(context)
        result["files_payload"] = {
            "mode_index": 1,
            "collection_id": collection_id,
            "collection_values": list(values),
        }
        return result

    if action == "remove_collection_value":
        collection_id = str(payload.get("collection_id", "")).strip()
        value = str(payload.get("value", "") or "")
        values = context.files.remove_collection_value(collection_id, value)
        _restart_zapret_if_running(context)
        result = _snapshot(context)
        result["files_payload"] = {
            "mode_index": 1,
            "collection_id": collection_id,
            "collection_values": list(values),
        }
        return result

    if action == "reset_user_overrides":
        collection_id = str(payload.get("collection_id", "")).strip()
        context.files.reset_user_overrides()
        _restart_zapret_if_running(context)
        values = context.files.read_collection(collection_id) if collection_id else []
        result = _snapshot(context)
        result["files_payload"] = {
            "mode_index": 1,
            "collection_id": collection_id,
            "collection_values": list(values),
        }
        return result

    if action == "load_files_payload":
        mode_index = int(payload.get("mode_index", 0) or 0)
        collection_id = str(payload.get("collection_id", "")).strip()
        file_filter = str(payload.get("file_filter", "all") or "all")
        records = None
        if mode_index == 2:
            if file_filter == "generals":
                records = _general_file_records(context)
            elif file_filter == "hosts":
                records = _host_file_records(context)
            else:
                records = context.files.list_files()
        return {
            "files_payload": {
                "mode_index": mode_index,
                "collection_id": collection_id,
                "file_filter": file_filter,
                "records": records,
                "collection_values": context.files.read_collection(collection_id) if mode_index == 1 else None,
            }
        }

    if action == "write_file_text":
        full_path = str(payload.get("path", "")).strip()
        content = str(payload.get("content", "") or "")
        if full_path:
            context.files.write_text(full_path, content)
        _restart_zapret_if_running(context)
        result = _snapshot(context)
        result["path"] = full_path
        return result

    if action == "rebuild_merge_runtime":
        context.merge.rebuild()
        result = _snapshot(context)
        result.update(_mods_payload(context))
        return result

    if action == "set_favorite_generals":
        favorites = [str(item).strip() for item in (payload.get("favorites", []) or []) if str(item).strip()]
        current = context.settings.get()
        current.favorite_zapret_generals = favorites
        context.settings.save()
        return _snapshot(context)

    if action == "set_general_autotest_done":
        done = bool(payload.get("done", True))
        current = context.settings.get()
        current.general_autotest_done = done
        context.settings.save()
        return _snapshot(context)

    if action == "run_general_diagnostics":
        cancel_path = str(payload.get("cancel_path", "") or "")
        results = context.processes.run_general_diagnostics(
            progress_callback=(
                lambda current, total, name: emit_progress(
                    {
                        "current": current,
                        "total": total,
                        "name": name,
                    }
                )
                if emit_progress is not None
                else None
            ),
            stop_callback=(lambda: bool(cancel_path) and os.path.exists(cancel_path)),
        )
        return {"results": results}

    if action == "run_general_diagnostic_single":
        general_id = str(payload.get("general_id", "")).strip()
        cancel_path = str(payload.get("cancel_path", "") or "")
        result = context.processes.run_single_general_diagnostic(
            general_id,
            progress_callback=(
                lambda current, total, name: emit_progress(
                    {"current": current, "total": total, "name": name}
                )
                if emit_progress is not None
                else None
            ),
            stop_callback=(lambda: bool(cancel_path) and os.path.exists(cancel_path)),
        )
        return result

    if action == "run_settings_diagnostics":
        cancel_path = str(payload.get("cancel_path", "") or "")
        result = context.processes.run_settings_diagnostics(
            progress_callback=(
                lambda current, total, name: emit_progress(
                    {"current": current, "total": total, "name": name}
                )
                if emit_progress is not None
                else None
            ),
            stop_callback=(lambda: bool(cancel_path) and os.path.exists(cancel_path)),
        )
        return result

    if action == "update_zapret_runtime":
        result = context.processes.update_zapret_runtime()
        result.update(_snapshot(context))
        return result

    if action == "update_tg_ws_proxy_runtime":
        result = context.processes.update_tg_ws_proxy_runtime()
        result.update(_snapshot(context))
        return result

    return {}


class BackendWorkerClient(QObject):
    task_finished = Signal(dict)
    task_failed = Signal(dict)
    task_progress = Signal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        ctx = mp.get_context("spawn")
        self._task_queue = ctx.Queue()
        self._result_queue = ctx.Queue()
        self._process = ctx.Process(target=_worker_main, args=(self._task_queue, self._result_queue), daemon=True)
        self._process.start()
        self._cancel_paths: dict[str, str] = {}
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(40)
        self._poll_timer.timeout.connect(self._poll_results)
        self._poll_timer.start()

    def submit(self, action: str, payload: dict[str, Any] | None = None) -> str:
        task_id = uuid.uuid4().hex
        task_payload = dict(payload or {})
        if action in {"run_general_diagnostics", "run_general_diagnostic_single", "run_settings_diagnostics"}:
            cancel_path = os.path.join(tempfile.gettempdir(), f"zapret_hub_cancel_{task_id}.flag")
            try:
                if os.path.exists(cancel_path):
                    os.remove(cancel_path)
            except OSError:
                pass
            self._cancel_paths[task_id] = cancel_path
            task_payload["cancel_path"] = cancel_path
        self._task_queue.put({"id": task_id, "action": action, "payload": task_payload})
        return task_id

    def cancel(self, task_id: str) -> None:
        cancel_path = self._cancel_paths.get(task_id)
        if not cancel_path:
            return
        try:
            with open(cancel_path, "w", encoding="utf-8") as handle:
                handle.write("cancelled")
        except OSError:
            pass

    def _poll_results(self) -> None:
        while True:
            try:
                message = self._result_queue.get_nowait()
            except queue.Empty:
                break
            if str(message.get("kind", "")) == "progress":
                self.task_progress.emit(message)
                continue
            task_id = str(message.get("id", ""))
            cancel_path = self._cancel_paths.pop(task_id, None)
            if cancel_path:
                try:
                    if os.path.exists(cancel_path):
                        os.remove(cancel_path)
                except OSError:
                    pass
            if bool(message.get("ok")):
                self.task_finished.emit(message)
            else:
                self.task_failed.emit(message)

    def stop(self) -> None:
        try:
            self._task_queue.put({"id": uuid.uuid4().hex, "action": "shutdown", "payload": {}})
        except Exception:
            pass
        self._poll_timer.stop()
        if self._process.is_alive():
            self._process.join(timeout=3)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)

    def request_shutdown_background(self) -> None:
        try:
            self._task_queue.put({"id": uuid.uuid4().hex, "action": "shutdown", "payload": {}})
        except Exception:
            pass
        self._poll_timer.stop()
