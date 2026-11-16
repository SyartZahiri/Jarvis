<#
.SYNOPSIS
  Installiert JARVIS für den aktuellen Benutzer (keine Administratorrechte nötig).

.DESCRIPTION
  Legt eine eigene Python-Umgebung unter %LOCALAPPDATA%\JARVIS\app an, installiert JARVIS
  aus diesem Ordner hinein und erstellt Verknüpfungen im Startmenü und auf dem Desktop.
  Erneut ausführen aktualisiert eine bestehende Installation.

.EXAMPLE
  .\install.ps1                      # installieren und starten
  .\install.ps1 -Extras voice        # zusätzlich Spracherkennung und Vorlesen
  .\install.ps1 -NoDesktop -NoStart  # ohne Desktop-Verknüpfung, nicht gleich starten
  .\install.ps1 -Uninstall           # Programm entfernen, Daten behalten
#>
[CmdletBinding()]
param(
  [string[]]$Extras = @(),
  [switch]$NoDesktop,
  [switch]$NoStart,
  [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$Source  = $PSScriptRoot
$Home_   = Join-Path $env:LOCALAPPDATA 'JARVIS'
$Venv    = Join-Path $Home_ 'app'
$Window  = Join-Path $Home_ 'window'
$Menu    = Join-Path ([Environment]::GetFolderPath('Programs')) 'JARVIS.lnk'
$Desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'JARVIS.lnk'

function Say([string]$Text, [string]$Color = 'Gray') { Write-Host "  $Text" -ForegroundColor $Color }
function Step([string]$Text) { Write-Host ""; Write-Host "> $Text" -ForegroundColor Cyan }

function Find-Python {
  # The py launcher knows every installed version; fall back to python on PATH.
  $candidates = @(
    @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('py', '-3'),
    @('python'), @('python3')
  )
  foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $arguments = @()
    if ($candidate.Count -gt 1) { $arguments += $candidate[1] }
    # No double quotes in the code: Windows PowerShell 5.1 strips them from native arguments.
    $arguments += @('-c', 'import sys; print(sys.executable if sys.version_info >= (3, 11) else str())')
    try {
      $found = (& $exe @arguments 2>$null | Select-Object -Last 1)
    } catch { continue }
    # The Microsoft Store placeholder prints nothing and opens the store instead.
    if ($found -and (Test-Path $found)) { return $found.Trim() }
  }
  foreach ($version in '313', '312', '311') {
    $path = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe"
    if (Test-Path $path) { return $path }
  }
  return $null
}

function New-Shortcut([string]$Path, [string]$Target, [string]$Icon) {
  $shell = New-Object -ComObject WScript.Shell
  $link = $shell.CreateShortcut($Path)
  $link.TargetPath = $Target
  $link.WorkingDirectory = Join-Path $env:USERPROFILE 'JARVIS'
  $link.Description = 'JARVIS - persönlicher Assistent'
  if (Test-Path $Icon) { $link.IconLocation = "$Icon,0" }
  $link.Save()
}

Write-Host ""
Write-Host "  JARVIS" -ForegroundColor White
Write-Host "  Persönlicher Assistent, lokal auf deinem Rechner" -ForegroundColor DarkGray

if ($Uninstall) {
  Step 'Entferne JARVIS'
  Get-Process msedge, chrome, brave -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -eq 'JARVIS' } | Stop-Process -ErrorAction SilentlyContinue
  foreach ($item in $Venv, $Window, $Menu, $Desktop) {
    if (Test-Path $item) { Remove-Item $item -Recurse -Force; Say "entfernt: $item" }
  }
  Say "Deine Daten (Gespräche, Gedächtnis, Einstellungen, verschlüsselte API-Keys)" 'Yellow'
  Say "liegen weiterhin in $Home_. Wer auch sie löschen will, entfernt den Ordner von Hand." 'Yellow'
  exit 0
}

if (-not (Test-Path (Join-Path $Source 'pyproject.toml'))) {
  throw "install.ps1 muss im JARVIS-Ordner liegen (pyproject.toml fehlt in $Source)."
}

Step 'Suche Python 3.11 oder neuer'
$Python = Find-Python
if (-not $Python) {
  Say 'Kein passendes Python gefunden.' 'Yellow'
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    $answer = Read-Host '  Python 3.12 jetzt über winget installieren? [J/n]'
    if ($answer -eq '' -or $answer -match '^[jJyY]') {
      winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
      $Python = Find-Python
    }
  }
  if (-not $Python) {
    Say 'Bitte Python von https://www.python.org/downloads/ installieren und dieses Skript erneut starten.' 'Red'
    exit 1
  }
}
Say $Python

Step 'Richte die Programmumgebung ein'
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
  New-Item -ItemType Directory -Force -Path $Home_ | Out-Null
  & $Python -m venv $Venv
  if ($LASTEXITCODE -ne 0) { throw 'Die virtuelle Umgebung konnte nicht angelegt werden.' }
  Say "neu angelegt: $Venv"
} else {
  Say "vorhanden, wird aktualisiert: $Venv"
}
$VenvPython = Join-Path $Venv 'Scripts\python.exe'

Step 'Installiere JARVIS'
& $VenvPython -m pip install --upgrade pip --quiet --disable-pip-version-check --no-cache-dir
$Target = $Source
if ($Extras.Count -gt 0) { $Target = "$Source[$($Extras -join ',')]" }
& $VenvPython -m pip install --upgrade --quiet --disable-pip-version-check --no-cache-dir $Target
if ($LASTEXITCODE -ne 0) { throw 'Die Installation ist fehlgeschlagen. Details stehen oben.' }
$Version = & $VenvPython -c 'import jarvis; print(jarvis.__version__)'
Say "JARVIS $Version installiert"

Step 'Lege Verknüpfungen an'
$Gui  = Join-Path $Venv 'Scripts\jarvis-gui.exe'
$Icon = & $VenvPython -c 'import jarvis, pathlib; print(pathlib.Path(jarvis.__file__).parent / ''web'' / ''icon.ico'')'
New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE 'JARVIS') | Out-Null
New-Shortcut $Menu $Gui $Icon
Say 'Startmenü: JARVIS'
if (-not $NoDesktop) {
  New-Shortcut $Desktop $Gui $Icon
  Say 'Desktop: JARVIS'
}

Step 'Prüfe Ollama (für lokale Modelle)'
$Ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if ((Test-Path $Ollama) -or (Get-Command ollama -ErrorAction SilentlyContinue)) {
  Say 'Ollama ist installiert.' 'Green'
} else {
  Say 'Ollama ist nicht installiert. Für ein lokales, kostenloses Modell: https://ollama.com/download' 'Yellow'
  Say 'Alternativ verbindest du beim ersten Start einen Cloud-Anbieter per API-Key.' 'Yellow'
}

Write-Host ""
Write-Host "  Fertig." -ForegroundColor Green
if ($NoDesktop) { Say 'JARVIS startet über das Startmenü.' } else { Say 'JARVIS startet über das Startmenü oder die Desktop-Verknüpfung.' }
Say 'Beim ersten Start führt dich ein Assistent durch die Einrichtung.'
Write-Host ""

if (-not $NoStart) { Start-Process $Gui }
