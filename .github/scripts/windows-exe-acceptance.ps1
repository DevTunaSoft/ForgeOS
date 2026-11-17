<#
Hosted-runner acceptance of a packaged ForgeOS.exe. NOT physical target-PC acceptance.

Runs the EXE's frozen acceptance modes, then drives the same EXE through
add -> plan -> run with Ollama and a coder model, against a disposable Python
project. Every step is written to <ResultsDir>\<Label>.json (summary) and
<ResultsDir>\<Label>.log (transcript), including when a step fails.
#>
param(
    [Parameter(Mandatory = $true)][string] $Exe,
    [Parameter(Mandatory = $true)][string] $UnderTest,
    [Parameter(Mandatory = $true)][string] $ResultsDir,
    [Parameter(Mandatory = $true)][string] $Label,
    [Parameter(Mandatory = $true)][string] $TestRef,
    [string] $Model = 'qwen2.5-coder:3b'
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null
$ResultsDir = (Resolve-Path -Path $ResultsDir).Path
$UnderTest = (Resolve-Path -Path $UnderTest).Path
$Exe = (Resolve-Path -Path $Exe).Path
$summaryPath = Join-Path $ResultsDir "$Label.json"
$logPath = Join-Path $ResultsDir "$Label.log"
Set-Content -Path $logPath -Value '' -Encoding utf8

$work = Join-Path $env:RUNNER_TEMP "acceptance-$Label"
Remove-Item -Recurse -Force -Path $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $work | Out-Null

$steps = [System.Collections.Generic.List[object]]::new()
$facts = [ordered]@{}
$startedUtc = [DateTime]::UtcNow.ToString('o')

function Write-Log([string] $Message) {
    $line = '[{0}] {1}' -f [DateTime]::UtcNow.ToString('HH:mm:ss'), $Message
    Add-Content -Path $logPath -Value $line -Encoding utf8
    Write-Host $line
}

function Save-Summary([string] $Verdict) {
    $document = [ordered]@{
        label          = $Label
        verdict        = $Verdict
        model          = $Model
        test_ref       = $TestRef
        under_test_sha = $facts['under_test_sha']
        designated_sha = $env:GITHUB_SHA
        run_id         = $env:GITHUB_RUN_ID
        run_attempt    = $env:GITHUB_RUN_ATTEMPT
        started_utc    = $startedUtc
        updated_utc    = [DateTime]::UtcNow.ToString('o')
        facts          = $facts
        steps          = $steps
    }
    $document | ConvertTo-Json -Depth 12 | Set-Content -Path $summaryPath -Encoding utf8
}

function Add-Step([string] $Name, [bool] $Ok, [string] $Detail) {
    $steps.Add([ordered]@{ name = $Name; ok = $Ok; detail = $Detail }) | Out-Null
    $state = if ($Ok) { 'ok' } else { 'FAILED' }
    Write-Log "STEP ${Name}: $state. $Detail"
    Save-Summary 'in_progress'
}

function Stop-Run([string] $Reason) {
    Add-Step 'abort' $false $Reason
    Save-Summary 'FAIL'
    Write-Log "RESULT FAIL for ${Label}: $Reason"
    exit 1
}

function Invoke-Logged {
    param(
        [Parameter(Mandatory = $true)][string] $Exe,
        [string[]] $Arguments = @(),
        [switch] $Quiet
    )
    Write-Log ('$ {0} {1}' -f $Exe, ($Arguments -join ' '))
    $global:LASTEXITCODE = -1
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $output = @(& $Exe @Arguments 2>&1 | ForEach-Object { "$_" })
    $watch.Stop()
    $code = $global:LASTEXITCODE
    $text = $output -join "`n"
    if (-not $Quiet) { Add-Content -Path $logPath -Value $text -Encoding utf8 }
    [pscustomobject]@{
        Code    = $code
        Seconds = [math]::Round($watch.Elapsed.TotalSeconds, 1)
        Text    = $text
        Tail    = (@($output | Select-Object -Last 12) -join "`n")
    }
}

function Invoke-Watched([string] $Name, [string] $ArgString, [int] $TimeoutMs) {
    Write-Log ('$ {0} {1}' -f $Exe, $ArgString)
    $process = Start-Process -FilePath $Exe -ArgumentList $ArgString -PassThru
    if (-not $process.WaitForExit($TimeoutMs)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Add-Step $Name $false ("exceeded {0} ms watchdog" -f $TimeoutMs)
        return $false
    }
    $ok = ($process.ExitCode -eq 0)
    Add-Step $Name $ok ("exit {0}" -f $process.ExitCode)
    return $ok
}

function Read-Task([string] $Id) {
    $listing = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'list') -Quiet
    $facts['forgeos_list'] = $listing.Text
    try {
        $tasks = $listing.Text | ConvertFrom-Json -ErrorAction Stop
        return ($tasks | Where-Object { [string]$_.id -eq $Id } | Select-Object -First 1)
    } catch {
        Write-Log "could not parse list output: $($_.Exception.Message)"
        return $null
    }
}

