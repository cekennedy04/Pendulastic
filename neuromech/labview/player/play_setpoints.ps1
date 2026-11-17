<#
Pendulum Player without a VI: plays one player file into the rig's setpoint shared variable.

    PLAY.bat <file.csv>                      (double-click PLAY.bat, or drag a file onto it)
    powershell -File play_setpoints.ps1 -File R001.csv -Url "psp://.../M1JointAngleDesired" [-DryRun] [-MaxDeg 30]

Does what PendulumPlayer.vi was specified to do, through the same NI DataSocket layer the VI would
use (CWDSLib.CWDataSocket, installed with LabVIEW), so StretchExperiment is not edited and nothing is
built in LabVIEW. Every 10 ms it writes the file's angle AT THE ELAPSED CLOCK TIME (clock-indexed, so a
late tick changes when the rig hears an update, never where the trajectory is). The rig's own position
loop tracks it; the encoder log records what the arm did.

Safety. The rules live in RigGuard.cs beside this script (the Pendulum Designer compiles the same file),
loaded with Add-Type at start-up; without it nothing plays. In order:
  * -MaxDeg can only LOWER the hard limit of 40 deg built into RigGuard.cs; above 40 is refused
  * refuses a file with a header, a non-number, NaN/Inf, time not starting at 0 or not increasing, a first
    angle other than 0, any |angle| over the limit, any change over 6 deg between 10 ms writes (600 deg/s),
    or a PREDICTED delivered peak over the limit (the rig overshoots fast content: ~2x at 5 Hz). A file is
    refused whole, never clipped. Every refusal leaves sent\<file>_<time>_refused.csv
  * reads the variable first; if it cannot be read it does NOT play (no step to 0 from wherever the arm
    is); if the setpoint is not at 0 it ramps there at -ReturnDps before starting
  * every write passes the backstop: NaN/Inf never sent, clamped to the limit, at most 6 deg per write.
    If the backstop ever engages while playing, playback stops and ramps to 0 (exit 5): the file passed
    the checks, so something is wrong. The event is in the sent log (phase "guard")
  * q or Esc during playback stops it and ramps the setpoint back to 0 at -ReturnDps
  * at the end it holds the last angle 2 s, then ramps back to 0 (unless -NoReturn)
  * only one player writes at a time: a machine-wide lock (shared with the Pendulum Designer) is taken
    before the ramp-in and released after the return to 0; a second player refuses to start
  * optional encoder watchdog: PICK_ENCODER.bat saves the arm's ACTUAL position variable to
    encoder_url.txt (or pass -EncoderUrl). Then every 50 ms of play it reads the arm and, if the arm is
    past the limit + 3 deg or further from every setpoint of the last 150 ms than the following-error
    bound (5 deg + 1.5 x what the identified plant predicts for this file), stops and ramps the setpoint
    to 0 (exit 6). Without it the console says "encoder watchdog OFF". It runs in this same process: a
    second opinion that moves the setpoint, not an independent hardware cutoff
What it cannot do: if 20 writes in a row fail it stops and the rig HOLDS the last value it received; if
this window is closed or killed mid-play the rig also holds the last setpoint. The amplifier fault (E-stop)
is the protection for both. The Angle Limit fields on the front panel do not bound this player.

