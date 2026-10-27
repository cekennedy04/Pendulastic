# One command to re-derive the PT zone thresholds and report what the control
# cohort measures for every scored parameter.
#
#     .\recalibrate.ps1
#
# Written because this run kept failing on everything except the analysis:
# the wrong working directory, a branch without the script, cmd.exe passing
# "#" comments through as arguments, and an unexpectedly dirty tree. None of
# those are the recalibration, so none of them should be in the way of it.
#
# It stops at the first real problem and says what to do. It writes a log and
# prints the path, so the whole output can be pasted back in one piece.

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# As run_pipeline.ps1 does: without it a Windows console defaults to cp1252 and
# any non-ASCII in the output raises UnicodeEncodeError mid-run, which looks
# like an analysis failure and is not one.
$env:PYTHONIOENCODING = "utf-8"
$env:OPENBLAS_NUM_THREADS = "1"

$log = Join-Path $PSScriptRoot ("recal-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".log")
function Say($m) { Write-Host $m; Add-Content -LiteralPath $log -Value $m }

Say "repo:   $PSScriptRoot"
Say "log:    $log"
Say ""

# 1. The tree must be clean, because step 2 changes branches.
$dirty = (git status --porcelain)
if ($dirty) {
  Say "STOPPING: uncommitted changes would be lost by the branch switch."
  Say $dirty
  Say ""
  Say "Commit them, or if they are only regenerated figures under"
  Say "neuromech/report/out or neuromech/out, discard with:"
  Say "    git checkout -- neuromech"
  exit 1
}

# 2. Get onto main, which is the only branch whose scorer these thresholds
#    are being derived for.
Say "fetching and switching to main..."
git fetch origin  2>&1 | Tee-Object -FilePath $log -Append
git checkout main 2>&1 | Tee-Object -FilePath $log -Append
git pull origin main 2>&1 | Tee-Object -FilePath $log -Append
Say ""
Say ("HEAD: " + (git log --oneline -1))
Say ""

# 3. The interpreter. The venv's python is what every other script here uses.
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $py)) {
  Say "STOPPING: no .venv\Scripts\python.exe. Use whichever python has numpy,"
  Say "scipy, matplotlib and pandas, e.g.:"
  Say "    python recalibrate_pt_zones.py --smoke"
  exit 1
}

if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "recalibrate_pt_zones.py"))) {
  Say "STOPPING: recalibrate_pt_zones.py is not here even on main. Something is"
  Say "wrong with the checkout -- report the HEAD line above."
  exit 1
}

# 4. Plumbing first, on synthetic limbs. About a minute. If this fails the
#    corpus run would fail the same way, and this says so in a minute rather
#    than after loading every trial.
Say "=== SMOKE (synthetic limbs; the thresholds it prints are meaningless) ==="
& $py recalibrate_pt_zones.py --smoke 2>&1 | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { Say ""; Say "SMOKE FAILED -- stopping before the corpus run."; exit 1 }

# 5. The real thing. --opti-root only if the recordings are not in the repo.
Say ""
Say "=== REAL RUN (control cohort) ==="
$opti = Join-Path $PSScriptRoot "OptiTrack_Recordings"
if (Test-Path -LiteralPath $opti) {
  & $py recalibrate_pt_zones.py 2>&1 | Tee-Object -FilePath $log -Append
} else {
  Say "OptiTrack_Recordings is not in the repo. Edit the line below to point at it:"
  Say '    .venv\Scripts\python.exe recalibrate_pt_zones.py --opti-root "D:\path\to\OptiTrack_Recordings"'
  exit 1
}

Say ""
Say "Done. Paste this whole file back:"
Say "    $log"
