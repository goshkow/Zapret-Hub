import argparse
import ctypes
import hashlib
import multiprocessing
import sys
import threading
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QIcon, QImage, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from zapret_hub.runtime_env import development_install_root, is_packaged_runtime, packaged_install_root, packaged_resource_root
from zapret_hub.workers import run_tg_ws_proxy_worker

class _BootstrapThread(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            from zapret_hub.bootstrap import bootstrap_application, build_startup_snapshot

            context = bootstrap_application()
            startup_snapshot = build_startup_snapshot(context)
            startup_show_onboarding = _preload_startup_onboarding(
                context,
                launch_hidden=False,
                startup_snapshot=startup_snapshot,
            )
            self.ready.emit(
                {
                    "context": context,
                    "startup_snapshot": startup_snapshot,
                    "startup_show_onboarding": startup_show_onboarding,
                }
            )
        except Exception as error:
            self.failed.emit(str(error))


def _startup_trace(message: str) -> None:
    try:
        path = Path(tempfile.gettempdir()) / "zapret_hub_startup_trace.log"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        pass


def _set_windows_app_id() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("goshkow.ZapretHub")  # type: ignore[attr-defined]
    except Exception:
        return


def _ensure_admin_windows(argv: list[str]) -> int:
    if not sys.platform.startswith("win"):
        return 0
    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return 0
    if is_admin:
        return 0

    params = " ".join(f'"{arg}"' if " " in arg else arg for arg in argv)
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    if result <= 32:
        return 3
    return 2


def _single_instance_key() -> str:
    base = str(sys.executable if is_packaged_runtime() else __file__)
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"zapret_hub_{digest}"


def _notify_existing_instance(server_name: str, message: bytes = b"SHOW") -> bool:
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if not socket.waitForConnected(220):
        return False
    socket.write(message)
    socket.flush()
    socket.waitForBytesWritten(220)
    socket.disconnectFromServer()
    return True


def _create_single_instance_server(server_name: str) -> QLocalServer | None:
    server = QLocalServer()
    if server.listen(server_name):
        return server
    QLocalServer.removeServer(server_name)
    if server.listen(server_name):
        return server
    return None


def _resolve_app_icon_path() -> Path | None:
    candidates: list[Path] = []
    if is_packaged_runtime():
        install_root = packaged_install_root()
        resource_root = packaged_resource_root()
        candidates.extend(
            [
                install_root / "ui_assets" / "icons" / "app.png",
                install_root / "ui_assets" / "icons" / "app.ico",
                resource_root / "ui_assets" / "icons" / "app.png",
                resource_root / "ui_assets" / "icons" / "app.ico",
            ]
        )
    else:
        install_root = development_install_root(__file__)
        candidates.extend(
            [
                install_root / "ui_assets" / "icons" / "app.png",
                install_root / "ui_assets" / "icons" / "app.ico",
            ]
        )
    for path in candidates:
        if path.exists():
            return path
    return None


def _load_app_icon() -> QIcon | None:
    path = _resolve_app_icon_path()
    if path is None:
        return None
    if path.suffix.lower() == ".png":
        image = QImage(str(path))
        if image.isNull():
            return None
        source = QPixmap.fromImage(image)
        if source.isNull():
            return None
        icon = QIcon()
        for size in (16, 20, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(
                source.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        return icon if not icon.isNull() else None
    icon = QIcon(str(path))
    return icon if not icon.isNull() else None


def _preload_startup_onboarding(context, *, launch_hidden: bool, startup_snapshot: dict[str, object] | None = None) -> bool:
    if launch_hidden:
        return False
    try:
        settings = context.settings.get()
        marker = context.paths.data_dir / ".onboarding_seen"
        if marker.exists():
            return False
        if bool(settings.general_autotest_done):
            return False
        if isinstance(startup_snapshot, dict):
            raw_options = startup_snapshot.get("general_options")
            if isinstance(raw_options, list):
                return any(isinstance(item, dict) and item.get("id") for item in raw_options)
        return False
    except Exception:
        return False


def run(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    _startup_trace("run: freeze_support passed")
    runtime_argv = list(argv if argv is not None else sys.argv[1:])
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", choices=["tg-ws-proxy"], default="")
    parser.add_argument("--autostart-launch", action="store_true")
    parser.add_argument("--tg-host", default="127.0.0.1")
    parser.add_argument("--tg-port", type=int, default=1443)
    parser.add_argument("--tg-secret", default="")
    parser.add_argument("--tg-verbose", action="store_true")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--hub-token", default="")
    known, _ = parser.parse_known_args(runtime_argv)

    if known.worker == "tg-ws-proxy":
        _startup_trace("run: worker=tg-ws-proxy")
        return run_tg_ws_proxy_worker(
            host=known.tg_host,
            port=known.tg_port,
            secret=known.tg_secret,
            verbose=known.tg_verbose,
        )
    if not known.autostart_launch:
        _startup_trace("run: ensure_admin start")
        elevate_result = _ensure_admin_windows(runtime_argv)
        _startup_trace(f"run: ensure_admin result={elevate_result}")
        if elevate_result in (2, 3):
            return elevate_result

    _set_windows_app_id()
    _startup_trace("run: before QApplication")
    app = QApplication(sys.argv)
    _startup_trace("run: QApplication created")
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Zapret Hub")
    app.setOrganizationName("ZapretHub")
    app_icon = _load_app_icon()
    _startup_trace(f"run: app_icon loaded={app_icon is not None}")
    if app_icon is not None:
        app.setWindowIcon(app_icon)
    instance_key = _single_instance_key()
    notify_message = b"PING" if known.autostart_launch else b"SHOW"
    if _notify_existing_instance(instance_key, notify_message):
        _startup_trace("run: existing instance notified, exiting")
        return 0

    bootstrap_thread = _BootstrapThread()
    _startup_trace("run: bootstrap thread created")
    app._bootstrap_thread = bootstrap_thread  # type: ignore[attr-defined]

    class _BootstrapBridge(QObject):
        @Slot(object)
        def finish_bootstrap(self, bundle: object) -> None:
            from zapret_hub.ui.main_window import MainWindow
            from zapret_hub.services.backend_worker import BackendWorkerClient

            _startup_trace("finish_bootstrap: entered")
            if not isinstance(bundle, dict):
                raise RuntimeError("Bootstrap result is invalid")
            context = bundle.get("context")
            startup_snapshot = bundle.get("startup_snapshot")
            startup_show_onboarding = bool(bundle.get("startup_show_onboarding"))
            if context is None:
                raise RuntimeError("Application context is missing")
            settings = context.settings.get()
            actual_autostart = bool(context.autostart.is_enabled())
            if bool(settings.autostart_windows) != actual_autostart:
                context.settings.update(autostart_windows=actual_autostart)
                settings = context.settings.get()
            launch_hidden = bool(known.autostart_launch and settings.start_in_tray)
            if launch_hidden:
                startup_show_onboarding = False
            context.backend = None
            _startup_trace("finish_bootstrap: before MainWindow")
            window = MainWindow(
                context,
                launch_hidden=launch_hidden,
                startup_show_onboarding=startup_show_onboarding,
                startup_snapshot=startup_snapshot if isinstance(startup_snapshot, dict) else None,
            )
            _startup_trace("finish_bootstrap: MainWindow created")
            if app_icon is not None:
                try:
                    window.setWindowIcon(app_icon)
                except Exception:
                    pass
            server = _create_single_instance_server(instance_key)
            if server is not None:
                def _on_new_connection() -> None:
                    while server.hasPendingConnections():
                        client = server.nextPendingConnection()
                        if client is not None:
                            if client.bytesAvailable() <= 0:
                                client.waitForReadyRead(350)
                            payload = bytes(client.readAll()).strip()
                            client.disconnectFromServer()
                            if payload == b"SHOW":
                                window.restore_from_external_launch()

                server.newConnection.connect(_on_new_connection)
                app._single_instance_server = server  # type: ignore[attr-defined]
                app._single_instance_window = window  # type: ignore[attr-defined]

            def _cleanup_before_quit() -> None:
                try:
                    if context.backend is not None:
                        context.backend.request_shutdown_background()
                    else:
                        threading.Thread(target=context.processes.stop_all, daemon=True).start()
                except Exception:
                    pass
                if server is not None:
                    try:
                        server.close()
                    except Exception:
                        pass

            app.aboutToQuit.connect(_cleanup_before_quit)
            if known.autostart_launch and settings.auto_run_components:
                def _start_after_backend() -> None:
                    if context.backend is not None:
                        window.start_enabled_components_async()
                autostart_callback = _start_after_backend
            else:
                autostart_callback = None
            if launch_hidden:
                _startup_trace("finish_bootstrap: hide window")
                window.hide()
            else:
                _startup_trace("finish_bootstrap: show window")
                window.show()
            _startup_trace("finish_bootstrap: after window visible call")

            def _attach_backend_after_show() -> None:
                _startup_trace("attach_backend: start")
                backend = BackendWorkerClient(app)
                _startup_trace("attach_backend: client created")
                context.backend = backend
                window.attach_backend_client(backend)
                _startup_trace("attach_backend: attached")
                if autostart_callback is not None:
                    autostart_callback()
                    _startup_trace("attach_backend: autostart callback done")

            QTimer.singleShot(900, _attach_backend_after_show)
            _startup_trace("finish_bootstrap: backend attach scheduled")

        @Slot(str)
        def fail_bootstrap(self, message: str) -> None:
            _startup_trace(f"finish_bootstrap: failed {message}")
            QMessageBox.critical(None, "Zapret Hub", message or "Failed to prepare the application")
            app.quit()

    bootstrap_bridge = _BootstrapBridge()
    app._bootstrap_bridge = bootstrap_bridge  # type: ignore[attr-defined]
    bootstrap_thread.ready.connect(bootstrap_bridge.finish_bootstrap, Qt.ConnectionType.QueuedConnection)
    bootstrap_thread.failed.connect(bootstrap_bridge.fail_bootstrap, Qt.ConnectionType.QueuedConnection)
    bootstrap_thread.start()
    _startup_trace("run: bootstrap thread started")
    return app.exec()

