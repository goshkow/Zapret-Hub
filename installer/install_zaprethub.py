from __future__ import annotations

import ctypes
import base64
from datetime import datetime
import locale
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from installer.embedded_app_icon import APP_PNG_BASE64
from PySide6.QtCore import QEasingCurve, QEvent, QObject, Property, QPropertyAnimation, QRectF, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QMouseEvent, QPainter, QPen, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QProgressBar,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

if sys.platform.startswith("win"):
    import winreg


def _is_ru() -> bool:
    try:
        lang = (locale.getdefaultlocale()[0] or "").lower()  # type: ignore[call-arg]
    except Exception:
        lang = ""
    return lang.startswith("ru")


RU = _is_ru()
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ZapretHub"
INSTALLER_LOG_PATH = Path(tempfile.gettempdir()) / "zapret_hub_installer.log"

def tr(ru: str, en: str) -> str:
    return ru if RU else en


def _resource_candidates() -> list[Path]:
    candidates: list[Path] = []
    try:
        file_path = Path(__file__).resolve()
    except Exception:
        file_path = None
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir)
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass))
        if file_path is not None:
            candidates.append(file_path.parent)
            for parent in file_path.parents:
                candidates.append(parent)
    else:
        if file_path is not None:
            candidates.append(file_path.parents[1])
            candidates.append(file_path.parent)
            for parent in file_path.parents:
                candidates.append(parent)
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def resource_root() -> Path:
    for candidate in _resource_candidates():
        if (candidate / "ui_assets" / "icons" / "installer_runtime_icon.png").exists():
            return candidate
    for candidate in _resource_candidates():
        if (candidate / "ui_assets" / "icons" / "app.png").exists():
            return candidate
    for candidate in _resource_candidates():
        if (candidate / "ui_assets" / "icons" / "app.ico").exists():
            return candidate
    return _resource_candidates()[0]


def payload_root() -> Path:
    for candidate in _resource_candidates():
        if (candidate / "installer_payload").exists():
            return candidate
        if (candidate / "win_x64.zip").exists() or (candidate / "win_arm64.zip").exists():
            return candidate
    return resource_root()


def _installer_log(event: str, **context: object) -> None:
    try:
        timestamp = datetime.now().isoformat(timespec="seconds")
        details = ", ".join(f"{key}={context[key]!r}" for key in sorted(context))
        line = f"[{timestamp}] {event}"
        if details:
            line += f" | {details}"
        with INSTALLER_LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except Exception:
        return


def _is_within_path(path: Path, root: Path) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
        resolved_path.relative_to(resolved_root)
        return True
    except Exception:
        return False


def _top_level_install_name(path: Path, install_dir: Path) -> str:
    try:
        relative = path.resolve().relative_to(install_dir.resolve())
    except Exception:
        return ""
    parts = relative.parts
    return parts[0] if parts else ""


def _is_preserved_user_root(path: Path, install_dir: Path) -> bool:
    return _top_level_install_name(path, install_dir) in {"data", "mods", "configs", "cache"}


def _embedded_app_pixmap() -> QPixmap:
    try:
        raw = base64.b64decode(APP_PNG_BASE64)
    except Exception:
        return QPixmap()
    image = QImage.fromData(raw, "PNG")
    if image.isNull():
        return QPixmap()
    return QPixmap.fromImage(image)


