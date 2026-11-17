<#
Hosted-runner acceptance for the real local-model path. This is NOT physical
target-PC acceptance.

Installs Ollama, pulls one coder model, then drives the ForgeOS CLI through
add -> plan -> run against a disposable Python project. Every step is written
to <ResultsDir>\<Label>.json (summary) and <ResultsDir>\<Label>.log (transcript),
including when a step fails.
#>
param(
    [Parameter(Mandatory = $true)][string] $UnderTest,
    [Parameter(Mandatory = $true)][string] $ResultsDir,
    [Parameter(Mandatory = $true)][string] $Label,
    [Parameter(Mandatory = $true)][string] $TestRef,
    [string] $Model = 'qwen2.5-coder:3b'
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8 = '1'

New-Item -ItemType Directory -Force -Path $ResultsDir | Out-Null
$ResultsDir = (Resolve-Path -Path $ResultsDir).Path
$UnderTest = (Resolve-Path -Path $UnderTest).Path
$summaryPath = Join-Path $ResultsDir "$Label.json"
$logPath = Join-Path $ResultsDir "$Label.log"
Set-Content -Path $logPath -Value '' -Encoding utf8
$env:PYTHONPATH = Join-Path $UnderTest 'src'

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

function Read-Task([string] $Id) {
    $listing = Invoke-Logged -Exe 'python' -Arguments @('-m', 'forgeos', 'list') -Quiet
    $facts['forgeos_list'] = $listing.Text
    try {
        $tasks = $listing.Text | ConvertFrom-Json -ErrorAction Stop
        return ($tasks | Where-Object { [string]$_.id -eq $Id } | Select-Object -First 1)
    } catch {
        Write-Log "could not parse forgeos list output: $($_.Exception.Message)"
        return $null
    }
}

# ---- environment --------------------------------------------------------
$facts['runner_os'] = (Get-CimInstance -ClassName Win32_OperatingSystem).Caption
$facts['python'] = ((& python --version) 2>&1 | Out-String).Trim()
$facts['under_test_sha'] = ((& git -C $UnderTest rev-parse HEAD) 2>&1 | Out-String).Trim()
Write-Log "runner: $($facts['runner_os']); $($facts['python']); $TestRef at $($facts['under_test_sha'])"
Save-Summary 'in_progress'

# ---- Ollama -------------------------------------------------------------
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

$forgeModels = Invoke-Logged -Exe 'python' -Arguments @('-m', 'forgeos', 'models')
$listsModel = [bool]($forgeModels.Text -match [regex]::Escape($Model))
Add-Step 'forgeos models' ($forgeModels.Code -eq 0) ("exit {0}; output lists {1}: {2}" -f $forgeModels.Code, $Model, $listsModel)

# ---- disposable project -------------------------------------------------
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

# ---- ForgeOS add -> plan -> run ----------------------------------------
$description = 'In calc.py, change add() so it returns a + b. Do not edit test_calc.py.'
$add = Invoke-Logged -Exe 'python' -Arguments @('-m', 'forgeos', 'add', $demo, $description)
$idMatch = [regex]::Match($add.Text, 'Created task #(\d+)')
if (-not $idMatch.Success) { Stop-Run "add printed no task id: $($add.Tail)" }
$taskId = $idMatch.Groups[1].Value
Add-Step 'add task' ($add.Code -eq 0) "created task #$taskId"

$plan = Invoke-Logged -Exe 'python' -Arguments @('-m', 'forgeos', 'plan', $taskId, '--model', $Model)
$facts['plan_seconds'] = $plan.Seconds
Add-Step 'plan' ($plan.Code -eq 0) ("exit {0} in {1}s. tail: {2}" -f $plan.Code, $plan.Seconds, $plan.Tail)
if ($plan.Code -ne 0) {
    $failed = Read-Task $taskId
    Stop-Run ("plan exited {0}; task status {1}; last_error {2}" -f $plan.Code, $failed.status, $failed.last_error)
}

$wsParent = Join-Path $work 'ForgeOS-work'
New-Item -ItemType Directory -Force -Path $wsParent | Out-Null
$run = Invoke-Logged -Exe 'python' -Arguments @('-m', 'forgeos', 'run', $taskId, '--workspace-parent', $wsParent, '--preset', 'python-unittest', '--model', $Model, '--allow-host-tests')
Add-Step 'run (edit, test, repair)' ($run.Code -eq 0) ("exit {0} in {1}s. tail: {2}" -f $run.Code, $run.Seconds, $run.Tail)

$task = Read-Task $taskId
$taskStatus = if ($task) { [string]$task.status } else { 'unknown' }
$taskResult = $null
if ($task -and $task.result) {
    try { $taskResult = $task.result | ConvertFrom-Json -ErrorAction Stop } catch { $taskResult = $null }
}
$edited = if ($taskResult) { @($taskResult.edited) } else { @() }
$lastError = if ($task) { [string]$task.last_error } else { '' }
Add-Step 'task status' ($taskStatus -eq 'passed') ("status {0}; edited: {1}; last_error: {2}" -f $taskStatus, ($edited -join ', '), $lastError)

# ---- independent evidence from the workspace ----------------------------
$testPassed = [bool]($taskResult -and $taskResult.test -and $taskResult.test.passed)
$testOutput = if ($taskResult -and $taskResult.test) { [string]$taskResult.test.output } else { '' }
Add-Step 'test evidence' $testPassed ("test passed: $testPassed; last lines: " + (($testOutput -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 3) -join ' | '))

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
$recordedSha = if ($taskResult -and $taskResult.sha256) { [string]$taskResult.sha256.'calc.py' } else { '' }
$actualSha = if (Test-Path $calcPath) { (Get-FileHash -Path $calcPath -Algorithm SHA256).Hash.ToLowerInvariant() } else { '' }
$facts['calc_sha256_recorded'] = $recordedSha
$facts['calc_sha256_actual'] = $actualSha
Add-Step 'worktree evidence' ($addFixed -and $testUnchanged) ("calc.py returns a + b: {0}; test_calc.py unchanged per git: {1}" -f $addFixed, $testUnchanged)

# ---- verdict ------------------------------------------------------------
$pass = ($plan.Code -eq 0) -and ($run.Code -eq 0) -and ($taskStatus -eq 'passed') -and $testPassed -and $addFixed -and $testUnchanged
$verdict = if ($pass) { 'PASS' } else { 'FAIL' }
Save-Summary $verdict
Write-Log "RESULT $verdict for ${Label} (test ref $TestRef at $($facts['under_test_sha']))"
if ($pass) { exit 0 }
exit 1
