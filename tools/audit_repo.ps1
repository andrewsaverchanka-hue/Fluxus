<#
Audits the local git repo for leftovers from the old Python build.

Run from the project root:
    powershell -ExecutionPolicy Bypass -File tools\audit_repo.ps1
#>
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "=== git status ==="
git status --short
Write-Host ""

Write-Host "=== local tags ==="
git tag -l
Write-Host ""

Write-Host "=== branches ==="
git branch -a
Write-Host ""

Write-Host "=== last commits ==="
git log --oneline -5
Write-Host ""

Write-Host "=== tracked build leftovers (exe/pyd/spec/dist/build) ==="
$tracked = git ls-files
$suspicious = $tracked | Where-Object {
    $_ -match '\.(exe|pyd|spec|dll|pyc)$' -or
    $_ -match '(^|/)(build|dist|dist_protected|update_tmp)/' -or
    $_ -eq 'build_info.json'
}
if ($suspicious) {
    $suspicious | ForEach-Object { Write-Host "  $_" }
} else {
    Write-Host "  none"
}
Write-Host ""

Write-Host "=== untracked files in root ==="
git status --porcelain | Where-Object { $_ -match '^\?\?' } | ForEach-Object {
    Write-Host "  $_"
}
Write-Host ""

Write-Host "=== version.py ==="
$line = Select-String -Path "version.py" -Pattern '^VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if ($line) { Write-Host ("  VERSION = " + $line.Matches[0].Groups[1].Value) }
Write-Host ""

Write-Host "=== remote tags (origin) ==="
git ls-remote --tags origin 2>$null | ForEach-Object { Write-Host "  $_" }
