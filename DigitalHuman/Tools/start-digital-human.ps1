param(
    [string]$EngineRoot = "D:\Epic Game\UE_5.8",
    [switch]$EditorGame,
    [switch]$Diagnostics,
    [switch]$SoftwareEncoding,
    [string]$SignallingUrl = "ws://127.0.0.1:8888"
)
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$encoderCodec = if ($SoftwareEncoding) { "VP8" } else { "H264" }
$endpoint = $null
if (-not [Uri]::TryCreate($SignallingUrl, [UriKind]::Absolute, [ref]$endpoint) -or
    $endpoint.Scheme -notin @("ws", "wss") -or $endpoint.UserInfo -or $endpoint.Fragment -or
    ($endpoint.Scheme -eq "ws" -and $endpoint.Host -notin @("127.0.0.1", "localhost"))) {
    throw "Use loopback ws:// signalling (including an SSH tunnel), or a trusted wss:// endpoint."
}
$arguments = @("-PixelStreamingConnectionURL=$SignallingUrl", "-AudioMixer", "-RenderOffscreen", "-ResX=1920", "-ResY=1080", "-ForceRes",
    "-PixelStreamingUseMediaCapture=true", "-PixelStreamingCaptureUseFence=true", "-PixelStreamingWebRTCFps=30",
    "-PixelStreamingEncoderCodec=$encoderCodec",
    '-ExecCmds="t.IdleWhenNotForeground 0,t.MaxFPS 30,sg.ViewDistanceQuality 2,sg.ShadowQuality 2"')
if ($Diagnostics) { $arguments += "-InterviewDiagnostics" }
if ($EditorGame) {
    $executable = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
    $arguments = @("`"$projectRoot\DigitalHuman.uproject`"", "/Game/Maps/L_Interview", "-game") + $arguments
} else {
    $executable = Join-Path $projectRoot "BuildOutput\Windows\DigitalHuman.exe"
    if (-not (Test-Path -LiteralPath $executable)) { throw "Package Windows Development into DigitalHuman\BuildOutput first, or use -EditorGame for a development check." }
}
Start-Process -FilePath $executable -ArgumentList $arguments -WindowStyle Hidden -Wait
