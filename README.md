# Деньгожуй — XIAO ESP32-S3 Sense

Отдельный проект: прошивка камеры/телеметрии, скрипты на ПК (прокси, OTA, телеметрия по USB/BLE).  
Не смешивается с репозиторием веб‑тренажёра «Интеллект‑Трен» (`IT` на GitHub).

## Состав

| Путь | Назначение |
|------|------------|
| `xiao_cam_stream/` | Основной скетч: Wi‑Fi, MJPEG, телеметрия, опционально BLE/PDM/OTA |
| `xiao_ble_mic_test/` | Тест BLE и PDM‑микрофона |
| `xiao_cam_viewer.html` | Подсказка по просмотру потока |
| `tools/xiao_cam_proxy.py` | Прокси с платы на `localhost` (для Cursor/браузера) |
| `tools/xiao_serial_telemetry.py` | Телеметрия: Serial / HTTP / BLE |
| `tools/xiao_flash_and_telemetry.ps1` | Прошивка по USB + HTTP‑телеметрия |
| `tools/xiao_wifi_ota.ps1` | Сборка и заливка по Wi‑Fi (ArduinoOTA) |

Скопируйте `xiao_cam_stream/secrets.h.example` → `xiao_cam_stream/secrets.h` (файл в `.gitignore`).

## Быстрый старт Wi‑Fi

1. Заполните `secrets.h` (пароль Wi‑Fi и при необходимости OTA).
2. Соберите и прошейте `xiao_cam_stream` (Arduino IDE / `arduino-cli`).

Для скриптов PowerShell ожидается portable **`tools/arduino-cli/arduino-cli.exe`** (см. `.gitignore` — каталог не в Git; скачайте `arduino-cli` и положите в `tools/arduino-cli/`).

## Телеметрия по USB

```bash
py -3 tools/xiao_serial_telemetry.py --port COM5 --http 8897
```

Откройте http://127.0.0.1:8897/

## Прокси к камере по LAN

```bash
py -3 tools/xiao_cam_proxy.py <IP_платы>
```

По умолчанию http://127.0.0.1:8898/  
Опционально: первая строка `camera_ip.txt` в корне этого проекта — IP платы.

## Публикация на GitHub

Имя репозитория — на латинице, например **`denzhogzhuy-ardu`**. В описании репозитория можно указать: **Деньгожуй — XIAO камера/телеметрия**.

```bash
git init
git add -A
git commit -m "Initial commit"
git remote add origin https://github.com/<логин>/denzhogzhuy-ardu.git
git branch -M main
git push -u origin main
```

Веб‑тренажёр «Глобал Т.Э.М.П.» — в другом репозитории: https://github.com/soulhiler/IT