# ---- environment and EXE identity ---------------------------------------
$facts['runner_os'] = (Get-CimInstance -ClassName Win32_OperatingSystem).Caption
$facts['python'] = ((& python --version) 2>&1 | Out-String).Trim()
$facts['under_test_sha'] = ((& git -C $UnderTest rev-parse HEAD) 2>&1 | Out-String).Trim()
$facts['exe_bytes'] = (Get-Item -Path $Exe).Length
$facts['exe_sha256'] = (Get-FileHash -Path $Exe -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Log ('runner: {0}; {1}; under test {2} at {3}' -f $facts['runner_os'], $facts['python'], $TestRef, $facts['under_test_sha'])
Write-Log ('EXE: {0} bytes, sha256 {1}' -f $facts['exe_bytes'], $facts['exe_sha256'])
Save-Summary 'in_progress'
if ($facts['exe_bytes'] -lt 1000000) { Stop-Run ("EXE is missing or too small: {0} bytes" -f $facts['exe_bytes']) }

# ---- frozen EXE acceptance modes (same modes as the product workflow) ----
$smoke = Invoke-Logged -Exe $Exe -Arguments @('--smoke')
$smokeOk = ($smoke.Code -eq 0) -and ($smoke.Text -match 'packaged imports OK')
Add-Step 'exe --smoke' $smokeOk ("exit {0}" -f $smoke.Code)

$snapshotPath = Join-Path $work 'acceptance-snapshot.json'
$snapshot = Invoke-Logged -Exe $Exe -Arguments @('--acceptance-snapshot-report', $snapshotPath)
$snapshotDocOk = $false
if (Test-Path $snapshotPath) {
    try {
        $snapDoc = Get-Content -Raw -Path $snapshotPath | ConvertFrom-Json -ErrorAction Stop
        $snapshotDocOk = ($snapDoc.artifact.build_source_commit -eq $facts['under_test_sha']) -and ($snapDoc.artifact.executable_sha256 -eq $facts['exe_sha256']) -and ($snapDoc.windows_host_evidence -eq $true) -and ($snapDoc.physical_windows_target_asserted -eq $false)
    } catch {
        Write-Log "snapshot report unreadable: $($_.Exception.Message)"
    }
}
$identityOk = ($snapshot.Code -eq 0) -and $snapshotDocOk
Add-Step 'exe acceptance identity' $identityOk ("exit {0}; source commit and exe sha256 match: {1}" -f $snapshot.Code, $snapshotDocOk)

$env:FORGEOS_SECURE_SMOKE_REPORT = Join-Path $work 'secure-smoke.txt'
$secureExitOk = Invoke-Watched -Name 'exe --secure-sandbox-smoke' -ArgString '--secure-sandbox-smoke' -TimeoutMs 120000
if (Test-Path $env:FORGEOS_SECURE_SMOKE_REPORT) { $facts['secure_smoke_report'] = Get-Content -Raw -Path $env:FORGEOS_SECURE_SMOKE_REPORT }
Remove-Item Env:FORGEOS_SECURE_SMOKE_REPORT -ErrorAction SilentlyContinue

$guiReport = Join-Path $work 'gui-report.json'
$guiExitOk = Invoke-Watched -Name 'exe --gui-acceptance-report' -ArgString ('--gui-acceptance-report "' + $guiReport + '"') -TimeoutMs 90000
$guiReportOk = $false
if (Test-Path $guiReport) {
    try {
        $gui = Get-Content -Raw -Path $guiReport | ConvertFrom-Json -ErrorAction Stop
        $facts['gui_report'] = $gui
        $guiReportOk = [bool]($gui.gui_started -and $gui.queue_initialized -and $gui.closed_cleanly -and ($gui.tasks_verified -eq 520))
    } catch {
        Write-Log "gui report unreadable: $($_.Exception.Message)"
    }
}
Add-Step 'exe gui report' $guiReportOk ("gui started, queue, clean close, 520 tasks: {0}" -f $guiReportOk)
$frozenOk = $smokeOk -and $identityOk -and $secureExitOk -and $guiExitOk -and $guiReportOk

# ---- Ollama ---------------------------------------------------------------
$ollamaDir = Join-Path $env:RUNNER_TEMP 'ollama'
New-Item -ItemType Directory -Force -Path $ollamaDir | Out-Null
$ollamaExe = Join-Path $ollamaDir 'ollama.exe'
$zip = Join-Path $ollamaDir 'ollama-windows-amd64.zip'
try {
    Invoke-WebRequest -Uri 'https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip' -OutFile $zip -UseBasicParsing -ErrorAction Stop
    & tar.exe -xf $zip -C $ollamaDir
    Remove-Item -Force -Path $zip -ErrorAction SilentlyContinue
} catch {
    Write-Log "ollama download error: $($_.Exception.Message)"
}
if (-not (Test-Path $ollamaExe)) { Stop-Run 'ollama.exe missing after download and extract' }
Add-Step 'ollama installed' $true $ollamaExe

Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $ollamaDir 'serve.out.log') `
    -RedirectStandardError (Join-Path $ollamaDir 'serve.err.log') | Out-Null
$serverUp = $false
for ($attempt = 0; $attempt -lt 60 -and -not $serverUp; $attempt++) {
    try {
        $version = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 5 -ErrorAction Stop
        $facts['ollama'] = [string]$version.version
        $serverUp = $true
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $serverUp) { Stop-Run 'ollama did not answer on 127.0.0.1:11434 within 120 seconds' }
Add-Step 'ollama server' $true "version $($facts['ollama'])"

$pull = Invoke-Logged -Exe $ollamaExe -Arguments @('pull', $Model) -Quiet
Add-Step 'model pull' ($pull.Code -eq 0) ("exit {0} in {1}s" -f $pull.Code, $pull.Seconds)
if ($pull.Code -ne 0) { Stop-Run "ollama pull $Model failed: $($pull.Tail)" }
$listed = Invoke-Logged -Exe $ollamaExe -Arguments @('list')
$facts['ollama_list'] = $listed.Text

# ---- disposable project ---------------------------------------------------
$demo = Join-Path $work 'demo-project'
New-Item -ItemType Directory -Force -Path $demo | Out-Null
$utf8 = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText((Join-Path $demo 'calc.py'), "def add(a, b):`n    return a - b`n", $utf8)
[System.IO.File]::WriteAllText((Join-Path $demo 'test_calc.py'), "import unittest`n`nfrom calc import add`n`n`nclass AddTests(unittest.TestCase):`n    def test_add(self):`n        self.assertEqual(add(2, 3), 5)`n`n`nif __name__ == '__main__':`n    unittest.main()`n", $utf8)
$identity = @('-c', 'user.name=acceptance', '-c', 'user.email=acceptance@example.invalid')
& git -C $demo init -q -b main
& git -C $demo add -A
& git -C $demo $identity commit -q -m 'Seed disposable project with a failing add()'
Add-Step 'demo project' ($LASTEXITCODE -eq 0) "git repo with failing add() at $demo"

# ---- the EXE drives add -> plan -> run ------------------------------------
$models = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'models')
$listsModel = [bool]($models.Text -match [regex]::Escape($Model))
Add-Step 'exe --cli models' ($models.Code -eq 0) ("exit {0}; lists {1}: {2}" -f $models.Code, $Model, $listsModel)

$description = 'In calc.py, change add() so it returns a + b. Do not edit test_calc.py.'
$add = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'add', $demo, $description)
$idMatch = [regex]::Match($add.Text, 'Created task #(\d+)')
if (-not $idMatch.Success) { Stop-Run "add printed no task id: $($add.Tail)" }
$taskId = $idMatch.Groups[1].Value
Add-Step 'exe --cli add' ($add.Code -eq 0) "created task #$taskId"

# A separate process must see the task that the first process just added.
$afterAdd = Read-Task $taskId
$persistOk = [bool]($afterAdd -and ([string]$afterAdd.status -eq 'queued'))
Add-Step 'task visible to a fresh process after add' $persistOk ("status {0}" -f $afterAdd.status)

$plan = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'plan', $taskId, '--model', $Model)
$facts['plan_seconds'] = $plan.Seconds
Add-Step 'exe --cli plan' ($plan.Code -eq 0) ("exit {0} in {1}s. tail: {2}" -f $plan.Code, $plan.Seconds, $plan.Tail)
if ($plan.Code -ne 0) {
    $failed = Read-Task $taskId
    Stop-Run ("plan exited {0}; task status {1}; last_error {2}" -f $plan.Code, $failed.status, $failed.last_error)
}

$wsParent = Join-Path $work 'ForgeOS-work'
New-Item -ItemType Directory -Force -Path $wsParent | Out-Null
$run = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'run', $taskId, '--workspace-parent', $wsParent, '--preset', 'python-unittest', '--model', $Model, '--allow-host-tests')
Add-Step 'exe --cli run (edit, test, repair)' ($run.Code -eq 0) ("exit {0} in {1}s. tail: {2}" -f $run.Code, $run.Seconds, $run.Tail)

$task = Read-Task $taskId
$taskStatus = if ($task) { [string]$task.status } else { 'unknown' }
$taskResult = $null
if ($task -and $task.result) {
    try { $taskResult = $task.result | ConvertFrom-Json -ErrorAction Stop } catch { $taskResult = $null }
}
$edited = if ($taskResult) { @($taskResult.edited) } else { @() }
$lastError = if ($task) { [string]$task.last_error } else { '' }
Add-Step 'task status' ($taskStatus -eq 'passed') ("status {0}; edited: {1}; last_error: {2}" -f $taskStatus, ($edited -join ', '), $lastError)

$testPassed = [bool]($taskResult -and $taskResult.test -and $taskResult.test.passed)
$testOutput = if ($taskResult -and $taskResult.test) { [string]$taskResult.test.output } else { '' }
Add-Step 'test evidence' $testPassed ("test passed: $testPassed; last lines: " + (($testOutput -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 3) -join ' | '))

# ---- independent evidence from the workspace ------------------------------
$ws = Join-Path $wsParent "task-$taskId"
$calcPath = Join-Path $ws 'calc.py'
$testPath = Join-Path $ws 'test_calc.py'
$calcText = if (Test-Path $calcPath) { Get-Content -Raw -Path $calcPath } else { '' }
$addFixed = [bool]($calcText -match 'return\s+a\s*\+\s*b')
# Judge "unchanged" through git, not raw bytes: on Windows the checked-out copy
# can differ from the seed only by CRLF line endings, which git normalizes.
$testUnchanged = [bool]((Test-Path $testPath) -and -not ((Invoke-Logged -Exe 'git' -Arguments @('-C', $ws, 'status', '--short') -Quiet).Text -match 'test_calc\.py'))
$worktreeStatus = Invoke-Logged -Exe 'git' -Arguments @('-C', $ws, 'status', '--short')
$worktreeDiff = Invoke-Logged -Exe 'git' -Arguments @('-C', $ws, 'diff', 'HEAD')
$facts['worktree_status'] = $worktreeStatus.Text
$facts['worktree_diff'] = $worktreeDiff.Text
Add-Step 'worktree evidence' ($addFixed -and $testUnchanged) ("calc.py returns a + b: {0}; test_calc.py unchanged per git: {1}" -f $addFixed, $testUnchanged)

# ---- verdict --------------------------------------------------------------
# ---- an unsupported frozen preset must fail closed (no host fallback) -----
$refusalOk = $false
$refusalDetail = 'no second task was created'
$refusalAdd = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'add', $demo, 'Second task used only for the unsupported-preset refusal check.')
$refusalMatch = [regex]::Match($refusalAdd.Text, 'Created task #(\d+)')
if ($refusalMatch.Success) {
    $refusalId = $refusalMatch.Groups[1].Value
    $refusal = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'test', $refusalId, '--preset', 'npm-test', '--allow-host-tests')
    $refusalOk = ($refusal.Code -ne 0) -and ($refusal.Text -match 'does not yet support preset')
    $refusalDetail = "npm-test exit {0}; refused with the expected message: {1}; tail: {2}" -f $refusal.Code, $refusalOk, $refusal.Tail
}
Add-Step 'exe refuses unsupported frozen preset' $refusalOk $refusalDetail

# ---- kill during planning, then recover (acceptance item 5: process termination) ----
$killOk = $false
$killDetail = 'no third task was created'
$notes = Join-Path $demo 'notes.txt'
Set-Content -Path $notes -Value 'keep me: unrelated work' -Encoding utf8
& git -C $demo add -A
& git -C $demo $identity commit -q -m 'Unrelated work that recovery must not touch'
$notesSha = (Get-FileHash -Path $notes -Algorithm SHA256).Hash
$calcSeedSha = (Get-FileHash -Path (Join-Path $demo 'calc.py') -Algorithm SHA256).Hash
$killAdd = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'add', $demo, 'Third task used only for the kill-and-recover check.')
$killMatch = [regex]::Match($killAdd.Text, 'Created task #(\d+)')
if ($killMatch.Success) {
    $killId = $killMatch.Groups[1].Value
    $planProc = Start-Process -FilePath $Exe -ArgumentList ('--cli plan ' + $killId + ' --model ' + $Model) -WindowStyle Hidden -PassThru
    $sawPlanning = $false
    for ($i = 0; $i -lt 120 -and -not $sawPlanning; $i++) {
        $probe = Read-Task $killId
        if ($probe -and ([string]$probe.status -eq 'planning')) { $sawPlanning = $true } else { Start-Sleep -Milliseconds 500 }
    }
    if ($sawPlanning -and -not $planProc.HasExited) {
        # PyInstaller one-file builds run the app in a child process, so kill the whole tree.
        $tree = Invoke-Logged -Exe 'taskkill.exe' -Arguments @('/PID', [string]$planProc.Id, '/T', '/F')
        Start-Sleep -Seconds 2
        $leftover = @(Get-Process -Name 'ForgeOS' -ErrorAction SilentlyContinue).Count
        $treeOk = ($tree.Code -eq 0) -and ($leftover -eq 0)
        Add-Step 'process tree killed while planning' $treeOk ("taskkill exit {0}; ForgeOS processes left: {1}" -f $tree.Code, $leftover)
        # 1. Right after the kill, the abandoned lease must keep recover away from the task.
        $early = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'recover')
        $leaseHeld = ([string](Read-Task $killId).status -eq 'planning')
        # 2. A second planner must be refused while the task is in flight.
        $second = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'plan', $killId, '--model', $Model)
        $secondRefused = ($second.Code -ne 0) -and ($second.Text -match 'not eligible for planning|active execution lease')
        # 3. Wait past the 600-second lease horizon, measured from the kill.
        Write-Log 'waiting 620 seconds for the abandoned lease to go stale'
        Start-Sleep -Seconds 620
        $late = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'recover')
        $recoveredQueued = ([string](Read-Task $killId).status -eq 'queued')
        # 4. The recovered task must be usable: plan, run, pass.
        $replan = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'plan', $killId, '--model', $Model)
        $killRun = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'run', $killId, '--workspace-parent', $wsParent, '--preset', 'python-unittest', '--model', $Model, '--allow-host-tests')
        $completed = ([string](Read-Task $killId).status -eq 'passed')
        # 5. Unrelated work in the project must be untouched.
        $projectUntouched = ((Get-FileHash -Path $notes -Algorithm SHA256).Hash -eq $notesSha) -and ((Get-FileHash -Path (Join-Path $demo 'calc.py') -Algorithm SHA256).Hash -eq $calcSeedSha)
        $killOk = $treeOk -and $sawPlanning -and $leaseHeld -and $secondRefused -and ($late.Code -eq 0) -and $recoveredQueued -and ($replan.Code -eq 0) -and ($killRun.Code -eq 0) -and $completed -and $projectUntouched
        $killDetail = "planning seen: {0}; live lease held: {1}; second planner refused: {2}; recover exit after lease horizon: {3}; re-queued: {4}; replan exit: {5}; run exit: {6}; passed: {7}; project untouched: {8}" -f $sawPlanning, $leaseHeld, $secondRefused, $late.Code, $recoveredQueued, $replan.Code, $killRun.Code, $completed, $projectUntouched
    } else {
        $killDetail = "planning was not observed before the process ended (planning seen: {0})" -f $sawPlanning
    }
}
Add-Step 'kill during planning, then recover' $killOk $killDetail

# ---- interrupted write: process death between two target replacements ----
$iwRoot = Join-Path $work 'iw-project'
New-Item -ItemType Directory -Force -Path $iwRoot | Out-Null
[System.IO.File]::WriteAllText((Join-Path $iwRoot 'a.txt'), "AAA original`n", $utf8)
[System.IO.File]::WriteAllText((Join-Path $iwRoot 'b.txt'), "BBB original`n", $utf8)
$injectPath = Join-Path $work 'inject_interrupt.py'
Set-Content -Path $injectPath -Encoding utf8 -Value @'
import sys
from pathlib import Path
from unittest import mock
import forgeos.transactions as t

