$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$com = $args[0]
if (-not $com) { $com = "COM3" }

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "uno_motor_web\.py" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 800
Write-Host "Веб-панель моторов → http://127.0.0.1:8765/  (COM: $com)"
py -3 tools\uno_motor_web.py $com
