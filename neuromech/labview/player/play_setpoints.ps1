<#
Pendulum Player without a VI: plays one player file into the rig's setpoint shared variable.

    PLAY.bat <file.csv>                      (double-click PLAY.bat, or drag a file onto it)
    powershell -File play_setpoints.ps1 -File R001.csv -Url "psp://.../M1JointAngleDesired" [-DryRun]

Does what PendulumPlayer.vi was specified to do, through the same NI DataSocket layer the VI would
use (CWDSLib.CWDataSocket, installed with LabVIEW), so StretchExperiment is not edited and nothing is
built in LabVIEW. Every 10 ms it writes the file's angle AT THE ELAPSED CLOCK TIME (clock-indexed, so a
late tick changes when the rig hears an update, never where the trajectory is). The rig's own position
loop tracks it; the encoder log records what the arm did.

Safety, in order:
  * refuses a file with a header, a first angle other than 0, or any |angle| above -MaxDeg
  * reads the variable first; if the arm's setpoint is not at 0 it ramps there slowly before starting
  * q or Esc during playback stops it and ramps the setpoint back to 0 at -ReturnDps
  * at the end it holds the last angle 2 s, then ramps back to 0 (unless -NoReturn)
The amplifier fault (E-stop) is still the real protection; the Angle Limit fields do not bound this.

