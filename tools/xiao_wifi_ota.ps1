#Requires -Version 5.1
<#
  Собрать xiao_cam_stream и залить по Wi-Fi (ArduinoOTA / esp_ota).
  Нужен непустой XIAO_OTA_PASSWORD в secrets.h на плате и тот же пароль ниже.

  Примеры:
    $env:XIAO_OTA_PASSWORD = "секрет"
    .\tools\xiao_wifi_ota.ps1
    .\tools\xiao_wifi_ota.ps1 -Ip 192.168.9.17
#>
param(
  [string] $Ip = "",
  [string] $Password = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

$cli = Join-Path $ProjectRoot "tools\arduino-cli\arduino-cli.exe"
if (-not (Test-Path $cli)) {
  Write-Error "Нет $cli"
}

$fqbn = "esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
$sketch = "xiao_cam_stream"

if (-not $Ip) {
  $cf = Join-Path $ProjectRoot "camera_ip.txt"
  if (Test-Path $cf) {
    foreach ($line in Get-Content $cf -Encoding UTF8) {
      $t = $line.Trim()
      if ($t -and -not $t.StartsWith("#")) {
        $Ip = $t
        break
      }
    }
  }
}
if (-not $Ip) {
  Write-Error "Укажи -Ip или первая строка в camera_ip.txt (IP платы)."
}

if (-not $Password) {
  $Password = [string]$env:XIAO_OTA_PASSWORD
}
if (-not $Password) {
  Write-Error "Задай пароль OTA: `$env:XIAO_OTA_PASSWORD = '...' (как #define XIAO_OTA_PASSWORD в secrets.h) или -Password ..."
}

Write-Host "Сборка $sketch ..."
& $cli compile --fqbn $fqbn $sketch
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "OTA upload -> $Ip ..."
& $cli upload -p $Ip --fqbn $fqbn $sketch --upload-field "password=$Password"
exit $LASTEXITCODE
