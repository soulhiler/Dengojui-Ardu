#Requires -Version 5.1
<#
.SYNOPSIS
  Stop old listener on TCP 8898, optional GET /control on board, start xiao_cam_proxy in background.

.EXAMPLE
  .\tools\start_xiao_cam_proxy.ps1
  .\tools\start_xiao_cam_proxy.ps1 -CameraIp 192.168.1.50
#>
param(
  [string] $CameraIp = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $ProjectRoot

function Stop-ListenerOnPort([int] $tcpPort) {
  $lines = netstat -ano | Select-String ":$tcpPort\s+.*LISTENING"
  foreach ($m in $lines) {
    $parts = ($m.Line -split '\s+') | Where-Object { $_ }
    $listenPid = [int]$parts[-1]
    if ($listenPid -gt 0) {
      Write-Host "Stopping PID $listenPid (port $tcpPort)..."
      Stop-Process -Id $listenPid -Force -ErrorAction SilentlyContinue
    }
  }
  Start-Sleep -Milliseconds 500
}

Stop-ListenerOnPort -tcpPort 8898

$ipArg = $CameraIp.Trim()
if (-not $ipArg) {
  $txt = Join-Path $ProjectRoot "camera_ip.txt"
  if (Test-Path $txt) {
    foreach ($line in Get-Content $txt -Encoding UTF8) {
      $t = $line.Trim()
      if ($t -and -not $t.StartsWith("#")) {
        $ipArg = $t
        break
      }
    }
  }
}

if ($ipArg) {
  Write-Host ('Board ' + $ipArg + ' - GET /control?cam=1&mic=1 ...')
  try {
    $u = 'http://{0}/control?cam=1&mic=1' -f $ipArg
    Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop | Out-Null
    Write-Host "OK: $u"
  }
  catch {
    Write-Host "Warning: /control failed (old firmware or offline): $($_.Exception.Message)"
  }
}

Write-Host "Starting proxy (background)..."
$pyArgs = @("-3", "tools\xiao_cam_proxy.py")
if ($ipArg) {
  $pyArgs += $ipArg
}
Start-Process -FilePath "py" -ArgumentList $pyArgs -WorkingDirectory $ProjectRoot -WindowStyle Hidden

Start-Sleep -Seconds 2
Write-Host ""
Write-Host "Open: http://127.0.0.1:8898/"
Write-Host "Telemetry UI: http://127.0.0.1:8898/telemetry"
if ($ipArg) {
  Write-Host "Board telemetry: http://$ipArg/telemetry"
}
