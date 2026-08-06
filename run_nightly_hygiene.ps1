# Nightly hygiene audit runner - invoked by a Windows Scheduled Task.
#
# Runs `claude -p` headlessly against docs/hygiene/nightly-runbook.md, from
# this repo's root, so the agent has full access to local git state (all 14
# worktrees, untracked cruft) and the local .venv - none of which a cloud
# /schedule routine could see (it only gets a fresh GitHub clone).
#
# --permission-mode bypassPermissions is required for a fully unattended
# overnight run (no human present to approve tool calls). The runbook itself
# is the safety envelope: it constrains the agent to no Edit/Write outside
# docs/reports/**, and no git command besides `add`/`commit` on the one new
# report file. --max-budget-usd is a hard cost ceiling in case something
# runs away.

$ErrorActionPreference = "Stop"
Set-Location "C:\Users\cladi\Pendulastic"

$claudeExe = "C:\Users\cladi\.local\bin\claude.exe"
$runbookPath = "docs\hygiene\nightly-runbook.md"
$logPath = "docs\hygiene\last-run.log"

$startTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"=== Nightly hygiene run started: $startTime ===" | Out-File -FilePath $logPath -Encoding utf8

Get-Content -Raw -Path $runbookPath | & $claudeExe -p `
    --permission-mode bypassPermissions `
    --max-budget-usd 3 `
    --output-format text `
    2>&1 | Out-File -FilePath $logPath -Encoding utf8 -Append

$exitCode = $LASTEXITCODE
$endTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"=== Nightly hygiene run finished: $endTime (exit $exitCode) ===" | Out-File -FilePath $logPath -Encoding utf8 -Append
