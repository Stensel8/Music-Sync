[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$SubmodulePath = "csv2tidal",
    [string]$SubmoduleBranch = "main",
    [switch]$Push
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path (Join-Path $RepositoryRoot ".gitmodules"))) {
    throw "No .gitmodules found in $RepositoryRoot"
}

Push-Location $RepositoryRoot

try {
    Write-Output "Updating submodule '$SubmodulePath' to '$SubmoduleBranch'..."

    $oldCommit = & git rev-parse "HEAD:$SubmodulePath" 2>$null
    $oldCommit = if ($oldCommit) { $oldCommit.Trim() } else { "" }

    & git submodule update --init --remote -- $SubmodulePath
    if ($LASTEXITCODE -ne 0) { throw "Failed to update submodule '$SubmodulePath'." }

    $newCommit = & git -C (Join-Path $RepositoryRoot $SubmodulePath) rev-parse HEAD 2>$null
    $newCommit = $newCommit.Trim()

    if ($oldCommit -eq $newCommit) {
        Write-Output "Submodule already up to date ($($newCommit.Substring(0,7))). Nothing to commit."
        return
    }

    & git add -- $SubmodulePath
    if ($LASTEXITCODE -ne 0) { throw "Failed to stage '$SubmodulePath'." }

    $shortOld = if ($oldCommit.Length -ge 7) { $oldCommit.Substring(0, 7) } else { "none" }
    $shortNew = $newCommit.Substring(0, 7)
    $message = "chore(submodule): bump csv2tidal $shortOld -> $shortNew"

    & git commit -m $message -- $SubmodulePath
    if ($LASTEXITCODE -ne 0) { throw "Commit failed." }

    Write-Output "Committed: $message"

    if ($Push) {
        & git push
        if ($LASTEXITCODE -ne 0) { throw "Push failed." }
        Write-Output "Pushed to remote."
    }
}
finally {
    Pop-Location
}
