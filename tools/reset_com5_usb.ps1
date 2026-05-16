#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$d = Get-PnpDevice | Where-Object { $_.FriendlyName -like '*COM5*' -and $_.InstanceId -like 'USB\VID_303A*' }
if (-not $d) { throw "COM5 Espressif USB device not found" }
Write-Host "Restarting:" $d.InstanceId
Restart-PnpDevice -InstanceId $d.InstanceId -Confirm:$false
Start-Sleep -Seconds 4
Write-Host "Done."
