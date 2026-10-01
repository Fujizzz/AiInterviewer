param([string]$EngineRoot = "D:\Epic Game\UE_5.8")
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
& (Join-Path $EngineRoot "Engine\Build\BatchFiles\RunUAT.bat") BuildCookRun "-project=$projectRoot\DigitalHuman.uproject" -noP4 -platform=Win64 -clientconfig=Development -build -cook -map=/Game/Maps/L_Interview -stage -pak -archive "-archivedirectory=$projectRoot\BuildOutput" -unattended -utf8output
if ($LASTEXITCODE -ne 0) { throw "Unreal packaging failed; inspect Saved\Logs and the AutomationTool log" }
