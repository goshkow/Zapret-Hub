param(
    [string]$InputPath = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:SIGNPATH_API_TOKEN)) {
    Write-Host "SignPath not configured. Skipping signing step."
    exit 0
}

Write-Host "SignPath placeholder is enabled, but real signing is not configured yet."
Write-Host "InputPath: $InputPath"
exit 0
