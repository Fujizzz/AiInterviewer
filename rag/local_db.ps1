param(
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action = 'start'
)

# Optional native Windows development database; Docker deployment is unchanged.
$ErrorActionPreference = 'Stop'
$ragWorkspace = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ragRuntime = Join-Path $ragWorkspace '.rag-runtime'
$ragPgBin = Join-Path $ragRuntime 'postgres\Library\bin'
$ragPgData = Join-Path $ragRuntime 'pgdata'
$ragPgCtl = Join-Path $ragPgBin 'pg_ctl.exe'
if (-not (Test-Path -LiteralPath $ragPgCtl)) {
    throw "Local PostgreSQL runtime is missing: $ragPgCtl. See rag/README.md."
}
if (-not (Test-Path -LiteralPath (Join-Path $ragPgData 'PG_VERSION'))) {
    throw "Local database has not been initialized: $ragPgData."
}
$ragPreviousPath = $env:PATH
try {
    $env:PATH = "$ragPgBin;$env:PATH"
    if ($Action -eq 'start') {
        & $ragPgCtl -D $ragPgData status *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Output 'Local RAG database is already running.'
        } else {
            & $ragPgCtl -D $ragPgData -l (Join-Path $ragRuntime 'postgres.log') -o '-h 127.0.0.1 -p 5432' -w start
        }
    } elseif ($Action -eq 'stop') {
        & $ragPgCtl -D $ragPgData -m fast -w stop
    } else {
        & $ragPgCtl -D $ragPgData status
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    $env:PATH = $ragPreviousPath
}
