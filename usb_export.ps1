<#
  Copy everything a fresh `git clone` of Pendulastic would NOT bring, onto a USB drive.
  Copy-only: nothing on this PC is deleted or modified (robocopy runs without /MIR or /PURGE).

  Usage (PowerShell, from the repo root):
    .\usb_export.ps1 -Drive E                      # data + git bundle + uncommitted work
    .\usb_export.ps1 -Drive E -IncludeSecrets      # also .env, .certs, .vercel
    .\usb_export.ps1 -Drive E -DryRun              # list what would be copied, copy nothing

  Result: E:\Pendulastic_USB_Export\
    git\pendulastic-all.bundle     every branch + tag, incl. local-only commits (not on GitHub)
    git\uncommitted\<worktree>\    git diff + untracked files for each worktree with edits
    data\<folder>\                 gitignored data dirs (recordings, models, training data, ...)
    state\                         gitignored root state files (calibration, mas_scores, sessions, ...)
    MANIFEST.txt                   file counts + sizes per folder, for checking the copy
    RESTORE.txt                    steps for the new device
  Needs ~21 GB free; format the drive exFAT or NTFS (not FAT32).
#>
param(
  [Parameter(Mandatory)][ValidatePattern('^[A-Za-z]$')][string]$Drive,
  [switch]$IncludeSecrets,
  [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$root = "$($Drive.ToUpper()):\Pendulastic_USB_Export"
if (-not (Test-Path "$($Drive):\")) { throw "Drive $($Drive): not found" }
if ($root.StartsWith($repo, 'OrdinalIgnoreCase')) { throw 'Destination must not be inside the repo' }

$dataDirs = 'Recordings','OptiTrack_Recordings','OptiTrack_Tracking_Data_Participant_0','models','training_data',
            'participants','uploads','sweep_cache','figures','pendulastic model analysis','new_models_analysis',
            'data','Model_Analysis_Outputs','Pendulastic_Mobile_App_Trial','to_do','bench_figures','ck_app_pt_test'
$stateFiles = 'calibration_coefficients.json','calibration_coefficients_lying_down.json','calibration_coefficients_upright.json',
              'imu_calibration_config.json','mas_scores.csv','sessions.json','tracking_selections.json',
              'FIX_APP_persist_unscorable_PROMPT.md','FIX_SCORING_release_detection_PROMPT.md'
$secretDirs = '.certs','.vercel'; $secretFiles = '.env'
# Not copied (regenerable): .venv (pip install -r requirements-lock.txt), build/, dist/, __pycache__, .pytest_cache

function Say($m) { Write-Host $m }
if ($DryRun) { Say "DRY RUN -> $root" }
else { New-Item -ItemType Directory -Force "$root\git\uncommitted","$root\data","$root\state" | Out-Null }

# 1) git: one bundle with every ref (covers local-only branches), plus uncommitted work per worktree
Say '== git bundle =='
if (-not $DryRun) {
  git -C $repo bundle create "$root\git\pendulastic-all.bundle" --all
  git -C $repo bundle verify "$root\git\pendulastic-all.bundle"
}
Say '== uncommitted work in each worktree =='
$wts = git -C $repo worktree list --porcelain | Where-Object { $_ -like 'worktree *' } | ForEach-Object { $_.Substring(9) }
foreach ($w in $wts) {
  $name = (Split-Path $w -Leaf)
  $changed = git -C $w status --short
  if (-not $changed) { Say "  clean: $name"; continue }
  Say "  DIRTY: $name ($(@($changed).Count) entries)"
  if ($DryRun) { continue }
  $o = "$root\git\uncommitted\$name"; New-Item -ItemType Directory -Force "$o\untracked" | Out-Null
  git -C $w rev-parse HEAD | Set-Content "$o\BASE_COMMIT.txt"
  git -C $w diff HEAD --binary | Set-Content -Encoding utf8 "$o\tracked-changes.patch"
  git -C $w ls-files --others --exclude-standard | ForEach-Object {
    $dest = Join-Path "$o\untracked" $_; New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Copy-Item -LiteralPath (Join-Path $w $_) $dest
  }
}

# 2) gitignored data dirs (robocopy: /E subfolders, no purge, retry once, skip junctions + caches)
Say '== data directories =='
foreach ($d in $dataDirs) {
  $src = Join-Path $repo $d
  if (-not (Test-Path $src)) { Say "  skip (missing): $d"; continue }
  Say "  $d"
  if ($DryRun) { continue }
  # /FFT = 2s timestamp tolerance (FAT32), so a re-run skips files already copied instead of recopying them
  robocopy $src "$root\data\$d" /E /COPY:DAT /DCOPY:DAT /FFT /R:1 /W:1 /XJ /XD __pycache__ /NFL /NDL /NP /NJH | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $d (exit $LASTEXITCODE)" }
}

# 3) gitignored root state files
Say '== state files =='
$files = @($stateFiles); if ($IncludeSecrets) { $files += $secretFiles }
foreach ($f in $files) {
  $src = Join-Path $repo $f
  if (Test-Path $src) { Say "  $f"; if (-not $DryRun) { Copy-Item -LiteralPath $src "$root\state\$f" } } else { Say "  skip (missing): $f" }
}
if ($IncludeSecrets) {
  foreach ($d in $secretDirs) {
    $src = Join-Path $repo $d
    if (Test-Path $src) { Say "  $d (secret)"; if (-not $DryRun) { robocopy $src "$root\state\$d" /E /R:1 /W:1 /NFL /NDL /NP /NJH | Out-Null } }
  }
  Say '  NOTE: .env/.certs contain secrets - treat the USB drive accordingly.'
} else { Say '  secrets (.env, .certs, .vercel) NOT copied; re-run with -IncludeSecrets if wanted' }

if ($DryRun) { Say 'Dry run done.'; return }

# 4) manifest + restore notes
Say '== manifest =='
$m = foreach ($d in (Get-ChildItem "$root\data","$root\state","$root\git" -Directory -ErrorAction SilentlyContinue)) {
  $s = Get-ChildItem $d.FullName -Recurse -File -Force | Measure-Object Length -Sum
  '{0}`t{1:N0} MB`t{2} files' -f $d.FullName.Substring($root.Length+1), ($s.Sum/1MB), $s.Count
}
$m | Set-Content "$root\MANIFEST.txt"; $m
Say '== verify: source vs USB (file count and bytes) =='
$bad = 0
foreach ($d in $dataDirs) {
  $src = Join-Path $repo $d; if (-not (Test-Path $src)) { continue }
  $a = Get-ChildItem $src -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\__pycache__\\' } | Measure-Object Length -Sum
  $b = Get-ChildItem "$root\data\$d" -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum
  if ($a.Count -ne $b.Count -or $a.Sum -ne $b.Sum) { $bad++; Say ("  MISMATCH {0}: source {1} files / {2:N0} MB, USB {3} files / {4:N0} MB" -f $d,$a.Count,($a.Sum/1MB),$b.Count,($b.Sum/1MB)) }
}
if ($bad -eq 0) { Say '  all data folders match the source' } else { Say "  $bad folder(s) differ - re-run the script to resume, and check the drive (chkdsk) if it persists" }
@"
RESTORE ON THE NEW DEVICE
1. git clone https://github.com/cekennedy04/Pendulastic.git ; cd Pendulastic
2. Pull in local-only branches (not on GitHub):
     git fetch <USB>\git\pendulastic-all.bundle 'refs/heads/*:refs/bundle/*'
     git branch <name> refs/bundle/<name>          # per branch you want, or: git checkout -b <name> refs/bundle/<name>
   (list them: git bundle list-heads <USB>\git\pendulastic-all.bundle)
3. Merge data back (folder names are the same as the repo root). This only ADDS files that are
   missing on the new laptop and never overwrites existing ones (/XC /XN /XO skip changed/newer/older):
     robocopy <USB>\Pendulastic_USB_Export\data . /E /XC /XN /XO /R:1 /W:1
   (add /L first to preview what would be copied)
   State files:  copy <USB>\Pendulastic_USB_Export\state\* into the repo root (.env/.certs/.vercel too, if included).
4. Uncommitted edits: for each folder in git\uncommitted\, check out BASE_COMMIT.txt, then
     git apply tracked-changes.patch   and copy the untracked\ files into place.
5. Recreate the environment:  python -m venv .venv ; .venv\Scripts\pip install -r requirements-lock.txt
   (models are in data\models; mediapipe weights live in ./models/)
"@ | Set-Content "$root\RESTORE.txt"
Say "Done -> $root"
