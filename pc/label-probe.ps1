# Label probe for the self-hosted PC runner.
#
# ForgeOS's sandbox gives its test copy a Low integrity label with icacls. On the
# PC that label is denied for folders under the runner's work tree. This probe
# checks the same label on new folders in three places and prints the token state
# that explains the result. The logs are public, so it prints no user name, no
# profile path and no SID; any user name is masked before printing.
$ErrorActionPreference = 'Continue'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userName = $env:USERNAME

function Clean([string] $text) {
    if (-not $text) { return '' }
    $masked = $text -replace ('\b' + [regex]::Escape($userName) + '\b'), '<user>'
    return (($masked -replace '\s+', ' ').Trim())
}

function Say([string] $label, [string] $value) {
    Write-Output ('PROBE ' + $label + ': ' + (Clean $value))
}

function Try-Label([string] $dir) {
    $text = (& icacls.exe $dir '/setintegritylevel' '(OI)(CI)L' '/T' '/L' '/Q' 2>&1 | Out-String)
    return [pscustomobject]@{ Code = $LASTEXITCODE; Text = $text }
}

$principal = New-Object Security.Principal.WindowsPrincipal($identity)
Say 'elevated' ([string] $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
$groups = (whoami /groups) | Where-Object { $_ -match 'Administrators|Mandatory Label' } | ForEach-Object { $_.Trim() }
Say 'token groups' ($groups -join ' | ')

$base = Join-Path $env:RUNNER_TEMP 'label-probe'
Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $base | Out-Null

# 1. New folder under the runner temp tree, no explicit grant.
$plain = Join-Path $base 'plain'
New-Item -ItemType Directory -Force -Path $plain | Out-Null
Say 'owner under runner temp' ((Get-Acl -LiteralPath $plain).Owner)
$r = Try-Label $plain
Say 'label under runner temp' ('exit ' + $r.Code + ' ' + $r.Text)

# 2. Same place after the current account gets an explicit full-control grant.
$sid = $identity.User.Value
$granted = Join-Path $base 'granted'
New-Item -ItemType Directory -Force -Path $granted | Out-Null
$grant = (& icacls.exe $granted '/grant:r' ('*' + $sid + ':(OI)(CI)F') 2>&1 | Out-String)
Say 'explicit grant' ('exit ' + $LASTEXITCODE + ' ' + $grant)
$grantedChild = Join-Path $granted 'child'
New-Item -ItemType Directory -Force -Path $grantedChild | Out-Null
$r = Try-Label $grantedChild
Say 'label after explicit grant' ('exit ' + $r.Code + ' ' + $r.Text)

# 3. New folder directly under the drive root.
$root = Join-Path $env:SystemDrive 'ForgeOS-label-probe'
Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $root | Out-Null
$rootChild = Join-Path $root 'child'
New-Item -ItemType Directory -Force -Path $rootChild | Out-Null
Say 'owner under drive root' ((Get-Acl -LiteralPath $rootChild).Owner)
$r = Try-Label $rootChild
Say 'label under drive root' ('exit ' + $r.Code + ' ' + $r.Text)

Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
Say 'cleanup' 'done'
exit 0
