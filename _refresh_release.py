import os
from pathlib import Path
import shutil
import zipfile


ROOT = Path(__file__).resolve().parent
VERSION = "1.4.1"
X64_SRC = ROOT / "dist" / "zapret_hub"
ARM_SRC = ROOT / ".release_cache" / "win_arm64"
INSTALLER_SRC = ROOT / "dist" / "install_zaprethub.exe"

PORTABLE_ROOT = ROOT / "portable"
MAIN_PORTABLE = PORTABLE_ROOT / "zapret_hub"
X64_PORTABLE = PORTABLE_ROOT / "win_x64"
ARM_PORTABLE = PORTABLE_ROOT / "win_arm64"
INSTALLER_PORTABLE = PORTABLE_ROOT / "installer"

PAYLOAD_DIR = ROOT / "installer_payload"
RELEASE_DIR = ROOT / f"release_{VERSION}"
ROOT_BUNDLES = ("runtime", "ui_assets", "sample_data")
ROOT_DATA_FILES = (
    "components.json",
    "installed_mods.json",
    "merge_state.json",
    "profiles.json",
    "settings.json",
)


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def copy_tree(src: Path, dst: Path) -> None:
    reset_dir(dst)
    shutil.copytree(
        src,
        dst,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )


def overlay_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in {".git", "__pycache__"}:
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(
                item,
                target,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
        else:
            if item.suffix.lower() == ".pyc":
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def make_zip(src_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in src_dir.rglob("*"):
            archive.write(path, path.relative_to(src_dir))


def overlay_root_bundles(dst: Path) -> None:
    for bundled_dir_name in ROOT_BUNDLES:
        bundled_dir = ROOT / bundled_dir_name
        if bundled_dir.exists():
            overlay_tree(bundled_dir, dst / bundled_dir_name)


def overlay_clean_data(dst: Path) -> None:
    src_data = ROOT / "data"
    if not src_data.exists():
        return
    dst_data = dst / "data"
    dst_data.mkdir(parents=True, exist_ok=True)
    for item in list(dst_data.iterdir()):
        if item.name not in ROOT_DATA_FILES:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
    for name in ROOT_DATA_FILES:
        src = src_data / name
        if src.exists():
            shutil.copy2(src, dst_data / name)


def main() -> None:
    copy_tree(X64_SRC, MAIN_PORTABLE)
    copy_tree(X64_SRC, X64_PORTABLE)
    overlay_root_bundles(MAIN_PORTABLE)
    overlay_root_bundles(X64_PORTABLE)
    overlay_clean_data(MAIN_PORTABLE)
    overlay_clean_data(X64_PORTABLE)

    if ARM_SRC.exists():
        copy_tree(ARM_SRC, ARM_PORTABLE)
        overlay_root_bundles(ARM_PORTABLE)
        overlay_clean_data(ARM_PORTABLE)
    else:
        reset_dir(ARM_PORTABLE)

    INSTALLER_PORTABLE.mkdir(parents=True, exist_ok=True)
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    make_zip(X64_PORTABLE, PAYLOAD_DIR / "win_x64.zip")
    if ARM_SRC.exists():
        make_zip(ARM_PORTABLE, PAYLOAD_DIR / "win_arm64.zip")

    reset_dir(RELEASE_DIR)
    copy_tree(X64_PORTABLE, RELEASE_DIR / "zapret_hub_portable_win_x64")
    make_zip(X64_PORTABLE, RELEASE_DIR / f"zapret_hub_{VERSION}_portable_win_x64.zip")

    if ARM_SRC.exists():
        copy_tree(ARM_PORTABLE, RELEASE_DIR / "zapret_hub_portable_win_arm64")
        make_zip(ARM_PORTABLE, RELEASE_DIR / f"zapret_hub_{VERSION}_portable_win_arm64.zip")

    if INSTALLER_SRC.exists():
        shutil.copy2(INSTALLER_SRC, ROOT / "install_zaprethub.exe")
        shutil.copy2(INSTALLER_SRC, INSTALLER_PORTABLE / "install_zaprethub.exe")
        installer_name = f"install_zaprethub_{VERSION}.exe"
        if os.environ.get("ZAPRET_HUB_RELEASE_UNIVERSAL", "").strip() == "1":
            installer_name = f"install_zaprethub_{VERSION}_Universal.exe"
        shutil.copy2(INSTALLER_SRC, RELEASE_DIR / installer_name)

    print("ok")


if __name__ == "__main__":
    main()
