<#
Flash the Tab5 camera firmware from a CI build, then check its boot log.

    .\flash_tab5.ps1 -Port COM6             # newest cam-build release (main)
    .\flash_tab5.ps1 -Port COM6 -Pr 19      # newest green build of a PR
    .\flash_tab5.ps1 -Port COM6 -Run 123456 # one workflow run

The C6 must already run ESP-Hosted 3.0.9 (it does once tab5-p4-c6-sdio-ota printed [PASS]).
Uses esptool from Python if installed, otherwise esptool's standalone Windows build (downloaded once).
-Pr and -Run need the GitHub CLI logged in. Prints a short summary; the full log is next to this script.
Exit codes: 1 download/flash failed, 2 panic or reboot in the log, 3 camera did not start.
#>
param(
    [Parameter(Mandatory)] [string] $Port,
    [int] $Pr,
    [long] $Run,
    [int] $Seconds = 15
)
$repo = "shadowlab/ATS_ETS_VolvoVNL_Cluster"
$dir = Join-Path $env:TEMP "vnl-tab5cam"
Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
New-Item -ItemType Directory $dir -ErrorAction Stop | Out-Null

if ($Pr) {
    $branch = gh pr view $Pr -R $repo --json headRefName -q .headRefName
    $Run = gh run list -R $repo -w tab5cam.yml -b $branch -s success -L 1 --json databaseId -q ".[0].databaseId"
    if (-not $Run) { "No green tab5cam build for PR $Pr"; exit 1 }
}
if ($Run) {
    gh run download $Run -R $repo -n vnl-tab5cam -D $dir
    if ($LASTEXITCODE) { "Download of run $Run failed"; exit 1 }
} else {
    # cam-build releases are not marked latest (that is the ESP32-S3 bridge), so find the newest one
    $rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases?per_page=50" -ErrorAction Stop |
        Where-Object { $_.tag_name -like "cam-*" } | Select-Object -First 1
    if (-not $rel) { "No cam-build release yet; use -Pr"; exit 1 }
    foreach ($name in "vnl-tab5cam.bin", "build-info.txt") {
        $a = $rel.assets | Where-Object name -eq $name
        Invoke-WebRequest $a.browser_download_url -OutFile (Join-Path $dir $name) -ErrorAction Stop
    }
    "release $($rel.tag_name)"
}

function Get-Esptool {
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

$log = Join-Path $PSScriptRoot "flash_tab5_$(Get-Date -Format yyyyMMdd_HHmmss).log"
Get-Content "$dir\build-info.txt" | Tee-Object -FilePath $log
$esptool = Get-Esptool
$esptoolArgs = @($esptool | Select-Object -Skip 1)
# The merged image fills 0x9000-0xFFFF (NVS + phy_init, see tab5cam/partitions.csv) with 0xFF. Write
# around it so Wi-Fi, regions and exposure saved on the Tab5 survive a reflash.
$img = [IO.File]::ReadAllBytes("$dir\vnl-tab5cam.bin")
$boot = "$dir\boot_0x0.bin"; $app = "$dir\app_0x10000.bin"
[IO.File]::WriteAllBytes($boot, $img[0..(0x9000 - 1)])
[IO.File]::WriteAllBytes($app, $img[0x10000..($img.Length - 1)])
& $esptool[0] @esptoolArgs --chip esp32p4 --port $Port --baud 460800 write_flash 0x0 $boot 0x10000 $app *>> $log
$flashed = $LASTEXITCODE -eq 0
"flash " + $(if ($flashed) { "OK" } else { "FAILED" }) | Tee-Object -FilePath $log -Append
if (-not $flashed) {
    Get-Content $log | Select-String -Pattern "error|fatal" | Select-Object -Last 5
    "log $log"
    exit 1
}

Start-Sleep -Seconds 2  # the USB port comes back after the reset
$bootLog = Join-Path $PSScriptRoot "tab5_boot_$(Get-Date -Format yyyyMMdd_HHmmss).log"
python "$PSScriptRoot\tab5_cam.py" bootlog --port $Port --seconds $Seconds --log $bootLog 2>&1 | Tee-Object -FilePath $log -Append
$rc = $LASTEXITCODE
"log $log, boot log $bootLog"
exit $rc
