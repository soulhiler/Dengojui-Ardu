#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
  Восстановление Wi-Fi ПК для Duangdeehouse2:
  - остановить агрессивные Killer-службы (не трогаем сам адаптер)
  - отключить энергосбережение Wi-Fi
  - отключить автоподключение к чужим сетям
  - восстановить профили Duangdeehouse2 (2.4 + 5G)
  - одна попытка подключения (без скан-шторма)
#>
$ErrorActionPreference = "Continue"

$wifiPass = $env:XIAO_WIFI_PASS
if (-not $wifiPass) {
  $sec = Join-Path (Split-Path $PSScriptRoot -Parent) "xiao_cam_stream\secrets.h"
  if (Test-Path $sec) {
    $secText = Get-Content $sec -Raw
    if ($secText -match 'kWifiPass\s*=\s*"([^"]+)"') { $wifiPass = $Matches[1] }
  }
}
if (-not $wifiPass) { throw "Задайте `$env:XIAO_WIFI_PASS или создайте xiao_cam_stream/secrets.h" }
$homeSsids = @(
    "Duangdeehouse2  2.4GHz",
    "Duangdeehouse2 2.4GHz",
    "Duangdeehouse2  5GHz",
    "Duangdeehouse2 5GHz"
)
$blockAuto = @(
    "Duangrudee House1-5G",
    "xiao-robot"
)

function Write-ProfileXml([string]$ssid, [string]$pass, [string]$path) {
    $xml = @"
<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>$ssid</name>
  <SSIDConfig><SSID><name>$ssid</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>auto</connectionMode>
  <MSM>
    <security>
      <authEncryption>
        <authentication>WPA2PSK</authentication>
        <encryption>AES</encryption>
        <useOneX>false</useOneX>
      </authEncryption>
      <sharedKey>
        <keyType>passPhrase</keyType>
        <protected>false</protected>
        <keyMaterial>$pass</keyMaterial>
      </sharedKey>
    </security>
  </MSM>
</WLANProfile>
"@
    Set-Content -Path $path -Value $xml -Encoding UTF8
}

Write-Host "=== 1) Killer: stop aggressive Wi-Fi services ==="
foreach ($svc in @("Killer Wifi Optimization Service", "KNDBWM", "Killer Analytics Service")) {
    $s = Get-Service -Name $svc -ErrorAction SilentlyContinue
    if ($s) {
        if ($s.Status -eq "Running") { Stop-Service $svc -Force -ErrorAction SilentlyContinue }
        Set-Service $svc -StartupType Disabled -ErrorAction SilentlyContinue
        Write-Host "  disabled: $svc"
    }
}

Write-Host "=== 2) Wi-Fi adapter: no power saving ==="
$wifi = Get-NetAdapter | Where-Object { $_.InterfaceDescription -match "Killer Wireless" } | Select-Object -First 1
if ($wifi) {
    powercfg -setacvalueindex SCHEME_CURRENT SUB_NONE $wifi.InterfaceGuid 0 2>&1 | Out-Null
    powercfg -setdcvalueindex SCHEME_CURRENT SUB_NONE $wifi.InterfaceGuid 0 2>&1 | Out-Null
    powercfg -setactive SCHEME_CURRENT | Out-Null
    try {
        Disable-NetAdapterPowerManagement -Name $wifi.Name -ErrorAction Stop
        Write-Host "  power save OFF: $($wifi.Name)"
    } catch {
        Write-Host "  power save: $($_.Exception.Message)"
    }
    try {
        Set-NetAdapterAdvancedProperty -Name $wifi.Name -DisplayName "Roaming Aggressiveness" -DisplayValue "1. Lowest" -ErrorAction Stop
        Write-Host "  roaming: lowest"
    } catch {
        Write-Host "  roaming: skip ($($_.Exception.Message))"
    }
}

Write-Host "=== 3) Block autoconnect to wrong SSIDs ==="
foreach ($ssid in $blockAuto) {
    netsh wlan set profileparameter name="$ssid" connectionmode=manual 2>&1 | Out-Null
    Write-Host "  manual: $ssid"
}

Write-Host "=== 4) Restore Duangdeehouse2 profiles ==="
$tmp = Join-Path $env:TEMP "dihouse_wifi_profiles"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
foreach ($ssid in $homeSsids) {
    $safe = ($ssid -replace '[^\w\-]', '_')
    $f = Join-Path $tmp "$safe.xml"
    Write-ProfileXml $ssid $wifiPass $f
    netsh wlan delete profile name="$ssid" 2>&1 | Out-Null
    netsh wlan add profile filename="$f" user=all 2>&1 | Out-Null
    Write-Host "  profile: $ssid"
}

Write-Host "=== 5) One connect attempt (2.4 GHz first) ==="
netsh wlan disconnect 2>&1 | Out-Null
Start-Sleep -Seconds 2
$target = "Duangdeehouse2  2.4GHz"
netsh wlan connect name="$target" 2>&1
Start-Sleep -Seconds 8
netsh wlan show interfaces

Write-Host "=== Done. If SSID not in air: power-cycle router Duangdeehouse2 physically. ==="
