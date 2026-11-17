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
    is). It reads it AGAIN after Enter and refuses if that fails or the setpoint moved more than 0.5 deg
    meanwhile (Manual Control or a Stretch profile is driving it); if the setpoint is not at 0 it ramps
    there at -ReturnDps (1-60 deg/s) before starting
  * every write passes the backstop: NaN/Inf never sent, clamped to the limit, at most 6 deg per write.
    If the backstop ever engages while playing, playback stops and ramps to 0 (exit 5): the file passed
    the checks, so something is wrong. The event is in the sent log (phase "guard")
  * q, Esc or Ctrl+C during playback stops it and ramps the setpoint back to 0 at -ReturnDps (Ctrl+C is
    caught while this window waits and plays; Ctrl+Break and closing the window are NOT: never use them)
  * at the end it holds the last angle 2 s, then ramps back to 0. Every ramp keeps writing until the
    value sent is the target (or its time + 5 s has passed)
  * any error after the first write (writes failing 20 times in a row, an exception) first tries a
    guarded ramp of the setpoint to 0 that tolerates failed writes; if that cannot get through it says
    RIG HOLDING LAST SETPOINT -- USE THE E-STOP (exit 4)
  * only one player writes at a time: a machine-wide lock (shared with the Pendulum Designer) is taken
    before the ramp-in and released after the return to 0; a second player refuses to start. The lock
    does not cover LabVIEW's own writers (Manual Control, a Stretch profile): do not use them while PLAY
    is waiting or playing
  * optional encoder watchdog: PICK_ENCODER.bat saves the arm's ACTUAL position variable to
    encoder_url.txt (or pass -EncoderUrl). Then every 50 ms of play it reads the arm and, if the arm is
    past the limit + 3 deg or further from every setpoint of the last 150 ms than the following-error
    bound (5 deg + 1.5 x what the identified plant predicts for this file), stops and ramps the setpoint
    to 0 (exit 6). Without it the console says "encoder watchdog OFF". It runs in this same process: a
    second opinion that moves the setpoint, not an independent hardware cutoff
What it cannot do: if writes stop getting through, the rig HOLDS the last value it received; if this
window is closed or killed mid-play the rig also holds the last setpoint. The amplifier fault (E-stop)
is the protection for both. The Angle Limit fields on the front panel do not bound this player.
The write period is RigGuard.PeriodMs (10 ms) and cannot be changed from the command line.

Written for Windows PowerShell 2.0 and later (the lab PC may be old): no PS3+ syntax.
Exit codes: 0 played, 1 refused/cancelled/stopped by key, 3 DataSocket not installed for this bitness,
4 cannot connect, or an error during the run (writes failing, an exception; after the guarded return to
0, or with RIG HOLDING LAST SETPOINT if that failed), 5 stopped by the backstop, 6 stopped by the
encoder watchdog.
Test-only switches (never needed at the bench): -TestFakeDataSocket "read=0|read=12.5|read=nan|read=fail,
write=fail" stands a fake variable in for NI DataSocket; -TestFakeEncoder "track|<deg>|fail|failafter"
a fake encoder; -TestBypassLoadChecks skips the load checks to exercise the backstop; -TestCtrlC
"signal@<s>|key@<s>" presses Ctrl+C (as a console signal, or as a keystroke) <s> seconds into play;
-TestSetpointAfterEnter <deg> makes the fake variable read <deg> after Enter. All are refused unless
-DryRun or -TestFakeDataSocket is given.
#>
param(
    [string]$File,
    [string]$Url = "",
    [double]$MaxDeg = 40,
    [double]$ReturnDps = 10,
    [switch]$DryRun,
    [switch]$Browse,
    [switch]$NoPrompt,
    [string]$EncoderUrl = "",
    [switch]$BrowseEncoder,
    [switch]$NoEncoderWatchdog,
    [string]$TestFakeDataSocket = "",
    [string]$TestFakeEncoder = "",
    [switch]$TestBypassLoadChecks,
    [string]$TestCtrlC = "",
    [string]$TestSetpointAfterEnter = ""
)