Written for Windows PowerShell 2.0 and later (the lab PC may be old): no PS3+ syntax.
Exit codes: 0 played, 1 refused/cancelled, 3 DataSocket not installed for this bitness, 4 cannot connect
or writes failing, 5 stopped by the backstop, 6 stopped by the encoder watchdog.
Test-only switches (never needed at the bench): -TestFakeDataSocket "read=0|read=12.5|read=nan|read=fail,
write=fail" stands a fake variable in for NI DataSocket; -TestFakeEncoder "track|<deg>|fail|failafter"
a fake encoder; -TestBypassLoadChecks skips the load checks to exercise the backstop and is refused
unless -DryRun or -TestFakeDataSocket is given.
#>
param(
    [string]$File,
    [string]$Url = "",
    [double]$MaxDeg = 40,
    [int]$PeriodMs = 10,
    [double]$ReturnDps = 10,
    [switch]$DryRun,
    [switch]$NoReturn,
    [switch]$Browse,
    [switch]$NoPrompt,
    [string]$EncoderUrl = "",
    [switch]$BrowseEncoder,
    [switch]$NoEncoderWatchdog,
    [string]$TestFakeDataSocket = "",
    [string]$TestFakeEncoder = "",
    [switch]$TestBypassLoadChecks
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$urlFile = Join-Path $here "variable_url.txt"
$logDir = Join-Path $here "sent"
# CWDSAccessModes, read from nids.dll's type library (not the intuitive 1/2: write is 4)
$CWDS_READ = 2
$CWDS_WRITE = 4
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$writer = $null
$reader = $null
$encoder = $null
$script:lock = $null
$encUrlFile = Join-Path $here "encoder_url.txt"

function Say($msg) { Write-Host $msg }
function Fail($msg, $code) {
    Write-Host ""; Write-Host "STOPPED: $msg" -ForegroundColor Red
    if ($script:lock -ne $null) { try { $script:lock.ReleaseMutex() } catch { }; $script:lock = $null }
    exit $code
}

# -- the safety rules (shared with the Pendulum Designer) --
$guardSrc = Join-Path $here "RigGuard.cs"
if (-not ("Pendulastic.RigGuard" -as [type])) {
    if (-not (Test-Path $guardSrc)) { Fail "RigGuard.cs is missing beside play_setpoints.ps1. It holds the safety rules; nothing plays without it." 1 }
    try { Add-Type -Path $guardSrc } catch { Fail ("could not load the safety rules in RigGuard.cs ({0}). Not played." -f $_.Exception.Message) 1 }
}

function LogStem {
    $name = "player"
    if ($File) { $name = [System.IO.Path]::GetFileNameWithoutExtension($File) }
    return ("{0}_{1}" -f $name, (Get-Date -Format "yyyyMMdd-HHmmss"))
}

function Refuse([string]$kind, [string]$msg) {
    # on record beside the runs, so check-log and the bench notes see every refusal
    try {
        if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
        $p = Join-Path $logDir ((LogStem) + "_refused.csv")
        [System.IO.File]::WriteAllText($p, [Pendulastic.RigGuard]::RefusalLog($kind, $msg))
        Write-Host ("Refusal logged to {0}" -f $p)
    } catch { Write-Host "(could not write the refusal log)" }
    foreach ($s in @($writer, $reader, $encoder)) { if ($s -ne $null) { try { [void]$s.SyncDisconnect(1000) } catch { } } }
    Fail ("{0}. Not played." -f $msg) 1
}

function NewFakeSocket([string]$spec) {
    # test stand-in for CWDSLib.CWDataSocket: the members this script uses, nothing reaches a rig
    $readOk = $true; $writeOk = $true; $val = 0.0
    foreach ($part in $spec.Split(",")) {
        $kv = $part.Trim().Split("=")
        if ($kv.Length -ne 2) { continue }
        if ($kv[0] -eq "read") {
            if ($kv[1] -eq "fail") { $readOk = $false }
            elseif ($kv[1] -eq "nan") { $val = [double]::NaN }
            else { $val = [double]::Parse($kv[1], $inv) }
        }
        if ($kv[0] -eq "write" -and $kv[1] -eq "fail") { $writeOk = $false }
    }
    $data = New-Object PSObject
    Add-Member -InputObject $data -MemberType NoteProperty -Name Value -Value $val
    $o = New-Object PSObject
    Add-Member -InputObject $o -MemberType NoteProperty -Name Data -Value $data
    Add-Member -InputObject $o -MemberType NoteProperty -Name LastMessage -Value "fake DataSocket"
    Add-Member -InputObject $o -MemberType NoteProperty -Name URL -Value "fake://setpoint"
    Add-Member -InputObject $o -MemberType NoteProperty -Name ReadOk -Value $readOk
    Add-Member -InputObject $o -MemberType NoteProperty -Name WriteOk -Value $writeOk
    Add-Member -InputObject $o -MemberType ScriptMethod -Name SyncConnectTo -Value { param($u, $m, $to) return $true }
    Add-Member -InputObject $o -MemberType ScriptMethod -Name SyncRead -Value { param($to) return $this.ReadOk }
    Add-Member -InputObject $o -MemberType ScriptMethod -Name SyncWrite -Value { param($v, $to) return $this.WriteOk }
    Add-Member -InputObject $o -MemberType ScriptMethod -Name SyncDisconnect -Value { param($to) return $true }
    Add-Member -InputObject $o -MemberType ScriptMethod -Name SelectURL -Value { param($a, $b, $c, $d) return $false }
    return $o
}

function NewSocket {
    if ($TestFakeDataSocket) { return (NewFakeSocket $TestFakeDataSocket) }
    return (New-Object -ComObject CWDSLib.CWDataSocket)
}

function NewFakeEncoder([string]$spec) {
    # test stand-in for the arm's actual-position variable. track = follows the last setpoint sent exactly
    $o = NewFakeSocket "read=0"
    $o.ReadOk = ($spec -ne "fail")
    if ($spec -eq "track" -or $spec -eq "failafter") {
        $d = New-Object PSObject
        Add-Member -InputObject $d -MemberType ScriptProperty -Name Value -Value { $script:last }
        $o.Data = $d
    } elseif ($spec -ne "fail") {
        $o.Data.Value = [double]::Parse($spec, $inv)
    }
    if ($spec -eq "failafter") {
        Add-Member -InputObject $o -MemberType NoteProperty -Name Reads -Value 0
        Add-Member -InputObject $o -MemberType ScriptMethod -Name SyncRead -Value { param($to) $this.Reads++; return ($this.Reads -le 3) } -Force
    }
    return $o
}

function NewEncoderSocket {
    if ($TestFakeEncoder) { return (NewFakeEncoder $TestFakeEncoder) }
    return (New-Object -ComObject CWDSLib.CWDataSocket)
}

# -- picking the encoder (actual position) variable on its own (PICK_ENCODER.bat) --
if ($BrowseEncoder -and -not $File) {
    try { $w = New-Object -ComObject CWDSLib.CWDataSocket }
    catch { Fail "NI DataSocket is not available to this PowerShell (bitness). The .bat retries in 32-bit." 3 }
    Write-Host "Pick the variable that holds the arm's ACTUAL (encoder) position, if StretchExperiment publishes one. Cancel = no watchdog."
    $m = [System.Reflection.Missing]::Value
    if (-not $w.SelectURL($m, $m, $m, $m)) { Fail "no encoder variable picked; the encoder watchdog stays OFF." 1 }
    Set-Content -Path $encUrlFile -Value $w.URL
    Write-Host ("Saved {0} to encoder_url.txt (delete that file to turn the watchdog off)" -f $w.URL); exit 0
}

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

# -- the limit and the file --
if (-not $File) { Fail "no file given. Drag a player .csv onto PLAY.bat, or pass -File." 1 }
$File = (Resolve-Path $File).Path
$why = [Pendulastic.RigGuard]::CheckLimit($MaxDeg)
if ($why) { Refuse "operator_limit" ("-MaxDeg: " + $why) }
if ($TestBypassLoadChecks -and -not ($DryRun -or $TestFakeDataSocket)) {
    Fail "-TestBypassLoadChecks is for tests only: it needs -DryRun or -TestFakeDataSocket and never plays to the rig." 1
}
$t = $null; $sp = $null
$bad = [Pendulastic.RigGuard]::Parse([System.IO.File]::ReadAllLines($File), [ref]$t, [ref]$sp)
if ($bad -ne $null) { Refuse $bad.Kind ("{0}: {1}" -f $File, $bad.Reason) }
$n = $t.Length
if ($n -lt 2) { Refuse "rows" "$File has fewer than two rows" }
$rep = [Pendulastic.RigGuard]::Check($t, $sp, $MaxDeg)
if (-not $rep.Ok) {
    if ($TestBypassLoadChecks) { Say ("TEST: load checks bypassed ({0}: {1})" -f $rep.Kind, $rep.Reason) }
    else { Refuse $rep.Kind ("{0}: {1}" -f [System.IO.Path]::GetFileName($File), $rep.Reason) }
}
$dur = $t[$n - 1]
$fs = ($n - 1) / $dur
$followBound = [Pendulastic.RigGuard]::FollowBoundDeg([Pendulastic.RigGuard]::PredictedFollowingError([Pendulastic.RigGuard]::Stream($t, $sp)))
if ($NoEncoderWatchdog) { $EncoderUrl = "" }
elseif (-not $EncoderUrl -and (Test-Path $encUrlFile)) { $EncoderUrl = ([string](Get-Content $encUrlFile | Select-Object -First 1)).Trim() }
if ($TestFakeEncoder -and -not $EncoderUrl) { $EncoderUrl = "fake://encoder" }
if ($DryRun) { $EncoderUrl = "" }

# -- one writer at a time (the Pendulum Designer takes the same lock) --
if (-not $DryRun) {
    $lockWhy = $null
    $script:lock = [Pendulastic.RigGuard]::AcquireWriterLock([ref]$lockWhy)
    if ($script:lock -eq $null) { Refuse "writer_lock" $lockWhy }
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
if (-not $DryRun) {
    try { $writer = NewSocket }
    catch { Fail "NI DataSocket is not available to this PowerShell (bitness). PLAY.bat retries in 32-bit." 3 }
    if ($TestFakeDataSocket -and -not $Url) { $Url = "fake://setpoint" }
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
Say ([string]::Format($inv, "Duration  {0:F1} s   peak {1:F1} deg   ends at {2:F1} deg   ({3:F0} Hz file)", $dur, $rep.RawPeakDeg, $sp[$n - 1], $fs))
Say ([string]::Format($inv, "Guard     limit {0:F1} deg   predicted delivered peak {1:F1} deg   largest change {2:F2} deg per 10 ms write", $MaxDeg, $rep.PredictedPeakDeg, $rep.MaxStepDeg))
if ($DryRun) { Say "Mode      DRY RUN: nothing is written to the rig; checks the file and the timing only" }
else { Say ("Variable  {0}" -f $Url) }
if ($TestFakeDataSocket) { Say ("Mode      TEST: fake DataSocket ({0}); nothing reaches a rig" -f $TestFakeDataSocket) }

$current = 0.0
if (-not $DryRun) {
    if (-not $writer.SyncConnectTo($Url, $CWDS_WRITE, 5000)) {
        Fail ("cannot connect to {0}: {1}. Check the IP and the name in Distributed System Manager." -f $Url, $writer.LastMessage) 4
    }
    $got = $false; $readWhy = "no reading"
    try {
        $reader = NewSocket
        if ($reader.SyncConnectTo($Url, $CWDS_READ, 3000) -and $reader.SyncRead(2000)) { $current = [double]$reader.Data.Value; $got = $true }
        else { $readWhy = [string]$reader.LastMessage }
    } catch { $readWhy = $_.Exception.Message }
    if (-not $got) {
        Refuse "setpoint_read" ("could not read the current setpoint back ({0}), so the start cannot be ramped from it. Check the variable in Distributed System Manager, put the arm at 0 (Manual Control -> Goto Position 0) and try again" -f $readWhy)
    }
    if ([double]::IsNaN($current) -or [double]::IsInfinity($current)) { Refuse "setpoint_read" ("the current setpoint reads {0}" -f $current) }
    if ([math]::Abs($current) -gt $MaxDeg) {
        Refuse "setpoint_read" ([string]::Format($inv, "the current setpoint reads {0:F2} deg, beyond the {1:F1} deg limit; bring the arm back with Manual Control first", $current, $MaxDeg))
    }
    Say ([string]::Format($inv, "Current   setpoint reads {0:F2} deg", $current))
    if ($EncoderUrl) {
        $encWhy = ""; $a = [double]::NaN; $got = $false
        try {
            $encoder = NewEncoderSocket
            if ($encoder.SyncConnectTo($EncoderUrl, $CWDS_READ, 3000) -and $encoder.SyncRead(2000)) { $a = [double]$encoder.Data.Value; $got = $true }
            else { $encWhy = [string]$encoder.LastMessage }
        } catch { $encWhy = $_.Exception.Message }
        if (-not $got) { Refuse "encoder" ("the encoder watchdog is set up ({0}) but cannot read it ({1}). Fix it, or play with -NoEncoderWatchdog (delete encoder_url.txt)" -f $EncoderUrl, $encWhy) }
        if ([double]::IsNaN($a) -or [double]::IsInfinity($a) -or [math]::Abs($a - $current) -gt [Pendulastic.RigGuard]::FollowFloorDeg) {
            Refuse "encoder" ([string]::Format($inv, "the encoder variable reads {0:F2} deg while the setpoint reads {1:F2} deg: is it the arm's actual position?", $a, $current))
        }
        Say ([string]::Format($inv, "Watchdog  ON: {0} reads {1:F2} deg; stops past {2:F1} deg or {3:F2} deg from the recent setpoints", $EncoderUrl, $a, $MaxDeg + [Pendulastic.RigGuard]::WatchdogOverLimitDeg, $followBound))
    } else {
        Say "Watchdog  encoder watchdog OFF (no encoder variable: PICK_ENCODER.bat sets one if the rig publishes it)"
    }
}

Say ""
Say "Start the phone, then StretchExperiment's log. Then press Enter to play  (q = cancel)."
if (-not $NoPrompt) {
    $key = [Console]::ReadKey($true)
    if ($key.Key -ne "Enter") { Fail "cancelled before playing." 1 }
}

$sent = New-Object System.Text.StringBuilder
[void]$sent.AppendLine([Pendulastic.RigGuard]::SentHeader)   # t_file_s: where in the file (-1 off the file)
$sw = New-Object System.Diagnostics.Stopwatch
$script:failures = 0
$script:last = $current
$script:guardEvent = ""
$script:guardStop = ""
$script:wdStop = ""
$script:encMissed = 0
$script:recent = New-Object 'double[]' ([Pendulastic.RigGuard]::FollowWindowWrites + 1)
$script:recentN = 0
$script:recentHead = 0
if (-not $DryRun -and -not $EncoderUrl) { [void]$sent.AppendLine("0.0000,-1.0000,encoder,,encoder watchdog OFF (no encoder variable picked)") }

function LogRow([string]$phase, [double]$tf, [double]$v, [string]$note) {
    [void]$sent.AppendLine([string]::Format($inv, "{0:F4},{1:F4},{2},{3:F4},{4}", $sw.Elapsed.TotalSeconds, $tf, $phase, $v, [Pendulastic.RigGuard]::Note($note)))
}

function Send([double]$v, [string]$phase, [double]$tf = -1) {
    $evt = $null
    $g = [Pendulastic.RigGuard]::Backstop($v, $script:last, $MaxDeg, [ref]$evt)
    if ($evt) {
        LogRow "guard" $tf $g ("{0}: {1}" -f $phase, $evt)
        if (-not $script:guardEvent) { $script:guardEvent = [string]::Format($inv, "{0} at {1:F2} s: {2}", $phase, $tf, $evt) }
        if ($phase -eq "play" -and -not $script:guardStop) { $script:guardStop = $script:guardEvent }
    }
    $script:last = $g
    $script:recent[$script:recentHead] = $g
    $script:recentHead = ($script:recentHead + 1) % $script:recent.Length
    if ($script:recentN -lt $script:recent.Length) { $script:recentN++ }
    LogRow $phase $tf $g ""
    if ($DryRun) { return }
    if ($writer.SyncWrite([double]$g, 100)) { $script:failures = 0 }
    else {
        $script:failures++
        if ($script:failures -ge 20) {
            LogRow "error" -1 $g "20 writes in a row failed"
            Fail ("20 writes in a row failed ({0}). The rig holds the last value it received: {1:F2} deg. Use the E-stop if the arm is not where it should be." -f $writer.LastMessage, $g) 4
        }
    }
}

function Watch([double]$tf) {
    # one encoder-watchdog look at the arm (every 5th write while playing)
    if ($encoder -eq $null -or $script:wdStop) { return }
    $ok = $false; $a = [double]::NaN; $why = ""
    try {
        if ($encoder.SyncRead([Pendulastic.RigGuard]::WatchdogReadTimeoutMs)) { $a = [double]$encoder.Data.Value; $ok = $true }
        else { $why = [string]$encoder.LastMessage }
    } catch { $why = $_.Exception.Message }
    if (-not $ok) {
        $script:encMissed++
        if ($script:encMissed -ge [Pendulastic.RigGuard]::WatchdogMaxMissed) { $script:wdStop = ("the encoder could not be read {0} times in a row ({1})" -f $script:encMissed, $why) }
    } else {
        $script:encMissed = 0
        LogRow "encoder" $tf $a ""
        $bad = [Pendulastic.RigGuard]::WatchdogCheck($a, $script:recent, $script:recentN, $MaxDeg, $followBound)
        if ($bad) { $script:wdStop = [string]::Format($inv, "at {0:F2} s {1}", $tf, $bad) }
    }
    if ($script:wdStop) { LogRow "watchdog" $tf $script:last $script:wdStop }
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
$tag = ""; if ($DryRun) { $tag = "_dryrun" }
try {
    $sw.Start()
    if ([math]::Abs($current) -gt 0.5) {
        Say ([string]::Format($inv, "Ramping the setpoint from {0:F1} to 0 deg at {1:F0} deg/s first...", $current, $ReturnDps))
        Ramp $current 0.0 "ramp_in"
        WaitTick ($sw.ElapsedMilliseconds + 1000)
    }
    # warm up before the play clock starts: PowerShell compiles each function on its first call, which
    # took 0.5 s on a busy PC; inside the first play write that made the clock-indexed second write jump
    # 0.8 s into the file (and the backstop stop the run). Re-sending the value the rig already holds moves nothing.
    [void][Pendulastic.RigGuard]::At($t, $sp, 0.0)
    Send $script:last "ramp_in"
    if ($encoder -ne $null) { Watch -1 }
    [void](NextTick $sw.ElapsedMilliseconds)
    Say "Playing. q or Esc stops and returns to 0."
    $t0 = $sw.ElapsedMilliseconds; $lastShown = -1; $tick = 0
    while ($true) {
        $now = $sw.ElapsedMilliseconds
        $e = ($now - $t0) / 1000.0
        $stamps.Add($now)
        Send ([Pendulastic.RigGuard]::At($t, $sp, $e)) "play" $e
        if ($script:guardStop) { break }
        $tick++; if (($tick % [Pendulastic.RigGuard]::WatchdogEveryTicks) -eq 0) { Watch $e }
        if ($script:wdStop) { break }
        if ($e -ge $dur) { break }
        $sec = [int][math]::Floor($e)
        if ($sec -ne $lastShown) { $lastShown = $sec; Write-Host -NoNewline ("`r  {0,5:F1} / {1:F1} s   setpoint {2,7:F2} deg " -f $e, $dur, $script:last) }
        if (AbortKey) { $aborted = $true; break }
        WaitTick (NextTick $t0)
    }
    Write-Host ""
    if ($script:guardStop) {
        Write-Host ("STOPPED BY THE GUARD: {0}. Returning to 0." -f $script:guardStop) -ForegroundColor Red
        LogRow "guard" -1 $script:last ("playback stopped by the backstop: " + $script:guardStop)
        Ramp $script:last 0.0 "abort_return"
        $tag = $tag + "_guard"
    } elseif ($script:wdStop) {
        Write-Host ("STOPPED BY THE ENCODER WATCHDOG: {0}. Ramping the setpoint to 0; check the arm, E-stop if it is not following." -f $script:wdStop) -ForegroundColor Red
        Ramp $script:last 0.0 "abort_return"
        $tag = $tag + "_watchdog"
    } elseif ($aborted) {
        Say ("Stopped by key at {0:F1} s. Returning to 0." -f (($sw.ElapsedMilliseconds - $t0) / 1000.0))
        Ramp $script:last 0.0 "abort_return"
    } elseif (-not $NoReturn) {
        $hold = $sw.ElapsedMilliseconds + 2000
        $ht = 0
        while ($sw.ElapsedMilliseconds -lt $hold -and -not $script:wdStop) {
            Send $sp[$n - 1] "hold"
            $ht++; if (($ht % [Pendulastic.RigGuard]::WatchdogEveryTicks) -eq 0) { Watch -1 }
            WaitTick ($sw.ElapsedMilliseconds + $PeriodMs)
        }
        if ($script:wdStop) { Write-Host ("STOPPED BY THE ENCODER WATCHDOG: {0}. Returning to 0." -f $script:wdStop) -ForegroundColor Red; $tag = $tag + "_watchdog" }
        if ([math]::Abs($script:last) -gt 0.5) { Say ([string]::Format($inv, "Returning from {0:F1} to 0 deg at {1:F0} deg/s.", $script:last, $ReturnDps)) }
        Ramp $script:last 0.0 "return"
    }
} finally {
    [void][PlayerTimer]::timeEndPeriod(1)
    if ($writer -ne $null) { try { [void]$writer.SyncDisconnect(1000) } catch { } }
    if ($reader -ne $null) { try { [void]$reader.SyncDisconnect(1000) } catch { } }
    if ($encoder -ne $null) { try { [void]$encoder.SyncDisconnect(1000) } catch { } }
    if ($script:lock -ne $null) { try { $script:lock.ReleaseMutex() } catch { } }   # after the return to 0
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
    $log = Join-Path $logDir ((LogStem) + $tag + ".csv")
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
if ($script:guardStop) { Write-Host "The guard stopped this run: the file passed the checks, so something is wrong. Do not repeat it until the sent log is understood." -ForegroundColor Red; exit 5 }
if ($script:wdStop) { exit 6 }
if ($aborted) { exit 1 }
Say "Done. Write the StretchExperiment log's file name into the run sheet's encoder_file column."
exit 0
