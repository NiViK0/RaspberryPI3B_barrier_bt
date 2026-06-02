param(
    [string]$OutputDir = "deploy",
    [string]$Version = "",
    [string]$ArchiveName = "",
    [string]$RemoteUser = "ltpibarrier",
    [string]$RemoteHost = "IP_BOARD"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$outputPath = Join-Path $repoRoot $OutputDir

if (-not $ArchiveName) {
    if ($Version) {
        $ArchiveName = "barrier-$Version-runtime-update.tar.gz"
    }
    else {
        $ArchiveName = "barrier-runtime-update.tar.gz"
    }
}

$archive = Join-Path $outputPath $ArchiveName
$remoteArchive = "/tmp/$ArchiveName"
$remoteDirName = [System.IO.Path]::GetFileNameWithoutExtension(
    [System.IO.Path]::GetFileNameWithoutExtension($ArchiveName)
)
$remoteDir = "/tmp/$remoteDirName"

New-Item -ItemType Directory -Force -Path $outputPath | Out-Null

Push-Location $repoRoot
try {
    if (Test-Path $archive) {
        Remove-Item -LiteralPath $archive -Force
    }

    $tarArgs = @(
        "--exclude=.git",
        "--exclude=$OutputDir",
        "--exclude=archive",
        "--exclude=__pycache__",
        "--exclude=*.pyc",
        "--exclude=.pytest_cache",
        "--exclude=.mypy_cache",
        "-czf",
        $archive,
        "."
    )

    & tar @tarArgs
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed with exit code $LASTEXITCODE"
    }

    Write-Host "Runtime update package is ready:"
    Write-Host "  Archive: $archive"
    Write-Host ""
    Write-Host "Upload to the board:"
    Write-Host "  scp `"$archive`" ${RemoteUser}@${RemoteHost}:${remoteArchive}"
    Write-Host ""
    Write-Host "Apply on the board:"
    Write-Host "  rm -rf $remoteDir"
    Write-Host "  mkdir -p $remoteDir"
    Write-Host "  tar -xzf $remoteArchive -C $remoteDir"
    Write-Host "  find $remoteDir -name '*.sh' -exec sed -i 's/\r$//' {} +"
    Write-Host "  sudo cp -a /opt/barrier/src `"/opt/barrier/src.backup.`$(date +%Y%m%d-%H%M%S)`""
    Write-Host "  sudo cp -a /opt/barrier/barrier.db `"/opt/barrier/barrier.db.backup.`$(date +%Y%m%d-%H%M%S)`""
    Write-Host "  sudo rsync -a --delete --exclude '.git' --exclude 'deploy' --exclude 'archive' $remoteDir/ /opt/barrier/src/"
    Write-Host "  sudo chown -R ${RemoteUser}:${RemoteUser} /opt/barrier/src"
    Write-Host "  sudo chmod +x /opt/barrier/src/scripts/*.sh"
    Write-Host "  /opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py init-db"
    Write-Host "  sudo systemctl restart barrier.service barrier-panel.service"
}
finally {
    Pop-Location
}
