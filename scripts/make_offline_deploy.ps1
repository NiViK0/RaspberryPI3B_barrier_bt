param(
    [string]$OutputDir = "deploy",
    [string]$ArchiveName = "barrier-deploy.tar.gz"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$outputPath = Join-Path $repoRoot $OutputDir
$wheelhouse = Join-Path $outputPath "wheelhouse"
$archive = Join-Path $outputPath $ArchiveName

New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

Push-Location $repoRoot
try {
    python -m pip download --dest $wheelhouse -r requirements.txt

    if (Test-Path $archive) {
        Remove-Item -LiteralPath $archive -Force
    }

    tar --exclude .git --exclude $OutputDir -czf $archive .

    Write-Host "Offline deploy package is ready:"
    Write-Host "  Archive:    $archive"
    Write-Host "  Wheelhouse: $wheelhouse"
    Write-Host ""
    Write-Host "Upload to the board:"
    Write-Host "  scp `"$archive`" ltpibarrier@IP_BOARD:/tmp/barrier-deploy.tar.gz"
    Write-Host "  scp -r `"$wheelhouse`" ltpibarrier@IP_BOARD:/tmp/barrier-wheelhouse"
}
finally {
    Pop-Location
}
