$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$jar = Join-Path $root "gradle\wrapper\gradle-wrapper.jar"
Invoke-WebRequest -Uri "https://github.com/gradle/gradle/raw/v8.9.0/gradle/wrapper/gradle-wrapper.jar" -OutFile $jar -UseBasicParsing
Invoke-WebRequest -Uri "https://github.com/gradle/gradle/raw/v8.9.0/gradlew.bat" -OutFile (Join-Path $root "gradlew.bat") -UseBasicParsing
Write-Host "gradle-wrapper.jar size:" (Get-Item $jar).Length
