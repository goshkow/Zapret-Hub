# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
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
        filevers=(1, 4, 1, 0),
        prodvers=(1, 4, 1, 0),
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
                        StringStruct("FileDescription", "Zapret Hub Installer"),
                        StringStruct("FileVersion", "1.4.1"),
                        StringStruct("InternalName", "install_zaprethub"),
                        StringStruct("OriginalFilename", "install_zaprethub.exe"),
                        StringStruct("ProductName", "Zapret Hub"),
                        StringStruct("ProductVersion", "1.4.1"),
                        StringStruct("Publisher", "goshkow"),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

datas = [
    (str(project_root / "installer_payload"), "installer_payload"),
    (str(project_root / "ui_assets"), "ui_assets"),
]

a = Analysis(
    [str(project_root / "installer" / "install_zaprethub.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    name="install_zaprethub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    exclude_binaries=False,
    icon=str(project_root / "ui_assets" / "icons" / "app.ico"),
    version=version_info,
)
