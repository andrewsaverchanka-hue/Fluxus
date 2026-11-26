<#
Protected Fluxus build for Windows (Nuitka + build watermark).

Run from the project root (where main.py is):
    powershell -ExecutionPolicy Bypass -File tools\build_protected.ps1 -Employee "Ivanov"

Steps:
  1) build Rust core, copy fluxus_native.pyd next to main.py;
  2) write build_info.json watermark;
  3) compile Python to C with Nuitka (no .py/.pyc in the bundle);
  4) sign the exe when -CertThumbprint is provided.
#>
param(
    [string]$Employee = "internal",
    [string]$CertThumbprint = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 0/4 Clean Nuitka cache =="
foreach ($dir in @("dist_protected", "main.build", "main.dist", "main.onefile-build")) {
    if (Test-Path $dir) {
        Remove-Item $dir -Recurse -Force
        Write-Host "  removed $dir"
    }
}

$versionLine = Select-String -Path "version.py" -Pattern '^VERSION\s*=\s*"([^"]+)"' |
    Select-Object -First 1
$appVersion = if ($versionLine) { $versionLine.Matches[0].Groups[1].Value } else { "?" }
Write-Host "  version.py VERSION = $appVersion"

Write-Host "== 1/4 Rust core =="
Push-Location fluxus-rs
cargo build --release -p fluxus-py
if ($LASTEXITCODE -ne 0) { throw "cargo build failed" }
Copy-Item "target\release\fluxus_native.dll" "..\fluxus_native.pyd" -Force
Copy-Item "py\fluxus_adapter.py" "..\fluxus_adapter.py" -Force
Pop-Location

Write-Host "== 2/4 build watermark =="
$stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
$chars = (48..57) + (97..122) | ForEach-Object { [char]$_ }
$buildId = -join (Get-Random -InputObject $chars -Count 10)
@{
    build_id = $buildId
    employee = $Employee
    built_at = $stamp
} | ConvertTo-Json | Set-Content -Encoding UTF8 build_info.json

Write-Host "== 3/4 Nuitka =="
& $Python -m nuitka --standalone --onefile --assume-yes-for-downloads `
    --enable-plugin=pyside6 `
    --output-filename=Fluxus.exe `
    --windows-console-mode=disable `
    --windows-icon-from-ico=logo.ico `
    --include-data-dir=assets=assets `
    --include-data-files=logo.png=logo.png `
    --include-data-files=logo_full.png=logo_full.png `
    --include-data-files=build_info.json=build_info.json `
    --company-name=Fluxus --product-name=Fluxus `
    --file-version=1.0.1.0 --product-version=1.0.1.0 `
    --output-dir=dist_protected `
    main.py
if ($LASTEXITCODE -ne 0) { throw "Nuitka build failed" }

Write-Host "== 4/4 signing =="
$exe = Get-ChildItem "dist_protected\*.exe" | Select-Object -First 1
if ($CertThumbprint -ne "") {
    & signtool sign /sha1 $CertThumbprint /fd SHA256 `
        /tr http://timestamp.digicert.com /td SHA256 $exe.FullName
} else {
    Write-Host "  skipped (no -CertThumbprint)"
}

Write-Host ""
Write-Host "Done: $($exe.FullName)"
Write-Host "Build id: $buildId ($Employee, $stamp)"
