#requires -Version 5.1
<#
.SYNOPSIS
Create or update the GitHub Release for Adaptive Swarm Pipeline v3.0.0.

.DESCRIPTION
Validates the local release tag and offline artifacts, then creates a GitHub
Release for v3.0.0 with the ZIP and SHA256 files attached. If the release
already exists, the artifacts and release notes are updated with --clobber.

.PARAMETER DryRun
Print the gh command without creating or updating the release.

.EXAMPLE
.\scripts\create_github_release.ps1
.\scripts\create_github_release.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Tag = "v3.0.0",
    [string]$Title = "",
    [switch]$DryRun
)

if ([string]::IsNullOrWhiteSpace($Title)) {
    $Title = "Adaptive Swarm Pipeline v3.0.0 " + [char]0x2014 + " Unified Autonomous Engine"
}

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$zipName = "adaptive-swarm-pipeline-v3.0.0.zip"
$zipPath = Join-Path $repoRoot "dist\$zipName"
$shaPath = "$zipPath.sha256"
$notesPath = Join-Path $repoRoot "docs\RELEASE_NOTES.md"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI 'gh' is required. Install it from https://cli.github.com/ and run 'gh auth login'."
}
if (-not (Test-Path $zipPath)) {
    throw "Missing release archive: $zipPath"
}
if (-not (Test-Path $shaPath)) {
    throw "Missing checksum: $shaPath"
}
if (-not (Test-Path $notesPath)) {
    throw "Missing release notes: $notesPath"
}

# Refuse to publish a stale or mismatched archive.
$expected = ((Get-Content $shaPath -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) {
    throw "SHA256 mismatch for $zipPath. expected=$expected actual=$actual"
}

git rev-parse --verify "$Tag^{commit}" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Local git tag '$Tag' does not exist or does not point to a commit."
}

Write-Host "Tag:       $Tag"
Write-Host "Title:     $Title"
Write-Host "Archive:   $zipPath"
Write-Host "Checksum:  $actual"
Write-Host "Notes:     $notesPath"

if ($DryRun) {
    Write-Host ""
    Write-Host "Dry run only. Command that would run when the release does not exist:"
    Write-Host "gh release create $Tag `"$zipPath`" `"$shaPath`" --title `"$Title`" --notes-file `"$notesPath`" --verify-tag"
    exit 0
}

gh release view $Tag --json tagName 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Release $Tag already exists; updating attachments and notes."
    gh release upload $Tag $zipPath $shaPath --clobber
    if ($LASTEXITCODE -ne 0) { throw "gh release upload failed." }
    gh release edit $Tag --title $Title --notes-file $notesPath
    if ($LASTEXITCODE -ne 0) { throw "gh release edit failed." }
}
else {
    Write-Host "Creating release $Tag."
    gh release create $Tag $zipPath $shaPath --title $Title --notes-file $notesPath --verify-tag
    if ($LASTEXITCODE -ne 0) { throw "gh release create failed." }
}

Write-Host "Published: https://github.com/khoacat12345/adaptive-swarm-pipeline/releases/tag/$Tag"
