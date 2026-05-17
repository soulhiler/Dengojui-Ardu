# Деньгожуй — XIAO ESP32-S3 Sense

Отдельный проект: прошивка камеры/телеметрии, привод TB6612FNG с платы, приложение **Android** (видео / мик / джойстик), скрипты на ПК (прокси, прошивка, OTA).

Воркспейс Cursor «It trane exp»; этот репозиторий — **`denzhogzhuy-ardu`**, не смешивается с веб‑тренажёром [IT](https://github.com/soulhiler/IT).

## Состав

| Путь | Назначение |
|------|------------|
| `xiao_cam_stream/` | Основной скетч: Wi‑Fi, MJPEG, `/telemetry`, `/control`, `/drive`, PDM TCP :81, TB6612 (см. `drive_config.h`) |
| `xiao_cam_stream/xiao_drive.h`, `drive_config.h` | Моторы + энкодеры; TB6612 или sign‑magnitude |
| `xiao_ble_mic_test/` | Тест BLE и PDM‑микрофона |
| `xiao_cam_viewer.html` | Подсказка по просмотру потока |
| `android/XiaoRobot/` | Android: камера (`/stream`), мик (TCP `:81`), джойстик → `/drive` |
| `brain/` | ИИ-«мозг» (Фаза 4): perceive→decide→act поверх HTTP-контракта; `docs/brain-api.md` |
| `docs/onboarding.md` | Инструкция новичку «с нуля» (Git/Python/Cursor, сборка, workflow) |
| `docs/dev-log.md` | Журнал разработки по фазам |
| `docs/security-history-cleanup.md` | Runbook чистки git-истории (user-gated) |
| `.github/workflows/` | CI: тесты `brain/`, сборка прошивки и APK |
| `docs/cursor-chat-archives/` | Архив переписок Cursor (`.jsonl` + `.zip`) |
| `tools/xiao_cam_proxy.py` | Прокси с платы на `localhost` (Cursor/браузер) |
| `tools/xiao_serial_telemetry.py` | Телеметрия: Serial / HTTP / BLE |
| `tools/xiao_flash_and_telemetry.ps1` | Прошивка по USB + авто‑порт USB‑JTAG + повторы upload |
| `tools/start_xiao_cam_proxy.ps1` | Фоновый прокси 8898 + опц. `/control` |
| `tools/bootstrap_arduino_cli.ps1`, `install_esp32_core.ps1` | `arduino-cli` и ядро `esp32:esp32` |
| `tools/upload_xiao_retry.ps1` | Повторная заливка при занятом COM |
| `tools/build_apk.bat` в `android/XiaoRobot/` | См. `android/XiaoRobot/README.md` (JDK 17 / JBR) |
| `tools/xiao_wifi_ota.ps1` | OTA по Wi‑Fi |

Скопируйте `xiao_cam_stream/secrets.h.example` → `xiao_cam_stream/secrets.h` (файл в `.gitignore`).

## Безопасность

- **Пароль Wi-Fi только в `xiao_cam_stream/secrets.h`** (в `.gitignore`); в коде — плейсхолдер.
- **Включите pre-commit защиту от секретов** (один раз, из корня репо):

  ```bash
  git config core.hooksPath .githooks
  ```

  Хук `.githooks/pre-commit` блокирует коммит `secrets.h`, реального пароля в коде и ранее утёкшей строки.
- **Инцидент:** ранее реальный Wi-Fi пароль попал в архив Cursor. Рабочее дерево очищено, но **строка остаётся в git-истории** до её перезаписи. Пароль на роутере считать скомпрометированным — **сменить**. Точный порядок чистки: [`docs/security-history-cleanup.md`](docs/security-history-cleanup.md).

## Прошивка

- FQBN: `esp32:esp32:XIAO_ESP32S3:PSRAM=opi`
- Версия: поля **`fw_version`** / **`fw_build`** в `GET /telemetry`
- Привод: `GET /drive?l=-255..255&r=...`, `GET /drive?stop=1`, `GET /control?...&drive=0|1`

## Android

См. **`android/XiaoRobot/README.md`**. Телефон и XIAO в одной Wi‑Fi.

## Быстрый старт Wi‑Fi

1. Заполните `secrets.h`.
2. Соберите и прошейте `xiao_cam_stream`.

Portable **`tools/arduino-cli/`** в `.gitignore`; можно запустить `tools/bootstrap_arduino_cli.ps1`.

## Телеметрия по USB

```bash
py -3 tools/xiao_serial_telemetry.py --port COM5 --http 8897
```

http://127.0.0.1:8897/

## Прокси к камере по LAN

```bash
py -3 tools/xiao_cam_proxy.py <IP_платы>
```

http://127.0.0.1:8898/ — первая строка `camera_ip.txt` = IP платы.

## Архив переписки Cursor

**`docs/cursor-chat-archives/README.md`** — формат JSONL, имена файлов и ZIP.

## Публикация на GitHub

Имя репозитория, например **`denzhogzhuy-ardu`**.

Веб‑тренажёр — отдельно: https://github.com/soulhiler/IT