$ErrorActionPreference = "Stop"
if ($args.Count -gt 0) {
    # a script without [CmdletBinding()] puts unknown switches here instead of failing: refuse them, so a
    # removed setting (-PeriodMs, -NoReturn) or a typo can never be silently ignored
    Write-Host ("STOPPED: unknown option(s): {0}. Not played." -f ($args -join " ")) -ForegroundColor Red
    exit 1
}
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

$script:origCtrlC = $null

function Say($msg) { Write-Host $msg }
function RestoreConsole {
    if ($script:origCtrlC -ne $null) { try { [Console]::TreatControlCAsInput = $script:origCtrlC } catch { }; $script:origCtrlC = $null }
}
function Fail($msg, $code) {
    Write-Host ""; Write-Host "STOPPED: $msg" -ForegroundColor Red
    RestoreConsole
    if ($script:lock -ne $null) { try { $script:lock.ReleaseMutex() } catch { }; $script:lock = $null }
    exit $code
}

# -- the safety rules (shared with the Pendulum Designer) --
$guardSrc = Join-Path $here "RigGuard.cs"
if (-not ("Pendulastic.RigGuard" -as [type])) {
    if (-not (Test-Path $guardSrc)) { Fail "RigGuard.cs is missing beside play_setpoints.ps1. It holds the safety rules; nothing plays without it." 1 }
    try { Add-Type -Path $guardSrc } catch { Fail ("could not load the safety rules in RigGuard.cs ({0}). Not played." -f $_.Exception.Message) 1 }
}
$PeriodMs = [Pendulastic.RigGuard]::PeriodMs          # fixed: the speed rule (6 deg per write) assumes it

