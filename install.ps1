# Creates the desktop shortcuts and (optionally) the Windows auto-start entry.
#   .\install.ps1            install shortcuts + auto-start
#   .\install.ps1 -Remove    remove auto-start (desktop shortcuts stay)
param([switch]$Remove)

$proj    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$desktop = [Environment]::GetFolderPath("Desktop")
$startup = [Environment]::GetFolderPath("Startup")
$pythonw = Join-Path $proj ".venv\Scripts\pythonw.exe"
$runbat  = Join-Path $proj "run.bat"
$shell   = New-Object -ComObject WScript.Shell

$autostart = Join-Path $startup "Relay.lnk"

if ($Remove) {
    if (Test-Path $autostart) {
        Remove-Item $autostart -Force
        Write-Output "auto-start removed: $autostart"
    } else {
        Write-Output "auto-start was not installed"
    }
    exit 0
}

if (-not (Test-Path $pythonw)) {
    Write-Error "pythonw.exe not found at $pythonw - run run.bat once to create the venv."
    exit 1
}

# Falls back to a stock shell icon only if the bundled one has gone missing.
$icon = Join-Path $proj "assets\relay.ico"
if (-not (Test-Path $icon)) {
    $icon = "$env:SystemRoot\System32\SHELL32.dll,138"
    Write-Warning "assets\relay.ico not found; shortcuts will use a stock icon"
}

# pythonw.exe is the console-less twin of python.exe: no window, ever.
function New-Shortcut($path, $target, $cmdArgs, $desc) {
    $sc = $shell.CreateShortcut($path)
    $sc.TargetPath       = $target
    $sc.Arguments        = [string]$cmdArgs
    $sc.WorkingDirectory = $proj
    $sc.Description      = $desc
    $sc.IconLocation     = $icon
    $sc.WindowStyle      = 7          # minimised, belt-and-braces with pythonw
    $sc.Save()
    Write-Output "  $path"
}

Write-Output "Shortcuts:"
New-Shortcut (Join-Path $desktop "Relay.lnk") $pythonw "-m relay" `
    "Dictate RO -> EN prompt (F9 or click the dot)"
New-Shortcut (Join-Path $desktop "Relay - Mic Test.lnk") $runbat "--mic-test" `
    "Check which microphone has signal"
New-Shortcut $autostart $pythonw "-m relay" `
    "Relay at Windows start-up"

Write-Output ""
Write-Output "Auto-start installed. Remove it with:  .\install.ps1 -Remove"
Write-Output "Log file: $(Join-Path $proj 'relay.log')"
