# Captures the ForgeOS main window on the PC runner, for a visual check.
#
# Starts the EXE with the job's isolated profile, waits for its window, and captures
# only that window with PrintWindow. It refuses to run while any ForgeOS process is
# already open, so it cannot capture the user's own app, and it refuses to fall back
# to a screen copy, which could include other windows. Output: OutDir\forgeos-window.png
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
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out ForgeRect rect);
    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
}
'@

$null = Start-Process -FilePath $Exe -PassThru
try {
    $hwnd = [IntPtr]::Zero
    $deadline = (Get-Date).AddSeconds(90)
    while ($hwnd -eq [IntPtr]::Zero -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        $hwnd = [ForgeWindow]::FindWindow($null, $title)
    }
    if ($hwnd -eq [IntPtr]::Zero) { throw 'the ForgeOS window did not appear within 90 seconds' }
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
