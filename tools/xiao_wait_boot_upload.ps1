#Requires -Version 5.1
<#
  Waits for Espressif COM and flashes fix (1.2.1). When you see "waiting COM",
  hold BOOT, tap RESET, release BOOT.
#>
param(
  [int] $Minutes = 8,
  [string[]] $Ports = @("COM6", "COM5", "COM7", "COM4")
)

$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot
$cli = Join-Path $ProjectRoot "tools\arduino-cli\arduino-cli.exe"
$fqbn = "esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
$sketch = "xiao_cam_stream"
$deadline = (Get-Date).AddMinutes($Minutes)

Write-Host "Build + export..."
& $cli compile --fqbn $fqbn --export-binaries --build-path "xiao_cam_stream/build_out" $sketch | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Waiting for COM (BOOT+RESET on XIAO now) until $deadline ..."
while ((Get-Date) -lt $deadline) {
  foreach ($p in $Ports) {
    & $cli upload -p $p --fqbn $fqbn $sketch 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
      Write-Host "OK uploaded via $p"
      Start-Sleep 12
      try {
        $t = Invoke-RestMethod "http://192.168.9.12/telemetry" -TimeoutSec 8
        Write-Host "fw_build=$($t.fw_build) fw_version=$($t.fw_version)"
      } catch {}
      exit 0
    }
  }
  Start-Sleep -Seconds 2
}
Write-Host "Timeout. Hold BOOT, tap RESET, rerun: .\tools\xiao_wait_boot_upload.ps1"
exit 1