root = Path(sys.argv[1])
real_publish = t._publish_staged
count = {"publishes": 0}

def die_on_second_replacement(*args, **kwargs):
    count["publishes"] += 1
    if count["publishes"] == 2:
        # SystemExit skips the transaction's "except Exception" cleanup, so the
        # journal stays pending exactly as it would after a process death.
        raise SystemExit("injected process death")
    return real_publish(*args, **kwargs)

crashed = False
with mock.patch.object(t, "_publish_staged", die_on_second_replacement):
    try:
        t.apply_transaction(root, [(root / "a.txt", "AAA edited\n"), (root / "b.txt", "BBB edited\n")])
    except SystemExit:
        crashed = True
if not crashed:
    print("UNEXPECTED: the transaction completed without the injected death")
    sys.exit(2)
print("injected death after 1 of 2 replacements")
'@
$env:PYTHONPATH = Join-Path $UnderTest 'src'
$inject = Invoke-Logged -Exe 'python' -Arguments @($injectPath, $iwRoot)
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$partialOk = ([System.IO.File]::ReadAllText((Join-Path $iwRoot 'a.txt')) -eq "AAA edited`n") -and ([System.IO.File]::ReadAllText((Join-Path $iwRoot 'b.txt')) -eq "BBB original`n")
$rec = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'recover-edits', $iwRoot)
$recoveredOk = ($rec.Code -eq 0) -and ($rec.Text -match 'Recovered edit journal')
$restoredOk = ([System.IO.File]::ReadAllText((Join-Path $iwRoot 'a.txt')) -eq "AAA original`n") -and ([System.IO.File]::ReadAllText((Join-Path $iwRoot 'b.txt')) -eq "BBB original`n")
$again = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'recover-edits', $iwRoot)
$idempotentOk = ($again.Code -eq 0) -and ($again.Text -match 'No pending edit journal')
$iwOk = ($inject.Code -eq 0) -and $partialOk -and $recoveredOk -and $restoredOk -and $idempotentOk
Add-Step 'interrupted write rolled back by recover-edits' $iwOk ("injected death: exit {0}; partial state seen: {1}; recover-edits recovered: {2}; both files restored: {3}; second recover no-op: {4}" -f $inject.Code, $partialOk, $recoveredOk, $restoredOk, $idempotentOk)

