param([string]$Node = "node")
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "PixelStreamingInfrastructure\Signalling\dist\cjs\pixelstreamingsignalling.js"))) {
    throw "Run Tools\setup-streaming.ps1 first"
}
& $Node (Join-Path $PSScriptRoot "local-signalling.cjs")