def app_icon() -> QIcon:
    embedded = _embedded_app_pixmap()
    if not embedded.isNull():
        return QIcon(embedded)
    installer_png_path = resource_root() / "ui_assets" / "icons" / "installer_runtime_icon.png"
    if installer_png_path.exists():
        image = QImage(str(installer_png_path))
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            if not pixmap.isNull():
                return QIcon(pixmap)
    png_path = resource_root() / "ui_assets" / "icons" / "app.png"
    if png_path.exists():
        image = QImage(str(png_path))
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            if not pixmap.isNull():
                return QIcon(pixmap)
    icon_path = resource_root() / "ui_assets" / "icons" / "app.ico"
    if icon_path.exists():
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            return icon
    if getattr(sys, "frozen", False):
        icon = QIcon(str(Path(sys.executable)))
        if not icon.isNull():
            return icon
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QColor("#5865f2"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
    painter.setPen(QPen(QColor("#ffffff"), 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(20, 44, 30, 24)
    painter.drawLine(30, 24, 44, 40)
    painter.end()
    return QIcon(pixmap)


def app_pixmap(size: int) -> QPixmap:
    embedded = _embedded_app_pixmap()
    dpr = 1.0
    app_instance = QApplication.instance()
    try:
        if app_instance is not None and app_instance.primaryScreen() is not None:
            dpr = max(1.0, float(app_instance.primaryScreen().devicePixelRatio()))
    except Exception:
        dpr = 1.0
    target_px = max(size, int(round(size * dpr)))
    if not embedded.isNull():
        scaled = embedded.scaled(target_px, target_px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        return scaled
    installer_png_path = resource_root() / "ui_assets" / "icons" / "installer_runtime_icon.png"
    if installer_png_path.exists():
        image = QImage(str(installer_png_path))
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        if not pixmap.isNull():
            scaled = pixmap.scaled(target_px, target_px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            return scaled
    icon_path = resource_root() / "ui_assets" / "icons" / "app.png"
    if icon_path.exists():
        image = QImage(str(icon_path))
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        if not pixmap.isNull():
            scaled = pixmap.scaled(target_px, target_px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            return scaled
    ico_path = resource_root() / "ui_assets" / "icons" / "app.ico"
    if ico_path.exists():
        pixmap = QPixmap(str(ico_path))
        if not pixmap.isNull():
            scaled = pixmap.scaled(target_px, target_px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            return scaled
    return app_icon().pixmap(size, size)


def close_icon() -> QIcon:
    icon_path = resource_root() / "ui_assets" / "icons" / "window_close_dark.svg"
    if icon_path.exists():
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            return icon
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor("#e7edf9"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(7, 7, 17, 17)
    painter.drawLine(17, 7, 7, 17)
    painter.end()
    return QIcon(pixmap)


def close_pixmap(size: int) -> QPixmap:
    icon = close_icon()
    pixmap = icon.pixmap(size, size)
    if not pixmap.isNull():
        return pixmap
    fallback = QPixmap(size, size)
    fallback.fill(Qt.GlobalColor.transparent)
    painter = QPainter(fallback)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor("#e7edf9"), max(1.8, size / 10.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    inset = max(5, int(size * 0.28))
    painter.drawLine(inset, inset, size - inset, size - inset)
    painter.drawLine(size - inset, inset, inset, size - inset)
    painter.end()
    return fallback


def apply_native_window_icons(widget: QWidget) -> None:
    if not sys.platform.startswith("win"):
        return
    icon = app_icon()
    try:
        widget.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)
    except Exception:
        pass


def title_logo() -> QIcon:
    png_path = resource_root() / "ui_assets" / "icons" / "app.png"
    if png_path.exists():
        icon = QIcon(str(png_path))
        if not icon.isNull():
            return icon
    return app_icon()


def default_install_dir() -> Path:
    return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Zapret Hub"


def _native_windows_machine() -> str:
    if not sys.platform.startswith("win"):
        return platform.machine().lower()
    try:
        process_machine = ctypes.c_ushort(0)
        native_machine = ctypes.c_ushort(0)
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        is_wow64_process2 = getattr(kernel32, "IsWow64Process2", None)
        if is_wow64_process2:
            current_process = kernel32.GetCurrentProcess()
            ok = is_wow64_process2(current_process, ctypes.byref(process_machine), ctypes.byref(native_machine))
            if ok:
                machine_map = {
                    0x014c: "x86",
                    0x8664: "amd64",
                    0xAA64: "arm64",
                }
                return machine_map.get(int(native_machine.value), platform.machine().lower())
    except Exception:
        pass
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine()).lower()
    if "arm64" in arch or "aarch64" in arch:
        return "arm64"
    if "amd64" in arch or "x86_64" in arch or "x64" in arch:
        return "amd64"
    return arch


def detect_payload_name() -> str:
    machine = _native_windows_machine()
    if "arm" in machine or "aarch64" in machine:
        return "win_arm64.zip"
    return "win_x64.zip"


def is_admin() -> bool:
    if not sys.platform.startswith("win"):
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def relaunch_with_elevation(args: list[str]) -> bool:
    if not sys.platform.startswith("win"):
        return True
    if not getattr(sys, "frozen", False):
        return False
    cmd = " ".join(f'"{arg}"' for arg in args)
    result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
        None, "runas", sys.executable, cmd, None, 1
    )
    return int(result) > 32


class ButtonInteractionOverlay(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._pressed = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.setVisible(self._progress > 0.001)
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def set_pressed(self, pressed: bool) -> None:
        self._pressed = bool(pressed)
        self.update()

    def paintEvent(self, event: QEvent) -> None:
        if self._progress <= 0.001:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        base = self.parentWidget().palette().button().color() if self.parentWidget() is not None else QColor('#1f2430')
        if base.lightness() < 128:
            overlay = QColor(255, 255, 255)
            max_alpha = 28 if not self._pressed else 42
        else:
            overlay = QColor(31, 41, 55)
            max_alpha = 14 if not self._pressed else 22
        overlay.setAlpha(int(max_alpha * self._progress))
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = min(18.0, max(8.0, min(rect.width(), rect.height()) / 2.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(overlay)
        painter.drawRoundedRect(rect, radius, radius)


class ButtonInteractionFilter(QObject):
    def __init__(self, widget: QWidget) -> None:
        super().__init__(widget)
        self._widget = widget
        self._overlay = ButtonInteractionOverlay(widget)
        self._overlay.setGeometry(widget.rect())
        self._animation: QPropertyAnimation | None = None
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._widget:
            if event.type() in {QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Move}:
                self._overlay.setGeometry(self._widget.rect())
                self._overlay.raise_()
            elif event.type() == QEvent.Type.Enter:
                self._overlay.raise_()
                self._overlay.set_pressed(False)
                self._animate(1.0, 180)
            elif event.type() == QEvent.Type.Leave:
                self._overlay.set_pressed(False)
                self._animate(0.0, 180)
            elif event.type() == QEvent.Type.MouseButtonPress:
                self._overlay.raise_()
                self._overlay.set_pressed(True)
                self._animate(1.0, 90)
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._overlay.set_pressed(False)
                self._animate(1.0 if self._widget.underMouse() else 0.0, 150)
        return super().eventFilter(watched, event)

    def _animate(self, target: float, duration: int) -> None:
        if self._animation is not None:
            self._animation.stop()
        animation = QPropertyAnimation(self._overlay, b"progress", self)
        animation.setDuration(duration)
        animation.setStartValue(self._overlay.progress)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.start()
        self._animation = animation


def attach_button_animations(widget: QWidget) -> None:
    if not isinstance(widget, (QPushButton, QToolButton)):
        return
    if widget.property("_interactionBound"):
        return
    widget.setProperty("_interactionBound", True)
    ButtonInteractionFilter(widget)


def set_windows_app_id() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("goshkow.ZapretHub.NuitkaInstaller.1.4.2.pngsync2")  # type: ignore[attr-defined]
    except Exception:
        return


def disable_native_window_rounding(hwnd: int) -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_DONOTROUND = 1
        value = ctypes.c_int(DWMWCP_DONOTROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception:
        return


def bring_widget_to_front(widget: QWidget) -> None:
    widget.raise_()
    widget.activateWindow()
    if not sys.platform.startswith("win"):
        return
    try:
        hwnd = int(widget.winId())
        SW_RESTORE = 9
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)  # type: ignore[attr-defined]
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)  # type: ignore[attr-defined]
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)  # type: ignore[attr-defined]
        ctypes.windll.user32.SetForegroundWindow(hwnd)  # type: ignore[attr-defined]
    except Exception:
        return


def _run_hidden(command: list[str]) -> None:
    startup = None
    flags = 0
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    subprocess.run(command, check=False, capture_output=True, creationflags=flags, startupinfo=startup)


def _run_hidden_script(script: str) -> None:
    startup = None
    flags = 0
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", script],
        check=False,
        capture_output=True,
        creationflags=flags,
        startupinfo=startup,
    )


def _remove_autostart_entries() -> None:
    if not sys.platform.startswith("win"):
        return
    _run_hidden(["schtasks", "/Delete", "/F", "/TN", "ZapretHub"])
    ps = r"""
$paths = @(
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run'
)
$names = @('ZapretHub', 'Zapret Hub', 'zapret_hub')
foreach ($path in $paths) {
  foreach ($name in $names) {
    try { Remove-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue } catch {}
  }
}
"""
    _run_hidden_script(ps)


def _terminate_running_instances(install_dir: Path | None = None) -> None:
    if not sys.platform.startswith("win"):
        return
    _remove_autostart_entries()
    _run_hidden(["sc", "stop", "zapret"])
    _run_hidden(["sc", "delete", "zapret"])
    for image_name in ("zapret_hub.exe", "TgWsProxy_windows.exe", "winws.exe"):
        _run_hidden(["taskkill", "/F", "/T", "/IM", image_name])
    if install_dir is not None:
        target = str(install_dir).lower().replace("'", "''")
        current_pid = os.getpid()
        ps = f"""
$needle = '{target}'
$selfPid = {current_pid}
Get-CimInstance Win32_Process | ForEach-Object {{
  if ($_.ProcessId -eq $selfPid) {{ return }}
  $exe = ''
  $cmd = ''
  try {{ $exe = [string]$_.ExecutablePath }} catch {{}}
  try {{ $cmd = [string]$_.CommandLine }} catch {{}}
  $joined = ($exe + ' ' + $cmd).ToLowerInvariant()
  if ($joined.Contains($needle)) {{
    try {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }} catch {{}}
  }}
}}
"""
        _run_hidden_script(ps)
        merged_runtime = (install_dir / "merged_runtime").resolve()
        active_runtime = (merged_runtime / "active_zapret").resolve()
        ps_handles = f"""
$paths = @('{str(merged_runtime).lower().replace("'", "''")}', '{str(active_runtime).lower().replace("'", "''")}')
$selfPid = {current_pid}
Get-CimInstance Win32_Process | ForEach-Object {{
  if ($_.ProcessId -eq $selfPid) {{ return }}
  $exe = ''
  $cmd = ''
  try {{ $exe = [string]$_.ExecutablePath }} catch {{}}
  try {{ $cmd = [string]$_.CommandLine }} catch {{}}
  $joined = ($exe + ' ' + $cmd).ToLowerInvariant()
  foreach ($path in $paths) {{
    if ($joined.Contains($path)) {{
      try {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }} catch {{}}
      break
    }}
  }}
}}
"""
        _run_hidden_script(ps_handles)
    time.sleep(0.35)


def _remove_shortcuts() -> None:
    shortcut_paths = [
        Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Zapret Hub.lnk",
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop" / "Zapret Hub.lnk",
        Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Zapret Hub.lnk",
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs\Zapret Hub.lnk",
    ]
    for path in shortcut_paths:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            continue


def _clear_path_attributes(path: Path) -> None:
    if not sys.platform.startswith("win") or not path.exists():
        return
    if path.is_dir():
        _run_hidden(["cmd", "/c", f'attrib -r -s -h "{path}" /s /d'])
    else:
        _run_hidden(["attrib", "-r", "-s", "-h", str(path)])


def _schedule_delete_on_reboot(path: Path) -> None:
    if not sys.platform.startswith("win") or not path.exists():
        return
    try:
        MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
        ctypes.windll.kernel32.MoveFileExW(str(path), None, MOVEFILE_DELAY_UNTIL_REBOOT)  # type: ignore[attr-defined]
    except Exception:
        return


def _quarantine_item(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        quarantine_root = Path(tempfile.gettempdir()) / "zapret_hub_cleanup"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        target = quarantine_root / f"{path.name}_{int(time.time() * 1000)}"
        shutil.move(str(path), str(target))
        try:
            _clear_path_attributes(target)
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink(missing_ok=True)
        finally:
            if target.exists():
                _schedule_delete_on_reboot(target)
        return not path.exists()
    except Exception:
        return False


def _safe_remove_item(path: Path, install_dir: Path | None = None) -> None:
    for _ in range(6):
        try:
            if not path.exists():
                return
            _clear_path_attributes(path)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=False)
            else:
                path.unlink()
            return
        except PermissionError:
            _terminate_running_instances(install_dir or path.parent)
            time.sleep(0.45)
        except Exception:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                return
            raise
    if path.exists():
        raise PermissionError(f"cannot replace: {path}")


def _wipe_install_dir(install_dir: Path) -> None:
    if not install_dir.exists():
        return
    ignored_leftovers = {"merged_runtime", "backups", "logs"}
    for _ in range(6):
        _terminate_running_instances(install_dir)
        for item in list(install_dir.iterdir()):
            try:
                _safe_remove_item(item, install_dir)
            except Exception:
                if item.name in ignored_leftovers:
                    if _quarantine_item(item):
                        continue
                    continue
                if _quarantine_item(item):
                    continue
                raise
        if not any(install_dir.iterdir()):
            return
        time.sleep(0.5)
    remaining = next((item for item in install_dir.iterdir() if item.name not in ignored_leftovers), None)
    if remaining is None:
        return
    raise PermissionError(f"cannot replace: {remaining}")


def _overlay_tree(source: Path, target: Path, install_dir: Path, preserve_names: set[str] | None = None) -> None:
    if not _is_within_path(target, install_dir):
        raise PermissionError(f"write target escaped install dir: {target}")
    preserve_names = preserve_names or set()
    target.mkdir(parents=True, exist_ok=True)
    source_names = {item.name for item in source.iterdir()}
    for existing in list(target.iterdir()):
        if existing.name in preserve_names:
            continue
        if existing.name in source_names:
            continue
        try:
            _safe_remove_item(existing, install_dir)
        except Exception:
            if not _quarantine_item(existing):
                if existing.is_dir() and not _is_preserved_user_root(existing, install_dir):
                    continue
                raise
    for item in source.iterdir():
        if item.name in preserve_names:
            continue
        dst = target / item.name
        if item.is_dir():
            _overlay_tree(item, dst, install_dir)
            continue
        if dst.exists():
            try:
                _safe_remove_item(dst, install_dir)
            except Exception:
                if not _quarantine_item(dst) and _is_preserved_user_root(dst, install_dir):
                    raise
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(item, dst)
        except Exception:
            if _is_preserved_user_root(dst, install_dir):
                raise


def _write_uninstall_registry(install_dir: Path, uninstaller_exe: Path, app_exe: Path) -> None:
    if not sys.platform.startswith("win"):
        return
    uninstall_cmd = f'"{uninstaller_exe}" --uninstall --install-dir "{install_dir}"'
    values = {
        "DisplayName": "Zapret Hub",
        "DisplayVersion": "1.4.0",
        "Publisher": "goshkow",
        "InstallLocation": str(install_dir),
        "DisplayIcon": str(app_exe),
        "UninstallString": uninstall_cmd,
        "QuietUninstallString": f'{uninstall_cmd} --silent',
        "NoModify": 1,
        "NoRepair": 1,
    }
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            access = winreg.KEY_WRITE
            if root == winreg.HKEY_LOCAL_MACHINE:
                access |= winreg.KEY_WOW64_64KEY
            with winreg.CreateKeyEx(root, UNINSTALL_KEY, 0, access) as key:
                for name, value in values.items():
                    if isinstance(value, int):
                        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
                    else:
                        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            return
        except Exception:
            continue


def _remove_uninstall_registry() -> None:
    if not sys.platform.startswith("win"):
        return
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            access = winreg.KEY_WRITE
            if root == winreg.HKEY_LOCAL_MACHINE:
                access |= winreg.KEY_WOW64_64KEY
            winreg.DeleteKeyEx(root, UNINSTALL_KEY, access=access, reserved=0)
        except Exception:
            continue


def _install_dir_from_registry() -> Path | None:
    if not sys.platform.startswith("win"):
        return None
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            access = winreg.KEY_READ
            if root == winreg.HKEY_LOCAL_MACHINE:
                access |= winreg.KEY_WOW64_64KEY
            with winreg.OpenKey(root, UNINSTALL_KEY, 0, access) as key:
                value, _ = winreg.QueryValueEx(key, "InstallLocation")
                path = Path(str(value))
                if path.exists():
                    return path
        except Exception:
            continue
    return None


def _launch_folder_removal(install_dir: Path) -> None:
    cmd = (
        "@echo off\r\n"
        ":retry\r\n"
        f'rmdir /s /q "{install_dir}"\r\n'
        f'if exist "{install_dir}" (\r\n'
        "  ping 127.0.0.1 -n 2 > nul\r\n"
        "  goto retry\r\n"
        ")\r\n"
    )
    startup = None
    flags = 0
    if sys.platform.startswith("win"):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    subprocess.Popen(["cmd", "/c", cmd], creationflags=flags, startupinfo=startup)


class InstallerDialog(QDialog):
    def __init__(
        self,
        title: str,
        text: str,
        with_yes_no: bool = False,
        parent: QWidget | None = None,
        yes_text: str | None = None,
        no_text: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._drag_pos = None
        self._result_yes = False
        self._result_mode = "cancel"
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setModal(True)
        self.setFixedSize(520, 230)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowIcon(app_icon())

        root = QWidget(self)
        root.setObjectName("DlgRoot")
        root.setGeometry(0, 0, 520, 230)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.title_bar = QFrame()
        self.title_bar.setObjectName("DlgTitle")
        self.title_bar.setFixedHeight(46)
        title_row = QHBoxLayout(self.title_bar)
        title_row.setContentsMargins(12, 8, 12, 8)
        title_row.setSpacing(8)
        icon = QLabel()
        icon.setFixedSize(20, 20)
        icon.setPixmap(app_icon().pixmap(20, 20))
        title_row.addWidget(icon)
        title_row.addWidget(QLabel(title))
        title_row.addStretch(1)
        close_btn = QToolButton()
        close_btn.setProperty("role", "close")
        close_btn.setIcon(QIcon(close_pixmap(14)))
        close_btn.setIconSize(QSize(14, 14))
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.reject)
        attach_button_animations(close_btn)
        title_row.addWidget(close_btn)
        layout.addWidget(self.title_bar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 16, 16, 16)
        body_layout.setSpacing(14)
        message = QLabel(text)
        message.setWordWrap(True)
        body_layout.addWidget(message, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        if with_yes_no:
            no_btn = QPushButton(no_text or tr("Нет", "No"))
            no_btn.clicked.connect(self._accept_no)
            yes_btn = QPushButton(yes_text or tr("Да", "Yes"))
            yes_btn.setObjectName("primary")
            yes_btn.clicked.connect(self._accept_yes)
            attach_button_animations(no_btn)
            attach_button_animations(yes_btn)
            row.addWidget(no_btn)
            row.addWidget(yes_btn)
        else:
            ok_btn = QPushButton("OK")
            ok_btn.setObjectName("primary")
            ok_btn.clicked.connect(self.accept)
            attach_button_animations(ok_btn)
            row.addWidget(ok_btn)
        body_layout.addLayout(row)
        layout.addWidget(body, 1)

        self.setStyleSheet(
            """
            #DlgRoot { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #11182a, stop:0.72 #11182a, stop:1 #162344); color: #dbe5fb; border: 1px solid #2a3f61; border-radius: 12px; font-family: Segoe UI; font-size: 10pt; }
            #DlgTitle { background: transparent; border-bottom: 1px solid #243551; }
            QLabel { background: transparent; color: #dbe5fb; }
            QPushButton { background: #253b62; border: 1px solid #396197; border-radius: 12px; padding: 8px 14px; min-width: 88px; color: #dbe5fb; }
            QPushButton#primary { background: #5865f2; border: 1px solid #7481ff; color: #fff; font-weight: 700; }
            QToolButton { border: none; background: transparent; min-width: 26px; min-height: 26px; max-width: 26px; max-height: 26px; border-radius: 12px; padding: 0px; margin: 0px; }
            QToolButton[role="close"]:hover { background: rgba(170, 84, 97, 0.62); border-radius: 12px; }
            """
        )

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        disable_native_window_rounding(int(self.winId()))
        apply_native_window_icons(self)
        bring_widget_to_front(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= self.title_bar.height():
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def _accept_yes(self) -> None:
        self._result_yes = True
        self._result_mode = "yes"
        self.accept()

    def _accept_no(self) -> None:
        self._result_yes = False
        self._result_mode = "no"
        self.accept()

    @property
    def result_yes(self) -> bool:
        return self._result_yes

    @property
    def result_mode(self) -> str:
        return self._result_mode


class InstallerWorker(QThread):
    progress = Signal(int)
    done = Signal(bool, str)

    def __init__(self, target_dir: Path, preserve_data: bool) -> None:
        super().__init__()
        self.target_dir = target_dir
        self.preserve_data = preserve_data

    def run(self) -> None:
        try:
            _installer_log(
                "install_start",
                cwd=str(Path.cwd()),
                executable=str(sys.executable),
                target_dir=str(self.target_dir),
                preserve_data=bool(self.preserve_data),
            )
            root = payload_root()
            payload_name = detect_payload_name()
            payload_zip = root / "installer_payload" / payload_name
            if not payload_zip.exists():
                direct_payload_zip = root / payload_name
                if direct_payload_zip.exists():
                    payload_zip = direct_payload_zip
            if not payload_zip.exists():
                raise FileNotFoundError(f"payload not found: {payload_zip}")
            _installer_log("payload_resolved", payload_root=str(root), payload_zip=str(payload_zip))

            self.progress.emit(8)
            _terminate_running_instances(self.target_dir)
            self.target_dir.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix="zapret_hub_install_"))
            _installer_log("staging_created", staging=str(staging))
            self.progress.emit(18)

            with zipfile.ZipFile(payload_zip, "r") as archive:
                archive.extractall(staging)
            _installer_log("payload_extracted", staging=str(staging))
            self.progress.emit(45)

            source_root = staging / "zapret_hub"
            if not source_root.exists():
                source_root = staging
            _installer_log("source_root_resolved", source_root=str(source_root))

            preserved_names = {"merged_runtime", "backups", "logs"}
            if self.preserve_data:
                preserved_names.update({"data", "mods", "configs", "cache"})
            _terminate_running_instances(self.target_dir)
            if not self.preserve_data:
                for runtime_dir_name in ("merged_runtime", "backups", "logs"):
                    runtime_dir = self.target_dir / runtime_dir_name
                    if not runtime_dir.exists():
                        continue
                    try:
                        _safe_remove_item(runtime_dir, self.target_dir)
                    except Exception:
                        _quarantine_item(runtime_dir)

            self.progress.emit(70)
            _overlay_tree(source_root, self.target_dir, self.target_dir, preserved_names)
            _installer_log("overlay_done", target_dir=str(self.target_dir))

            shutil.rmtree(staging, ignore_errors=True)
            self.progress.emit(100)
            _installer_log("install_done", target_dir=str(self.target_dir))
            self.done.emit(True, "")
        except Exception as error:
            _installer_log("install_failed", error=str(error))
            self.done.emit(False, str(error))


class InstallerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._drag_pos = None
        self.worker: InstallerWorker | None = None
        self.install_path = default_install_dir()
        self.preserve_existing_data = True
        self.setWindowTitle("Zapret Hub Installer")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(580, 380)
        self.setWindowIcon(app_icon())
        self._build_ui()
        self._load_existing_install()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        shell = QVBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.title_bar = QFrame()
        self.title_bar.setObjectName("InstallerTitleBar")
        self.title_bar.setFixedHeight(46)
        title_row = QHBoxLayout(self.title_bar)
        title_row.setContentsMargins(12, 8, 12, 8)
        title_row.setSpacing(8)

        icon = QLabel()
        icon.setFixedSize(20, 20)
        icon.setPixmap(app_pixmap(20))
        title_row.addWidget(icon)
        title_row.addWidget(QLabel("Zapret Hub"))
        title_row.addStretch(1)
        close_btn = QToolButton()
        close_btn.setProperty("role", "close")
        close_btn.setIcon(QIcon(close_pixmap(14)))
        close_btn.setIconSize(QSize(14, 14))
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.close)
        attach_button_animations(close_btn)
        title_row.addWidget(close_btn)
        shell.addWidget(self.title_bar)

        self.stack = QStackedWidget()
        shell.addWidget(self.stack, 1)

        self.page_start = QWidget()
        start_layout = QVBoxLayout(self.page_start)
        start_layout.setContentsMargins(20, 20, 20, 20)
        start_layout.setSpacing(12)
        head = QLabel(tr("Добро пожаловать в установщик Zapret Hub", "Welcome to Zapret Hub Installer"))
        head.setObjectName("title")
        start_layout.addWidget(head)
        desc = QLabel(
            tr(
                "Приложение устанавливает Zapret Hub и автоматически выбирает подходящую версию под вашу систему.",
                "This installer deploys Zapret Hub and automatically picks the proper build for your system.",
            )
        )
        desc.setWordWrap(True)
        start_layout.addWidget(desc)
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(str(self.install_path))
        browse_btn = QPushButton(tr("Обзор", "Browse"))
        browse_btn.clicked.connect(self._choose_dir)
        attach_button_animations(browse_btn)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse_btn)
        start_layout.addLayout(path_row)
        start_layout.addStretch(1)
        install_btn = QPushButton(tr("Установить", "Install"))
        install_btn.setObjectName("primary")
        install_btn.setMinimumHeight(42)
        install_btn.clicked.connect(self._start_install)
        attach_button_animations(install_btn)
        start_layout.addWidget(install_btn)
        self.stack.addWidget(self.page_start)

        self.page_progress = QWidget()
        progress_layout = QVBoxLayout(self.page_progress)
        progress_layout.setContentsMargins(20, 20, 20, 20)
        progress_layout.setSpacing(12)
        progress_layout.addWidget(QLabel(tr("Установка...", "Installing...")))
        progress_layout.addStretch(1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedHeight(24)
        progress_layout.addWidget(self.bar)
        progress_layout.addStretch(1)
        self.stack.addWidget(self.page_progress)

        self.page_done = QWidget()
        done_layout = QVBoxLayout(self.page_done)
        done_layout.setContentsMargins(20, 20, 20, 20)
        done_layout.setSpacing(12)
        done_layout.addWidget(QLabel(tr("Установка завершена", "Installation complete")))
        self.desktop_cb = QCheckBox(tr("Создать ярлык на рабочем столе", "Create desktop shortcut"))
        self.startmenu_cb = QCheckBox(tr("Создать ярлык в меню Пуск", "Create Start Menu shortcut"))
        self.desktop_cb.setChecked(True)
        self.startmenu_cb.setChecked(True)
        done_layout.addWidget(self.desktop_cb)
        done_layout.addWidget(self.startmenu_cb)
        done_layout.addStretch(1)
        finish_btn = QPushButton(tr("Готово", "Finish"))
        finish_btn.setObjectName("primary")
        finish_btn.setMinimumHeight(42)
        finish_btn.clicked.connect(self._finish)
        attach_button_animations(finish_btn)
        done_layout.addWidget(finish_btn)
        self.stack.addWidget(self.page_done)

        check_icon = str((resource_root() / "ui_assets" / "icons" / "check.svg").resolve()).replace("\\", "/")
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: transparent; }}
            QWidget#Root {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #11182a, stop:0.7 #11182a, stop:1 #162344); color: #dbe5fb; font-family: Segoe UI; font-size: 10pt; border: 1px solid #2a3f61; border-radius: 12px; }}
            #InstallerTitleBar {{ background: transparent; border-bottom: 1px solid #243551; }}
            QLabel#title {{ font-size: 18pt; font-weight: 800; color: #ffffff; }}
            QLabel {{ background: transparent; }}
            QLineEdit {{ background: #15213a; border: 1px solid #304a73; border-radius: 10px; padding: 9px; font-size: 11pt; }}
            QPushButton {{ background: #253b62; border: 1px solid #396197; border-radius: 12px; padding: 10px 14px; font-size: 11pt; color: #dbe5fb; }}
            QPushButton#primary {{ background: #5865f2; border: 1px solid #7481ff; color: #fff; font-weight: 800; }}
            QToolButton {{ border: none; background: transparent; min-width: 26px; min-height: 26px; max-width: 26px; max-height: 26px; border-radius: 12px; padding: 0px; margin: 0px; }}
            QToolButton[role="close"]:hover {{ background: rgba(170, 84, 97, 0.62); border-radius: 12px; }}
            QProgressBar {{ background: #15213a; border: 1px solid #304a73; border-radius: 10px; text-align: center; }}
            QProgressBar::chunk {{ background: #5865f2; border-radius: 9px; }}
            QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid #4f6a98; background: transparent; }}
            QCheckBox::indicator:checked {{ background: #5865f2; border: 1px solid #7a86ff; image: url("{check_icon}"); }}
            """
        )

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        disable_native_window_rounding(int(self.winId()))
        apply_native_window_icons(self)
        bring_widget_to_front(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= self.title_bar.height():
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def _load_existing_install(self) -> None:
        existing = _install_dir_from_registry()
        if existing:
            self.path_edit.setText(str(existing))

    def _choose_dir(self) -> None:
        picked = QFileDialog.getExistingDirectory(self, tr("Выбор папки", "Choose install directory"), self.path_edit.text())
        if picked:
            self.path_edit.setText(picked)

    def _start_install(self) -> None:
        raw_path = self.path_edit.text().strip() or str(default_install_dir())
        self.install_path = Path(raw_path).expanduser()
        if not self.install_path.is_absolute():
            self.install_path = (Path.cwd() / self.install_path).resolve()
        _installer_log("ui_start_install", selected_path=raw_path, normalized_target=str(self.install_path))
        if self.install_path.exists():
            existing_items = [item for item in self.install_path.iterdir()]
        else:
            existing_items = []
        if existing_items:
            choice = self._ask_existing_install_mode()
            if choice == "cancel":
                return
            self.preserve_existing_data = choice == "preserve"
        else:
            self.preserve_existing_data = True
        if sys.platform.startswith("win") and getattr(sys, "frozen", False) and not is_admin():
            args = [
                "--elevated-install",
                "--install-dir",
                str(self.install_path),
                "--preserve-data" if self.preserve_existing_data else "--clean-install",
            ]
            if relaunch_with_elevation(args):
                self.close()
                return
            InstallerDialog("Error", tr("Не удалось запросить права администратора.", "Failed to request administrator privileges."), parent=self).exec()
            return
        self.stack.setCurrentWidget(self.page_progress)
        self.worker = InstallerWorker(self.install_path, preserve_data=self.preserve_existing_data)
        self.worker.progress.connect(self.bar.setValue)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _ask_existing_install_mode(self) -> str:
        dialog = InstallerDialog(
            tr("Найдена предыдущая версия", "Existing installation found"),
            tr(
                "Хотите ли вы переустановить программу, удалив все данные, или обновить, сохранив все ваши пользовательские данные?",
                "Do you want to reinstall the app and remove all data, or update it while keeping all of your user data?",
            ),
            with_yes_no=True,
            parent=self,
            yes_text=tr("Обновить", "Update"),
            no_text=tr("Переустановить", "Reinstall"),
        )
        dialog.exec()
        if dialog.result_mode == "yes":
            return "preserve"
        if dialog.result_mode == "no":
            return "clean"
        return "cancel"

    def _on_done(self, ok: bool, error: str) -> None:
        if not ok:
            InstallerDialog("Error", error, parent=self).exec()
            self.stack.setCurrentWidget(self.page_start)
            return
        self._register_uninstaller()
        self.stack.setCurrentWidget(self.page_done)

    def _register_uninstaller(self) -> None:
        app_exe = self.install_path / "zapret_hub.exe"
        uninstaller_exe = self.install_path / "uninstall_zaprethub.exe"
        try:
            current_installer = Path(sys.executable).resolve()
            if current_installer.exists() and current_installer.suffix.lower() == ".exe":
                shutil.copy2(current_installer, uninstaller_exe)
            _write_uninstall_registry(self.install_path, uninstaller_exe, app_exe)
        except Exception:
            pass

    def _create_shortcut(self, target: Path, name: str, desktop: bool) -> None:
        if desktop:
            base = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
        else:
            base = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"
        base.mkdir(parents=True, exist_ok=True)
        lnk_path = base / f"{name}.lnk"
        ps = (
            "$WScriptShell = New-Object -ComObject WScript.Shell; "
            f"$Shortcut = $WScriptShell.CreateShortcut('{str(lnk_path)}'); "
            f"$Shortcut.TargetPath = '{str(target)}'; "
            f"$Shortcut.WorkingDirectory = '{str(target.parent)}'; "
            f"$Shortcut.IconLocation = '{str(target)},0'; "
            "$Shortcut.Save();"
        )
        _installer_log(
            "shortcut_prepare",
            shortcut_target=str(target),
            shortcut_workdir=str(target.parent),
            shortcut_path=str(lnk_path),
            desktop=bool(desktop),
        )
        startup = None
        flags = 0
        if sys.platform.startswith("win"):
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            capture_output=True,
            check=False,
            creationflags=flags,
            startupinfo=startup,
        )

    def _launch_installed_app(self, exe: Path) -> None:
        if not exe.exists():
            return
        _installer_log("launch_target", launch_target=str(exe), launch_workdir=str(exe.parent))
        if sys.platform.startswith("win"):
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 1
            subprocess.Popen([str(exe)], cwd=str(exe.parent), startupinfo=startup)
            return
        subprocess.Popen([str(exe)], cwd=str(exe.parent))

    def _finish(self) -> None:
        exe = self.install_path / "zapret_hub.exe"
        if self.desktop_cb.isChecked():
            self._create_shortcut(exe, "Zapret Hub", desktop=True)
        if self.startmenu_cb.isChecked():
            self._create_shortcut(exe, "Zapret Hub", desktop=False)
        if exe.exists():
            try:
                self._launch_installed_app(exe)
            except Exception:
                pass
        self.close()


def main() -> int:
    set_windows_app_id()
    if (
        sys.platform.startswith("win")
        and getattr(sys, "frozen", False)
        and "--uninstall" not in sys.argv
        and "--elevated-ui" not in sys.argv
        and not is_admin()
    ):
        if relaunch_with_elevation(["--elevated-ui", *sys.argv[1:]]):
            return 0
        return 1
    if "--uninstall" in sys.argv:
        if not is_admin():
            relaunch_with_elevation(sys.argv[1:])
            return 0
        app = QApplication(sys.argv)
        app.setWindowIcon(app_icon())
        install_arg = ""
        if "--install-dir" in sys.argv:
            try:
                install_arg = sys.argv[sys.argv.index("--install-dir") + 1]
            except Exception:
                install_arg = ""
        install_dir = Path(install_arg) if install_arg else (_install_dir_from_registry() or default_install_dir())
        silent = "--silent" in sys.argv
        if not silent:
            confirm = InstallerDialog(
                tr("Удаление Zapret Hub", "Remove Zapret Hub"),
                tr(
                    "Удалить Zapret Hub и все данные внутри папки установки?\n\nВнешние папки и сторонние файлы не будут затронуты.",
                    "Remove Zapret Hub and all data inside the install folder?\n\nExternal folders and third-party files will not be touched.",
                ),
                with_yes_no=True,
            )
            confirm.exec()
            if not confirm.result_yes:
                return 0
        _terminate_running_instances(install_dir)
        _remove_shortcuts()
        _remove_uninstall_registry()
        if install_dir.exists():
            _launch_folder_removal(install_dir)
        if not silent:
            InstallerDialog(
                tr("Удаление запущено", "Uninstall started"),
                tr("Приложение будет удалено через несколько секунд.", "The app will be removed in a few seconds."),
            ).exec()
        return 0

    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    window = InstallerWindow()
    if "--install-dir" in sys.argv:
        try:
            window.path_edit.setText(sys.argv[sys.argv.index("--install-dir") + 1])
        except Exception:
            pass
    window.show()
    if "--elevated-install" in sys.argv:
        preserve_data = "--preserve-data" in sys.argv
        if "--clean-install" in sys.argv:
            preserve_data = False
        window.preserve_existing_data = preserve_data
        QTimer.singleShot(0, window._start_install)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
