#Requires -Version 5.1
<#
.SYNOPSIS
  Найти XIAO ESP32-S3 по USB, прошить xiao_cam_stream (PSRAM=OPI), поднять телеметрию HTTP :8897.

.EXAMPLE
  .\tools\xiao_flash_and_telemetry.ps1
  .\tools\xiao_flash_and_telemetry.ps1 -Port COM7
  .\tools\xiao_flash_and_telemetry.ps1 -SkipTelemetry
#>
param(
  [string] $Port = "",
  [switch] $SkipTelemetry,
  [int] $HttpPort = 8897
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

$cli = Join-Path $ProjectRoot "tools\arduino-cli\arduino-cli.exe"
if (-not (Test-Path $cli)) {
  Write-Error "Нет portable arduino-cli: $cli (скачайте в tools\arduino-cli или поставьте arduino-cli в PATH)."
}

$fqbn = "esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
$sketch = "xiao_cam_stream"

function Stop-ListenerOnPort([int] $tcpPort) {
  $lines = netstat -ano | Select-String ":$tcpPort\s+.*LISTENING"
  foreach ($m in $lines) {
    $parts = ($m.Line -split '\s+') | Where-Object { $_ }
    $listenPid = [int]$parts[-1]
    if ($listenPid -gt 0) {
      Write-Host "Останавливаю PID $listenPid (порт $tcpPort)..."
      Stop-Process -Id $listenPid -Force -ErrorAction SilentlyContinue
    }
  }
  Start-Sleep -Milliseconds 600
}

function Get-XiaoPortFromCli() {
  $raw = & $cli board list --format json 2>&1
  if ($LASTEXITCODE -ne 0) { return $null }
  $data = $raw | ConvertFrom-Json
  foreach ($dp in $data.detected_ports) {
    if (-not $dp.matching_boards) { continue }
    foreach ($b in $dp.matching_boards) {
      if ($b.fqbn -like "esp32:esp32:XIAO_ESP32S3*") {
        return $dp.port.address
      }
    }
  }
  return $null
}

$p = $Port.Trim()
if (-not $p) {
  Write-Host "Поиск платы (arduino-cli board list)..."
  $p = Get-XiaoPortFromCli
}
if (-not $p) {
  Write-Error "Плата XIAO ESP32-S3 не найдена по USB. Подключите кабель, драйвер USB-JTAG/CDC, закройте Serial Monitor и повторите. Или укажите: -Port COM5"
}

Stop-ListenerOnPort $HttpPort

Write-Host "Сборка $sketch ..."
& $cli compile --fqbn $fqbn $sketch
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Загрузка на $p ..."
& $cli upload -p $p --fqbn $fqbn $sketch
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Готово: прошивка залита на $p"

if ($SkipTelemetry) { return }

Write-Host "Телеметрия: http://127.0.0.1:$HttpPort/  (закройте это окно — остановит сервер)"
$tel = Join-Path $ProjectRoot "tools\xiao_serial_telemetry.py"
& py -3 $tel --http $HttpPort
