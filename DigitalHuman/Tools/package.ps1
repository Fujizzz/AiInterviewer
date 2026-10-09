param([string]$EngineRoot = "D:\Epic Game\UE_5.8")
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logDirectory = Join-Path $projectRoot "Saved\AssetCleanup\Package-$stamp"
$stagingRoot = Join-Path $projectRoot "BuildOutput\PackageStaging-$stamp"
$outputRoot = Join-Path $projectRoot "BuildOutput\Windows"
$backupRoot = Join-Path $projectRoot "Saved\AssetCleanup\BuildBackup-$stamp\Windows"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

if (Get-Process UnrealEditor, UnrealEditor-Cmd, DigitalHuman -ErrorAction SilentlyContinue) {
    throw "Save and close Unreal Editor and DigitalHuman before packaging."
}

Write-Output "Pinning the native speech model for this engine."
$modelLog = Join-Path $logDirectory "runtime-cook.log"
& (Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor-Cmd.exe") "$projectRoot\DigitalHuman.uproject" -run=pythonscript "-script=$PSScriptRoot\configure_runtime_cook.py" -nullrhi -unattended -nosplash -nop4 -DisablePlugins=FabLauncher "-abslog=$modelLog" *> (Join-Path $logDirectory "runtime-cook-output.txt")
if ($LASTEXITCODE -ne 0) {
    Get-Content -LiteralPath $modelLog -Tail 35
    throw "Could not pin the speech model; the previous package is unchanged."
}

Write-Output "Cooking L_Interview into a fresh package without debug symbols."
$packageLog = Join-Path $logDirectory "package-output.txt"
& (Join-Path $EngineRoot "Engine\Build\BatchFiles\RunUAT.bat") BuildCookRun "-project=$projectRoot\DigitalHuman.uproject" -noP4 -platform=Win64 -clientconfig=Development -build -cook -map=/Game/Maps/L_Interview -stage -pak -nodebuginfo -archive "-archivedirectory=$stagingRoot" -unattended -utf8output *> $packageLog
if ($LASTEXITCODE -ne 0) {
    Get-Content -LiteralPath $packageLog -Tail 50
    throw "Unreal packaging failed; the previous package is unchanged. See $packageLog"
}

$newWindows = Join-Path $stagingRoot "Windows"
if (-not (Test-Path -LiteralPath (Join-Path $newWindows "DigitalHuman.exe"))) {
    throw "Packaging did not produce DigitalHuman.exe; the previous package is unchanged."
}

# All directory moves are constrained to this project. Keep the prior build
# recoverable, and use a fresh archive so old PDBs/logs cannot leak into uploads.
$temporaryZip = Join-Path $projectRoot "BuildOutput\DigitalHuman-$stamp.zip"
$uploadZip = Join-Path $projectRoot "BuildOutput\DigitalHuman.zip"
$backupZip = Join-Path (Split-Path -Parent $backupRoot) "DigitalHuman.zip"
foreach ($target in @($newWindows, $outputRoot, $backupRoot, $temporaryZip, $uploadZip, $backupZip)) {
    $absolute = [IO.Path]::GetFullPath($target)
    if (-not $absolute.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Package path escapes the project: $absolute"
    }
}
# ZIP stays alongside Windows, rather than inside the directory being zipped.
Write-Output "Creating the upload ZIP."
Compress-Archive -Path (Join-Path $newWindows "*") -DestinationPath $temporaryZip -CompressionLevel Optimal
$zip = [IO.Compression.ZipFile]::OpenRead($temporaryZip)
try {
    if (-not ($zip.Entries | Where-Object { $_.FullName -eq "DigitalHuman.exe" })) {
        throw "Upload ZIP has no root DigitalHuman.exe; the previous package is unchanged."
    }
} finally { $zip.Dispose() }

# Publish runtime and upload archive together, restoring both if a swap fails.
$oldRuntimeMoved = $false
$oldZipMoved = $false
$newRuntimeMoved = $false
try {
    New-Item -ItemType Directory -Path (Split-Path -Parent $backupRoot) -Force | Out-Null
    if (Test-Path -LiteralPath $outputRoot) {
        Move-Item -LiteralPath $outputRoot -Destination $backupRoot
        $oldRuntimeMoved = $true
    }
    if (Test-Path -LiteralPath $uploadZip) {
        Move-Item -LiteralPath $uploadZip -Destination $backupZip
        $oldZipMoved = $true
    }
    Move-Item -LiteralPath $newWindows -Destination $outputRoot
    $newRuntimeMoved = $true
    Move-Item -LiteralPath $temporaryZip -Destination $uploadZip
} catch {
    if ($newRuntimeMoved) { Move-Item -LiteralPath $outputRoot -Destination $newWindows }
    if ($oldRuntimeMoved) { Move-Item -LiteralPath $backupRoot -Destination $outputRoot }
    if ($oldZipMoved) { Move-Item -LiteralPath $backupZip -Destination $uploadZip }
    throw
}
Remove-Item -LiteralPath $stagingRoot
$runtimeBytes = (Get-ChildItem -LiteralPath $outputRoot -Recurse -File | Measure-Object Length -Sum).Sum
Write-Output "Runtime package: $outputRoot ($runtimeBytes bytes)"
Write-Output "Upload archive: $uploadZip ($((Get-Item -LiteralPath $uploadZip).Length) bytes)"
