#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$dest = Join-Path $root "tools\arduino-cli"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$zip = Join-Path $dest "arduino-cli.zip"
$uri = "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip"
Write-Host "Downloading arduino-cli..."
Invoke-WebRequest -Uri $uri -OutFile $zip -UseBasicParsing
Write-Host "Extracting..."
Expand-Archive -Path $zip -DestinationPath $dest -Force
Remove-Item $zip -Force
$exe = Get-ChildItem -Path $dest -Recurse -Filter "arduino-cli.exe" | Select-Object -First 1
if (-not $exe) { throw "arduino-cli.exe not found after extract" }
Write-Host "OK:" $exe.FullName
