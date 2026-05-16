#Requires -Version 5.1
param(
  [string] $Port = "COM5",
  [int] $Attempts = 12,
  [int] $DelaySec = 5
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$cli = Join-Path $root "tools\arduino-cli\arduino-cli.exe"
Set-Location $root
$fqbn = "esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
for ($i = 1; $i -le $Attempts; $i++) {
  Write-Host "Upload attempt $i / $Attempts -> $Port ..."
  & $cli upload -p $Port --fqbn $fqbn xiao_cam_stream
  if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: firmware uploaded."
    exit 0
  }
  if ($i -lt $Attempts) {
    Write-Host "Waiting ${DelaySec}s (close Serial Monitor / other COM users)..."
    Start-Sleep -Seconds $DelaySec
  }
}
exit 1
