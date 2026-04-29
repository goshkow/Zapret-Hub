from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import time
import webbrowser
import shutil
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from zapret_hub import __version__
from zapret_hub.domain import ComponentDefinition, ComponentState
from zapret_hub.runtime_env import is_packaged_runtime
from zapret_hub.services.logging_service import LoggingManager
from zapret_hub.services.settings import SettingsManager
from zapret_hub.services.storage import StorageManager

_VPN_PROCESS_PATTERNS = (
    "nekobox",
    "nekoray",
    "v2rayn",
    "xray",
    "xrayw",
    "sing-box",
    "singbox",
    "clash",
    "mihomo",
    "hiddify",
    "outline",
    "wireguard",
    "openvpn",
    "amnezia",
    "warp",
)

_VPN_ADAPTER_PATTERNS = (
    "wintun",
    "wireguard",
    "openvpn",
    "tap-",
    "tap_windows",
    "vpn",
    "v2ray",
    "xray",
    "nekobox",
    "nekoray",
    "sing-box",
    "clash",
    "mihomo",
    "tun",
)


class _WindowsJob:
    def __init__(self) -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.job = self.kernel32.CreateJobObjectW(None, None)
        if not self.job:
            return

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation = 9

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self.kernel32.SetInformationJobObject(
            self.job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )

    def assign_pid(self, pid: int) -> None:
        if not self.job:
            return
        PROCESS_ALL_ACCESS = 0x1F0FFF
        handle = self.kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if handle:
            self.kernel32.AssignProcessToJobObject(self.job, handle)
            self.kernel32.CloseHandle(handle)


