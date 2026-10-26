<#
Flash the ESP32-S3 with a CI-built image, then log J1708 for a few seconds.

    .\flash_ci.ps1 -Port COM7             # latest release (main)
    .\flash_ci.ps1 -Port COM7 -Pr 7       # newest green build of a PR
    .\flash_ci.ps1 -Port COM7 -Run 123456 # one workflow run

Uses esptool from Python if installed, otherwise downloads esptool's
standalone Windows build once (no compiler or Python packages needed). -Pr and
-Run need the GitHub CLI logged in. Plug into the board's native USB port. The full log is written
next to this script; only a summary is printed. Exits non-zero on failure.
#>
param(
    [Parameter(Mandatory)] [string] $Port,
    [int] $Pr,
    [long] $Run,
    [int] $Seconds = 10
)
# Not "Stop": Windows PowerShell treats redirected stderr from esptool as an error.
$repo = "shadowlab/ATS_ETS_VolvoVNL_Cluster"
$dir = Join-Path $env:TEMP "vnl-bridge"
Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
New-Item -ItemType Directory $dir -ErrorAction Stop | Out-Null

if ($Pr) {
    $branch = gh pr view $Pr -R $repo --json headRefName -q .headRefName
    $Run = gh run list -R $repo -w firmware.yml -b $branch -s success -L 1 --json databaseId -q ".[0].databaseId"
    if (-not $Run) { throw "No green firmware build for PR $Pr" }
}
if ($Run) {
    gh run download $Run -R $repo -n vnl-bridge -D $dir
    if ($LASTEXITCODE) { throw "Download of run $Run failed" }
} else {
    $base = "https://github.com/$repo/releases/latest/download"
    Invoke-WebRequest "$base/vnl-bridge.bin" -OutFile "$dir\vnl-bridge.bin" -ErrorAction Stop
    Invoke-WebRequest "$base/build-info.txt" -OutFile "$dir\build-info.txt" -ErrorAction Stop
}

function Get-Esptool {
    # pip can't install esptool on very new Pythons without a C compiler,
    # so fall back to the standalone build from esptool's GitHub releases.
    python -m esptool version *> $null
    if ($LASTEXITCODE -eq 0) { return , @("python", "-m", "esptool") }
    $home_ = Join-Path $env:LOCALAPPDATA "vnl-bridge\esptool"
    $exe = Get-ChildItem $home_ -Recurse -Filter esptool.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $exe) {
        $release = Invoke-RestMethod "https://api.github.com/repos/espressif/esptool/releases/latest" -ErrorAction Stop
        $asset = $release.assets | Where-Object { $_.name -match "win(64|dows-amd64)\.zip$" } | Select-Object -First 1
        if (-not $asset) { throw "No Windows build in esptool $($release.tag_name)" }
        New-Item -ItemType Directory -Force $home_ | Out-Null
        $zip = Join-Path $home_ $asset.name
        Invoke-WebRequest $asset.browser_download_url -OutFile $zip -ErrorAction Stop
        Expand-Archive $zip -DestinationPath $home_ -Force
        $exe = Get-ChildItem $home_ -Recurse -Filter esptool.exe | Select-Object -First 1
    }
    return , @($exe.FullName)
}

$log = Join-Path $PSScriptRoot "flash_$(Get-Date -Format yyyyMMdd_HHmmss).log"
Get-Content "$dir\build-info.txt" | Tee-Object -FilePath $log
$esptool = Get-Esptool
$esptoolArgs = @($esptool | Select-Object -Skip 1)
& $esptool[0] @esptoolArgs --chip esp32s3 --port $Port --baud 921600 write_flash 0x0 "$dir\vnl-bridge.bin" *>> $log
$flashed = $LASTEXITCODE -eq 0
"flash " + $(if ($flashed) { "OK" } else { "FAILED" }) | Tee-Object -FilePath $log -Append
if (-not $flashed) {
    Get-Content $log | Select-String -Pattern "error|fatal" | Select-Object -Last 5
    "log $log"
    exit 1
}

Start-Sleep -Seconds 2  # the USB port comes back after the reset
python "$PSScriptRoot\j1708_listen.py" --port $Port --seconds $Seconds *>> $log
$listened = $LASTEXITCODE -eq 0
Get-Content $log | Select-String -Pattern "MID |Unsplittable|Error|Traceback" | Select-Object -Last 10
"log $log"
if (-not $listened) { exit 1 }
