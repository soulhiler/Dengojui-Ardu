#Requires -Version 5.1
<#
  Восстановление XIAO после GPIO20/I2C: сборка + заливка (USB, ArduinoOTA :3232, HTTP /update).
#>
param(
  [string] $Ip = "",
  [string] $Password = "",
  [string[]] $Ports = @("COM6", "COM5", "COM3")
)

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

$cli = Join-Path $ProjectRoot "tools\arduino-cli\arduino-cli.exe"
$fqbn = "esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
$sketch = "xiao_cam_stream"

if (-not $Ip) {
  $cf = Join-Path $ProjectRoot "camera_ip.txt"
  if (Test-Path $cf) {
    foreach ($line in Get-Content $cf -Encoding UTF8) {
      $t = $line.Trim()
      if ($t -and -not $t.StartsWith("#")) { $Ip = $t; break }
    }
  }
}
if (-not $Ip) { $Ip = "192.168.9.12" }

if (-not $Password) {
  $Password = $env:XIAO_OTA_PASSWORD
  if (-not $Password) {
    $sec = Join-Path $ProjectRoot "xiao_cam_stream\secrets.h"
    if (Test-Path $sec) {
      $t = Get-Content $sec -Raw
      if ($t -match 'XIAO_OTA_PASSWORD\s+"([^"]+)"') { $Password = $Matches[1] }
    }
  }
}
if (-not $Password) { Write-Warning "OTA password empty — задайте -Password или XIAO_OTA_PASSWORD" }

Write-Host "=== 1) Сборка $sketch ==="
& $cli compile --fqbn $fqbn $sketch
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "=== 2) Сброс USB Espressif (303A) ==="
Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -match 'VID_303A' } | ForEach-Object {
  try {
    Disable-PnpDevice -InstanceId $_.InstanceId -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
  } catch {}
}
Start-Sleep -Seconds 2
Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -match 'VID_303A' } | ForEach-Object {
  try {
    Enable-PnpDevice -InstanceId $_.InstanceId -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
  } catch {}
}
Start-Sleep -Seconds 3

Write-Host "=== 3) USB upload ==="
foreach ($p in $Ports) {
  Write-Host "try $p ..."
  & $cli upload -p $p --fqbn $fqbn $sketch 2>&1 | Select-Object -Last 4
  if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: USB $p"
    exit 0
  }
}

Write-Host "=== 4) ArduinoOTA :3232 -> $Ip ==="
$env:XIAO_OTA_PASSWORD = $Password
& $cli upload -p $Ip --fqbn $fqbn $sketch --upload-field "password=$Password" 2>&1 | Select-Object -Last 6
if ($LASTEXITCODE -eq 0) {
  Write-Host "OK: ArduinoOTA"
  exit 0
}

Write-Host "=== 5) HTTP POST /update (если уже есть 1.2.1+ с OTA) ==="
py -3 (Join-Path $ProjectRoot "tools\xiao_http_ota.py") --ip $Ip --pwd $Password 2>&1
if ($LASTEXITCODE -eq 0) {
  Write-Host "OK: HTTP OTA"
  exit 0
}

Write-Host ""
Write-Host "Auto flash failed. build 11 has no /update and port 3232 is closed."
Write-Host "Once: hold BOOT, tap RESET, flash via COM: tools\xiao_flash_and_telemetry.ps1 -Port COMx"
Write-Host "Then Wi-Fi only: tools\xiao_recover_and_flash.ps1 or xiao_http_ota.py"
exit 1