# ---- damaged checkpoints: resume-safe must refuse them, never resume them ----
$dmgAdd1 = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'add', $demo, 'Damaged checkpoint A for the resume guard check.')
$dmgAdd2 = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'add', $demo, 'Damaged checkpoint B for the resume guard check.')
$dmgId1 = [regex]::Match($dmgAdd1.Text, 'Created task #(\d+)').Groups[1].Value
$dmgId2 = [regex]::Match($dmgAdd2.Text, 'Created task #(\d+)').Groups[1].Value
$damagePath = Join-Path $work 'damage_checkpoints.py'
Set-Content -Path $damagePath -Encoding utf8 -Value @'
import json
import sys
from pathlib import Path
from forgeos.cli import default_db
from forgeos.core import Store

first, second = int(sys.argv[1]), int(sys.argv[2])
payloads = {
    first: '{"phase": "test_pending", "workspace": ',
    second: json.dumps({"phase": "test_pending",
                        "workspace": str(Path.home() / "elsewhere" / "task-999")}),
}
store = Store(default_db())
try:
    for task_id, payload in payloads.items():
        store.db.execute("UPDATE tasks SET status='blocked', result=? WHERE id=?", (payload, task_id))
    store.db.commit()
finally:
    store.close()
print("damaged", first, second)
'@
$env:PYTHONPATH = Join-Path $UnderTest 'src'
$damage = Invoke-Logged -Exe 'python' -Arguments @($damagePath, $dmgId1, $dmgId2)
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$resume = Invoke-Logged -Exe $Exe -Arguments @('--cli', 'resume-safe', '--workspace-parent', $wsParent, '--preset', 'python-unittest', '--allow-host-tests')
$resumeReport = $null
try { $resumeReport = $resume.Text | ConvertFrom-Json -ErrorAction Stop } catch { $resumeReport = $null }
$skippedIds = if ($resumeReport) { @($resumeReport.skipped | ForEach-Object { [string]$_.id }) } else { @() }
$resumedCount = if ($resumeReport) { @($resumeReport.resumed).Count } else { -1 }
$bothRefused = ($skippedIds -contains $dmgId1) -and ($skippedIds -contains $dmgId2)
$dmgOk = ($damage.Code -eq 0) -and ($resume.Code -eq 0) -and ($resumedCount -eq 0) -and $bothRefused
Add-Step 'damaged checkpoints are refused by resume-safe' $dmgOk ("tasks {0} and {1} damaged: damage exit {2}; resume-safe exit {3}; resumed {4}; both refused: {5}" -f $dmgId1, $dmgId2, $damage.Code, $resume.Code, $resumedCount, $bothRefused)

$pass = $frozenOk -and ($plan.Code -eq 0) -and ($run.Code -eq 0) -and ($taskStatus -eq 'passed') -and $testPassed -and $addFixed -and $testUnchanged -and $persistOk -and $refusalOk -and $killOk -and $iwOk -and $dmgOk
$verdict = if ($pass) { 'PASS' } else { 'FAIL' }
Save-Summary $verdict
Write-Log "RESULT $verdict for ${Label} (test ref $TestRef at $($facts['under_test_sha']), exe sha256 $($facts['exe_sha256']))"
if ($pass) { exit 0 }
exit 1
