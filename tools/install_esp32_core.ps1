#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$cli = Join-Path (Split-Path $PSScriptRoot -Parent) "tools\arduino-cli\arduino-cli.exe"
if (-not (Test-Path $cli)) { throw "Missing $cli - run bootstrap_arduino_cli.ps1 first" }
& $cli config init
& $cli config set board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
& $cli core update-index
& $cli core install esp32:esp32
Write-Host "esp32:esp32 core installed OK"
