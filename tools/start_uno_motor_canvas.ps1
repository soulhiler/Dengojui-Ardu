# Мост для кнопок Canvas uno-motor-control → Arduino COM
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$port = $args[0]
if (-not $port) { $port = "COM3" }
Write-Host "Мост Canvas → $port (Ctrl+C для выхода)"
py -3 tools/uno_motor_canvas_bridge.py $port
