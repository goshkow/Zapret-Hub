# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo,
    FixedFileInfo,
    StringFileInfo,
    StringTable,
    StringStruct,
    VarFileInfo,
    VarStruct,
)

project_root = Path(SPECPATH).resolve().parent
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=(1, 4, 2, 0),
        prodvers=(1, 4, 2, 0),
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "goshkow"),
                        StringStruct("FileDescription", "Zapret Hub"),
                        StringStruct("FileVersion", "1.4.2"),
                        StringStruct("InternalName", "zapret_hub"),
                        StringStruct("OriginalFilename", "zapret_hub.exe"),
                        StringStruct("ProductName", "Zapret Hub"),
                        StringStruct("ProductVersion", "1.4.2"),
                        StringStruct("Publisher", "goshkow"),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)
datas = [
    (str(project_root / "sample_data"), "sample_data"),
    (str(project_root / "runtime"), "runtime"),
    (str(project_root / "ui_assets"), "ui_assets"),
]
crypto_hiddenimports = collect_submodules("cryptography")
certifi_datas = collect_data_files("certifi")

a = Analysis(
    [str(project_root / "src" / "zapret_hub" / "main.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas + certifi_datas,
    hiddenimports=[
        "asyncio",
        "asyncio.base_events",
        "asyncio.base_futures",
        "asyncio.base_subprocess",
        "asyncio.events",
        "asyncio.futures",
        "asyncio.locks",
        "asyncio.protocols",
        "asyncio.queues",
        "asyncio.runners",
        "asyncio.selector_events",
        "asyncio.streams",
        "asyncio.subprocess",
        "asyncio.tasks",
        "asyncio.transports",
        "argparse",
        "base64",
        "collections",
        "dataclasses",
        "hashlib",
        "hmac",
        "logging",
        "logging.handlers",
        "os",
        "random",
        "socket",
        "ssl",
        "string",
        "struct",
        "threading",
        "typing",
        "urllib",
        "urllib.request",
    ] + crypto_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name="zapret_hub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    exclude_binaries=True,
    icon=str(project_root / "ui_assets" / "icons" / "app.ico"),
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="zapret_hub",
)