Written for Windows PowerShell 2.0 and later (the lab PC may be old): no PS3+ syntax.
Exit codes: 0 played, 1 refused/cancelled, 3 DataSocket not installed for this bitness, 4 cannot connect.
#>
param(
    [string]$File,
    [string]$Url = "",
    [double]$MaxDeg = 45,
    [int]$PeriodMs = 10,
    [double]$ReturnDps = 10,
    [switch]$DryRun,
    [switch]$NoReturn,
    [switch]$Browse,
    [switch]$NoPrompt
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$urlFile = Join-Path $here "variable_url.txt"
# CWDSAccessModes, read from nids.dll's type library (not the intuitive 1/2: write is 4)
$CWDS_READ = 2
$CWDS_WRITE = 4

function Say($msg) { Write-Host $msg }
function Fail($msg, $code) { Write-Host ""; Write-Host "STOPPED: $msg" -ForegroundColor Red; exit $code }

# -- picking the variable on its own (PICK_VARIABLE.bat) --
if ($Browse -and -not $File) {
    try { $w = New-Object -ComObject CWDSLib.CWDataSocket }
    catch { Fail "NI DataSocket is not available to this PowerShell (bitness). The .bat retries in 32-bit." 3 }
    Write-Host "Pick the setpoint variable (expand the RT target -> variables - network - RT (separate) -> M1JointAngleDesired)."
    $m = [System.Reflection.Missing]::Value
    if (-not $w.SelectURL($m, $m, $m, $m)) { Fail "no variable picked." 1 }
    Set-Content -Path $urlFile -Value $w.URL
    Write-Host ("Saved {0} to variable_url.txt" -f $w.URL); exit 0
}

# -- the file --
if (-not $File) { Fail "no file given. Drag a player .csv onto PLAY.bat, or pass -File." 1 }
$File = (Resolve-Path $File).Path
$lines = [System.IO.File]::ReadAllLines($File)
$n = 0; foreach ($l in $lines) { if ($l.Trim().Length -gt 0) { $n++ } }
if ($n -lt 2) { Fail "$File has fewer than two rows." 1 }
if ($lines[0].Trim() -match "^[A-Za-z]") {
    Fail "$File starts with a header row. Use the file from the session's player\ folder (no header), not commands\." 1
}
$t = New-Object 'double[]' $n
$sp = New-Object 'double[]' $n
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$k = 0
foreach ($l in $lines) {
    $s = $l.Trim(); if ($s.Length -eq 0) { continue }
    $p = $s.Split(",")
    if ($p.Length -lt 2) { Fail "row $($k + 1) of $File does not have two columns: '$s'" 1 }
    $t[$k] = [double]::Parse($p[0], $inv); $sp[$k] = [double]::Parse($p[1], $inv); $k++
}
for ($i = 1; $i -lt $n; $i++) { if ($t[$i] -le $t[$i - 1]) { Fail "time does not increase at row $($i + 1) of $File." 1 } }
if ([math]::Abs($t[0]) -gt 1e-6) { Fail "the file's time must start at 0 (it starts at $($t[0]))." 1 }
if ([math]::Abs($sp[0]) -gt 0.01) { Fail "the file's first angle must be 0 (it is $($sp[0])). Every player file starts at 0." 1 }
$peak = 0.0; foreach ($v in $sp) { if ([math]::Abs($v) -gt $peak) { $peak = [math]::Abs($v) } }
if ($peak -gt $MaxDeg) {
    Fail ("the file reaches {0:F1} deg; the limit is {1:F1} deg (-MaxDeg). Not played." -f $peak, $MaxDeg) 1
}
$dur = $t[$n - 1]
$fs = ($n - 1) / $dur

function SetpointAt([double]$sec) {
    # linear interpolation at elapsed time, the same lookup the VI spec used
    if ($sec -le 0) { return $sp[0] }
    if ($sec -ge $dur) { return $sp[$n - 1] }
    $x = $sec * $fs; $j = [int][math]::Floor($x)
    while ($j -lt $n - 2 -and $t[$j + 1] -le $sec) { $j++ }
    while ($j -gt 0 -and $t[$j] -gt $sec) { $j-- }
    $f = ($sec - $t[$j]) / ($t[$j + 1] - $t[$j])
    return $sp[$j] + $f * ($sp[$j + 1] - $sp[$j])
}

# -- 1 ms timer so a 10 ms period is a 10 ms period --
if (-not ("PlayerTimer" -as [type])) {
    Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
public static class PlayerTimer {
    [DllImport("winmm.dll")] public static extern uint timeBeginPeriod(uint ms);
    [DllImport("winmm.dll")] public static extern uint timeEndPeriod(uint ms);
}
"@
}

# -- the variable --
$writer = $null
if (-not $DryRun) {
    try { $writer = New-Object -ComObject CWDSLib.CWDataSocket }
    catch { Fail "NI DataSocket is not available to this PowerShell (bitness). PLAY.bat retries in 32-bit." 3 }
    if ($Browse -or (-not $Url -and -not (Test-Path $urlFile))) {
        Say "Pick the setpoint variable (expand the RT target -> variables - network - RT (separate) -> M1JointAngleDesired)."
        $picked = $writer.SelectURL([System.Reflection.Missing]::Value, [System.Reflection.Missing]::Value, [System.Reflection.Missing]::Value, [System.Reflection.Missing]::Value)
        if (-not $picked) { Fail "no variable picked." 1 }
        $Url = $writer.URL
        Set-Content -Path $urlFile -Value $Url
        Say "Saved to variable_url.txt; later runs reuse it (-Browse to pick again)."
    }
    if (-not $Url) { $Url = (Get-Content $urlFile | Select-Object -First 1).Trim() }
}

Say ""
Say ("File      {0}" -f $File)
Say ("Duration  {0:F1} s   peak {1:F1} deg   ends at {2:F1} deg   ({3:F0} Hz file)" -f $dur, $peak, $sp[$n - 1], $fs)
if ($DryRun) { Say "Mode      DRY RUN: nothing is written to the rig; checks the file and the timing only" }
else { Say ("Variable  {0}" -f $Url) }

$reader = $null
$current = $null
if (-not $DryRun) {
    if (-not $writer.SyncConnectTo($Url, $CWDS_WRITE, 5000)) {
        Fail ("cannot connect to {0}: {1}. Check the IP and the name in Distributed System Manager." -f $Url, $writer.LastMessage) 4
    }
    try {
        $reader = New-Object -ComObject CWDSLib.CWDataSocket
        if ($reader.SyncConnectTo($Url, $CWDS_READ, 3000) -and $reader.SyncRead(2000)) { $current = [double]$reader.Data.Value }
    } catch { $current = $null }
    if ($current -eq $null) { Say "Current   could not read the setpoint back: make sure the arm is at 0 (Manual Control -> Goto Position 0)." }
    else { Say ("Current   setpoint reads {0:F2} deg" -f $current) }
}

Say ""
Say "Start the phone, then StretchExperiment's log. Then press Enter to play  (q = cancel)."
if (-not $NoPrompt) {
    $key = [Console]::ReadKey($true)
    if ($key.Key -ne "Enter") { Fail "cancelled before playing." 1 }
}

$sent = New-Object System.Text.StringBuilder
[void]$sent.AppendLine("t_wall_s,t_file_s,phase,setpoint_deg")   # t_file_s: where in the file (-1 off the file)
$sw = New-Object System.Diagnostics.Stopwatch
$script:failures = 0
$script:last = 0.0

function Send([double]$v, [string]$phase, [double]$tf = -1) {
    $script:last = $v
    [void]$sent.AppendLine([string]::Format($inv, "{0:F4},{1:F4},{2},{3:F4}", $sw.Elapsed.TotalSeconds, $tf, $phase, $v))
    if ($DryRun) { return }
    if ($writer.SyncWrite([double]$v, 100)) { $script:failures = 0 }
    else {
        $script:failures++
        if ($script:failures -ge 20) { Fail ("20 writes in a row failed ({0}). The rig holds the last value it received: {1:F2} deg." -f $writer.LastMessage, $v) 4 }
    }
}

function WaitTick([long]$dueMs) {
    # yield, never Sleep(1): Windows 11 ignores the 1 ms timer request for a window it considers hidden,
    # and Sleep(1) then lasts 15.6 ms. Busy for the length of a run, on one core; the VI would block instead.
    while ($sw.ElapsedMilliseconds -lt $dueMs) { [System.Threading.Thread]::Sleep(0) }
}

function NextTick([long]$t0) {
    # the next 10 ms boundary after now: a late tick skips ahead instead of bursting to catch up
    return $t0 + ([math]::Floor(($sw.ElapsedMilliseconds - $t0) / $PeriodMs) + 1) * $PeriodMs
}

function Ramp([double]$from, [double]$to, [string]$phase) {
    $dist = [math]::Abs($to - $from); if ($dist -lt 0.01) { Send $to $phase; return }
    $secs = $dist / $ReturnDps; $t0 = $sw.ElapsedMilliseconds
    while ($true) {
        $e = ($sw.ElapsedMilliseconds - $t0) / 1000.0
        if ($e -ge $secs) { Send $to $phase; break }
        Send ($from + ($to - $from) * $e / $secs) $phase
        WaitTick (NextTick $t0)
    }
}

# a redirected console (scheduled task, remote shell) throws on KeyAvailable; test once, not every tick
$script:keys = $true
try { [void][Console]::KeyAvailable } catch { $script:keys = $false }

function AbortKey {
    if (-not $script:keys) { return $false }
    try {
        while ([Console]::KeyAvailable) {
            $k = [Console]::ReadKey($true)
            if ($k.Key -eq "Escape" -or $k.KeyChar -eq "q" -or $k.KeyChar -eq "Q") { return $true }
        }
    } catch { }
    return $false
}

[void][PlayerTimer]::timeBeginPeriod(1)
try { (Get-Process -Id $PID).PriorityClass = "High" } catch { }   # fewer late ticks while other programs run
$stamps = New-Object 'System.Collections.Generic.List[System.Int64]'
$aborted = $false
try {
    $sw.Start()
    if ($current -ne $null -and [math]::Abs($current) -gt 0.5) {
        Say ("Ramping the setpoint from {0:F1} to 0 deg at {1:F0} deg/s first..." -f $current, $ReturnDps)
        Ramp $current 0.0 "ramp_in"
        WaitTick ($sw.ElapsedMilliseconds + 1000)
    }
    Say "Playing. q or Esc stops and returns to 0."
    $t0 = $sw.ElapsedMilliseconds; $lastShown = -1
    while ($true) {
        $now = $sw.ElapsedMilliseconds
        $e = ($now - $t0) / 1000.0
        $stamps.Add($now)
        Send (SetpointAt $e) "play" $e
        if ($e -ge $dur) { break }
        $sec = [int][math]::Floor($e)
        if ($sec -ne $lastShown) { $lastShown = $sec; Write-Host -NoNewline ("`r  {0,5:F1} / {1:F1} s   setpoint {2,7:F2} deg " -f $e, $dur, $script:last) }
        if (AbortKey) { $aborted = $true; break }
        WaitTick (NextTick $t0)
    }
    Write-Host ""
    if ($aborted) {
        Say ("Stopped by key at {0:F1} s. Returning to 0." -f (($sw.ElapsedMilliseconds - $t0) / 1000.0))
        Ramp $script:last 0.0 "abort_return"
    } elseif (-not $NoReturn) {
        $hold = $sw.ElapsedMilliseconds + 2000
        while ($sw.ElapsedMilliseconds -lt $hold) { Send $sp[$n - 1] "hold"; WaitTick ($sw.ElapsedMilliseconds + $PeriodMs) }
        if ([math]::Abs($sp[$n - 1]) -gt 0.5) { Say ("Returning from {0:F1} to 0 deg at {1:F0} deg/s." -f $sp[$n - 1], $ReturnDps) }
        Ramp $sp[$n - 1] 0.0 "return"
    }
} finally {
    [void][PlayerTimer]::timeEndPeriod(1)
    if ($writer -ne $null) { try { [void]$writer.SyncDisconnect(1000) } catch { } }
    if ($reader -ne $null) { try { [void]$reader.SyncDisconnect(1000) } catch { } }
    $logDir = Join-Path $here "sent"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
    $tag = ""; if ($DryRun) { $tag = "_dryrun" }
    $log = Join-Path $logDir ("{0}_{1}{2}.csv" -f [System.IO.Path]::GetFileNameWithoutExtension($File), (Get-Date -Format "yyyyMMdd-HHmmss"), $tag)
    [System.IO.File]::WriteAllText($log, $sent.ToString())
}

# -- how regular the updates were (check-log reports the same from the rig's side) --
$gaps = New-Object System.Collections.ArrayList
for ($i = 1; $i -lt $stamps.Count; $i++) { [void]$gaps.Add([double]($stamps[$i] - $stamps[$i - 1])) }
$g = $gaps.ToArray([double]); [array]::Sort($g)
if ($g.Length -gt 0) {
    $med = $g[[int]($g.Length / 2)]; $p95 = $g[[int]([math]::Min($g.Length - 1, [math]::Floor(0.95 * $g.Length)))]; $mx = $g[$g.Length - 1]
    Say ("Updates   {0} sent, every {1:F0} ms median, {2:F0} ms 95th pct, {3:F0} ms worst" -f ($g.Length + 1), $med, $p95, $mx)
    if ($p95 -gt 25) { Say "WARNING   updates slower than 25 ms: close other programs on this PC and play again." }
}
Say ("Sent log  {0}" -f $log)
if ($aborted) { exit 1 }
Say "Done. Write the StretchExperiment log's file name into the run sheet's encoder_file column."
exit 0
