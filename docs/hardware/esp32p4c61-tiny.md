# Wireless-Tag ESP32P4C61-TINY

> ESP32-P4 (вычислитель) + ESP32-C61 (радио), краудфандинг. Вердикт: для текущего
> робота избыточно и преждевременно — остаться на XIAO ESP32-S3 Sense.

## Что это

Открытая AIoT-плата на модуле **WT01P461-S1**, объединяющем два RISC-V SoC:

- **ESP32-P4** — вычислитель: 2× RISC-V @400 МГц, FPU + AI-расширения, до **32 МБ
  PSRAM**, 16 МБ flash, **MIPI-CSI/DSI**, аппаратный **H.264-энкодер**, JPEG-кодек,
  ISP (до 1080p). **Собственного радио нет.**
- **ESP32-C61** — компаньон-радио: 1× RISC-V, **Wi-Fi 6 + BLE 5**, 320 КБ SRAM,
  без 802.15.4. Подключён к P4 (SDIO/SPI, ESP-Hosted).

Плата: 69×33 мм, 2× USB-C, слот microSD, разъёмы MIPI-CSI (камера) и MIPI-DSI
(дисплей), много выведенных GPIO. Kickstarter: ~$40 (super early bird, MSRP
$81.50); в комплекте 2.8" MIPI-дисплей, MIPI-камера, антенна, гребёнки, USB-C.
Отгрузка — ориентировочно июль 2026.

## Требования проекта

Из `xiao_cam_stream/xiao_cam_stream.ino` и `drive_config.h`:

- Камера OV2640 по **DVP** через `esp_camera` (initCamera, пины 10–18/38–48),
  MJPEG `/stream`, снимок `/capture`.
- PDM-микрофон (ESP_I2S, GPIO42/41) → сырой PCM по TCP `:81`.
- Привод TB6612: 6 пинов PWM/dir + 4 пина энкодеров.
- Wi-Fi STA (HTTP-сервер, телеметрия, `/control`, `/drive`), BLE-телеметрия, OTA.
- Всё на **Arduino** (arduino-esp32, arduino-cli).
- Малый форм-фактор (робот), питание от 3.7 В аккумулятора.

## Плюсы

- Аппаратный **H.264** → настоящий видеопоток (RTSP/H.264) вместо MJPEG: в разы
  меньше трафика при лучшем качестве, до 1080p.
- ISP + JPEG-кодек, 32 МБ PSRAM — мощный пайплайн камеры и задел под **edge-AI**
  (детекция/зрение на борту робота).
- **Wi-Fi 6 + BLE 5** (новее Wi-Fi 4 / BLE 5.0 у S3), microSD на борту, много GPIO.

## Минусы и риски

- **Arduino-поддержка ESP32-P4 — только бета (2026).** Проект целиком на Arduino.
  MIPI-CSI камера, H.264, DSI требуют ESP-IDF, не Arduino.
- **MIPI-CSI ≠ DVP.** Весь код `esp_camera` (DVP, пины OV2640) не переносится;
  нужна другая камера/разъём и другой стек.
- **Двухчиповая архитектура** (P4 + C61): Wi-Fi через компаньон (ESP-Hosted) —
  сложнее сетевой стек, чем у single-chip S3.
- PDM-микрофон, BLE-телеметрия, карта пинов привода — всё переписывать под новый
  GPIO-map.
- **Краудфандинг:** отгрузка только июль 2026, экосистема незрелая, нет
  проверенных Arduino-примеров камеры.
- Крупнее (69×33 против 21×17.5 мм XIAO) и, вероятно, прожорливее.

## Аналоги

По возрастанию сложности перехода:

1. **Остаться на XIAO ESP32-S3 Sense** (текущая) — зрелый single-chip, Arduino,
   камера+мик+SD, крошечный. Лучший выбор под текущие цели.
2. **Апгрейд в рамках S3 / DVP / Arduino:** XIAO с **OV5640 (5 Мп)**, либо
   ESP32-S3-EYE / Freenove ESP32-S3-CAM — минимальная переделка кода, выше
   разрешение.
3. **Если реально нужен класс P4 и готовы на ESP-IDF:** **Espressif ESP32-P4-EYE**
   (официальная, vision-focused, хорошая документация), **DFRobot FireBeetle 2
   ESP32-P4** (компаньон ESP32-C6, Wi-Fi 6, wiki/Arduino) или **Waveshare
   ESP32-P4-NANO**. Все отгружаются сейчас, экосистема зрелее WT-платы.
4. **На радар:** **ESP32-S31** (анонс: 2× RISC-V, Wi-Fi+BT+Ethernet+802.15.4,
   возможный апгрейд XIAO).

## Вердикт и рекомендация

Для текущего «Деньгожуй» — **оставить XIAO ESP32-S3 Sense**. Он закрывает все
задачи одним Arduino-дружелюбным чипом.

ESP32-P4 рассматривать как путь **«v2»** только при добавлении edge-AI/зрения,
H.264-видео, 1080p или дисплея. При переходе на P4 выбрать **Espressif
ESP32-P4-EYE** или **DFRobot FireBeetle-2 ESP32-P4**, а не Kickstarter-плату WT
(отгружаются сейчас, лучше Arduino/IDF-поддержка). Не завязывать рабочий проект
на краудфандинговое железо.

## Источники

- CNX Software — [WT ESP32P4C61-TINY combines ESP32-P4 + ESP32-C61](https://www.cnx-software.com/2026/06/02/wireless-tag-esp32p4c61-tiny-board-combines-esp32-p4-esp32-c61/)
- Kickstarter — [ESP32-Cx TINY / Wireless-Tag](https://www.kickstarter.com/projects/c-h/the-esp32-cx-tiny)
- Espressif — [ESP32-P4 SoC](https://www.espressif.com/en/products/socs/esp32-p4), [ESP32-P4-EYE user guide](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32p4/esp32-p4-eye/user_guide.html)
- arduino-esp32 — [CSI Camera support ESP32-P4 (issue #11695)](https://github.com/espressif/arduino-esp32/issues/11695)
- DFRobot — [FireBeetle 2 ESP32-P4](https://www.dfrobot.com/product-2915.html); Waveshare — [ESP32-P4-NANO](https://www.waveshare.com/wiki/ESP32-P4-Nano-StartPage)
- Seeed — [XIAO ESP32-S3 Sense](https://www.seeedstudio.com/XIAO-ESP32S3-Sense-p-5639.html), [ESP32-S31 vs ESP32-S3](https://www.seeedstudio.com/blog/2026/04/14/esp32-s31-vs-esp32-s3-should-the-xiao-get-an-upgrade/)