class ProcessManager:
    def __init__(
        self,
        storage: StorageManager,
        logging: LoggingManager,
        settings: SettingsManager,
    ) -> None:
        self.storage = storage
        self.logging = logging
        self.settings = settings
        self._processes: dict[str, subprocess.Popen[Any]] = {}
        self._states: dict[str, ComponentState] = {}
        self._current_zapret_runtime: Path | None = None
        self._state_cache: list[ComponentState] = []
        self._state_cache_at = 0.0
        self._hub_runtime_token = secrets.token_urlsafe(24)
        self._log_streams: dict[str, Any] = {}
        self._job = _WindowsJob() if sys.platform.startswith("win") else None
        self._creationflags = 0
        self._startupinfo: subprocess.STARTUPINFO | None = None
        if sys.platform.startswith("win"):
            self._creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            self._startupinfo = startup

    def list_components(self) -> list[ComponentDefinition]:
        raw_items = self.storage.read_json(self.storage.paths.data_dir / "components.json", default=[])
        settings = self.settings.get()
        components = [ComponentDefinition(**item) for item in raw_items]
        for component in components:
            component.enabled = component.id in settings.enabled_component_ids
            component.autostart = component.id in settings.autostart_component_ids
        return components

    def list_zapret_generals(self) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        bundles = self._get_zapret_bundles(enabled_only=True)
        for bundle in bundles:
            bundle_id = bundle["id"]
            bundle_title = bundle["title"]
            root = bundle["path"]
            for script in sorted(root.glob("*.bat")):
                name = script.name.lower()
                if name.startswith("service"):
                    continue
                option_id = f"{bundle_id}|{script.name}"
                options.append(
                    {
                        "id": option_id,
                        "name": script.name,
                        "bundle": bundle_title,
                        "bundle_id": bundle_id,
                        "path": str(script),
                    }
                )
        return options

    def prompt_telegram_proxy_link(self) -> None:
        settings = self.settings.get()
        secret = (settings.tg_proxy_secret or "").strip().lower()
        if secret.startswith("dd") and len(secret) > 2:
            secret = secret[2:]
        if not secret:
            secret = secrets.token_hex(16)
            settings = self.settings.update(tg_proxy_secret=secret)
        self._open_telegram_proxy_link(
            host=settings.tg_proxy_host,
            port=int(settings.tg_proxy_port),
            secret=secret,
        )

    def list_states(self) -> list[ComponentState]:
        if self._state_cache and (time.time() - self._state_cache_at) < 0.7:
            return [
                ComponentState(
                    component_id=state.component_id,
                    status=state.status,
                    pid=state.pid,
                    last_error=state.last_error,
                )
                for state in self._state_cache
            ]
        states = self._compute_states()
        self._state_cache = [
            ComponentState(
                component_id=state.component_id,
                status=state.status,
                pid=state.pid,
                last_error=state.last_error,
            )
            for state in states
        ]
        self._state_cache_at = time.time()
        return states

    def _compute_states(self) -> list[ComponentState]:
        states: list[ComponentState] = []
        settings = self.settings.get()
        for component in self.list_components():
            state = self._states.get(component.id, ComponentState(component_id=component.id))
            if component.id == "zapret":
                state.status = "running" if self._is_image_running("winws.exe") else "stopped"
                state.pid = None
            elif component.id == "tg-ws-proxy":
                worker = self._processes.get(component.id)
                listening = self._is_port_listening(settings.tg_proxy_host, int(settings.tg_proxy_port))
                if (worker and worker.poll() is None) or listening:
                    state.status = "running"
                    state.pid = worker.pid if worker and worker.poll() is None else None
                else:
                    state.status = "stopped"
                    state.pid = None
            else:
                process = self._processes.get(component.id)
                if process and process.poll() is None:
                    state.status = "running"
                    state.pid = process.pid
                else:
                    state.status = "stopped"
                    state.pid = None
            states.append(state)
        return states

    def _invalidate_state_cache(self) -> None:
        self._state_cache = []
        self._state_cache_at = 0.0

    def start_component(self, component_id: str) -> ComponentState:
        component = next(item for item in self.list_components() if item.id == component_id)
        if component.id == "zapret":
            state = self._start_zapret(component_id)
            self._invalidate_state_cache()
            return state
        if component.id == "tg-ws-proxy":
            state = self._start_tg_ws_proxy(component_id)
            self._invalidate_state_cache()
            return state
        current = self._processes.get(component_id)
        if current and current.poll() is None:
            return self._states.get(component_id, ComponentState(component_id=component_id, status="running", pid=current.pid))

        process = subprocess.Popen(
            component.command,
            text=True,
            creationflags=self._creationflags,
            startupinfo=self._startupinfo,
        )
        if self._job:
            self._job.assign_pid(process.pid)
        state = ComponentState(component_id=component_id, status="running", pid=process.pid)
        self._processes[component_id] = process
        self._states[component_id] = state
        self.logging.log("info", "Component started", component_id=component_id, pid=process.pid)
        self._invalidate_state_cache()
        return state

    def stop_component(self, component_id: str) -> ComponentState:
        state = self._states.get(component_id, ComponentState(component_id=component_id))

        if component_id == "zapret":
            self._force_stop_zapret_runtime()
            self._close_source_log_stream("zapret")
            self._processes.pop(component_id, None)
            state.status = "stopped" if not self._is_image_running("winws.exe") else "running"
            state.pid = None
            if state.status != "stopped":
                state.last_error = "Failed to stop winws.exe"
            self._states[component_id] = state
            self.logging.log("info", "Zapret stopped")
            self._invalidate_state_cache()
            return state

        if component_id == "tg-ws-proxy":
            process = self._processes.get(component_id)
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
            if process and process.pid:
                self._run_quiet(["taskkill", "/PID", str(process.pid), "/F"])
            self._kill_image("TgWsProxy_windows.exe")
            self._close_source_log_stream("tg-ws-proxy")
            state.status = "stopped"
            state.pid = None
            self._states[component_id] = state
            self.logging.log("info", "TG WS Proxy stopped")
            self._invalidate_state_cache()
            return state
        process = self._processes.get(component_id)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        state.status = "stopped"
        state.pid = None
        self._states[component_id] = state
        self.logging.log("info", "Component stopped", component_id=component_id)
        self._close_source_log_stream(component_id)
        self._invalidate_state_cache()
        return state

    def start_enabled_components(self) -> list[ComponentState]:
        started = []
        for component in self.list_components():
            if component.enabled:
                try:
                    started.append(self.start_component(component.id))
                except Exception as error:
                    state = ComponentState(
                        component_id=component.id,
                        status="error",
                        last_error=str(error),
                    )
                    self._states[component.id] = state
                    self.logging.log("error", "Enabled component failed to start", component_id=component.id, error=str(error))
                    started.append(state)
        return started

    def stop_all(self) -> list[ComponentState]:
        stopped = [self.stop_component(component.id) for component in self.list_components()]
        self._cleanup_merged_runtime()
        return stopped

    def toggle_component_enabled(self, component_id: str) -> ComponentDefinition:
        components = self.list_components()
        target = next(component for component in components if component.id == component_id)
        target.enabled = not target.enabled
        enabled_ids = sorted(component.id for component in components if component.enabled)
        self.settings.update(enabled_component_ids=enabled_ids)
        if not target.enabled:
            self.stop_component(component_id)
        self.logging.log("info", "Component enabled state changed", component_id=component_id, enabled=target.enabled)
        self._invalidate_state_cache()
        return target

    def toggle_component_autostart(self, component_id: str) -> ComponentDefinition:
        components = self.list_components()
        target = next(component for component in components if component.id == component_id)
        target.autostart = not target.autostart
        autostart_ids = sorted(component.id for component in components if component.autostart)
        self.settings.update(autostart_component_ids=autostart_ids)
        self.logging.log("info", "Component autostart state changed", component_id=component_id, autostart=target.autostart)
        return target

    def _start_zapret(self, component_id: str) -> ComponentState:
        # всегда перезапускаем, чтобы не было конфликтов со сторонними процессами
        self.stop_component(component_id)
        selected_option = self._resolve_selected_general_option()
        if selected_option is None:
            state = ComponentState(component_id=component_id, status="error", last_error="No general script found.")
            self._states[component_id] = state
            return state

        selected_script = Path(selected_option["path"])
        selected_bundle_root = Path(selected_script).parent
        active_root: Path | None = None
        process: subprocess.Popen[Any] | None = None
        try:
            active_root = self._prepare_active_zapret_runtime(
                selected_bundle_root=selected_bundle_root,
                selected_bundle_id=selected_option["bundle_id"],
                selected_script_name=selected_script.name,
            )
            self._current_zapret_runtime = active_root
            self._apply_zapret_runtime_switches(active_root)
            active_script = active_root / selected_script.name
            self._ensure_zapret_user_lists(active_root / "lists")
            self._materialize_visible_merged_runtime(active_root)
            bin_dir = active_root / "bin"
            lists_dir = active_root / "lists"
            if not active_script.exists():
                raise FileNotFoundError(f"Selected general was not materialized: {active_script}")
            if not (bin_dir / "winws.exe").exists():
                raise FileNotFoundError(f"winws.exe was not materialized: {bin_dir / 'winws.exe'}")
            winws_command = self._extract_winws_command(active_script, bin_dir=bin_dir, lists_dir=lists_dir)
            winws_command = self._apply_vpn_priority_to_command(winws_command, lists_dir=lists_dir)
            if not winws_command:
                state = ComponentState(
                    component_id=component_id,
                    status="error",
                    last_error="Failed to parse winws command from selected general file.",
                )
                self._states[component_id] = state
                self.logging.log("error", "Zapret command parse failed", script=str(active_script))
                return state
            process = subprocess.Popen(
                winws_command,
                cwd=str(bin_dir),
                creationflags=self._creationflags,
                startupinfo=self._startupinfo,
                stdout=self._open_source_log_stream("zapret"),
                stderr=subprocess.STDOUT,
            )
            if self._job:
                self._job.assign_pid(process.pid)
            self._processes[component_id] = process
            running = False
            for _ in range(24):
                if self._is_image_running("winws.exe"):
                    running = True
                    break
                time.sleep(0.25)
            if running:
                state = ComponentState(component_id=component_id, status="running", pid=process.pid)
                self.logging.log("info", "Zapret started", script=str(active_script), command=winws_command[0])
            else:
                state = ComponentState(
                    component_id=component_id,
                    status="error",
                    last_error="winws did not start. Run app as Administrator and check antivirus exclusions for WinDivert.",
                )
                self.logging.log("error", "Zapret failed to start", script=str(active_script))
        except OSError as error:
            if getattr(error, "winerror", 0) == 740:
                state = ComponentState(
                    component_id=component_id,
                    status="error",
                    last_error="Administrator rights are required for winws/WinDivert.",
                )
                self.logging.log("error", "Zapret start failed: admin required")
            else:
                state = ComponentState(component_id=component_id, status="error", last_error=str(error))
                self.logging.log("error", "Zapret start failed", error=str(error))
        except shutil.Error as error:
            state = ComponentState(component_id=component_id, status="error", last_error=str(error))
            self.logging.log("error", "Zapret runtime build failed", error=str(error))
        except Exception as error:
            state = ComponentState(component_id=component_id, status="error", last_error=str(error))
            self.logging.log("error", "Zapret start crashed", error=str(error))
        if state.status != "running":
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            self._force_stop_zapret_runtime()
            if active_root is not None:
                self._reset_active_runtime_dir(active_root)
            self._current_zapret_runtime = None
        self._states[component_id] = state
        return state

    def _extract_winws_command(self, script_path: Path, bin_dir: Path, lists_dir: Path) -> list[str]:
        game_filter, game_filter_tcp, game_filter_udp = self._get_game_filter_values(script_path.parent)
        lines = self._read_batch_logical_lines(script_path)
        for line in lines:
            if "winws.exe" not in line.lower():
                continue
            try:
                parts = shlex.split(line, posix=False)
            except ValueError:
                continue
            if not parts:
                continue
            winws_idx = next((i for i, item in enumerate(parts) if "winws.exe" in item.lower()), -1)
            if winws_idx < 0:
                continue

            executable = self._expand_batch_value(
                parts[winws_idx],
                script_dir=script_path.parent,
                bin_dir=bin_dir,
                lists_dir=lists_dir,
                game_filter=game_filter,
                game_filter_tcp=game_filter_tcp,
                game_filter_udp=game_filter_udp,
            ).strip().strip('"')
            if not executable:
                continue
            exe_path = Path(executable)
            if not exe_path.is_absolute():
                script_relative = script_path.parent / executable
                if script_relative.exists():
                    exe_path = script_relative
                elif exe_path.name.lower() == "winws.exe":
                    exe_path = bin_dir / "winws.exe"
                else:
                    exe_path = bin_dir / exe_path.name
            args: list[str] = []
            for raw_arg in parts[winws_idx + 1 :]:
                arg = self._expand_batch_value(
                    raw_arg,
                    script_dir=script_path.parent,
                    bin_dir=bin_dir,
                    lists_dir=lists_dir,
                    game_filter=game_filter,
                    game_filter_tcp=game_filter_tcp,
                    game_filter_udp=game_filter_udp,
                ).strip()
                if not arg or arg == "^":
                    continue
                # убираем лишние кавычки из bat-синтаксиса
                if arg.startswith('"') and arg.endswith('"') and len(arg) >= 2:
                    arg = arg[1:-1]
                if '="' in arg and arg.endswith('"'):
                    key, value = arg.split('="', 1)
                    arg = f"{key}={value[:-1]}"
                args.append(arg)
            return [str(exe_path), *args]
        return []

    def _apply_vpn_priority_to_command(self, command: list[str], *, lists_dir: Path) -> list[str]:
        if not command or not sys.platform.startswith("win"):
            return command
        try:
            vpn_data = self._detect_vpn_priority_context()
        except Exception as error:
            self.logging.log("warning", "Failed to detect VPN priority context", error=str(error))
            return command

        adapter_indexes = [int(item) for item in vpn_data.get("adapter_indexes", []) if str(item).isdigit()]
        remote_ips = [str(item).strip() for item in vpn_data.get("remote_ips", []) if str(item).strip()]
        if not adapter_indexes and not remote_ips:
            return command

        updated = list(command)
        if adapter_indexes:
            raw_filter = " and ".join(f"(ifIdx != {index} and subIfIdx != {index})" for index in sorted(set(adapter_indexes)))
            updated.append(f"--wf-raw-part={raw_filter}")

        if remote_ips:
            vpn_exclude_path = lists_dir / "ipset-vpn-exclude.txt"
            vpn_exclude_path.write_text("\n".join(sorted(set(remote_ips))) + "\n", encoding="utf-8")
            updated.append(f"--ipset-exclude={vpn_exclude_path}")

        self.logging.log(
            "info",
            "Applied VPN priority safeguards to zapret",
            adapter_indexes=sorted(set(adapter_indexes)),
            remote_ips=sorted(set(remote_ips)),
        )
        return updated

    def _detect_vpn_priority_context(self) -> dict[str, list[str]]:
        script = r"""
$patterns = @('nekobox','nekoray','v2rayn','xray','xrayw','sing-box','singbox','clash','mihomo','hiddify','outline','wireguard','openvpn','amnezia','warp')
$adapterPatterns = @('wintun','wireguard','openvpn','tap-','tap_windows','vpn','v2ray','xray','nekobox','nekoray','sing-box','clash','mihomo','tun')

$procById = @{}
Get-CimInstance Win32_Process | ForEach-Object {
  $name = ([string]$_.Name).ToLowerInvariant()
  $path = ([string]$_.ExecutablePath).ToLowerInvariant()
  $cmd = ([string]$_.CommandLine).ToLowerInvariant()
  foreach ($pattern in $patterns) {
    if ($name.Contains($pattern) -or $path.Contains($pattern) -or $cmd.Contains($pattern)) {
      $procById[[int]$_.ProcessId] = $true
      break
    }
  }
}

$remoteIps = New-Object System.Collections.Generic.HashSet[string]
Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | ForEach-Object {
  $pid = [int]$_.OwningProcess
  if (-not $procById.ContainsKey($pid)) { return }
  $ip = ([string]$_.RemoteAddress).Trim()
  if (-not $ip) { return }
  if ($ip -in @('127.0.0.1','0.0.0.0','::','::1')) { return }
  [void]$remoteIps.Add($ip)
}

$adapterIndexes = New-Object System.Collections.Generic.HashSet[int]
Get-NetAdapter -ErrorAction SilentlyContinue | ForEach-Object {
  $joined = (([string]$_.Name) + ' ' + ([string]$_.InterfaceDescription)).ToLowerInvariant()
  foreach ($pattern in $adapterPatterns) {
    if ($joined.Contains($pattern)) {
      [void]$adapterIndexes.Add([int]$_.ifIndex)
      break
    }
  }
}

[pscustomobject]@{
  adapter_indexes = @($adapterIndexes | Sort-Object)
  remote_ips = @($remoteIps | Sort-Object)
} | ConvertTo-Json -Compress
"""
        proc = self._run_powershell_json(script)
        if not proc:
            return {"adapter_indexes": [], "remote_ips": []}
        try:
            payload = json.loads(proc)
        except json.JSONDecodeError:
            return {"adapter_indexes": [], "remote_ips": []}
        adapter_indexes = payload.get("adapter_indexes", []) if isinstance(payload, dict) else []
        remote_ips = payload.get("remote_ips", []) if isinstance(payload, dict) else []
        if not isinstance(adapter_indexes, list):
            adapter_indexes = [adapter_indexes] if adapter_indexes not in (None, "") else []
        if not isinstance(remote_ips, list):
            remote_ips = [remote_ips] if remote_ips not in (None, "") else []
        return {
            "adapter_indexes": [str(item) for item in adapter_indexes if str(item).strip()],
            "remote_ips": [str(item) for item in remote_ips if self._looks_like_ip_address(str(item))],
        }

    def _run_powershell_json(self, script: str) -> str:
        startup = self._startupinfo
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            check=False,
            creationflags=self._creationflags,
            startupinfo=startup,
        )
        if proc.returncode != 0:
            self.logging.log("warning", "PowerShell helper failed", stderr=(proc.stderr or "").strip()[-1000:])
            return ""
        return (proc.stdout or "").strip()

    def _looks_like_ip_address(self, value: str) -> bool:
        candidate = value.strip()
        if not candidate:
            return False
        if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", candidate):
            return True
        return ":" in candidate and re.fullmatch(r"[0-9a-fA-F:]+", candidate) is not None

    def _read_batch_logical_lines(self, script_path: Path) -> list[str]:
        raw_lines = script_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        logical_lines: list[str] = []
        current = ""
        for raw in raw_lines:
            line = raw.strip()
            if not line or line.startswith("::") or line.lower().startswith("rem "):
                continue
            if current:
                current = f"{current} {line}"
            else:
                current = line
            if current.endswith("^"):
                current = current[:-1].rstrip()
                continue
            logical_lines.append(current)
            current = ""
        if current:
            logical_lines.append(current)
        return logical_lines

    def _expand_batch_value(
        self,
        value: str,
        *,
        script_dir: Path,
        bin_dir: Path,
        lists_dir: Path,
        game_filter: str,
        game_filter_tcp: str,
        game_filter_udp: str,
    ) -> str:
        result = value
        script_prefix = str(script_dir) + os.sep
        replacements = {
            "%~dp0": script_prefix,
            "%CD%": str(script_dir),
            "%BIN%": str(bin_dir) + os.sep,
            "%LISTS%": str(lists_dir) + os.sep,
            "%GameFilter%": game_filter,
            "%GameFilterTCP%": game_filter_tcp,
            "%GameFilterUDP%": game_filter_udp,
        }
        for key, replacement in replacements.items():
            result = result.replace(key, replacement).replace(key.lower(), replacement).replace(key.upper(), replacement)
        return result

    def _get_game_filter_values(self, runtime_root: Path) -> tuple[str, str, str]:
        mode_from_settings = (self.settings.get().zapret_game_filter_mode or "").strip().lower()
        if mode_from_settings == "auto":
            mode_from_settings = ""
        if mode_from_settings == "all":
            return ("1024-65535", "1024-65535", "1024-65535")
        if mode_from_settings == "tcp":
            return ("1024-65535", "1024-65535", "12")
        if mode_from_settings == "udp":
            return ("1024-65535", "12", "1024-65535")
        if mode_from_settings == "disabled":
            return ("12", "12", "12")
        mode_file = runtime_root / "utils" / "game_filter.enabled"
        if not mode_file.exists():
            return ("12", "12", "12")
        mode = mode_file.read_text(encoding="utf-8", errors="ignore").strip().lower()
        if mode == "all":
            return ("1024-65535", "1024-65535", "1024-65535")
        if mode == "tcp":
            return ("1024-65535", "1024-65535", "12")
        if mode == "udp":
            return ("1024-65535", "12", "1024-65535")
        return ("12", "12", "12")

    def _apply_zapret_runtime_switches(self, runtime_root: Path) -> None:
        settings = self.settings.get()
        lists_dir = runtime_root / "lists"
        utils_dir = runtime_root / "utils"
        lists_dir.mkdir(parents=True, exist_ok=True)
        utils_dir.mkdir(parents=True, exist_ok=True)

        ipset_mode = (settings.zapret_ipset_mode or "loaded").strip().lower()
        ipset_all = lists_dir / "ipset-all.txt"
        if ipset_mode == "none":
            ipset_all.write_text("203.0.113.113/32\n", encoding="utf-8")
        elif ipset_mode == "any":
            ipset_all.write_text("", encoding="utf-8")
        elif not ipset_all.exists():
            ipset_all.write_text("", encoding="utf-8")

        game_mode = (settings.zapret_game_filter_mode or "disabled").strip().lower()
        game_flag = utils_dir / "game_filter.enabled"
        if game_mode in ("all", "tcp", "udp"):
            game_flag.write_text(game_mode, encoding="utf-8")
        elif game_flag.exists():
            game_flag.unlink(missing_ok=True)

    def _start_tg_ws_proxy(self, component_id: str) -> ComponentState:
        # всегда перезапускаем, чтобы не было конфликтов со сторонними процессами
        self.stop_component(component_id)

        settings = self.settings.get()
        secret = (settings.tg_proxy_secret or "").strip().lower()
        if secret.startswith("dd") and len(secret) > 2:
            secret = secret[2:]
        if not secret:
            secret = secrets.token_hex(16)
        if secret != settings.tg_proxy_secret:
            settings = self.settings.update(tg_proxy_secret=secret)
        # подчищаем старый процесс, если он остался в трее
        self._kill_image("TgWsProxy_windows.exe")
        try:
            (self.storage.paths.logs_dir / "tg_worker_error.log").unlink(missing_ok=True)
        except Exception:
            pass
        command = self._build_worker_command(
            "tg-ws-proxy",
            tg_host=settings.tg_proxy_host,
            tg_port=int(settings.tg_proxy_port),
            tg_secret=secret,
        )
        process = subprocess.Popen(
            command,
            cwd=str(self.storage.paths.install_root),
            creationflags=self._creationflags,
            startupinfo=self._startupinfo,
            env=self._build_worker_env(),
            stdout=self._open_source_log_stream("tg-ws-proxy"),
            stderr=subprocess.STDOUT,
        )
        listen_host = settings.tg_proxy_host
        listen_port = int(settings.tg_proxy_port)
        ready = False
        for _ in range(16):
            if process.poll() is not None:
                break
            if self._is_port_listening(listen_host, listen_port):
                ready = True
                break
            time.sleep(0.35)
        if not ready:
            error_hint = "TG WS Proxy worker did not open listening port."
            worker_error_log = self.storage.paths.logs_dir / "tg_worker_error.log"
            if worker_error_log.exists():
                error_hint = worker_error_log.read_text(encoding="utf-8")[-1000:]
            state = ComponentState(
                component_id=component_id,
                status="error",
                last_error=error_hint,
            )
            self._states[component_id] = state
            self.logging.log("error", "TG WS Proxy worker failed to start", error=error_hint)
            return state
        if self._job:
            self._job.assign_pid(process.pid)
        state = ComponentState(component_id=component_id, status="running", pid=process.pid)
        self._processes[component_id] = process
        self._states[component_id] = state
        self.logging.log("info", "TG WS Proxy worker started", pid=process.pid)
        signature = f"{settings.tg_proxy_host}:{int(settings.tg_proxy_port)}:{secret}"
        if settings.tg_proxy_link_prompt_signature != signature:
            self._ensure_telegram_and_open_proxy_link(
                host=settings.tg_proxy_host,
                port=int(settings.tg_proxy_port),
                secret=secret,
            )
            self.settings.update(tg_proxy_link_prompt_signature=signature)
        return state

    def _build_worker_command(self, worker: str, **kwargs: Any) -> list[str]:
        cmd: list[str]
        if is_packaged_runtime():
            cmd = [sys.executable, "--worker", worker]
        else:
            cmd = [self._worker_python_executable(), "-m", "zapret_hub.worker_entry", "--worker", worker]

        for key, value in kwargs.items():
            option = "--" + key.replace("_", "-")
            cmd.extend([option, str(value)])
        return cmd

    def _build_worker_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if not is_packaged_runtime():
            src_root = str(self.storage.paths.install_root / "src")
            current = str(env.get("PYTHONPATH", "") or "")
            parts = [item for item in current.split(os.pathsep) if item]
            if src_root not in parts:
                parts.insert(0, src_root)
            env["PYTHONPATH"] = os.pathsep.join(parts)
        return env

    def _worker_python_executable(self) -> str:
        if is_packaged_runtime():
            return sys.executable
        install_root = self.storage.paths.install_root
        candidates = [
            install_root / ".venv" / "Scripts" / "python.exe",
            install_root / ".venv" / "bin" / "python",
            Path(sys.executable),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return sys.executable

    def _get_zapret_bundles(self, enabled_only: bool) -> list[dict[str, Any]]:
        bundles: list[dict[str, Any]] = []
        base = self.storage.paths.runtime_dir / "zapret-discord-youtube"
        index_map = {
            str(item.get("id", "")): str(item.get("name", "")).strip()
            for item in (self.storage.read_json(self.storage.paths.cache_dir / "mods_index.json", default=[]) or [])
            if isinstance(item, dict)
        }
        installed_raw = self.storage.read_json(self.storage.paths.data_dir / "installed_mods.json", default=[]) or []
        for raw in installed_raw:
            if raw.get("source_type") != "zapret_bundle":
                continue
            if enabled_only and not raw.get("enabled"):
                continue
            path = Path(raw.get("path", ""))
            if not path.exists():
                continue
            mod_id = str(raw.get("id", "bundle"))
            title = str(raw.get("name") or "").strip() or index_map.get(mod_id) or mod_id
            bundles.append({"id": mod_id, "title": title, "path": path})
        if base.exists():
            bundles.append({"id": "base", "title": "", "path": base})
        return bundles

    def _resolve_selected_general_option(self) -> dict[str, str] | None:
        options = self.list_zapret_generals()
        if not options:
            return None
        settings = self.settings.get()
        selected = settings.selected_zapret_general
        picked = next((item for item in options if item["id"] == selected), None)
        if picked is None:
            preferred = next(
                (
                    item
                    for item in options
                    if item["name"].lower() == "general.bat" and str(item.get("bundle_id", "")) == "base"
                ),
                next((item for item in options if str(item.get("bundle_id", "")) == "base"), options[0]),
            )
            selected = preferred["id"]
            self.settings.update(selected_zapret_general=selected)
            picked = preferred
        return picked

    def _prepare_active_zapret_runtime(self, selected_bundle_root: Path, selected_bundle_id: str, selected_script_name: str) -> Path:
        self._cleanup_inactive_zapret_runtimes()
        active_root = self._next_active_runtime_dir()
        base_root = self.storage.paths.runtime_dir / "zapret-discord-youtube"
        if base_root.exists():
            shutil.copytree(base_root, active_root, dirs_exist_ok=True, ignore=self._runtime_copy_ignore)
        else:
            shutil.copytree(selected_bundle_root, active_root, dirs_exist_ok=True, ignore=self._runtime_copy_ignore)

        lists_target = active_root / "lists"
        bin_target = active_root / "bin"
        utils_target = active_root / "utils"
        lists_target.mkdir(parents=True, exist_ok=True)
        bin_target.mkdir(parents=True, exist_ok=True)
        utils_target.mkdir(parents=True, exist_ok=True)

        layered_bundles = self._get_zapret_bundles(enabled_only=True)
        for bundle in layered_bundles:
            bundle_id = bundle["id"]
            bundle_root = Path(bundle["path"])
            if bundle_id != "base":
                self._overlay_zapret_bundle_runtime(active_root, bundle_root)
            lists_source = bundle_root / "lists"
            if not lists_source.exists():
                continue
            self._merge_lists_into_target(lists_target, lists_source)

        selected_script = selected_bundle_root / selected_script_name
        if selected_script.exists():
            shutil.copy2(selected_script, active_root / selected_script.name)

        self._apply_user_collection_overrides(lists_target)
        self._materialize_visible_merged_runtime(active_root)
        return active_root

    def _overlay_zapret_bundle_runtime(self, active_root: Path, bundle_root: Path) -> None:
        for script in bundle_root.glob("*.bat"):
            if script.name.lower().startswith("service"):
                continue
            shutil.copy2(script, active_root / script.name)

        for folder_name in ("bin", "utils"):
            source_dir = bundle_root / folder_name
            target_dir = active_root / folder_name
            if not source_dir.exists():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            for source in source_dir.glob("*"):
                if source.is_file():
                    shutil.copy2(source, target_dir / source.name)

    def _materialize_visible_merged_runtime(self, active_root: Path) -> None:
        target_root = self.storage.paths.merged_runtime_dir / "zapret"
        if target_root.exists():
            shutil.rmtree(target_root, ignore_errors=True)
        shutil.copytree(active_root, target_root, dirs_exist_ok=True, ignore=self._runtime_copy_ignore)

    def _runtime_copy_ignore(self, directory: str, names: list[str]) -> set[str]:
        ignored_names = {".git", ".github", "__pycache__", ".mypy_cache", ".pytest_cache"}
        ignored_suffixes = {".pyc", ".pyo"}
        return {name for name in names if name in ignored_names or Path(name).suffix.lower() in ignored_suffixes}

    def _merge_lists_into_target(self, target_lists: Path, source_lists: Path) -> None:
        for source in source_lists.glob("*.txt"):
            target = target_lists / source.name
            existing = self._read_list_lines(target)
            incoming = self._read_list_lines(source)
            merged = self._merge_with_conflict_resolution(target_lists, target.name.lower(), existing, incoming)
            target.write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")

    def _merge_with_conflict_resolution(
        self,
        target_lists: Path,
        filename: str,
        existing: list[str],
        incoming: list[str],
    ) -> list[str]:
        conflict_map = {
            "list-general.txt": "list-exclude.txt",
            "list-exclude.txt": "list-general.txt",
            "ipset-all.txt": "ipset-exclude.txt",
            "ipset-exclude.txt": "ipset-all.txt",
            "list-general-user.txt": "list-exclude-user.txt",
            "list-exclude-user.txt": "list-general-user.txt",
            "ipset-all-user.txt": "ipset-exclude-user.txt",
            "ipset-exclude-user.txt": "ipset-all-user.txt",
        }
        merged: list[str] = []
        seen: set[str] = set()
        for line in [*existing, *incoming]:
            if not line or line in seen:
                continue
            seen.add(line)
            merged.append(line)
        opposite = conflict_map.get(filename)
        if not opposite:
            return merged
        opposite_path = target_lists / opposite
        if not opposite_path.exists():
            return merged
        opposite_values = set(self._read_list_lines(opposite_path))
        return [line for line in merged if line not in opposite_values]

    def _apply_user_collection_overrides(self, lists_dir: Path) -> None:
        overrides_path = self.storage.paths.data_dir / "file_overrides.json"
        raw = self.storage.read_json(overrides_path, default={}) or {}
        mapping = {
            "domains": "list-general.txt",
            "exclude_domains": "list-exclude.txt",
            "all_ips": "ipset-all.txt",
            "ips": "ipset-exclude.txt",
        }
        for kind, filename in mapping.items():
            target = lists_dir / filename
            values = self._read_list_lines(target)
            override = raw.get(kind, {}) if isinstance(raw, dict) else {}
            removed = {str(item).strip() for item in list((override or {}).get("removed", []) or []) if str(item).strip()}
            added = [str(item).strip() for item in list((override or {}).get("added", []) or []) if str(item).strip()]
            result = [item for item in values if item not in removed]
            seen = set(result)
            for item in added:
                if item in seen:
                    continue
                seen.add(item)
                result.append(item)
            target.write_text("\n".join(result) + ("\n" if result else ""), encoding="utf-8")

    def _read_list_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        lines: list[str] = []
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line:
                continue
            lines.append(line)
        return lines

    def auto_select_working_general(self) -> dict[str, object] | None:
        options = self.list_zapret_generals()
        if not options:
            return None
        original = self.settings.get().selected_zapret_general
        best_result: dict[str, object] | None = None
        for option in options:
            outcome = self._run_general_connectivity_check(option["id"])
            if best_result is None or int(outcome.get("passed_targets", 0)) > int(best_result.get("passed_targets", 0)):
                best_result = {
                    "id": option["id"],
                    "status": outcome["status"],
                    "passed_targets": outcome.get("passed_targets", 0),
                    "total_targets": outcome.get("total_targets", 0),
                }
            if outcome["status"] == "ok":
                self.stop_component("zapret")
                self.logging.log("info", "Auto-selected zapret general", general=option["id"])
                return {
                    "id": option["id"],
                    "status": "ok",
                    "passed_targets": outcome.get("passed_targets", 0),
                    "total_targets": outcome.get("total_targets", 0),
                }
            self.stop_component("zapret")
        if best_result is not None and best_result.get("id"):
            self.settings.update(selected_zapret_general=str(best_result["id"]))
            return best_result
        self.settings.update(selected_zapret_general=original)
        return None

    def _capture_diagnostic_settings(self) -> dict[str, object]:
        settings = self.settings.get()
        return {
            "selected_zapret_general": settings.selected_zapret_general,
            "zapret_ipset_mode": settings.zapret_ipset_mode,
            "zapret_game_filter_mode": settings.zapret_game_filter_mode,
        }

    def _restore_diagnostic_settings(self, snapshot: dict[str, object]) -> None:
        self.settings.update(
            selected_zapret_general=str(snapshot.get("selected_zapret_general", "") or ""),
            zapret_ipset_mode=str(snapshot.get("zapret_ipset_mode", "loaded") or "loaded"),
            zapret_game_filter_mode=str(snapshot.get("zapret_game_filter_mode", "disabled") or "disabled"),
        )

    def _prepare_diagnostic_runtime(self, *, general_id: str, ipset_mode: str, game_mode: str) -> bool:
        original_running = self._is_image_running("winws.exe")
        if original_running:
            self.stop_component("zapret")
        self.settings.update(
            selected_zapret_general=general_id,
            zapret_ipset_mode=ipset_mode,
            zapret_game_filter_mode=game_mode,
        )
        return original_running

    def run_single_general_diagnostic(
        self,
        general_id: str,
        progress_callback: callable | None = None,
        stop_callback: callable | None = None,
    ) -> dict[str, object]:
        options = {item["id"]: item for item in self.list_zapret_generals()}
        option = options.get(general_id)
        if option is None:
            return {"status": "error", "error": "general not found", "passed_targets": 0, "total_targets": 0}
        settings_snapshot = self._capture_diagnostic_settings()
        original_running = self._prepare_diagnostic_runtime(
            general_id=general_id,
            ipset_mode="loaded",
            game_mode="all",
        )
        try:
            outcome = self._run_general_connectivity_check(
                general_id,
                stop_callback=stop_callback,
                targets=self._load_standard_test_targets(),
                progress_callback=progress_callback,
            )
            return {
                "id": option["id"],
                "name": option["name"],
                "bundle": option["bundle"],
                "status": str(outcome["status"]),
                "error": str(outcome.get("error", "")),
                "passed_targets": int(outcome.get("passed_targets", 0)),
                "total_targets": int(outcome.get("total_targets", 0)),
            }
        finally:
            self.stop_component("zapret")
            self._restore_diagnostic_settings(settings_snapshot)
            if original_running and str(settings_snapshot.get("selected_zapret_general", "")):
                self.start_component("zapret")

    def run_general_diagnostics(
        self,
        progress_callback: callable | None = None,
        stop_callback: callable | None = None,
    ) -> list[dict[str, str]]:
        options = self.list_zapret_generals()
        if not options:
            return []

        settings_snapshot = self._capture_diagnostic_settings()
        original_running = self._is_image_running("winws.exe")
        results: list[dict[str, str]] = []
        targets = self._load_standard_test_targets()
        per_general_steps = max(2, len(targets) + 1)
        total_steps = len(options) * per_general_steps

        try:
            if original_running:
                self.stop_component("zapret")
            for index, option in enumerate(options, start=1):
                if stop_callback is not None and stop_callback():
                    break
                self.settings.update(
                    selected_zapret_general=option["id"],
                    zapret_ipset_mode="loaded",
                    zapret_game_filter_mode="all",
                )
                base_step = (index - 1) * per_general_steps
                if progress_callback is not None:
                    progress_callback(base_step + 1, total_steps, option["name"])
                outcome = self._run_general_connectivity_check(
                    option["id"],
                    stop_callback=stop_callback,
                    targets=targets,
                    progress_callback=(
                        lambda completed, total, target_name, *, _base=base_step, _steps=per_general_steps, _option=option: (
                            progress_callback(
                                min(_base + 1 + completed, _base + _steps),
                                total_steps,
                                f"{_option['name']} - {target_name} ({completed}/{total})",
                            )
                            if progress_callback is not None
                            else None
                        )
                    ),
                )
                if progress_callback is not None:
                    progress_callback(base_step + per_general_steps, total_steps, option["name"])
                results.append(
                    {
                        "id": option["id"],
                        "name": option["name"],
                        "bundle": option["bundle"],
                        "status": str(outcome["status"]),
                        "error": str(outcome.get("error", "")),
                        "passed_targets": str(outcome.get("passed_targets", 0)),
                        "total_targets": str(outcome.get("total_targets", 0)),
                    }
                )
                self.stop_component("zapret")
        finally:
            self._restore_diagnostic_settings(settings_snapshot)
            if original_running and str(settings_snapshot.get("selected_zapret_general", "")):
                self.start_component("zapret")

        return results

    def run_settings_diagnostics(
        self,
        progress_callback: callable | None = None,
        stop_callback: callable | None = None,
    ) -> dict[str, object]:
        original = self.settings.get()
        general_id = str(original.selected_zapret_general or "").strip()
        if not general_id:
            return {"results": [], "status": "error", "error": "No selected general"}
        ipset_modes = ["loaded", "none", "any"]
        game_modes = ["disabled", "all", "tcp", "udp", "auto"]
        combinations = [(ipset, game) for ipset in ipset_modes for game in game_modes]
        targets = self._load_standard_test_targets()
        results: list[dict[str, object]] = []
        total = max(1, len(combinations))
        original_running = self._is_image_running("winws.exe")
        try:
            if original_running:
                self.stop_component("zapret")
            for index, (ipset_mode, game_mode) in enumerate(combinations, start=1):
                if stop_callback is not None and stop_callback():
                    break
                self.settings.update(
                    selected_zapret_general=general_id,
                    zapret_ipset_mode=ipset_mode,
                    zapret_game_filter_mode=game_mode,
                )
                started_at = time.time()
                outcome = self._run_general_connectivity_check(general_id, stop_callback=stop_callback, targets=targets)
                elapsed = round(time.time() - started_at, 2)
                passed = int(outcome.get("passed_targets", 0))
                total_targets = int(outcome.get("total_targets", 0))
                results.append(
                    {
                        "ipset_mode": ipset_mode,
                        "game_mode": game_mode,
                        "status": str(outcome.get("status", "error")),
                        "passed_targets": passed,
                        "total_targets": total_targets,
                        "elapsed": elapsed,
                    }
                )
                if progress_callback is not None:
                    progress_callback(index, total, f"{ipset_mode} / {game_mode}")
                self.stop_component("zapret")
        finally:
            self.settings.update(
                selected_zapret_general=original.selected_zapret_general,
                zapret_ipset_mode=original.zapret_ipset_mode,
                zapret_game_filter_mode=original.zapret_game_filter_mode,
            )
            if original_running and general_id:
                self.start_component("zapret")

        ranked = sorted(
            results,
            key=lambda item: (-int(item.get("passed_targets", 0)), float(item.get("elapsed", 999999.0))),
        )
        best = ranked[0] if ranked else None
        return {"results": ranked, "best": best, "status": "ok" if ranked else "error"}

    def fetch_latest_zapret_release(self) -> dict[str, str]:
        api_url = "https://api.github.com/repos/Flowseal/zapret-discord-youtube/releases/latest"
        request = Request(api_url, headers={"User-Agent": f"ZapretHub/{__version__}"})
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latest_version = str(payload.get("tag_name") or payload.get("name") or "").strip().lstrip("v")
        asset = next(
            (
                item
                for item in list(payload.get("assets") or [])
                if isinstance(item, dict) and str(item.get("name", "")).lower().endswith(".zip")
            ),
            None,
        )
        return {
            "latest_version": latest_version,
            "asset_url": str((asset or {}).get("browser_download_url", "")),
            "asset_name": str((asset or {}).get("name", "")),
        }

    def fetch_latest_tg_ws_proxy_release(self) -> dict[str, str]:
        api_url = "https://api.github.com/repos/Flowseal/tg-ws-proxy/releases/latest"
        request = Request(api_url, headers={"User-Agent": f"ZapretHub/{__version__}"})
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latest_version = str(payload.get("tag_name") or payload.get("name") or "").strip().lstrip("v")
        assets = [item for item in list(payload.get("assets") or []) if isinstance(item, dict)]
        windows_asset = next(
            (
                item
                for item in assets
                if str(item.get("name", "")).strip().lower() == "tgwsproxy_windows.exe"
            ),
            None,
        )
        return {
            "latest_version": latest_version,
            "source_url": str(payload.get("zipball_url") or "").strip(),
            "exe_url": str((windows_asset or {}).get("browser_download_url", "")).strip(),
            "exe_name": str((windows_asset or {}).get("name", "")).strip() or "TgWsProxy_windows.exe",
        }

    def update_zapret_runtime(self) -> dict[str, str]:
        release = self.fetch_latest_zapret_release()
        latest_version = str(release.get("latest_version", "")).strip()
        current_version = self.storage._detect_zapret_version()
        if latest_version and current_version == latest_version:
            return {"status": "up-to-date", "version": current_version}
        asset_url = str(release.get("asset_url", "")).strip()
        if not asset_url:
            return {"status": "error", "error": "No zip asset found"}
        runtime_root = self.storage.paths.runtime_dir / "zapret-discord-youtube"
        was_running = self._is_image_running("winws.exe")
        temp_root = Path(tempfile.mkdtemp(prefix="zapret_hub_zapret_update_"))
        try:
            zip_path = temp_root / (release.get("asset_name") or "zapret.zip")
            with urlopen(Request(asset_url, headers={"User-Agent": f"ZapretHub/{__version__}"}), timeout=60) as response:
                zip_path.write_bytes(response.read())
            extract_root = temp_root / "extract"
            extract_root.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as archive:
                archive.extractall(extract_root)
            source_root = next((p for p in extract_root.iterdir() if p.is_dir() and (p / "bin").exists() and (p / "lists").exists()), None)
            if source_root is None:
                return {"status": "error", "error": "Invalid zapret archive"}
            if was_running:
                self.stop_component("zapret")
            backup = self.storage.create_backup(runtime_root, "pre-update-zapret")
            if runtime_root.exists():
                shutil.rmtree(runtime_root, ignore_errors=True)
            shutil.copytree(source_root, runtime_root, dirs_exist_ok=True)
            self.storage._ensure_default_bundled_mod("unified-by-goshkow", {
                "name": "Unified",
                "author": "goshkow",
                "description": "Bundled unified pack",
                "version": latest_version or current_version,
                "source_url": "bundled://unified-by-goshkow",
            }, force_refresh=True)
            if was_running:
                self.start_component("zapret")
            self.logging.log("info", "Zapret updated", version=latest_version, backup=str(backup.path if backup else ""))
            return {"status": "updated", "version": latest_version or current_version}
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def update_tg_ws_proxy_runtime(self) -> dict[str, str]:
        release = self.fetch_latest_tg_ws_proxy_release()
        latest_version = str(release.get("latest_version", "")).strip()
        current_version = self.storage._detect_tgws_version()
        if latest_version and current_version == latest_version:
            return {"status": "up-to-date", "version": current_version}
        source_url = str(release.get("source_url", "")).strip()
        exe_url = str(release.get("exe_url", "")).strip()
        if not source_url or not exe_url:
            return {"status": "error", "error": "No tg-ws-proxy source or Windows asset found"}

        runtime_root = self.storage.paths.runtime_dir / "tg-ws-proxy"
        was_running = False
        try:
            tg_state = next((item for item in self.list_states() if item.component_id == "tg-ws-proxy"), None)
            was_running = bool(tg_state and tg_state.status == "running")
        except Exception:
            was_running = False
        temp_root = Path(tempfile.mkdtemp(prefix="zapret_hub_tgws_update_"))
        try:
            source_zip = temp_root / "tg-ws-proxy.zip"
            with urlopen(Request(source_url, headers={"User-Agent": f"ZapretHub/{__version__}"}), timeout=60) as response:
                source_zip.write_bytes(response.read())
            extract_root = temp_root / "extract"
            extract_root.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source_zip, "r") as archive:
                archive.extractall(extract_root)
            source_root = next((p for p in extract_root.iterdir() if p.is_dir() and (p / "proxy").exists()), None)
            if source_root is None:
                return {"status": "error", "error": "Invalid tg-ws-proxy source archive"}

            windows_exe_path = temp_root / str(release.get("exe_name", "TgWsProxy_windows.exe"))
            with urlopen(Request(exe_url, headers={"User-Agent": f"ZapretHub/{__version__}"}), timeout=60) as response:
                windows_exe_path.write_bytes(response.read())

            if was_running:
                self.stop_component("tg-ws-proxy")

            backup = self.storage.create_backup(runtime_root, "pre-update-tg-ws-proxy")
            staging_root = temp_root / "runtime_new"
            shutil.copytree(source_root, staging_root, dirs_exist_ok=True)
            (staging_root / "bin").mkdir(parents=True, exist_ok=True)
            shutil.copy2(windows_exe_path, staging_root / "bin" / "TgWsProxy_windows.exe")

            if runtime_root.exists():
                shutil.rmtree(runtime_root, ignore_errors=True)
            shutil.copytree(staging_root, runtime_root, dirs_exist_ok=True)
            init_py = runtime_root / "proxy" / "__init__.py"
            if latest_version and init_py.exists():
                try:
                    content = init_py.read_text(encoding="utf-8", errors="ignore")
                    content = re.sub(r'__version__\s*=\s*["\'].*?["\']', f'__version__ = "{latest_version}"', content, count=1)
                    init_py.write_text(content, encoding="utf-8")
                except Exception:
                    pass
            self.storage.ensure_layout()
            if was_running:
                self.start_component("tg-ws-proxy")
            self.logging.log(
                "info",
                "TG WS Proxy updated",
                version=latest_version,
                backup=str(backup.path if backup else ""),
            )
            return {"status": "updated", "version": latest_version or current_version}
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def _cleanup_merged_runtime(self) -> None:
        self._cleanup_inactive_zapret_runtimes()
        current_root = self._current_zapret_runtime
        if current_root and current_root.exists():
            self._reset_active_runtime_dir(current_root)
        self._current_zapret_runtime = None

    def _run_general_connectivity_check(
        self,
        general_id: str,
        stop_callback: callable | None = None,
        targets: list[dict[str, str]] | None = None,
        progress_callback: callable | None = None,
    ) -> dict[str, object]:
        self.settings.update(selected_zapret_general=general_id)
        state = self._start_zapret("zapret")
        if state.status != "running":
            return {
                "status": "error",
                "error": state.last_error or "failed to start",
                "passed_targets": 0,
                "total_targets": 0,
            }

        targets = list(targets or self._load_standard_test_targets())
        if not targets:
            return {
                "status": "ok",
                "error": "",
                "passed_targets": 0,
                "total_targets": 0,
            }

        if stop_callback is not None and stop_callback():
            return {
                "status": "cancelled",
                "error": "cancelled",
                "passed_targets": 0,
                "total_targets": len(targets),
            }

        passed_targets = 0
        failed_names: list[str] = []
        completed_targets = 0
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(targets)))) as executor:
            future_map = {executor.submit(self._target_is_reachable, target): target for target in targets}
            for future in as_completed(future_map):
                if stop_callback is not None and stop_callback():
                    executor.shutdown(wait=False, cancel_futures=True)
                    return {
                        "status": "cancelled",
                        "error": "cancelled",
                        "passed_targets": passed_targets,
                        "total_targets": len(targets),
                    }
                target = future_map[future]
                try:
                    ok = future.result()
                except Exception:
                    ok = False
                if ok:
                    passed_targets += 1
                else:
                    failed_names.append(str(target["name"]))
                completed_targets += 1
                if progress_callback is not None:
                    progress_callback(completed_targets, len(targets), str(target.get("name", "")))

        if failed_names:
            return {
                "status": "error",
                "error": f"failed targets: {', '.join(failed_names[:6])}",
                "passed_targets": passed_targets,
                "total_targets": len(targets),
            }
        return {
            "status": "ok",
            "error": "",
            "passed_targets": passed_targets,
            "total_targets": len(targets),
        }

    def _load_standard_test_targets(self) -> list[dict[str, str]]:
        targets_file = self.storage.paths.runtime_dir / "zapret-discord-youtube" / "utils" / "targets.txt"
        targets: list[dict[str, str]] = []
        if targets_file.exists():
            pattern = re.compile(r'^\s*(.+?)\s*=\s*"(.+)"\s*$')
            for raw in targets_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                match = pattern.match(raw.strip())
                if not match:
                    continue
                name = match.group(1).strip()
                value = match.group(2).strip()
                targets.append(self._convert_test_target(name, value))
        if targets:
            return targets

        defaults = [
            ("Discord Main", "https://discord.com"),
            ("Discord Gateway", "https://gateway.discord.gg"),
            ("YouTube Web", "https://www.youtube.com"),
            ("Google Main", "https://www.google.com"),
            ("Cloudflare Web", "https://www.cloudflare.com"),
            ("Cloudflare DNS 1.1.1.1", "PING:1.1.1.1"),
            ("Google DNS 8.8.8.8", "PING:8.8.8.8"),
        ]
        return [self._convert_test_target(name, value) for name, value in defaults]

    def _convert_test_target(self, name: str, value: str) -> dict[str, str]:
        if value.upper().startswith("PING:"):
            host = value.split(":", 1)[1].strip()
            return {"name": name, "type": "ping", "host": host}
        host = value.replace("https://", "").replace("http://", "").split("/", 1)[0].strip()
        return {"name": name, "type": "url", "url": value, "host": host}

    def _target_is_reachable(self, target: dict[str, str]) -> bool:
        target_type = target.get("type", "url")
        if target_type == "ping":
            return self._ping_target(target.get("host", ""))

        url = target.get("url", "").strip()
        if not url:
            return False
        tests = [
            ["--http1.1"],
            ["--tlsv1.2", "--tls-max", "1.2"],
            ["--tlsv1.3", "--tls-max", "1.3"],
        ]
        for extra in tests:
            if self._curl_target(url, extra):
                return True
        return False

    def _curl_target(self, url: str, extra_args: list[str]) -> bool:
        curl_path = shutil.which("curl.exe") or shutil.which("curl")
        if not curl_path:
            return False
        proc = self._run_quiet(
            [
                curl_path,
                "-I",
                "-s",
                "--connect-timeout",
                "2",
                "-m",
                "3",
                "-o",
                "NUL",
                "-w",
                "%{http_code}",
                "--show-error",
                *extra_args,
                url,
            ]
        )
        code = (proc.stdout or "").strip()
        return proc.returncode == 0 and bool(code)

    def _ping_target(self, host: str) -> bool:
        if not host:
            return False
        proc = self._run_quiet(["ping", "-n", "1", "-w", "1200", host])
        return proc.returncode == 0

    def _build_zapret_args(self, bin_dir: Path, lists_dir: Path) -> list[str]:
        tls_google = str(bin_dir / "tls_clienthello_www_google_com.bin")
        tls_4pda = str(bin_dir / "tls_clienthello_4pda_to.bin")
        quic_google = str(bin_dir / "quic_initial_www_google_com.bin")
        list_general = str(lists_dir / "list-general.txt")
        list_general_user = str(lists_dir / "list-general-user.txt")
        list_exclude = str(lists_dir / "list-exclude.txt")
        list_exclude_user = str(lists_dir / "list-exclude-user.txt")
        ipset_all = str(lists_dir / "ipset-all.txt")
        ipset_all_user = str(lists_dir / "ipset-all-user.txt")
        ipset_exclude = str(lists_dir / "ipset-exclude.txt")
        ipset_exclude_user = str(lists_dir / "ipset-exclude-user.txt")

        return [
            "--wf-tcp=80,443,2053,2083,2087,2096,8443",
            "--wf-udp=443,19294-19344,50000-50100",
            "--filter-udp=443",
            f"--hostlist={list_general}",
            f"--hostlist={list_general_user}",
            f"--hostlist-exclude={list_exclude}",
            f"--hostlist-exclude={list_exclude_user}",
            f"--ipset-exclude={ipset_exclude}",
            f"--ipset-exclude={ipset_exclude_user}",
            "--dpi-desync=fake",
            "--dpi-desync-repeats=6",
            f"--dpi-desync-fake-quic={quic_google}",
            "--new",
            "--filter-udp=19294-19344,50000-50100",
            "--filter-l7=discord,stun",
            "--dpi-desync=fake",
            "--dpi-desync-repeats=6",
            "--new",
            "--filter-tcp=2053,2083,2087,2096,8443",
            "--hostlist-domains=discord.media",
            "--dpi-desync=multisplit",
            "--dpi-desync-split-seqovl=681",
            "--dpi-desync-split-pos=1",
            f"--dpi-desync-split-seqovl-pattern={tls_google}",
            "--new",
            "--filter-tcp=443",
            f"--hostlist={str(lists_dir / 'list-google.txt')}",
            "--ip-id=zero",
            "--dpi-desync=multisplit",
            "--dpi-desync-split-seqovl=681",
            "--dpi-desync-split-pos=1",
            f"--dpi-desync-split-seqovl-pattern={tls_google}",
            "--new",
            "--filter-tcp=80,443",
            f"--hostlist={list_general}",
            f"--hostlist={list_general_user}",
            f"--hostlist-exclude={list_exclude}",
            f"--hostlist-exclude={list_exclude_user}",
            f"--ipset-exclude={ipset_exclude}",
            f"--ipset-exclude={ipset_exclude_user}",
            "--dpi-desync=multisplit",
            "--dpi-desync-split-seqovl=568",
            "--dpi-desync-split-pos=1",
            f"--dpi-desync-split-seqovl-pattern={tls_4pda}",
            "--new",
            "--filter-udp=443",
            f"--ipset={ipset_all}",
            f"--ipset={ipset_all_user}",
            f"--hostlist-exclude={list_exclude}",
            f"--hostlist-exclude={list_exclude_user}",
            f"--ipset-exclude={ipset_exclude}",
            f"--ipset-exclude={ipset_exclude_user}",
            "--dpi-desync=fake",
            "--dpi-desync-repeats=6",
            f"--dpi-desync-fake-quic={quic_google}",
            "--new",
            "--filter-tcp=80,443,8443",
            f"--ipset={ipset_all}",
            f"--ipset={ipset_all_user}",
            f"--hostlist-exclude={list_exclude}",
            f"--hostlist-exclude={list_exclude_user}",
            f"--ipset-exclude={ipset_exclude}",
            f"--ipset-exclude={ipset_exclude_user}",
            "--dpi-desync=multisplit",
            "--dpi-desync-split-seqovl=568",
            "--dpi-desync-split-pos=1",
            f"--dpi-desync-split-seqovl-pattern={tls_4pda}",
            "--new",
            "--filter-tcp=1024-65535",
            f"--ipset={ipset_all}",
            f"--ipset={ipset_all_user}",
            f"--ipset-exclude={ipset_exclude}",
            f"--ipset-exclude={ipset_exclude_user}",
            "--dpi-desync=multisplit",
            "--dpi-desync-any-protocol=1",
            "--dpi-desync-cutoff=n3",
            "--dpi-desync-split-seqovl=568",
            "--dpi-desync-split-pos=1",
            f"--dpi-desync-split-seqovl-pattern={tls_4pda}",
            "--new",
            "--filter-udp=1024-65535",
            f"--ipset={ipset_all}",
            f"--ipset={ipset_all_user}",
            f"--ipset-exclude={ipset_exclude}",
            f"--ipset-exclude={ipset_exclude_user}",
            "--dpi-desync=fake",
            "--dpi-desync-repeats=12",
            "--dpi-desync-any-protocol=1",
            f"--dpi-desync-fake-unknown-udp={quic_google}",
            "--dpi-desync-cutoff=n2",
        ]

    def _ensure_zapret_user_lists(self, lists_dir: Path) -> None:
        defaults = {
            "ipset-all-user.txt": "",
            "ipset-exclude-user.txt": "",
            "list-general-user.txt": "",
            "list-exclude-user.txt": "",
        }
        for filename, content in defaults.items():
            source = self.storage.paths.configs_dir / filename
            target = lists_dir / filename
            if source.exists():
                try:
                    target.write_text(source.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
                    continue
                except Exception:
                    pass
            if not target.exists():
                target.write_text(content, encoding="utf-8")

    def _is_image_running(self, image_name: str) -> bool:
        proc = self._run_quiet(["tasklist", "/FI", f"IMAGENAME eq {image_name}"])
        output = (proc.stdout or "").lower()
        return image_name.lower() in output

    def _kill_image(self, image_name: str) -> None:
        self._run_quiet(["taskkill", "/IM", image_name, "/F", "/T"])

    def _force_stop_zapret_runtime(self) -> None:
        process = self._processes.get("zapret")
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if process and process.pid:
            self._run_quiet(["taskkill", "/PID", str(process.pid), "/F", "/T"])
        if self._zapret_service_exists():
            self._run_quiet(["sc", "stop", "zapret"])
            self._run_quiet(["sc", "delete", "zapret"])
        for _ in range(8):
            self._kill_image("winws.exe")
            if not self._is_image_running("winws.exe"):
                break
            time.sleep(0.35)
        self._processes.pop("zapret", None)
        self._current_zapret_runtime = None

    def _reset_active_runtime_dir(self, active_root: Path) -> None:
        for _ in range(6):
            try:
                shutil.rmtree(active_root, ignore_errors=False)
                return
            except PermissionError:
                self._force_stop_zapret_runtime()
                time.sleep(0.35)
            except Exception:
                shutil.rmtree(active_root, ignore_errors=True)
                if not active_root.exists():
                    return
        quarantine_root = Path(tempfile.gettempdir()) / "zapret_hub_runtime_cleanup"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        quarantine_target = quarantine_root / f"active_zapret_{int(time.time() * 1000)}"
        try:
            shutil.move(str(active_root), str(quarantine_target))
            shutil.rmtree(quarantine_target, ignore_errors=True)
        except Exception:
            shutil.rmtree(active_root, ignore_errors=True)

    def _next_active_runtime_dir(self) -> Path:
        self.storage.paths.merged_runtime_dir.mkdir(parents=True, exist_ok=True)
        return self.storage.paths.merged_runtime_dir / f"active_zapret_{int(time.time() * 1000)}"

    def _cleanup_inactive_zapret_runtimes(self) -> None:
        merged_root = self.storage.paths.merged_runtime_dir
        if not merged_root.exists():
            return
        current_root = self._current_zapret_runtime.resolve() if self._current_zapret_runtime and self._current_zapret_runtime.exists() else None
        for candidate in merged_root.glob("active_zapret*"):
            try:
                if current_root and candidate.resolve() == current_root:
                    continue
            except Exception:
                pass
            self._reset_active_runtime_dir(candidate)

    def _zapret_service_exists(self) -> bool:
        proc = self._run_quiet(["sc", "query", "zapret"])
        return proc.returncode == 0

    def _run_quiet(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            creationflags=self._creationflags,
            startupinfo=self._startupinfo,
        )

    def _is_port_listening(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.8):
                return True
        except OSError:
            return False

    def _open_source_log_stream(self, source: str):
        self._close_source_log_stream(source)
        path = Path(self.logging.source_log_path(source))
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8", errors="ignore")
        handle.write(f"\n[{datetime.utcnow().isoformat()}] session-start\n")
        handle.flush()
        self._log_streams[source] = handle
        return handle

    def _close_source_log_stream(self, source: str) -> None:
        handle = self._log_streams.pop(source, None)
        if handle is None:
            return
        try:
            handle.flush()
            handle.close()
        except Exception:
            pass

    def _is_telegram_running(self) -> bool:
        for image_name in ("Telegram.exe", "Telegram Desktop.exe"):
            if self._is_image_running(image_name):
                return True
        return False

    def _start_telegram_desktop(self) -> bool:
        candidates = [
            Path(os.environ.get("APPDATA", "")) / "Telegram Desktop" / "Telegram.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Telegram Desktop" / "Telegram.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Telegram Desktop" / "Telegram.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Telegram Desktop" / "Telegram.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                try:
                    subprocess.Popen(
                        [str(candidate)],
                        creationflags=self._creationflags,
                        startupinfo=self._startupinfo,
                    )
                    self.logging.log("info", "Telegram launch requested", path=str(candidate))
                    return True
                except Exception as error:
                    self.logging.log("warning", "Failed to start Telegram", path=str(candidate), error=str(error))
        return False

    def _ensure_telegram_and_open_proxy_link(self, host: str, port: int, secret: str) -> None:
        self.logging.log("info", "TG WS Proxy auto-connect requested", component_id="tg-ws-proxy", host=host, port=port)
        if not self._is_telegram_running():
            self.logging.log("info", "Telegram Desktop is not running, attempting to launch it", component_id="tg-ws-proxy")
            self._start_telegram_desktop()
            for _ in range(40):
                if self._is_telegram_running():
                    self.logging.log("info", "Telegram Desktop detected after launch", component_id="tg-ws-proxy")
                    break
                time.sleep(0.25)
        if not self._is_telegram_running():
            self.logging.log("warning", "Telegram was not detected after proxy start", component_id="tg-ws-proxy")
            return
        self.logging.log("info", "Sending proxy link to Telegram", component_id="tg-ws-proxy")
        self._open_telegram_proxy_link(host=host, port=port, secret=secret)

    def _open_telegram_proxy_link(self, host: str, port: int, secret: str) -> None:
        link = f"tg://proxy?server={host}&port={port}&secret=dd{secret}"
        try:
            if sys.platform.startswith("win"):
                os.startfile(link)  # type: ignore[attr-defined]
            else:
                webbrowser.open(link)
            self.logging.log("info", "Telegram proxy link opened", link=link)
        except Exception as error:
            self.logging.log("warning", "Failed to open Telegram proxy link", link=link, error=str(error))