# Ctrl+C: as a keystroke it is read as input while TreatControlCAsInput is on (set from the prompt to the
# end); as a console signal (or if that setting fails) this handler cancels it and raises a flag. Either
# way the run stops through the normal return to 0 instead of the script dying mid-swing.
if (-not ("PlayerConsole" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class PlayerConsole {
    public static volatile bool CtrlC;
    static bool hooked;
    public static void Hook() {
        if (hooked) return;
        hooked = true;
        Console.CancelKeyPress += new ConsoleCancelEventHandler(OnCancel);
    }
    static void OnCancel(object sender, ConsoleCancelEventArgs e) {
        if (e.SpecialKey == ConsoleSpecialKey.ControlC) { e.Cancel = true; CtrlC = true; }
    }
    // test hooks: a Ctrl+C as a console signal, or as a keystroke in this console's input
    [DllImport("kernel32.dll")] public static extern bool GenerateConsoleCtrlEvent(uint ev, uint group);
    [StructLayout(LayoutKind.Explicit, Size = 20)]
    public struct KeyRec {
        [FieldOffset(0)] public ushort EventType; [FieldOffset(4)] public int KeyDown; [FieldOffset(8)] public ushort Repeat;
        [FieldOffset(10)] public ushort VK; [FieldOffset(12)] public ushort Scan; [FieldOffset(14)] public ushort Ch; [FieldOffset(16)] public uint Ctrl;
    }
    [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int h);
    [DllImport("kernel32.dll")] static extern bool WriteConsoleInputW(IntPtr h, KeyRec[] buf, uint n, out uint written);
    public static bool PressCtrlCKey() {
        KeyRec[] r = new KeyRec[2];
        r[0].EventType = 1; r[0].KeyDown = 1; r[0].Repeat = 1; r[0].VK = 0x43; r[0].Scan = 0x2E; r[0].Ch = 3; r[0].Ctrl = 0x0008;
        r[1] = r[0]; r[1].KeyDown = 0;
        uint w; return WriteConsoleInputW(GetStdHandle(-10), r, 2, out w);
    }
}
"@
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
$why = [Pendulastic.RigGuard]::CheckReturnDps($ReturnDps)
if ($why) { Refuse "return_speed" ("-ReturnDps: " + $why) }
if (($TestBypassLoadChecks -or $TestCtrlC -or $TestSetpointAfterEnter) -and -not ($DryRun -or $TestFakeDataSocket)) {
    Fail "the -Test... switches are for tests only: they need -DryRun or -TestFakeDataSocket and never play to the rig." 1
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
    $why = [Pendulastic.RigGuard]::EncoderUrlProblem($EncoderUrl, $Url)
    if ($why) { Refuse "encoder" $why }
}

Say ""
Say ("File      {0}" -f $File)
Say ([string]::Format($inv, "Duration  {0:F1} s   peak {1:F1} deg   ends at {2:F1} deg   ({3:F0} Hz file)", $dur, $rep.RawPeakDeg, $sp[$n - 1], $fs))
Say ([string]::Format($inv, "Guard     limit {0:F1} deg   predicted delivered peak {1:F1} deg   largest change {2:F2} deg within 10 ms", $MaxDeg, $rep.PredictedPeakDeg, $rep.MaxStepDeg))
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

[PlayerConsole]::Hook()
try { $script:origCtrlC = [Console]::TreatControlCAsInput; [Console]::TreatControlCAsInput = $true } catch { $script:origCtrlC = $null }

Say ""
Say "Start the phone, then StretchExperiment's log. Then press Enter to play  (q = cancel)."
Say "Do not use Manual Control or a Stretch profile while this waits or plays. Stop with q or Esc."
if (-not $NoPrompt) {
    $key = [Console]::ReadKey($true)
    if ($key.Key -ne "Enter" -or [PlayerConsole]::CtrlC) { Fail "cancelled before playing." 1 }
}

# the setpoint may have moved while the prompt waited (Manual Control, a Stretch profile): read it again
# and ramp from the fresh value; refuse if it cannot be read or moved
if (-not $DryRun) {
    if ($TestSetpointAfterEnter) {
        if ($TestSetpointAfterEnter -eq "fail") { $reader.ReadOk = $false } else { $reader.Data.Value = [double]::Parse($TestSetpointAfterEnter, $inv) }
    }
    $again = [double]::NaN; $readWhy = "no reading"
    try { if ($reader.SyncRead(2000)) { $again = [double]$reader.Data.Value } else { $readWhy = [string]$reader.LastMessage } } catch { $readWhy = $_.Exception.Message }
    if ([double]::IsNaN($again) -or [double]::IsInfinity($again)) {
        Refuse "setpoint_read" ("could not read the setpoint again after Enter ({0}); nothing was sent" -f $readWhy)
    }
    if ([math]::Abs($again - $current) -gt 0.5) {
        Refuse "setpoint_moved" ([string]::Format($inv, "the setpoint moved from {0:F2} to {1:F2} deg while PLAY waited for Enter: something else is driving it (Manual Control, a Stretch profile). Stop that, put the arm at 0 and play again; nothing was sent", $current, $again))
    }
    $current = $again
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
    if ($DryRun) { $script:lastWriteOk = $true; return }
    $ok = $false; $why = ""
    try { $ok = [bool]$writer.SyncWrite([double]$g, 100); if (-not $ok) { $why = [string]$writer.LastMessage } } catch { $why = $_.Exception.Message }
    $script:lastWriteOk = $ok
    if ($ok) { $script:failures = 0; return }
    $script:failures++
    if ($script:failures -ge 20 -and -not $script:safeMode) {
        # leaves the play/hold/ramp loop for the guarded return in the catch below
        throw ("20 writes in a row failed ({0})" -f $why)
    }
}

function SafeReturn([string]$why) {
    # best effort after an error: ramp the setpoint to 0 through the backstop, tolerating failed writes,
    # for at most the ramp's own time + RampExtraS. Says loudly when it could not get through.
    $script:safeMode = $true; $script:failures = 0
    $script:errorWhy = $why
    Write-Host ""; Write-Host ("ERROR: {0}. Trying to ramp the setpoint to 0..." -f $why) -ForegroundColor Red
    LogRow "error" -1 $script:last $why
    try { $reached = Ramp $script:last 0.0 "abort_return" $false } catch { $reached = $false; LogRow "error" -1 $script:last ("the error return failed: " + $_.Exception.Message) }
    if ($reached) {
        LogRow "error" -1 0 "error return reached 0"
        Write-Host "The setpoint was ramped back to 0." -ForegroundColor Yellow
    } else {
        $script:holding = $true
        LogRow "error" -1 $script:last "RIG HOLDING LAST SETPOINT -- USE THE E-STOP"
        Write-Host ([string]::Format($inv, "RIG HOLDING LAST SETPOINT ({0:F2} deg last sent) -- USE THE E-STOP if the arm is not at rest.", $script:last)) -ForegroundColor Red
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

function Ramp([double]$from, [double]$to, [string]$phase, [bool]$keysAbort = $true) {
    # writes until the value SENT (through the backstop) is $to and the rig took it, or until the ramp's
    # time + RampExtraS. Returns whether it got there. q/Esc/Ctrl+C during the ramp-in cancels the play
    # that would follow (the ramp to 0 itself always finishes).
    $secs = [math]::Abs($to - $from) / $ReturnDps; $t0 = $sw.ElapsedMilliseconds
    $deadline = $t0 + [long](1000.0 * ($secs + [Pendulastic.RigGuard]::RampExtraS))
    while ($true) {
        $e = ($sw.ElapsedMilliseconds - $t0) / 1000.0
        if ($e -ge $secs) { $v = $to } else { $v = $from + ($to - $from) * $e / $secs }
        Send $v $phase
        if ($e -ge $secs -and [math]::Abs($script:last - $to) -lt 1e-9 -and $script:lastWriteOk) { return $true }
        if ($sw.ElapsedMilliseconds -ge $deadline) { return $false }
        if ($keysAbort -and $phase -eq "ramp_in" -and (AbortKey)) { $script:aborted = $true }
        WaitTick (NextTick $t0)
    }
}

# a redirected console (scheduled task, remote shell) throws on KeyAvailable; test once, not every tick
$script:keys = $true
try { [void][Console]::KeyAvailable } catch { $script:keys = $false }

function AbortKey {
    if ([PlayerConsole]::CtrlC) { $script:ctrlC = $true; return $true }
    if (-not $script:keys) { return $false }
    try {
        while ([Console]::KeyAvailable) {
            $k = [Console]::ReadKey($true)
            if ([int]$k.KeyChar -eq 3) { $script:ctrlC = $true; return $true }
            if ($k.Key -eq "Escape" -or $k.KeyChar -eq "q" -or $k.KeyChar -eq "Q") { return $true }
        }
    } catch { }
    return $false
}

function TestCtrlCDue([double]$e) {
    # test hook (-TestCtrlC signal@<s> | key@<s>): one Ctrl+C, <s> seconds into play
    if (-not $TestCtrlC -or $script:testCtrlCDone) { return }
    $p = $TestCtrlC.Split("@")
    if ($e -lt [double]::Parse($p[1], $inv)) { return }
    $script:testCtrlCDone = $true
    if ($p[0] -eq "signal") { [void][PlayerConsole]::GenerateConsoleCtrlEvent(0, 0) } else { [void][PlayerConsole]::PressCtrlCKey() }
}

[void][PlayerTimer]::timeBeginPeriod(1)
try { (Get-Process -Id $PID).PriorityClass = "High" } catch { }   # fewer late ticks while other programs run
$stamps = New-Object 'System.Collections.Generic.List[System.Int64]'
$aborted = $false
$script:ctrlC = $false
$script:safeMode = $false
$script:holding = $false
$script:errorWhy = ""
$script:lastWriteOk = $true
$script:testCtrlCDone = $false
$returnShort = $false
$tag = ""; if ($DryRun) { $tag = "_dryrun" }
try {
  try {
    $sw.Start()
    # warm up before any clock-indexed ramp or play: PowerShell compiles each function on its first call,
    # which took 0.5 s on a busy PC; inside the first play write that made the second write jump 0.8 s into
    # the file. Re-sending the value the rig already holds moves nothing.
    [void][Pendulastic.RigGuard]::At($t, $sp, 0.0)
    Send $script:last "ramp_in"
    if ($encoder -ne $null) { Watch -1 }
    [void](NextTick $sw.ElapsedMilliseconds)
    if (AbortKey) { $aborted = $true }
    if ([math]::Abs($current) -gt 0.5) {
        Say ([string]::Format($inv, "Ramping the setpoint from {0:F1} to 0 deg at {1:F0} deg/s first...", $current, $ReturnDps))
        if (-not (Ramp $current 0.0 "ramp_in")) { throw "the ramp-in did not reach 0" }
        if (-not $aborted) { WaitTick ($sw.ElapsedMilliseconds + 1000) }
    }
    if ($aborted) { throw "CANCELLED_IN_RAMP" }
    Say "Playing. q or Esc stops and returns to 0 (never Ctrl+Break or closing this window)."
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
        TestCtrlCDue $e
        if (AbortKey) { $aborted = $true; break }
        WaitTick (NextTick $t0)
    }
    Write-Host ""
    if ($script:guardStop) {
        Write-Host ("STOPPED BY THE GUARD: {0}. Returning to 0." -f $script:guardStop) -ForegroundColor Red
        LogRow "guard" -1 $script:last ("playback stopped by the backstop: " + $script:guardStop)
        $returnShort = -not (Ramp $script:last 0.0 "abort_return")
        $tag = $tag + "_guard"
    } elseif ($script:wdStop) {
        Write-Host ("STOPPED BY THE ENCODER WATCHDOG: {0}. Ramping the setpoint to 0; check the arm, E-stop if it is not following." -f $script:wdStop) -ForegroundColor Red
        $returnShort = -not (Ramp $script:last 0.0 "abort_return")
        $tag = $tag + "_watchdog"
    } elseif ($aborted) {
        $how = "q/Esc"; if ($script:ctrlC) { $how = "Ctrl+C" }
        LogRow "stop" -1 $script:last ("stopped by " + $how)
        Say ("Stopped by {0} at {1:F1} s. Returning to 0." -f $how, (($sw.ElapsedMilliseconds - $t0) / 1000.0))
        $returnShort = -not (Ramp $script:last 0.0 "abort_return")
    } else {
        $hold = $sw.ElapsedMilliseconds + 2000
        $ht = 0
        while ($sw.ElapsedMilliseconds -lt $hold -and -not $script:wdStop) {
            Send $sp[$n - 1] "hold"
            $ht++; if (($ht % [Pendulastic.RigGuard]::WatchdogEveryTicks) -eq 0) { Watch -1 }
            if (AbortKey) { $aborted = $true; break }
            WaitTick ($sw.ElapsedMilliseconds + $PeriodMs)
        }
        if ($script:wdStop) { Write-Host ("STOPPED BY THE ENCODER WATCHDOG: {0}. Returning to 0." -f $script:wdStop) -ForegroundColor Red; $tag = $tag + "_watchdog" }
        if ([math]::Abs($script:last) -gt 0.5) { Say ([string]::Format($inv, "Returning from {0:F1} to 0 deg at {1:F0} deg/s.", $script:last, $ReturnDps)) }
        $returnShort = -not (Ramp $script:last 0.0 "return")
    }
    if ($returnShort) { throw "the return to 0 did not complete in time" }
  } catch {
    $msg = [string]$_.Exception.Message
    if ($msg -eq "CANCELLED_IN_RAMP") {
        LogRow "stop" -1 $script:last "cancelled during the ramp-in; the setpoint is at 0"
        Say "Cancelled during the ramp-in; the setpoint is at 0. Not played."
    } else {
        SafeReturn $msg
        $tag = $tag + "_error"
    }
  }
} finally {
    RestoreConsole
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
if ($script:errorWhy) {
    if ($script:holding) { Write-Host "RIG HOLDING LAST SETPOINT -- USE THE E-STOP. Do not play again until the variable and the network are checked." -ForegroundColor Red }
    else { Write-Host ("Stopped by an error ({0}); the setpoint was returned to 0. Bring the sent log back." -f $script:errorWhy) -ForegroundColor Red }
    exit 4
}
if ($script:guardStop) { Write-Host "The guard stopped this run: the file passed the checks, so something is wrong. Do not repeat it until the sent log is understood." -ForegroundColor Red; exit 5 }
if ($script:wdStop) { exit 6 }
if ($aborted) { exit 1 }
Say "Done. Write the StretchExperiment log's file name into the run sheet's encoder_file column."
exit 0
