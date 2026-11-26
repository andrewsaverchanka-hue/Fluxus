<#
Syntax check for the PowerShell scripts (does not build or upload anything).

Run:
    powershell -ExecutionPolicy Bypass -File tools\check_syntax.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$files = @(
    "tools\build_protected.ps1",
    "tools\publish_release.ps1"
)

$failed = $false
foreach ($file in $files) {
    if (-not (Test-Path $file)) {
        Write-Host "MISSING $file"
        $failed = $true
        continue
    }
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path $file).Path, [ref]$null, [ref]$errors
    ) | Out-Null
    if ($errors -and $errors.Count -gt 0) {
        $failed = $true
        Write-Host "FAIL $file"
        foreach ($e in $errors) {
            Write-Host ("   line {0}: {1}" -f $e.Extent.StartLineNumber, $e.Message)
        }
    } else {
        Write-Host "OK   $file"
    }
}

if ($failed) { exit 1 }
Write-Host "All scripts are syntactically valid."
