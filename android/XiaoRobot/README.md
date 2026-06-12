## Собрать APK

1. Установите **Android SDK** (через Android Studio → SDK Manager). Обычный путь:  
   `C:\Users\<вы>\AppData\Local\Android\Sdk`

2. Создайте **`local.properties`** (шаблон: `local.properties.example`):

```properties
sdk.dir=C\:\\Users\\FabLab\\AppData\\Local\\Android\\Sdk
```

3. **JDK 17** для командной строки: в `gradle.properties` раскомментируйте  
   `org.gradle.java.home=...` и укажите **JBR** из Android Studio  
   (`C:\Program Files\Android\Android Studio\jbr` или аналог).  
   Без этого `gradlew` на машине с только Java 8 выдаст ошибку.

4. В терминале:
```bat
cd android\XiaoRobot
gradlew.bat assembleDebug
```

APK: **`app\build\outputs\apk\debug\xiao-robot-v<N>-debug.apk`** (версия из `dist/app-version.txt`)

Либо откройте папку `XiaoRobot` в Android Studio и **Build → Build Bundle(s) / APK(s) → Build APK(s)**.

## Что делает приложение

См. основной README репозитория. Телефон и XIAO в одной Wi‑Fi; IP платы в поле ввода.
