# Инструкция новичку — с нуля

Как поднять окружение и начать работать над «Деньгожуй», даже если ты
никогда не писал код. Делай по шагам.

## 1. Поставь инструменты

| Что | Откуда | Зачем |
|-----|--------|-------|
| Git | https://git-scm.com/downloads | версии кода, совместная работа |
| Python 3 | https://www.python.org/downloads/ | tooling и ИИ-«мозг» (`brain/`) |
| Cursor | https://www.cursor.com | редактор с ИИ-агентом (или Claude Code / VS Code) |
| Android Studio (+ JDK 17) | https://developer.android.com/studio | сборка APK пульта |

При установке Python включи галочку **«Add Python to PATH»**.
При установке Git — оставляй значения по умолчанию.

## 2. Склонируй репозиторий

```bash
git clone https://github.com/soulhiler/Dengojui-Ardu.git
cd Dengojui-Ardu
```

## 3. Безопасность (обязательно, один раз)

```bash
git config core.hooksPath .githooks
```

Это включит проверку, не даёт случайно закоммитить пароль/секрет.

Пароль Wi-Fi живёт только локально:

```bash
copy xiao_cam_stream\secrets.h.example xiao_cam_stream\secrets.h   # Windows
```

Открой `xiao_cam_stream/secrets.h`, впиши пароль сети. **Этот файл в
`.gitignore` — никогда не коммить его и не вставляй пароль в код.**
Подробнее: `README.md` → «Безопасность», `docs/security-history-cleanup.md`.

## 4. Прошивка платы (XIAO ESP32-S3 Sense)

Windows PowerShell из корня репозитория:

```powershell
.\tools\bootstrap_arduino_cli.ps1     # скачать arduino-cli
.\tools\install_esp32_core.ps1        # поставить ядро esp32
.\tools\xiao_flash_and_telemetry.ps1  # собрать, залить, смотреть телеметрию
```

FQBN: `esp32:esp32:XIAO_ESP32S3:PSRAM=opi`.

## 5. Android-пульт (APK)

См. `android/XiaoRobot/README.md`. Коротко:

```bash
cd android/XiaoRobot
gradlew.bat assembleDebug
```

APK: `app/build/outputs/apk/debug/app-debug.apk`. Нужен JDK 17.

## 6. ИИ-«мозг»

```bash
py -3 -m unittest discover -s brain -p "test_*.py"   # тесты (без платы)
py -3 brain/brain.py 127.0.0.1 --dry-run --once      # демо-цикл офлайн
py -3 brain/brain.py <IP_платы> --token <если включён>
```

Контракт: `docs/brain-api.md`.

## 7. Как вести работу через Git

1. Своя ветка: `git checkout -b моя-задача`
2. Правки маленькими осмысленными коммитами (сообщения по-русски):
   `git add <файлы>` → `git commit -m "что и зачем"`
3. Запись в `docs/dev-log.md` (новое сверху): что/зачем/статус/дальше.
4. Пуш **своей ветки** (не `main`, без `--force`):
   `git push -u origin моя-задача`
5. На GitHub — Pull Request, обсуждение, мердж.

> Рерайт истории и `force-push` — только владелец репо и только по
> процедуре `docs/security-history-cleanup.md`.

## Куда смотреть дальше

`README.md` (обзор) · `docs/dev-log.md` (что уже сделано по фазам) ·
`docs/brain-api.md` (контракт мозга) · `docs/cursor-chat-archives/`
(история решений).
