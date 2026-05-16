@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

set "JBR="
if exist "C:\Program Files\Android\Android Studio\jbr\bin\java.exe" set "JBR=C:\Program Files\Android\Android Studio\jbr"
if exist "%LOCALAPPDATA%\Programs\Android Studio\jbr\bin\java.exe" set "JBR=%LOCALAPPDATA%\Programs\Android Studio\jbr"

if defined JBR (
  set "JAVA_HOME=%JBR%"
  set "PATH=%JBR%\bin;%PATH%"
  echo Using JAVA_HOME=%JAVA_HOME%
) else (
  echo WARNING: Android Studio JBR not found. Install Android Studio or JDK 17+.
  echo Manually set JAVA_HOME before gradlew.bat
)

if not exist "local.properties" (
  if exist "%LOCALAPPDATA%\Android\Sdk\platforms" (
    set "SDKP=%LOCALAPPDATA:\=/%"
    echo sdk.dir=%SDKP%/Android/Sdk> local.properties
    echo Wrote local.properties from default SDK path
  ) else (
    echo Missing local.properties - copy local.properties.example and set sdk.dir
    exit /b 1
  )
)

call gradlew.bat assembleDebug --no-daemon
exit /b %ERRORLEVEL%
