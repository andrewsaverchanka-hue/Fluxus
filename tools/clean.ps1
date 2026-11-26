<#
Removes local build artifacts that can leak an old version into a new build.

Run from the project root:
    powershell -ExecutionPolicy Bypass -File tools\clean.ps1
#>
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$targets = @(
    "dist_protected",
    "main.build",
    "main.dist",
    "main.onefile-build",
    "build",
    "dist",
    "build_info.json"
)

foreach ($target in $targets) {
    if (Test-Path $target) {
        Remove-Item $target -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "removed $target"
    }
}

Get-ChildItem -Path . -Filter "__pycache__" -Recurse -Directory -ErrorAction SilentlyContinue |
    ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
Write-Host "removed __pycache__ folders"

Write-Host ""
Write-Host "Remaining exe files in project root:"
Get-ChildItem -Path . -Filter "*.exe" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Write-Host ("  {0}  {1:N1} MB  {2}  FileVersion={3}" -f `
            $_.Name, ($_.Length / 1MB), $_.LastWriteTime, $_.VersionInfo.FileVersion)
    }

Write-Host ""
Write-Host "Source of truth:"
$line = Select-String -Path "version.py" -Pattern '^VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if ($line) { Write-Host ("  version.py VERSION = " + $line.Matches[0].Groups[1].Value) }
