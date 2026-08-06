# run_pipeline.ps1
# Waits for gen_training_data.py (PID $genPid) then runs the full pipeline.
# Output: pipeline_log.txt, align_out.txt, calibrate_out.txt, validate_out.txt

$root    = "C:\Users\cladi\Pendulastic"
$python  = "$root\.venv\Scripts\python.exe"
$logFile = "$root\pipeline_log.txt"
$genPid  = 25832

$env:PYTHONIOENCODING = "utf-8"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts  $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

function Run-Step {
    param($label, $script, $outFile)
    Log "=== START: $label ==="
    $start = Get-Date
    $errFile = "$root\err_$script.txt"
    $proc = Start-Process -FilePath $python `
        -ArgumentList "-u $root\$script" `
        -RedirectStandardOutput $outFile `
        -RedirectStandardError $errFile `
        -NoNewWindow -PassThru -Wait
    $dur = [int]((Get-Date) - $start).TotalSeconds
    Log "=== DONE:  $label  exit=$($proc.ExitCode)  time=${dur}s ==="
    if ($proc.ExitCode -ne 0) {
        Log "ERROR: $label failed"
        if (Test-Path $errFile) {
            Get-Content $errFile -Tail 20 | ForEach-Object { Log "  ERR: $_" }
        }
        exit 1
    }
    if (Test-Path $outFile) {
        Get-Content $outFile -Tail 15 | ForEach-Object { Log "  >> $_" }
    }
}

# Wait for gen_training_data.py to finish
Log "Pipeline started - waiting for PID $genPid (gen_training_data.py)"

$maxWait = 14400
$waited  = 0

while ($waited -lt $maxWait) {
    $gp = Get-Process -Id $genPid -ErrorAction SilentlyContinue
    if (-not $gp) {
        Log "PID $genPid finished."
        break
    }
    if ($waited % 120 -eq 0) {
        $n = (Get-ChildItem "$root\training_data\images" -File -ErrorAction SilentlyContinue | Measure-Object).Count
        Log "  gen_training_data running... images=$n  cpu=$($gp.CPU)s"
    }
    Start-Sleep 15
    $waited += 15
}

if ($waited -ge $maxWait) {
    Log "ERROR: gen_training_data.py did not finish in time."
    exit 1
}

# Check COCO JSON
$cocoJson = "$root\training_data\annotations\coco_keypoints.json"
if (-not (Test-Path $cocoJson)) {
    Log "ERROR: COCO JSON missing."
    exit 1
}

$cocoRaw = Get-Content $cocoJson -Raw
$annCount = ([regex]::Matches($cocoRaw, '"mp_image_angle_deg"')).Count
Log "COCO JSON found. Annotation count: $annCount"

if ($annCount -lt 1000) {
    Log "ERROR: Too few annotations ($annCount). gen_training_data.py may have failed."
    exit 1
}

# Run pipeline
Run-Step "align_and_calibrate.py" "align_and_calibrate.py" "$root\align_out.txt"
Run-Step "calibrate.py"           "calibrate.py"           "$root\calibrate_out.txt"
Run-Step "validate_tracking.py"   "validate_tracking.py"   "$root\validate_out.txt"

# Final summary
Log ""
Log "========================================"
Log "FINAL RESULTS"
Log "========================================"

$calJson = "$root\calibration_coefficients.json"
if (Test-Path $calJson) {
    $cal  = Get-Content $calJson -Raw | ConvertFrom-Json
    $c0   = [math]::Round($cal.coefficients[0], 4)
    $c1   = [math]::Round($cal.coefficients[1], 4)
    $c2   = [math]::Round($cal.coefficients[2], 6)
    $rmse = [math]::Round($cal.loto_overall_rmse_deg, 2)
    $mae  = [math]::Round($cal.loto_overall_mae_deg, 2)
    $ntr  = $cal.n_trials
    $nsmp = $cal.n_samples

    Log "Calibration:  ot = $c0 + $c1*mp + $c2*mp^2"
    Log "Trials: $ntr   Samples: $nsmp"
    Log "LOTO RMSE: $rmse deg   MAE: $mae deg"

    if ($c1 -gt 0.5) {
        Log "PASS: c1=$c1 is positive => patient tracking confirmed"
    } else {
        Log "FAIL: c1=$c1 should be > 0.5 => tracking fix not working"
    }

    if ($rmse -le 5.0) {
        Log "TARGET MET: LOTO RMSE $rmse deg <= 5 deg"
    } elseif ($rmse -le 10.0) {
        Log "CLOSE: LOTO RMSE $rmse deg (target <=5 deg)"
    } else {
        Log "NEEDS WORK: LOTO RMSE $rmse deg - outlier trials may be pulling it up"
    }
} else {
    Log "WARNING: calibration_coefficients.json not found"
}

Log ""
Log "Figures saved to: $root\figures\"
Log "Pipeline complete. Good morning!"
