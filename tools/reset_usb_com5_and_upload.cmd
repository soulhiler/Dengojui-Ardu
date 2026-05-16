@echo off
setlocal
set "ID=USB\VID_303A&PID_1001&MI_00\6&27706EC2&0&0000"
echo Disabling USB device...
pnputil /disable-device "%ID%"
timeout /t 3 /nobreak >nul
echo Enabling USB device...
pnputil /enable-device "%ID%"
timeout /t 5 /nobreak >nul
cd /d "%~dp0.."
echo Uploading...
tools\arduino-cli\arduino-cli.exe upload -p COM5 --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi xiao_cam_stream
exit /b %ERRORLEVEL%
