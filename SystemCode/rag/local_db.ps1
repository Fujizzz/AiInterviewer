<#
Responsibilities: Control the existing native Windows RAG PostgreSQL development instance.
Implementation: Resolve the workspace above the Git checkout, prepend the PostgreSQL binaries
for this invocation, and delegate start/stop/status to pg_ctl; restore PATH in finally.
Related Modules: README.md documents the optional runtime; docker-compose.yml is independent.
Declaration Index: None.
Variable Index:
- Action: Validated start/stop/status operation; defaults to start.
- ErrorActionPreference: Terminate on PowerShell errors.
- ragWorkspace: Workspace above the checkout; SystemCode adds one directory level to resolution.
- ragRuntime: Existing .rag-runtime outside Git; no database or dependency is created here.
- ragPgBin: PostgreSQL native binary directory.
- ragPgData: Existing cluster data directory, validated through PG_VERSION.
- ragPgCtl: pg_ctl executable used for every operation.
- ragPreviousPath: Original process PATH, restored even when delegation fails.
Inputs: Action, script location, the existing runtime files and process PATH.
Outputs: pg_ctl output/exit status and an already-running notice; start writes postgres.log.
Logic: Keep the pre-reorganization workspace location instead of moving a live database.
Constraints: Missing runtime/cluster is an explicit error; no installation or fallback occurs.
#>
param(
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action = 'start'
)

# Optional native Windows development database; Docker deployment is unchanged.
$ErrorActionPreference = 'Stop'
$ragWorkspace = Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent
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
