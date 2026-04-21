from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict
from datetime import datetime
from typing import Any

from zapret_hub.domain import LogEntry
from zapret_hub.services.storage import StorageManager


class LoggingManager:
    def __init__(self, storage: StorageManager) -> None:
        self.storage = storage
        self.log_path = self.storage.paths.logs_dir / "app.log"
        self.zapret_log_path = self.storage.paths.logs_dir / "zapret.log"
        self.tg_log_path = self.storage.paths.logs_dir / "tg_ws_proxy.log"
        self.reset_runtime_logs()

    def reset_runtime_logs(self) -> None:
        for path in (self.log_path, self.zapret_log_path, self.tg_log_path, self.storage.paths.logs_dir / "tg_worker_error.log"):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            except Exception:
                continue

    def source_log_path(self, source: str) -> str:
        source_id = (source or "").strip().lower()
        if source_id == "zapret":
            return str(self.zapret_log_path)
        if source_id == "tg-ws-proxy":
            return str(self.tg_log_path)
        return str(self.log_path)

    def log(self, level: str, message: str, **context: Any) -> LogEntry:
        entry = LogEntry(
            timestamp=datetime.utcnow().isoformat(),
            level=level.upper(),
            message=message,
            context=context,
        )
        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        return entry

    def read_entries(self) -> list[LogEntry]:
        if not self.log_path.exists():
            return []
        entries: list[LogEntry] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            entries.append(LogEntry(**payload))
        return entries

    def read_source_lines(self, source: str, limit: int = 250) -> list[str]:
        source_id = (source or "app").strip().lower()
        if source_id == "app":
            return self._format_entries(self.read_entries()[-limit:])
        if source_id == "zapret":
            lines = self._format_entries(
                [
                    entry
                    for entry in self.read_entries()
                    if str(entry.context.get("component_id", "") or "") == "zapret"
                    or "zapret" in entry.message.lower()
                ][-limit:]
            )
            lines.extend(self._read_plain_log_tail("zapret.log", limit=limit, heading=None))
            return lines[-limit:]
        if source_id == "tg-ws-proxy":
            entries = [
                entry
                for entry in self.read_entries()
                if str(entry.context.get("component_id", "") or "") == "tg-ws-proxy"
                or "tg ws proxy" in entry.message.lower()
                or "telegram proxy" in entry.message.lower()
            ]
            lines = self._format_entries(entries[-limit:])
            lines.extend(self._read_plain_log_tail("tg_ws_proxy.log", limit=limit, heading=None))
            lines.extend(self._read_plain_log_tail("tg_worker_error.log", limit=80, heading="tg_worker_error.log"))
            return lines[-limit:] if len(lines) > limit else lines
        if source_id == "all":
            combined = []
            combined.extend([f"[app] {line}" for line in self._format_entries(self.read_entries()[-limit:])])
            combined.extend([f"[zapret] {line}" for line in self._read_plain_log_tail("zapret.log", limit=limit, heading=None)])
            combined.extend([f"[tg-ws-proxy] {line}" for line in self._read_plain_log_tail("tg_ws_proxy.log", limit=limit, heading=None)])
            combined.extend([f"[tg-ws-proxy] {line}" for line in self._read_plain_log_tail("tg_worker_error.log", limit=80, heading=None)])
            return combined[-limit:]
        return self._format_entries(self.read_entries()[-limit:])

    def _format_entries(self, entries: list[LogEntry]) -> list[str]:
        lines: list[str] = []
        for entry in entries:
            context_suffix = f" {entry.context}" if entry.context else ""
            lines.append(f"[{entry.timestamp}] {entry.level}: {entry.message}{context_suffix}")
        return lines

    def _read_plain_log_tail(self, filename: str, *, limit: int, heading: str | None = None) -> list[str]:
        path = self.storage.paths.logs_dir / filename
        if not path.exists():
            return []
        tail = deque(maxlen=limit)
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip():
                tail.append(line)
        if not tail:
            return []
        prefix = [f"=== {heading or filename} ==="] if heading else []
        return prefix + list(tail)
