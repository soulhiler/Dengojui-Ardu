#Requires -Version 5.1
<#
.SYNOPSIS
  Найти XIAO ESP32-S3 по USB, прошить xiao_cam_stream (PSRAM=OPI), поднять телеметрию HTTP :8897.

  Частые сбои прошивки:
  1) COM занят (Serial Monitor, терминал Cursor, другой py/arduino) — PermissionError / «port is busy».
     Скрипт по умолчанию несколько раз повторяет upload с паузой.
  2) arduino-cli board list не называет плату XIAO, а даёт «ESP32 Family Device» (fqbn esp32_family) при
     нативном USB ESP32-S3 (VID 0x303A, PID 0x1001). Раньше авто-поиск такой порт пропускал — теперь он учитывается.

.EXAMPLE
  .\tools\xiao_flash_and_telemetry.ps1
  .\tools\xiao_flash_and_telemetry.ps1 -Port COM7
  .\tools\xiao_flash_and_telemetry.ps1 -SkipTelemetry
  .\tools\xiao_flash_and_telemetry.ps1 -UploadRetries 15 -UploadRetryDelaySec 5
#>
param(
  [string] $Port = "",
  [switch] $SkipTelemetry,
  [int] $HttpPort = 8897,
  [int] $UploadRetries = 8,
  [int] $UploadRetryDelaySec = 4
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
  # Нативный USB ESP32-S3 (Seeed XIAO и др.): CLI часто показывает только esp32_family, не XIAO_ESP32S3.
  foreach ($dp in $data.detected_ports) {
    if (-not $dp.port) { continue }
    $props = $dp.port.properties
    if (-not $props) { continue }
    $vid = [string]$props.vid
    $pid = [string]$props.pid
    if ($vid -ne "0x303A" -or $pid -ne "0x1001") { continue }
    if (-not $dp.matching_boards) { continue }
    foreach ($b in $dp.matching_boards) {
      if ($b.fqbn -eq "esp32:esp32:esp32_family") {
        Write-Host "Обнаружен ESP32-S3 USB-JTAG (VID ${vid} PID ${pid}) на $($dp.port.address) — используем для загрузки XIAO/S3."
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

Write-Host "Загрузка на $p (до $UploadRetries попыток при занятом COM)..."
$uploadOk = $false
for ($i = 1; $i -le $UploadRetries; $i++) {
  & $cli upload -p $p --fqbn $fqbn $sketch
  if ($LASTEXITCODE -eq 0) {
    $uploadOk = $true
    break
  }
  if ($i -lt $UploadRetries) {
    Write-Warning "Попытка $i / $UploadRetries не удалась (код $LASTEXITCODE). Закрой Serial Monitor / монитор порта в Cursor и другие программы с COM. Повтор через ${UploadRetryDelaySec}s..."
    Start-Sleep -Seconds $UploadRetryDelaySec
  }
}
if (-not $uploadOk) {
  Write-Error "Загрузка не удалась после $UploadRetries попыток. Освободи $p или укажи другой -Port."
}

Write-Host "Готово: прошивка залита на $p"

if ($SkipTelemetry) { return }

Write-Host "Телеметрия: http://127.0.0.1:$HttpPort/  (закройте это окно — остановит сервер)"
$tel = Join-Path $ProjectRoot "tools\xiao_serial_telemetry.py"
& py -3 $tel --http $HttpPort
