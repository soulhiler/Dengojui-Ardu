#Requires -Version 5.1
Get-PnpDevice -Class Ports -ErrorAction SilentlyContinue |
  Where-Object { $_.InstanceId -match 'USB' } |
  Select-Object Status, FriendlyName, InstanceId |
  Format-Table -AutoSize
