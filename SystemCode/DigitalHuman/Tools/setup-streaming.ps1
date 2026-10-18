param([string]$Pnpm = "pnpm")
$ErrorActionPreference = "Stop"
$infra = Join-Path $PSScriptRoot "PixelStreamingInfrastructure"
$revision = "6b8cfb460bda09703e85178f1f77aa6faec9e890"
if (-not (Test-Path -LiteralPath (Join-Path $infra ".git"))) {
    & git clone --branch UE5.8 https://github.com/EpicGamesExt/PixelStreamingInfrastructure.git $infra
    if ($LASTEXITCODE -ne 0) { throw "Infrastructure download failed" }
    & git -C $infra checkout $revision
    if ($LASTEXITCODE -ne 0) { throw "Cannot select the tested UE 5.8 infrastructure revision" }
}
# Build only Epic's common/protocol libraries. The local host wrapper supplies
# loopback binding, so no public TURN or SFU services are needed for this demo.
@"
packages:
  - Common
  - Signalling
linkWorkspacePackages: true
"@ | Set-Content -LiteralPath (Join-Path $infra "pnpm-workspace.yaml") -Encoding utf8
$env:CI = "true"
& $Pnpm --dir $infra install --ignore-scripts
if ($LASTEXITCODE -ne 0) { throw "Infrastructure dependencies failed" }
foreach ($module in @("Common", "Signalling")) {
    & $Pnpm --dir (Join-Path $infra $module) run build:cjs
    if ($LASTEXITCODE -ne 0) { throw "$module build failed" }
}
$web = Join-Path $PSScriptRoot "..\..\backend\frontend\digital-human"
& $Pnpm --dir $web install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { throw "Player dependencies failed" }
& $Pnpm --dir $web run build
if ($LASTEXITCODE -ne 0) { throw "Player build failed" }
