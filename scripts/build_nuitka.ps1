param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$OutputDir = "dist_nuitka"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$PythonExe = $Python
& $PythonExe scripts\sync_app_icon.py
if ($LASTEXITCODE -ne 0) { throw "sync_app_icon.py failed with exit code $LASTEXITCODE" }
$stagingRoot = Join-Path $root ".nuitka_staging"
$runtimeStage = Join-Path $stagingRoot "runtime"

if (Test-Path $stagingRoot) {
    Remove-Item $stagingRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $runtimeStage -Force | Out-Null

$excludeDirNames = @(".git", ".github", "__pycache__", ".mypy_cache", ".pytest_cache")
$excludeFilePatterns = @("*.pyc", "*.pyo")

Get-ChildItem (Join-Path $root "runtime") -Force | ForEach-Object {
    $name = $_.Name
    if ($excludeDirNames -contains $name) {
        return
    }
    $destination = Join-Path $runtimeStage $name
    if ($_.PSIsContainer) {
        Copy-Item $_.FullName $destination -Recurse -Force
    }
    else {
        Copy-Item $_.FullName $destination -Force
    }
}

foreach ($excludeDirName in $excludeDirNames) {
    Get-ChildItem $runtimeStage -Recurse -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq $excludeDirName } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

foreach ($pattern in $excludeFilePatterns) {
    Get-ChildItem $runtimeStage -Recurse -File -Force -Filter $pattern -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

& $Python -m nuitka `
  --standalone `
  --assume-yes-for-downloads `
  --no-deployment-flag=self-execution `
  --zig `
  --enable-plugin=pyside6 `
  --windows-console-mode=disable `
  --windows-icon-from-ico=ui_assets\icons\app_shell.ico `
  --company-name="goshkow" `
  --product-name="Zapret Hub" `
  --file-version="1.4.2.0" `
  --product-version="1.4.2.0" `
  --file-description="Zapret Hub" `
  --copyright="goshkow" `
  --output-dir=$OutputDir `
  --output-filename="zapret_hub.exe" `
  --include-data-dir=sample_data=sample_data `
  --include-data-dir=ui_assets=ui_assets `
  --include-package=cryptography `
  --include-package=certifi `
  --include-package-data=certifi `
  --nofollow-import-to=tkinter `
  --remove-output `
  src\zapret_hub\main.py
if ($LASTEXITCODE -ne 0) { throw "Nuitka app build failed with exit code $LASTEXITCODE" }

$distDir = Get-ChildItem -Path $OutputDir -Directory -Filter "*.dist" | Select-Object -First 1
if (-not $distDir) {
    throw "Nuitka output directory (*.dist) not found in $OutputDir"
}

$runtimeTarget = Join-Path $distDir.FullName "runtime"
if (Test-Path $runtimeTarget) {
    Remove-Item $runtimeTarget -Recurse -Force
}
Copy-Item $runtimeStage $runtimeTarget -Recurse -Force

if (Test-Path $stagingRoot) {
    Remove-Item $stagingRoot -Recurse -Force
}
