<#
Copy the study data that git does not carry (Recordings, OptiTrack, MediaPipe) to a USB drive,
then verify it file-by-file.

  .\usb_export_data.ps1 -Drive D            # copy (resumable) + verify against source
  .\usb_export_data.ps1 -Drive D -DryRun    # sizes only, copies nothing
  .\usb_export_data.ps1 -Drive E -VerifyOnly   # run ON THE NEW DEVICE: checks the drive against MANIFEST.csv

Destination: <Drive>:\Pendulastic_Data\
#>
param(
    [Parameter(Mandatory)][string]$Drive,
    [switch]$DryRun,
    [switch]$VerifyOnly
)
$ErrorActionPreference = 'Stop'
$Drive = $Drive.TrimEnd(':', '\')
$src = $PSScriptRoot
$dst = "${Drive}:\Pendulastic_Data"
$manifest = Join-Path $dst 'MANIFEST.csv'

$folders = @('Recordings', 'OptiTrack_Recordings', 'OptiTrack_Tracking_Data_Participant_0', 'sweep_cache', 'models\mediapipe')
$files = @('excluded_trials.json', 'trial_quality_tags.json', 'tracking_selections.json',
    'calibration_coefficients.json', 'calibration_coefficients_lying_down.json',
    'calibration_coefficients_upright.json', 'imu_calibration_config.json', 'rmse_best_config.json')

function Check-Tree($root, $rows) {
    $bad = 0
    foreach ($r in $rows) {
        $p = Join-Path $root $r.path
        if (-not (Test-Path -LiteralPath $p)) { Write-Host "MISSING  $($r.path)"; $bad++; continue }
        if ((Get-Item -LiteralPath $p).Length -ne [int64]$r.size) { Write-Host "SIZE     $($r.path)"; $bad++ }
    }
    return [int]$bad
}

if ($VerifyOnly) {
    if (-not (Test-Path $manifest)) { throw "No MANIFEST.csv at $manifest - export never finished." }
    $rows = Import-Csv $manifest
    $bad = Check-Tree $dst $rows
    "{0} files in manifest, {1} problems" -f @($rows).Count, $bad
    if ($bad -gt 0) { exit 1 }
    'VERIFIED: every file present at the right size.'
    exit 0
}

# Source inventory
$rows = @()
foreach ($f in $folders) {
    $p = Join-Path $src $f
    if (-not (Test-Path $p)) { Write-Warning "skip (absent): $f"; continue }
    Get-ChildItem -LiteralPath $p -Recurse -File -Force | Where-Object { $_.FullName -notmatch '__pycache__' } | ForEach-Object {
        $rows += [pscustomobject]@{ path = $_.FullName.Substring($src.Length + 1); size = $_.Length }
    }
}
foreach ($f in $files) {
    $p = Join-Path $src $f
    if (Test-Path $p) { $rows += [pscustomobject]@{ path = $f; size = (Get-Item $p).Length } }
}
$totalGB = ($rows | Measure-Object size -Sum).Sum / 1GB
$big = $rows | Where-Object { $_.size -ge 4GB - 1 }
"{0:N0} files, {1:N2} GB to copy" -f $rows.Count, $totalGB

$vol = New-Object IO.DriveInfo "${Drive}:"
if (-not $vol.IsReady) { throw "${Drive}: is not ready - is the USB plugged in?" }
$freeGB = $vol.AvailableFreeSpace / 1GB
"Target {0}: {1}, {2:N1} GB free" -f "${Drive}:", $vol.DriveFormat, $freeGB
if ($freeGB -lt $totalGB + 0.5) { throw "Not enough free space on ${Drive}:" }
if ($vol.DriveFormat -eq 'FAT32' -and $big) { throw "FAT32 cannot hold files >= 4 GB: $($big.path -join ', ')" }
if ($DryRun) { 'Dry run - nothing copied.'; return }

New-Item -ItemType Directory -Force $dst | Out-Null
foreach ($f in $folders) {
    $s = Join-Path $src $f
    if (-not (Test-Path $s)) { continue }
    "== $f"
    robocopy $s (Join-Path $dst $f) /E /COPY:DAT /DCOPY:DAT /R:2 /W:2 /XJ /XD __pycache__ /NP /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $f (exit $LASTEXITCODE). Re-run the same command to resume." }
}
foreach ($f in $files) {
    if (Test-Path (Join-Path $src $f)) { Copy-Item (Join-Path $src $f) (Join-Path $dst $f) -Force }
}

# Verify against the source inventory before declaring success
$bad = Check-Tree $dst $rows
if ($bad -gt 0) { throw "$bad files missing or wrong size - NOT writing the manifest. Re-run to resume." }
$rows | Export-Csv $manifest -NoTypeInformation
"DONE: {0:N0} files verified, manifest written. On the new device run: .\usb_export_data.ps1 -Drive <letter> -VerifyOnly" -f $rows.Count
