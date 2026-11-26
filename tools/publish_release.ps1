<#
Build Fluxus locally and upload the exe to GitHub Releases.

Example:
    powershell -ExecutionPolicy Bypass -File tools\publish_release.ps1 `
        -Tag v1.0.2 -Notes "collection parsing fix" -Employee "Ivanov"

Requires:
  * GitHub CLI (gh) with `gh auth login`
    OR environment variable GITHUB_TOKEN (repo scope).
  * Nuitka: pip install nuitka ordered-set zstandard

Sources stay on your machine; only the exe is uploaded.
#>
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [string]$Notes = "",
    [string]$Employee = "release",
    [string]$Repo = "andrewsaverchanka-hue/Fluxus",
    [switch]$Draft,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Get-PatchNotes([string]$Tag) {
    $file = Join-Path $root "PatchNotes.md"
    if (-not (Test-Path $file)) { return "" }
    $text = Get-Content $file -Raw -Encoding UTF8
    $pattern = "(?ms)^##\s*" + [regex]::Escape($Tag) + "\s*\r?\n(.*?)(?=^##\s|\z)"
    $match = [regex]::Match($text, $pattern)
    if ($match.Success) { return $match.Groups[1].Value.Trim() }
    return $text.Trim()
}

if ($Tag -notmatch '^v\d+\.\d+\.\d+$') {
    throw "Tag must look like v1.0.2 (got: $Tag)"
}

# Tag must match VERSION from version.py, otherwise the old version ships.
$versionLine = Select-String -Path "version.py" -Pattern '^VERSION\s*=\s*"([^"]+)"' |
    Select-Object -First 1
if (-not $versionLine) { throw "VERSION not found in version.py" }
$appVersion = $versionLine.Matches[0].Groups[1].Value
if ("v$appVersion" -ne $Tag) {
    throw "Tag $Tag does not match version.py VERSION=$appVersion"
}
Write-Host "  version.py matches: $appVersion"

Write-Host "== 1/3 Build =="
if ($SkipBuild) {
    Write-Host "  skipped (-SkipBuild)"
} else {
    powershell -ExecutionPolicy Bypass -File tools\build_protected.ps1 -Employee $Employee
    if ($LASTEXITCODE -ne 0) { throw "Build failed" }
}

$exe = Get-ChildItem "dist_protected\*.exe" | Select-Object -First 1
if (-not $exe) { throw "exe not found in dist_protected" }
if ($exe.Name -ne "Fluxus.exe") {
    # Auto-update expects Fluxus.exe - rename before publishing.
    $renamed = Join-Path $exe.DirectoryName "Fluxus.exe"
    Move-Item $exe.FullName $renamed -Force
    $exe = Get-Item $renamed
    Write-Host "  renamed to Fluxus.exe"
}
$mb = [math]::Round($exe.Length / 1MB, 1)
Write-Host "  exe: $($exe.FullName) ($mb MB)"

Write-Host "== 2/3 Tag check =="
$gh = Get-Command gh -ErrorAction SilentlyContinue
if ($gh) {
    # gh writes 'release not found' to stderr when the release is missing;
    # with ErrorActionPreference=Stop PowerShell treats that as fatal.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    gh release view $Tag --repo $Repo *> $null
    $exists = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEap
    if ($exists) {
        throw "Release $Tag already exists. Bump VERSION in version.py and the tag."
    }
} elseif (-not $env:GITHUB_TOKEN) {
    throw "Neither gh nor GITHUB_TOKEN found. Install GitHub CLI or set the token."
}

Write-Host "== 3/3 Publish =="
if ($Notes) {
    $notes = $Notes
} else {
    $notes = Get-PatchNotes $Tag
    if (-not $notes) { $notes = "Fluxus $Tag" }
    else { Write-Host "  release notes: PatchNotes.md" }
}

if ($gh) {
    $ghArgs = @($Tag, $exe.FullName, "--repo", $Repo,
                "--title", "Fluxus $Tag", "--notes", $notes)
    if ($Draft) { $ghArgs += "--draft" }
    gh release create @ghArgs
} else {
    $headers = @{
        Authorization = "Bearer $env:GITHUB_TOKEN"
        Accept = "application/vnd.github+json"
    }
    $body = @{
        tag_name = $Tag
        name = "Fluxus $Tag"
        body = $notes
        draft = [bool]$Draft
        prerelease = $false
    } | ConvertTo-Json

    $json = $body | ConvertTo-Json
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $release = Invoke-RestMethod -Method Post `
        -Uri "https://api.github.com/repos/$Repo/releases" `
        -Headers $headers -Body $bytes `
        -ContentType "application/json; charset=utf-8"

    $uploadUrl = $release.upload_url -replace '\{.*\}$', ''
    $uploadUrl = "$uploadUrl?name=$($exe.Name)"
    Invoke-RestMethod -Method Post -Uri $uploadUrl -Headers $headers `
        -Body ([IO.File]::ReadAllBytes($exe.FullName)) `
        -ContentType "application/octet-stream" | Out-Null
}

Write-Host ""
Write-Host "Done: https://github.com/$Repo/releases/tag/$Tag"
Write-Host "Auto-update will see it via releases/latest."
