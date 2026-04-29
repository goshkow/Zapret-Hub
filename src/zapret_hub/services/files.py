from __future__ import annotations

from pathlib import Path
import re
import json

from zapret_hub.domain.models import FileRecord
from zapret_hub.services.settings import SettingsManager
from zapret_hub.services.storage import StorageManager


class FilesManager:
    def __init__(self, storage: StorageManager, settings: SettingsManager | None = None) -> None:
        self.storage = storage
        self.settings = settings
        self._overrides_path = self.storage.paths.data_dir / "file_overrides.json"
        self._collection_cache: dict[str, tuple[tuple[tuple[str, int, int], ...], list[str]]] = {}
        self.allowed_roots = [
            self.storage.paths.configs_dir,
            self.storage.paths.default_packs_dir,
            self.storage.paths.mods_dir,
            self.storage.paths.runtime_dir,
            self.storage.paths.merged_runtime_dir,
            self.storage.paths.data_dir,
        ]

    def list_files(self) -> list[FileRecord]:
        records: list[FileRecord] = []
        for root in self.allowed_roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and self._is_editable_file(path):
                    records.append(
                        FileRecord(
                            path=str(path),
                            relative_path=str(path.relative_to(self.storage.paths.install_root)),
                            size=path.stat().st_size,
                        )
                    )
        return sorted(records, key=lambda item: item.relative_path.lower())

    def list_user_collections(self) -> list[dict[str, str]]:
        return [
            {"id": item["id"], "title": item["title"], "path": str(self._collection_path(str(item["id"])))}
            for item in self._collection_definitions()
        ]

    def local_hosts_path(self) -> Path:
        return self.storage.paths.runtime_dir / "zapret-discord-youtube" / ".service" / "hosts"

    def read_collection(self, kind: str) -> list[str]:
        layered_values = self._read_cached_layered_collection_values(kind)
        values: list[str] = []
        seen: set[str] = set()
        for value in layered_values:
            if value in seen:
                continue
            seen.add(value)
            values.append(value)
        for value in self._managed_collection_values(kind):
            if value in seen:
                continue
            seen.add(value)
            values.insert(0, value)
        return values

    def write_collection(self, kind: str, values: list[str]) -> None:
        self._invalidate_collection_cache(kind)
        managed = set(self._managed_collection_values(kind))
        normalized = [item for item in self.normalize_collection_values(kind, values) if item not in managed]
        base_set = set(self._read_base_collection_values(kind))
        overrides = self._read_overrides()
        overrides[kind] = {
            "added": [item for item in normalized if item not in base_set],
            "removed": [item for item in self.normalize_collection_values(kind, list(base_set - set(normalized))) if item not in managed],
        }
        self._write_overrides(overrides)
        self._materialize_user_collection(kind)

    def add_collection_values(self, kind: str, raw_text: str) -> list[str]:
        current = self.read_collection(kind)
        incoming = self.normalize_collection_values(kind, self._split_raw_values(kind, raw_text))
        seen = set(current)
        for value in incoming:
            if value in seen:
                continue
            seen.add(value)
            current.append(value)
        self.write_collection(kind, current)
        return current

    def remove_collection_value(self, kind: str, value: str) -> list[str]:
        if self.is_managed_collection_value(kind, value):
            return self.read_collection(kind)
        current = [item for item in self.read_collection(kind) if item != value]
        self.write_collection(kind, current)
        return current

    def reset_user_overrides(self) -> None:
        self._collection_cache.clear()
        self._write_overrides({})
        for kind in self._collection_definitions():
            self._materialize_user_collection(str(kind["id"]))

    def is_managed_collection_value(self, kind: str, value: str) -> bool:
        normalized = value.strip()
        return normalized in set(self._managed_collection_values(kind))

    def normalize_collection_values(self, kind: str, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            if not value:
                continue
            if kind in {"domains", "exclude_domains"}:
                value = self._normalize_domain(value)
            elif kind in {"all_ips", "ips"}:
                value = self._normalize_ip(value)
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    def _split_raw_values(self, kind: str, raw_text: str) -> list[str]:
        if kind in {"all_ips", "ips"}:
            parts = re.split(r"[\s,;]+", raw_text.strip())
            return [item for item in parts if item]
        prepared = raw_text.replace("\r", " ").replace("\n", " ")
        parts = re.split(r"[\s,;]+", prepared.strip())
        return [item for item in parts if item]

    def read_text(self, path: str) -> str:
        target = Path(path)
        self._guard(target)
        return target.read_text(encoding="utf-8")

    def write_text(self, path: str, content: str) -> None:
        target = Path(path)
        self._guard(target)
        target.write_text(content, encoding="utf-8")

    def _guard(self, path: Path) -> None:
        resolved = path.resolve()
        if not any(str(resolved).startswith(str(root.resolve())) for root in self.allowed_roots):
            raise ValueError(f"Path is outside allowed roots: {resolved}")

    def _collection_path(self, kind: str) -> Path:
        mapping = {
            "domains": self.storage.paths.configs_dir / "list-general-user.txt",
            "exclude_domains": self.storage.paths.configs_dir / "list-exclude-user.txt",
            "all_ips": self.storage.paths.configs_dir / "ipset-all-user.txt",
            "ips": self.storage.paths.configs_dir / "ipset-exclude-user.txt",
        }
        if kind not in mapping:
            raise ValueError(f"Unsupported collection kind: {kind}")
        return mapping[kind]

    def _ensure_collection_file(self, kind: str) -> None:
        path = self._collection_path(kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")

    def _collection_source_paths(self, kind: str) -> list[Path]:
        self._ensure_collection_file(kind)
        sources: list[Path] = []
        merged_lists = self._latest_merged_lists_dir()
        if merged_lists is not None:
            merged_path = self._merged_collection_path(kind, merged_lists)
            if merged_path.exists():
                sources.append(merged_path)
        else:
            runtime_lists = self.storage.paths.runtime_dir / "zapret-discord-youtube" / "lists"
            runtime_path = self._merged_collection_path(kind, runtime_lists)
            if runtime_path.exists():
                sources.append(runtime_path)
            for mod_lists in self._enabled_mod_list_dirs():
                mod_path = self._merged_collection_path(kind, mod_lists)
                if mod_path.exists():
                    sources.append(mod_path)
        user_path = self._collection_path(kind)
        if user_path.exists():
            sources.append(user_path)
        return sources

    def _read_base_collection_values(self, kind: str) -> list[str]:
        if self._latest_merged_lists_dir() is None:
            layered = self._read_layered_base_without_merged_runtime(kind)
            if layered is not None:
                return layered
        values: list[str] = []
        seen: set[str] = set()
        for path in self._collection_source_paths(kind):
            if path == self._collection_path(kind):
                continue
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                value = raw.strip()
                if not value or value.startswith("#"):
                    continue
                if value in {"domain.example.abc", "203.0.113.113/32"}:
                    continue
                if value in seen:
                    continue
                seen.add(value)
                values.append(value)
        return values

    def _read_layered_base_without_merged_runtime(self, kind: str) -> list[str] | None:
        conflict_pairs = {
            "domains": "exclude_domains",
            "exclude_domains": "domains",
            "all_ips": "ips",
            "ips": "all_ips",
        }
        opposite_kind = conflict_pairs.get(kind)
        if opposite_kind is None:
            return None
        runtime_lists = self.storage.paths.runtime_dir / "zapret-discord-youtube" / "lists"
        layers: list[Path] = []
        if runtime_lists.exists():
            layers.append(runtime_lists)
        mod_layers = list(self._enabled_mod_list_dirs())
        layers.extend(reversed(mod_layers))
        current_values: list[str] = []
        opposite_values: list[str] = []
        current_seen: set[str] = set()
        opposite_seen: set[str] = set()
        for layer in layers:
            self._apply_layered_collection_values(
                kind,
                self._merged_collection_path(kind, layer),
                current_values,
                current_seen,
                opposite_values,
                opposite_seen,
            )
            self._apply_layered_collection_values(
                opposite_kind,
                self._merged_collection_path(opposite_kind, layer),
                opposite_values,
                opposite_seen,
                current_values,
                current_seen,
            )
        return current_values

    def _apply_layered_collection_values(
        self,
        kind: str,
        path: Path,
        primary_values: list[str],
        primary_seen: set[str],
        opposite_values: list[str],
        opposite_seen: set[str],
    ) -> None:
        if not path.exists():
            return
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            value = raw.strip()
            if not value or value.startswith("#") or value in {"domain.example.abc", "203.0.113.113/32"}:
                continue
            normalized = self.normalize_collection_values(kind, [value])
            if not normalized:
                continue
            item = normalized[0]
            if item in opposite_seen:
                opposite_seen.remove(item)
                opposite_values[:] = [entry for entry in opposite_values if entry != item]
            if item in primary_seen:
                continue
            primary_seen.add(item)
            primary_values.append(item)

    def _read_layered_collection_values(self, kind: str) -> list[str]:
        base_values = self._read_base_collection_values(kind)
        overrides = self._read_overrides().get(kind, {})
        removed = set(self.normalize_collection_values(kind, list(overrides.get("removed", []) or [])))
        added = self.normalize_collection_values(kind, list(overrides.get("added", []) or []))
        result = [item for item in base_values if item not in removed]
        seen = set(result)
        for item in added:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result

    def _read_cached_layered_collection_values(self, kind: str) -> list[str]:
        signature = self._collection_signature(kind)
        cached = self._collection_cache.get(kind)
        if cached is not None and cached[0] == signature:
            return list(cached[1])
        values = self._read_layered_collection_values(kind)
        self._collection_cache[kind] = (signature, list(values))
        return values

    def _collection_signature(self, kind: str) -> tuple[tuple[str, int, int], ...]:
        signature: list[tuple[str, int, int]] = []
        for path in self._collection_source_paths(kind):
            try:
                stat = path.stat()
                signature.append((str(path), int(stat.st_mtime_ns), int(stat.st_size)))
            except OSError:
                signature.append((str(path), 0, 0))
        return tuple(signature)

    def _invalidate_collection_cache(self, kind: str | None = None) -> None:
        if kind is None:
            self._collection_cache.clear()
            return
        self._collection_cache.pop(kind, None)

    def _read_overrides(self) -> dict[str, dict[str, list[str]]]:
        raw = self.storage.read_json(self._overrides_path, default={}) or {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, list[str]]] = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            result[str(key)] = {
                "added": [str(item).strip() for item in list(value.get("added", []) or []) if str(item).strip()],
                "removed": [str(item).strip() for item in list(value.get("removed", []) or []) if str(item).strip()],
            }
        return result

    def _write_overrides(self, payload: dict[str, dict[str, list[str]]]) -> None:
        self.storage.write_json(self._overrides_path, payload)

    def _materialize_user_collection(self, kind: str) -> None:
        path = self._collection_path(kind)
        self._ensure_collection_file(kind)
        overrides = self._read_overrides().get(kind, {})
        content_values = self.normalize_collection_values(kind, list(overrides.get("added", []) or []))
        path.write_text("\n".join(content_values) + ("\n" if content_values else ""), encoding="utf-8")

    def _latest_merged_lists_dir(self) -> Path | None:
        merged_root = self.storage.paths.merged_runtime_dir
        if not merged_root.exists():
            return None
        candidates = [
            path / "lists"
            for path in merged_root.glob("active_zapret*")
            if path.is_dir() and (path / "lists").exists()
        ]
        if candidates:
            candidates.sort(key=lambda item: item.parent.stat().st_mtime, reverse=True)
            return candidates[0]
        materialized = merged_root / "_materialized_lists" / "lists"
        if materialized.exists():
            return materialized
        return None

    def rebuild_materialized_collections(self) -> None:
        materialized_root = self.storage.paths.merged_runtime_dir / "_materialized_lists"
        lists_dir = materialized_root / "lists"
        lists_dir.mkdir(parents=True, exist_ok=True)
        mapping = {
            "domains": "list-general.txt",
            "exclude_domains": "list-exclude.txt",
            "all_ips": "ipset-all.txt",
            "ips": "ipset-exclude.txt",
        }
        for kind, filename in mapping.items():
            values = self._read_layered_base_without_merged_runtime(kind) or []
            target = lists_dir / filename
            target.write_text("\n".join(values) + ("\n" if values else ""), encoding="utf-8")
        self._invalidate_collection_cache()

    def _merged_collection_path(self, kind: str, lists_dir: Path) -> Path:
        mapping = {
            "domains": lists_dir / "list-general.txt",
            "exclude_domains": lists_dir / "list-exclude.txt",
            "all_ips": lists_dir / "ipset-all.txt",
            "ips": lists_dir / "ipset-exclude.txt",
        }
        return mapping[kind]

    def _normalize_domain(self, value: str) -> str:
        prepared = value.strip().lower()
        if not prepared:
            return ""
        prepared = prepared.replace("https://", "").replace("http://", "")
        prepared = prepared.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()
        prepared = prepared.lstrip(".")
        if prepared.startswith("www."):
            prepared = prepared[4:]
        if not prepared or " " in prepared:
            return ""
        if re.fullmatch(r"[a-z0-9._:-]+", prepared) is None:
            return ""
        return prepared

    def _normalize_ip(self, value: str) -> str:
        prepared = value.strip()
        if not prepared:
            return ""
        if prepared.lower() == "localhost":
            return "127.0.0.1"
        if re.fullmatch(r"[0-9a-fA-F:.\/]+", prepared) is None:
            return ""
        return prepared

    def _managed_collection_values(self, kind: str) -> list[str]:
        if kind != "ips" or self.settings is None:
            return []
        try:
            host = str(self.settings.get().tg_proxy_host or "").strip()
        except Exception:
            return []
        normalized = self._normalize_ip(host)
        return [normalized] if normalized else []

    def _is_editable_file(self, path: Path) -> bool:
        suffix = path.suffix.lower()
        if suffix not in {".txt", ".bat", ".cmd", ".json", ".yaml", ".yml"}:
            if path == self.local_hosts_path():
                return True
            return False
        lowered = path.name.lower()
        if lowered.endswith(".backup"):
            return False
        if path == self.local_hosts_path():
            return True
        if path.is_relative_to(self.storage.paths.configs_dir):
            return True
        if path.is_relative_to(self.storage.paths.mods_dir):
            return (
                path.parent.name.lower() in {"lists", "utils"}
                or suffix in {".bat", ".cmd"}
            )
        if path.is_relative_to(self.storage.paths.default_packs_dir):
            return path.parent.name.lower() in {"lists", "utils"}
        runtime_service_dir = self.storage.paths.runtime_dir / "zapret-discord-youtube" / ".service"
        if path.is_relative_to(runtime_service_dir):
            return path.name.lower() == "hosts"
        return False

    def _collection_definitions(self) -> list[dict[str, str]]:
        return [
            {"id": "domains", "title": "Domains"},
            {"id": "exclude_domains", "title": "Exclude domains"},
            {"id": "all_ips", "title": "IP lists"},
            {"id": "ips", "title": "Exclude IPs"},
        ]

    def _enabled_mod_list_dirs(self) -> list[Path]:
        installed = self.storage.read_json(self.storage.paths.data_dir / "installed_mods.json", default=[]) or []
        result: list[Path] = []
        for item in installed:
            if not isinstance(item, dict):
                continue
            if str(item.get("source_type", "")) != "zapret_bundle":
                continue
            if not bool(item.get("enabled")):
                continue
            path = Path(str(item.get("path", ""))) / "lists"
            if path.exists():
                result.append(path)
        return result
