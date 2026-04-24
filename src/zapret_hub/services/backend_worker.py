from __future__ import annotations

import multiprocessing as mp
import os
import queue
import tempfile
import uuid
from dataclasses import asdict
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal


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
        if running_ids == set(active_ids):
            for cid in active_ids:
                context.processes.stop_component(cid)
            mode = "disconnect"
        else:
            for cid in active_ids:
                if cid not in running_ids:
                    context.processes.start_component(cid)
            mode = "connect"
        result = {"mode": mode}
        result.update(_snapshot(context))
        return result

    if action == "start_enabled_components":
        context.processes.start_enabled_components()
        return _snapshot(context)

    if action == "apply_settings":
        before = context.settings.get()
        tg_before = (before.tg_proxy_host, int(before.tg_proxy_port), before.tg_proxy_secret)
        zapret_before = (before.zapret_ipset_mode, before.zapret_game_filter_mode, before.selected_zapret_general)
        theme_before = before.theme
        language_before = before.language
        autostart_before = bool(before.autostart_windows)
        context.settings.update(**payload)
        tg_after = (
            str(payload.get("tg_proxy_host", context.settings.get().tg_proxy_host)),
            int(payload.get("tg_proxy_port", context.settings.get().tg_proxy_port)),
            str(payload.get("tg_proxy_secret", context.settings.get().tg_proxy_secret)),
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
        installed = {item.id: item for item in context.mods.list_installed()}
        if mod_id not in installed:
            context.mods.install(mod_id)
            installed = {item.id: item for item in context.mods.list_installed()}
        if mod_id in installed:
            context.mods.set_enabled(mod_id, not installed[mod_id].enabled)
        result = {"mod_id": mod_id}
        result.update(_snapshot(context))
        return result

    if action == "move_mod":
        mod_id = str(payload.get("mod_id", "")).strip()
        direction = int(payload.get("direction", 0) or 0)
        if mod_id and direction:
            context.mods.move(mod_id, direction)
        return _snapshot(context)

    if action == "set_mod_emoji":
        mod_id = str(payload.get("mod_id", "")).strip()
        emoji = str(payload.get("emoji", "")).strip()
        if mod_id and emoji:
            context.mods.set_emoji(mod_id, emoji)
        return _snapshot(context)

    if action == "restart_zapret_if_running":
        states = {item.component_id: item for item in context.processes.list_states()}
        if states.get("zapret") and states["zapret"].status == "running":
            context.processes.stop_component("zapret")
            context.processes.start_component("zapret")
        return _snapshot(context)

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
