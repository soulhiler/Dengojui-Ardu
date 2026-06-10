#Requires -Version 5.1
<#
  Ждёт появления XIAO ESP32-S3 на USB (VID 303A) или COM-порта.
  Пример: .\tools\wait_xiao_com.ps1 -Seconds 120
#>
param([int] $Seconds = 90)

Write-Host "Жду XIAO USB (Espressif 303A) до $Seconds с… (Ctrl+C — выход)"
$deadline = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $deadline) {
  $present = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -match 'VID_303A&PID_1001' }
  if ($present) {
    Write-Host "Найдено на USB:"
    $present | Format-Table Status, FriendlyName, InstanceId -AutoSize
    py -3 -m serial.tools.list_ports
    exit 0
  }
  $ports = py -3 -m serial.tools.list_ports 2>$null
  if ($ports -match 'COM\d' -and $ports -notmatch 'только COM3') {
    Write-Host $ports
    exit 0
  }
  Start-Sleep -Seconds 2
}
Write-Host "XIAO на USB не появилась. Wi-Fi может работать при питании без линий D+/D-."
exit 1
