# Captures the ForgeOS main window on the PC runner, for a visual check.
#
# Starts the EXE with the job's isolated profile, waits for its window, and captures
# only that window with PrintWindow. It refuses to run while any ForgeOS process is
# already open, so it cannot capture the user's own app, and it refuses to fall back
# to a screen copy, which could include other windows. The app's output is saved
# next to the image. Output: OutDir\forgeos-window.png, app-stdout.txt, app-stderr.txt
param(
    [Parameter(Mandatory = $true)][string] $Exe,
    [Parameter(Mandatory = $true)][string] $OutDir
)

$ErrorActionPreference = 'Stop'
$title = 'ForgeOS | Autonomous Development Dashboard'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (Get-Process -Name 'ForgeOS' -ErrorAction SilentlyContinue) {
    throw 'a ForgeOS process is already running; not capturing'
}

Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public struct ForgeRect { public int Left; public int Top; public int Right; public int Bottom; }
public static class ForgeWindow {
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out ForgeRect rect);
    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
}
'@

$stdoutPath = Join-Path $OutDir 'app-stdout.txt'
$stderrPath = Join-Path $OutDir 'app-stderr.txt'
$app = Start-Process -FilePath $Exe -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

function Show-AppState([string] $Reason) {
    Write-Output $Reason
    Write-Output ("launcher exited: {0}" -f $app.HasExited)
    if ($app.HasExited) { Write-Output ("launcher exit code: {0}" -f $app.ExitCode) }
    Get-Process -Name 'ForgeOS' -ErrorAction SilentlyContinue |
        Select-Object Id, Responding, MainWindowTitle, MainWindowHandle |
        Format-Table -AutoSize | Out-String | Write-Output
    foreach ($path in @($stdoutPath, $stderrPath)) {
        if (Test-Path $path) {
            Write-Output ("--- {0} (last 40 lines)" -f (Split-Path $path -Leaf))
            Get-Content -Path $path -Tail 40 | Write-Output
        }
    }
}

try {
    # The window's handle comes from its process. FindWindow by title did not return
    # it in the diagnostic run, although the process reported the exact title.
    $target = $null
    $deadline = (Get-Date).AddSeconds(120)
    while ($null -eq $target -and -not $app.HasExited -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        $target = Get-Process -Name 'ForgeOS' -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowTitle -eq $title -and $_.MainWindowHandle -ne [IntPtr]::Zero } |
            Select-Object -First 1
    }
    if ($null -eq $target) {
        Show-AppState 'the ForgeOS window was not found'
        throw 'the ForgeOS window did not appear'
    }
    $hwnd = $target.MainWindowHandle
    Start-Sleep -Seconds 4

    $rect = New-Object ForgeRect
    if (-not [ForgeWindow]::GetWindowRect($hwnd, [ref] $rect)) { throw 'GetWindowRect failed' }
    $width = $rect.Right - $rect.Left
    $height = $rect.Bottom - $rect.Top
    if ($width -le 0 -or $height -le 0) { throw "bad window size $width x $height" }

    $bitmap = New-Object System.Drawing.Bitmap -ArgumentList $width, $height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $hdc = $graphics.GetHdc()
        try {
            $printed = [ForgeWindow]::PrintWindow($hwnd, $hdc, 2)
        } finally {
            $graphics.ReleaseHdc($hdc)
        }
        if (-not $printed) { throw 'PrintWindow failed; not falling back to a screen copy' }
        $png = Join-Path $OutDir 'forgeos-window.png'
        $bitmap.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
        Write-Output ('captured {0}x{1} window, {2} bytes' -f $width, $height, (Get-Item $png).Length)
    } finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
} finally {
    # The guard above means every ForgeOS process now running is the one started here.
    Get-Process -Name 'ForgeOS' -ErrorAction SilentlyContinue | ForEach-Object {
        & taskkill.exe /PID $_.Id /T /F | Out-Null
    }
}
