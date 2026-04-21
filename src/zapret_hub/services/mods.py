from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import re
import shutil
import tempfile
import zipfile
from urllib.request import urlopen
import json
import random

from zapret_hub import __version__
from zapret_hub.domain import InstalledMod, ModIndexItem
from zapret_hub.services.logging_service import LoggingManager
from zapret_hub.services.merge import MergeEngine
from zapret_hub.services.settings import SettingsManager
from zapret_hub.services.storage import StorageManager


class ModsManager:
    _EMOJI_CHOICES = ["✨", "🪄", "🔥", "⚡", "🧩", "🎮", "🌐", "🛡️", "🚀", "💎", "📦", "🧪"]
    def __init__(
        self,
        storage: StorageManager,
        logging: LoggingManager,
        merge: MergeEngine,
        settings: SettingsManager,
    ) -> None:
        self.storage = storage
        self.logging = logging
        self.merge = merge
        self.settings = settings
        self._installed_path = self.storage.paths.data_dir / "installed_mods.json"
        if not self._installed_path.exists():
            self.storage.write_json(self._installed_path, [])
        self._cleanup_installed_duplicate_generals()

    def fetch_index(self, *, refresh_remote: bool = False) -> list[ModIndexItem]:
        settings = self.settings.get()
        if refresh_remote and settings.mods_index_url:
            try:
                with urlopen(settings.mods_index_url, timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.storage.write_json(self.storage.paths.cache_dir / "mods_index.json", payload)
                self.logging.log("info", "Mods index refreshed from URL", url=settings.mods_index_url)
            except Exception as error:
                self.logging.log("warning", "Failed to refresh mods index from URL", url=settings.mods_index_url, error=str(error))
        raw = self.storage.read_json(self.storage.paths.cache_dir / "mods_index.json", default=[]) or []
        return [ModIndexItem(**item) for item in raw]

    def list_installed(self) -> list[InstalledMod]:
        raw = self.storage.read_json(self._installed_path, default=[]) or []
        return [InstalledMod(**item) for item in raw]

    def move(self, mod_id: str, direction: int) -> list[InstalledMod]:
        installed = self.list_installed()
        index = next((i for i, item in enumerate(installed) if item.id == mod_id), -1)
        if index < 0:
            return installed
        target = max(0, min(len(installed) - 1, index + direction))
        if target == index:
            return installed
        item = installed.pop(index)
        installed.insert(target, item)
        self.storage.write_json(self._installed_path, [asdict(entry) for entry in installed])
        self.merge.rebuild()
        return installed

    def set_emoji(self, mod_id: str, emoji: str) -> InstalledMod:
        installed = self.list_installed()
        entry = next(item for item in installed if item.id == mod_id)
        entry.emoji = emoji.strip() or entry.emoji
        self.storage.write_json(self._installed_path, [asdict(item) for item in installed])
        return entry

    def install(self, mod_id: str) -> InstalledMod:
        item = next(entry for entry in self.fetch_index() if entry.id == mod_id)
        target_dir = self.storage.paths.mods_dir / mod_id
        target_dir.mkdir(parents=True, exist_ok=True)
        payload_path = target_dir / "payload.json"
        if not payload_path.exists():
            self.storage.write_json(
                payload_path,
                {
                    "rules": [f"{mod_id}-rule"],
                    "metadata": {"installed_from": item.source_url},
                },
            )

        installed = self.list_installed()
        existing = next((entry for entry in installed if entry.id == mod_id), None)
        if existing:
            existing.version = item.version
            existing.path = str(target_dir)
            result = existing
        else:
            result = InstalledMod(id=mod_id, version=item.version, path=str(target_dir), enabled=False)
            installed.append(result)

        self.storage.write_json(self._installed_path, [asdict(entry) for entry in installed])
        self.logging.log("info", "Mod installed", mod_id=mod_id, version=item.version)
        return result

    def set_enabled(self, mod_id: str, enabled: bool) -> InstalledMod:
        installed = self.list_installed()
        entry = next(item for item in installed if item.id == mod_id)
        entry.enabled = enabled
        self.storage.write_json(self._installed_path, [asdict(item) for item in installed])
        enabled_ids = {item.id for item in installed if item.enabled}
        self.settings.update(enabled_mod_ids=sorted(enabled_ids))
        self.merge.rebuild()
        self.logging.log("info", "Mod state changed", mod_id=mod_id, enabled=enabled)
        return entry

    def remove(self, mod_id: str) -> None:
        installed = [item for item in self.list_installed() if item.id != mod_id]
        self.storage.write_json(self._installed_path, [asdict(item) for item in installed])
        target_dir = self.storage.paths.mods_dir / mod_id
        if target_dir.exists():
            self.storage.create_backup(target_dir, "pre-remove-mod")
            shutil.rmtree(target_dir)
        self.merge.rebuild()
        self.logging.log("info", "Mod removed", mod_id=mod_id)

    def import_from_path(self, source_path: str) -> InstalledMod:
        return self.import_from_paths([source_path])

    def import_from_paths(self, source_paths: list[str], suggested_name: str | None = None) -> InstalledMod:
        valid_sources = [Path(item) for item in source_paths if item]
        if not valid_sources:
            raise ValueError("Nothing was selected for import.")

        for source in valid_sources:
            if not source.exists():
                raise FileNotFoundError(f"Path not found: {source}")

        with tempfile.TemporaryDirectory(prefix="zapret_hub_mod_") as temp_dir:
            staged_root = Path(temp_dir) / "staged"
            staged_root.mkdir(parents=True, exist_ok=True)
            for source in valid_sources:
                self._stage_source_for_import(source, staged_root)

            fallback_name = suggested_name or next((item.stem if item.is_file() else item.name for item in valid_sources), "mod")
            return self._import_staged_bundle(staged_root, suggested_name=fallback_name)

    def import_from_github(self, repo_url: str) -> InstalledMod:
        owner, repo, api_url = self._normalize_github_repo(repo_url)
        if owner.lower() == "flowseal" and repo.lower() == "zapret-discord-youtube":
            raise ValueError("Оригинальный репозиторий Flowseal уже встроен в приложение и не может быть добавлен как модификация.")

        headers = {"User-Agent": f"ZapretHub/{__version__}"}
        self.logging.log("info", "GitHub mod import started", repo=repo, owner=owner)
        with urlopen(
            self._build_request(api_url, headers),
            timeout=15,
        ) as response:
            repo_info = json.loads(response.read().decode("utf-8"))

        zip_url = str(repo_info.get("zipball_url") or "").strip()
        repo_name = str(repo_info.get("name") or repo).strip() or repo
        description = str(repo_info.get("description") or "").strip()
        author = str((repo_info.get("owner") or {}).get("login") or owner).strip() or owner
        if not zip_url:
            raise ValueError("GitHub repository metadata does not contain a zipball URL.")

        with tempfile.TemporaryDirectory(prefix="zapret_hub_github_") as temp_dir:
            zip_path = Path(temp_dir) / f"{repo_name}.zip"
            with urlopen(self._build_request(zip_url, headers), timeout=30) as response:
                zip_path.write_bytes(response.read())
            return self._import_from_github_zip(zip_path, repo_name, author, description, repo_url)

    def _import_from_github_zip(
        self,
        zip_path: Path,
        repo_name: str,
        author: str,
        description: str,
        repo_url: str,
    ) -> InstalledMod:
        with tempfile.TemporaryDirectory(prefix="zapret_hub_github_unzip_") as temp_dir:
            temp_root = Path(temp_dir) / "unzipped"
            with zipfile.ZipFile(zip_path, "r") as archive:
                archive.extractall(temp_root)
            entry = self._import_staged_bundle(
                temp_root,
                suggested_name=repo_name,
                display_name=repo_name,
                author=author,
                description=description,
                source_url=repo_url,
            )
        return entry

    def _import_staged_bundle(
        self,
        staged_root: Path,
        *,
        suggested_name: str,
        display_name: str | None = None,
        author: str = "goshkow",
        description: str = "",
        source_url: str = "",
    ) -> InstalledMod:
        general_sources, list_sources, bin_sources, utils_sources = self._collect_import_candidates(staged_root)
        general_scripts = self._dedupe_general_names(sorted(general_sources))
        if not general_scripts and not list_sources:
            raise ValueError(
                "Не найдено ни одного совместимого general-файла или списка. Добавьте .bat/.cmd конфиг или .txt-листы Zapret."
            )

        mod_id = self._unique_mod_id(suggested_name)
        target_dir = self.storage.paths.mods_dir / mod_id
        self._materialize_mod_bundle(
            target_dir=target_dir,
            general_sources={name: general_sources[name] for name in general_scripts if name in general_sources},
            list_sources=list_sources,
            bin_sources=bin_sources,
            utils_sources=utils_sources,
        )

        installed = self.list_installed()
        entry = InstalledMod(
            id=mod_id,
            version=datetime.utcnow().strftime("%Y.%m.%d"),
            path=str(target_dir),
            name=display_name or suggested_name,
            author=author,
            description=description or self._build_bundle_description(general_scripts, list_sources),
            source_url=source_url,
            enabled=True,
            source_type="zapret_bundle",
            general_scripts=general_scripts,
            emoji="🪄" if mod_id == "unified-by-goshkow" else random.choice(self._EMOJI_CHOICES),
        )
        installed.insert(0, entry)
        self.storage.write_json(self._installed_path, [asdict(item) for item in installed])

        enabled_ids = {item.id for item in installed if item.enabled}
        self.settings.update(enabled_mod_ids=sorted(enabled_ids))
        self.merge.rebuild()
        self.logging.log("info", "Zapret bundle imported", mod_id=mod_id, path=str(target_dir), generals=general_scripts, source=source_url or "local")
        return entry

    def _build_bundle_description(self, general_scripts: list[str], list_sources: dict[str, list[Path]]) -> str:
        parts: list[str] = []
        if general_scripts:
            parts.append(f"General: {len(general_scripts)}")
        if list_sources:
            parts.append(f"Lists: {len(list_sources)}")
        return " | ".join(parts)

    def _materialize_mod_bundle(
        self,
        *,
        target_dir: Path,
        general_sources: dict[str, Path],
        list_sources: dict[str, list[Path]],
        bin_sources: dict[str, Path],
        utils_sources: dict[str, Path],
    ) -> None:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)

        bin_target = target_dir / "bin"
        lists_target = target_dir / "lists"
        utils_target = target_dir / "utils"
        bin_target.mkdir(parents=True, exist_ok=True)
        lists_target.mkdir(parents=True, exist_ok=True)
        utils_target.mkdir(parents=True, exist_ok=True)

        base_bin = self.storage.paths.runtime_dir / "zapret-discord-youtube" / "bin"
        if base_bin.exists():
            for file_path in base_bin.glob("*"):
                if file_path.is_file():
                    shutil.copy2(file_path, bin_target / file_path.name)

        for name, script in general_sources.items():
            shutil.copy2(script, target_dir / name)

        for name, source in bin_sources.items():
            shutil.copy2(source, bin_target / name)

        base_utils = self.storage.paths.runtime_dir / "zapret-discord-youtube" / "utils"
        if base_utils.exists():
            for file_path in base_utils.glob("*"):
                if file_path.is_file():
                    shutil.copy2(file_path, utils_target / file_path.name)

        for name, source in utils_sources.items():
            shutil.copy2(source, utils_target / name)

        for name, sources in list_sources.items():
            merged: list[str] = []
            seen: set[str] = set()
            for source in sources:
                for raw in source.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = raw.strip()
                    if not line or line in seen:
                        continue
                    seen.add(line)
                    merged.append(line)
            (lists_target / name).write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")

    def _stage_source_for_import(self, source: Path, staged_root: Path) -> None:
        if source.is_dir():
            target = staged_root / source.name
            if target.exists():
                target = staged_root / f"{source.name}_{datetime.utcnow().strftime('%H%M%S%f')}"
            shutil.copytree(source, target, dirs_exist_ok=True)
            return

        if source.suffix.lower() == ".zip":
            unpack_dir = staged_root / f"{source.stem}_{datetime.utcnow().strftime('%H%M%S%f')}"
            unpack_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source, "r") as archive:
                archive.extractall(unpack_dir)
            return

        shutil.copy2(source, staged_root / source.name)

    def _collect_import_candidates(self, root: Path) -> tuple[dict[str, Path], dict[str, list[Path]], dict[str, Path], dict[str, Path]]:
        general_sources: dict[str, Path] = {}
        list_sources: dict[str, list[Path]] = {}
        bin_sources: dict[str, Path] = {}
        utils_sources: dict[str, Path] = {}
        allowed_bin_suffixes = {".exe", ".dll", ".bin", ".sys", ".dat"}
        allowed_utils_suffixes = {".txt", ".ps1", ".enabled", ".json", ".cmd", ".bat"}

        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            lowered = file_path.name.lower()
            suffix = file_path.suffix.lower()
            parent_lower = file_path.parent.name.lower()

            if suffix in {".bat", ".cmd"} and not lowered.startswith("service"):
                if lowered not in general_sources:
                    general_sources[file_path.name] = file_path
                continue

            if suffix == ".txt":
                if lowered.startswith(("readme", "license", "changelog")):
                    continue
                if parent_lower == "lists" or lowered.startswith(("list-", "ipset", "hosts")):
                    list_sources.setdefault(file_path.name, []).append(file_path)
                    continue
                if self._looks_like_runtime_list(file_path):
                    list_sources.setdefault(file_path.name, []).append(file_path)
                    continue

            if suffix in allowed_bin_suffixes or (suffix == ".cmd" and "bin" in {part.lower() for part in file_path.parts}):
                if file_path.name not in bin_sources:
                    bin_sources[file_path.name] = file_path
                continue

            if parent_lower == "utils" or lowered in {"targets.txt", "game_filter.enabled", "check_updates.enabled"}:
                if suffix in allowed_utils_suffixes or "." not in file_path.name:
                    utils_sources.setdefault(file_path.name, file_path)

        return general_sources, list_sources, bin_sources, utils_sources

    def _looks_like_runtime_list(self, file_path: Path) -> bool:
        try:
            sample = file_path.read_text(encoding="utf-8", errors="ignore")[:4096]
        except Exception:
            return False
        if not sample.strip():
            return False
        return any(marker in sample.lower() for marker in (".com", ".gg", ".ru", ".net", "/", ":"))

    def _dedupe_general_names(self, names: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for name in names:
            lowered = name.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            result.append(name)
        return result

    def _build_request(self, url: str, headers: dict[str, str]):
        from urllib.request import Request

        return Request(url, headers=headers)

    def _normalize_github_repo(self, repo_url: str) -> tuple[str, str, str]:
        raw = repo_url.strip()
        if not raw:
            raise ValueError("Ссылка на GitHub пустая.")
        if raw.endswith(".git"):
            raw = raw[:-4]
        parsed = urlparse(raw)
        if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            raise ValueError("Поддерживаются только обычные ссылки на GitHub-репозитории.")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ValueError("Не удалось распознать owner/repo в ссылке GitHub.")
        owner, repo = parts[0], parts[1]
        return owner, repo, f"https://api.github.com/repos/{owner}/{repo}"

    def _detect_zapret_bundle_root(self, root: Path) -> Path:
        if self._looks_like_zapret_bundle(root):
            return root
        for child in root.iterdir():
            if child.is_dir() and self._looks_like_zapret_bundle(child):
                return child
        raise ValueError("Selected source does not look like a zapret bundle (service.bat/bin/lists not found).")

    def _looks_like_zapret_bundle(self, path: Path) -> bool:
        return (path / "service.bat").exists() and (path / "bin").is_dir() and (path / "lists").is_dir()

    def _scan_general_scripts(self, bundle_root: Path, skip_base_duplicates: bool = False) -> list[str]:
        scripts: list[str] = []
        base_names = self._base_general_names() if skip_base_duplicates else set()
        for script in bundle_root.glob("*.bat"):
            name = script.name.lower()
            if name.startswith("service"):
                continue
            if name in base_names:
                continue
            scripts.append(script.name)
        return sorted(scripts)

    def _base_general_names(self) -> set[str]:
        base_root = self.storage.paths.runtime_dir / "zapret-discord-youtube"
        names: set[str] = set()
        if not base_root.exists():
            return names
        for script in base_root.glob("*.bat"):
            lowered = script.name.lower()
            if lowered.startswith("service"):
                continue
            names.add(lowered)
        return names

    def _cleanup_installed_duplicate_generals(self) -> None:
        installed = self.list_installed()
        base_names = self._base_general_names()
        changed = False
        for item in installed:
            if item.source_type != "zapret_bundle":
                continue
            bundle = Path(item.path)
            if not bundle.exists():
                continue
            kept_scripts: list[str] = []
            for script in bundle.glob("*.bat"):
                lowered = script.name.lower()
                if lowered.startswith("service"):
                    continue
                if lowered in base_names:
                    script.unlink(missing_ok=True)
                    changed = True
                    continue
                kept_scripts.append(script.name)
            normalized = sorted(set(kept_scripts))
            if sorted(item.general_scripts) != normalized:
                item.general_scripts = normalized
                changed = True
        if changed:
            self.storage.write_json(self._installed_path, [asdict(item) for item in installed])

    def _unique_mod_id(self, name: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower() or "mod"
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return f"{slug}-{stamp}"
