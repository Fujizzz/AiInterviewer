param(
    [string]$EngineRoot = "D:\Epic Game\UE_5.8",
    [switch]$EditorGame,
    [switch]$Diagnostics
)
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$arguments = @("-PixelStreamingConnectionURL=ws://127.0.0.1:8888", "-AudioMixer", "-RenderOffscreen", "-ResX=1280", "-ResY=720", "-ForceRes", '-ExecCmds="t.MaxFPS 30,sg.ViewDistanceQuality 2,sg.ShadowQuality 2"')
if ($Diagnostics) { $arguments += "-InterviewDiagnostics" }
if ($EditorGame) {
    $executable = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
    $arguments = @("`"$projectRoot\DigitalHuman.uproject`"", "/Game/Maps/L_Interview", "-game") + $arguments
} else {
    $executable = Join-Path $projectRoot "BuildOutput\Windows\DigitalHuman.exe"
    if (-not (Test-Path -LiteralPath $executable)) { throw "Package Windows Development into DigitalHuman\BuildOutput first, or use -EditorGame for a development check." }
}
Start-Process -FilePath $executable -ArgumentList $arguments -WindowStyle Hidden -Wait
