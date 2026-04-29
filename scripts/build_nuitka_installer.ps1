param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$PayloadDir = "installer_payload",
    [string]$OutputDir = "dist_installer"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

& $Python scripts\sync_app_icon.py
if ($LASTEXITCODE -ne 0) { throw "sync_app_icon.py failed with exit code $LASTEXITCODE" }

$payloadRoot = Join-Path $root $PayloadDir
$payloadX64 = Join-Path $payloadRoot "win_x64.zip"
$payloadArm64 = Join-Path $payloadRoot "win_arm64.zip"
if (-not (Test-Path $payloadX64)) { throw "installer payload missing: $payloadX64" }
if (-not (Test-Path $payloadArm64)) { throw "installer payload missing: $payloadArm64" }

& $Python -m nuitka `
  --onefile `
  --onefile-no-compression `
  --assume-yes-for-downloads `
  --no-deployment-flag=self-execution `
  --zig `
  --enable-plugin=pyside6 `
  --windows-console-mode=disable `
  --windows-uac-admin `
  --windows-icon-from-ico=ui_assets\icons\app_shell.ico `
  --company-name="goshkow" `
  --product-name="Zapret Hub Installer" `
  --file-version="1.4.2.0" `
  --product-version="1.4.2.0" `
  --file-description="Zapret Hub Installer" `
  --copyright="goshkow" `
  --output-dir=$OutputDir `
  --output-filename="install_zaprethub_1.4.2_universal.exe" `
  --include-data-dir=$PayloadDir=installer_payload `
  --include-data-dir=ui_assets=ui_assets `
  --nofollow-import-to=tkinter `
  --remove-output `
  installer\install_zaprethub.py
if ($LASTEXITCODE -ne 0) { throw "Nuitka installer build failed with exit code $LASTEXITCODE" }
