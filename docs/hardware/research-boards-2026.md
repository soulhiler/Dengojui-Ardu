# Исследование: платы для робота «Деньгожуй» — MCU и SBC (2026)

> Обзорно-сравнительный отчёт по двум классам плат (микроконтроллеры и одноплатные
> Linux-компьютеры) под мобильного робота. Приоритеты проекта: on-board AI/зрение,
> ROS/робототехника, камера/видео, питание/батарея. Метод — глубокое исследование
> с веб-поиском по 5 направлениям и перекрёстной проверкой ключевых фактов.
> Дата: июнь 2026.
>
> **Статус:** обзор-основание. Конкретные кандидаты позже разобраны детальнее —
> [candidate-1-budget-compute.md](candidate-1-budget-compute.md) (бюджетный тир) и
> [orange-pi-lineup-2026.md](orange-pi-lineup-2026.md) (линейка Orange Pi).

## Контекст и задача

Робот «Деньгожуй» сейчас — single-chip на **XIAO ESP32-S3 Sense** (камера OV2640 по
DVP + MJPEG, PDM-микрофон, привод TB6612 с энкодерами, Wi-Fi/BLE, прошивка на Arduino).
Цель отчёта — понять рынок плат, где граница между микроконтроллерами (MCU) и
одноплатными микрокомпьютерами (SBC), и что потенциально подойдёт как «мозг» или
компаньон при развитии проекта (зрение/AI, ROS, качественное видео).

**Главный вывод сразу:** для робота с компьютерным зрением и ROS индустриальный
стандарт — **гибрид MCU + SBC**: микроконтроллер держит реальное время (моторы,
энкодеры, PID), а одноплатник на Linux тянет зрение/навигацию/сеть. Ни один MCU не
тянет полноценный YOLO; ни один SBC не даёт надёжного realtime-управления моторами.

---

## 1. MCU vs SBC: где граница

Принципиальное отличие — наличие **MMU и полноценной ОС Linux**.

| | Микроконтроллер (MCU) | Одноплатник (SBC) |
|---|---|---|
| Примеры | ESP32-S3/P4, RP2040/2350, Teensy, STM32 | Raspberry Pi, Orange Pi, Radxa, Jetson |
| ОС | bare-metal / RTOS, один бинарник | Linux (Ubuntu/Debian): процессы, ФС, SSH |
| Память | КБ–единицы МБ SRAM/PSRAM | ГБ DDR |
| Софт зрения/AI | TinyML (96×96), нет YOLO | OpenCV/PyTorch/YOLO из коробки |
| ROS2 | только **micro-ROS** | полноценный ROS2 + Nav2/SLAM |
| Realtime GPIO/PWM | детерминированный, жёсткий | джиттер (Linux не realtime) |
| АЦП | есть | у Raspberry Pi нет вовсе |
| Загрузка | миллисекунды | 10–30+ секунд |
| Питание | мВт; сон — мкА | 0.6–15 Вт |
| Сбой питания | переживает | риск повреждения ФС |
| Цена | $1–15 | $8–250+ |

**Вывод:** всё, что должно тикать с гарантированной частотой или экономить каждый мА —
на MCU; всё, что требует ОС/AI/сети — на SBC. Проверенная аналогия — **Klipper**
(3D-печать): Pi считает логику, MCU генерирует реальные импульсы.

---

## 2. Классы и типы плат

### MCU-класс (микроконтроллеры)

| Плата / SoC | CPU | RAM/PSRAM | Радио | Камера | HW-видеокодек | Цена 2026 |
|---|---|---|---|---|---|---|
| **ESP32-S3** | 2×LX7 240 МГц | +до 8–16 МБ PSRAM | Wi-Fi 4, BLE 5 | DVP | JPEG (сенсор) | ~$8–15 |
| **ESP32-P4** (+C6) | 2×RISC-V 360 МГц | +до 32 МБ PSRAM | через компаньон C6 (Wi-Fi 6) | **MIPI-CSI** + DVP | **H.264 1080p30** + JPEG, ISP | EV-Board ~$55; M5 ~$21 |
| **ESP32-C6** | RISC-V 160 МГц | 512 КБ | Wi-Fi 6, BLE, 802.15.4 | нет | нет | ~$5–10 |
| **RP2040** (Pico/W) | 2×M0+ 133 МГц | 264 КБ | W: Wi-Fi 4+BLE | нет (PIO) | нет | $4–6 |
| **RP2350** (Pico 2/W) | 2×M33 / RISC-V 150 МГц | 520 КБ | W: Wi-Fi 4+BLE | нет (PIO) | нет | $5–7 |
| **Teensy 4.0/4.1** | M7 600 МГц | 1 МБ | нет | нет | нет | $24–38 |
| **STM32H7** | M7 ≤480 МГц | +внешн. SDRAM | нет | DCMI | JPEG (часть H7) | чип $8–15 |
| **XIAO ESP32-S3 Sense** (текущая) | 2×LX7 240 МГц | 8 МБ PSRAM | Wi-Fi 4, BLE 5 | OV2640 (→OV5640), microSD, мик | JPEG (сенсор) | ~$14 |
| **ESP32-CAM** | 2×LX6 240 МГц | 4 МБ PSRAM | Wi-Fi 4, BT | OV2640, microSD | JPEG (сенсор) | ~$6–10 |

Сильные стороны MCU как «моторного мозга»: Teensy 4.x — **4 аппаратных квадратурных
энкодера + 32 PWM-таймера**; STM32 advanced-timers для FOC; RP2040/2350 PIO идеален
для энкодеров/шаговиков.

### SBC-класс (Linux-одноплатники)

| Плата / SoC | CPU | NPU | HW-видео (encode) | MIPI-CSI | Питание (idle/load) | Цена 2026 |
|---|---|---|---|---|---|---|
| **Pi Zero 2 W** | 4×A53 1 ГГц | нет | слабый | 1× | ~0.6 / 2.5 Вт | ~$15 |
| **Pi 4** | 4×A72 1.8 ГГц | нет | **H.264 1080p30** | 1× | ~3 / 6.5 Вт | ~$45+ |
| **Pi 5** | 4×A76 2.4 ГГц | нет (внешн. Hailo) | **нет HW-encode!** (софт) | 2× | ~3 / 8.8 Вт | 1GB ~$45; 16GB ~$205–305 |
| **Orange Pi 5 / Plus** | RK3588(S) | **6 TOPS** | H.264/H.265/AV1 8K | до 2× | ~3–5 / 8–12 Вт | 8GB ~$120–180+ |
| **Radxa Rock 5B/5C** | RK3588(S2) | **6 TOPS** | H.264/H.265 8K | 2× | ~3–5 / 10–15 Вт | 5C от ~$50; 5B 8GB $157+ |
| **Radxa Zero 3W** | RK3566 | ~1 TOPS | H.265 1080p60 | 1× | ~1 / 3–4 Вт | $15–66 |
| **Luckfox Pico (Max/Ultra)** | RV1106 A7+RISC-V | **0.5–1 TOPS** | H.264/H.265 (ISP) | 1× | ~0.3–1.5 Вт | ~$13–30 |
| **Milk-V Duo 256M** | SG2002 A53+RISC-V | **1 TOPS** | H.264/H.265 (ISP) | 1× | ~0.2–1 Вт | ~$8 |
| **BeagleY-AI** | TI AM67A | **4 TOPS** | HW video/vision | **2×** | ~3 / 8 Вт | ~$70–75 |
| **Khadas VIM4** | A311D2 | **3.2 TOPS** | HW enc, HDMI-in | 1× | ~3 / 9 Вт | ~$220 |
| **Jetson Orin Nano Super** | 6×A78 | **67 TOPS** (GPU) | **нет NVENC!** (софт) | до 4× | 7–25 Вт | **$249** |
| **Jetson Orin NX 16GB** | 8×A78 | **100 TOPS** | **NVENC** H.264/265/AV1 | до 6× | 10–25 Вт | $699–969 (модуль) |

> ⚠️ **У Raspberry Pi нет встроенного NPU ни в одной модели** — для AI ставят внешний
> Hailo через PCIe/HAT. У RK3588-плат, BeagleY-AI, Khadas, Jetson — NPU/GPU на борту.

---

## 3. Приоритет: On-board AI / зрение

Реальные бенчмарки (FPS на YOLO 640×640, INT8):

| Плата | TOPS | Реальный FPS на YOLO | Цена | Потребление |
|---|---|---|---|---|
| **Jetson Orin Nano Super** | 67 | YOLOv8n ~80–120; v8s ~50–60; v8m ~25–30 | $249 | 7–25 Вт |
| **Jetson Orin NX 16GB** | 100 | YOLOv8n ~65 (15 мс); v8s ~60 | $699+ | 10–25 Вт |
| **Pi 5 + AI HAT+ (Hailo-8)** | 26 | пик NPU v8n ~431/v8s ~491; **реально на видео 30–60** | ~$110+Pi | ~5 Вт (Hailo) |
| **Pi 5 + AI Kit (Hailo-8L)** | 13 | v8s ~30 | ~$70+Pi | ~2.5 Вт |
| **RK3588** (Orange Pi 5 / Rock 5) | 6 | YOLOv5s ~54 пик / ~30 реально | $90–180 | 5–10 Вт |
| **Luckfox (RV1106)** | 0.5–1 | единицы FPS, лёгкая детекция | ~$10–25 | <2 Вт |
| **Google Coral** | 4 | MobileNet-SSD ~46; YOLO плохо | $59–79 | ~2 Вт |
| **ESP32-S3** (MCU) | — | детекция лиц ~10; espdet_pico >7 @224×224 | $5–15 | <1 Вт |

**Что реально работает для детекции в реальном времени на роботе:**
- **Топ по производительности+экосистеме:** Jetson Orin Nano Super — CUDA/TensorRT, любые YOLO без боли с конвертацией, запас под 30 FPS + SLAM параллельно.
- **Лучшая энергоэффективность + ROS:** Pi 5 + Hailo-8 AI HAT+. **Грабли (важно):** PCIe по умолчанию Gen2 — нужно вручную включать **Gen3** в `config.txt`, иначе FPS падает почти вдвое; «официальные» 400+ FPS — чистый NPU, на живом видео с NMS реально 30–60.
- **Автономный Linux без NVIDIA:** RK3588. **Грабли:** неподдерживаемые NPU операторы падают на CPU → +500% латентности; нужны готовые RKNN-сборки.
- **MCU (ESP32-S3): только TinyML** — распознавание присутствия/лиц/жестов на 96×96…240×240, не многоклассовый YOLO.

---

## 4. Приоритет: ROS/робототехника + архитектура

**ROS2 (Humble/Jazzy)** работает на SBC (Pi 4/5, Jetson, RK3588). На MCU полный ROS2
не идёт (нужны динамическая память и DDS) — там **micro-ROS** (rclc + Micro-XRCE-DDS,
клиент <75 КБ Flash / ~3 КБ RAM, поверх serial/UDP; на SBC крутится micro-ROS Agent).

**Почему моторику отдают MCU:** Linux не realtime — софтовый ШИМ даёт джиттер, прерывания
энкодеров ненадёжны, аппаратных PWM-пинов мало. У Raspberry Pi по сути один HW-PWM.

### Рекомендуемая архитектура ROS-робота (SBC + MCU)

```
┌─ ВЕРХ: SBC (Pi 5 / Jetson Orin / RK3588) ─ Ubuntu + ROS2 ──┐
│  Nav2, SLAM (slam_toolbox/Cartographer), зрение/AI (NPU),  │
│  видео-пайплайн камера→GStreamer→HW-энкодер→WebRTC/RTSP     │
└───────────────── micro-ROS Agent / serial ─────────────────┘
                              │ UART / USB / UDP
┌─ НИЗ: MCU (ESP32 / Teensy / STM32 / RP2040) ─ realtime ─────┐
│  ШИМ моторов, энкодеры по прерываниям, PID, IMU, авар.стоп  │
└─────────────────────────────────────────────────────────────┘
```

Готовые стеки этой схемы: **linorobot2** (Nav2+SLAM на SBC + ESP32/Teensy/RP2040 motor-FW),
**Roboost**, **Rosmo**, Waveshare **UGV Beast ROS2** (Pi + ESP32), Yahboom **MicroROS-Pi5**
(Pi5 + ESP32-S3 co-processor).

---

## 5. Приоритет: Камера/видео

Критичные нюансы аппаратного кодирования:

- ⚠️ **У Raspberry Pi 5 НЕТ аппаратного H.264-энкодера вообще** (есть только HW-декод
  H.265). Кодирование — софтом libx264 на CPU: 1080p30 ~60% одного ядра. У **Pi 4**
  наоборот — есть HW H.264 1080p30 encode. Это разворот: для записи/стрима видео Pi 4
  иногда выгоднее Pi 5.
- ⚠️ **У Jetson Orin Nano вырезан NVENC** — кодирование софтом (до 71 FPS ultrafast /
  24 FPS medium @10 Мбит). Аппаратный NVENC есть только у **Orin NX / AGX Orin**.
- ✅ **RK3588** — мощный VPU: H.264/H.265 encode до 8K@30, до 16 каналов 1080p30. Лучший
  по видео среди доступных SBC.
- ✅ **ESP32-P4** — аппаратный H.264 1080p30 + MIPI-CSI + ISP: законченное IP-камера-решение на MCU.

**Камеры:** MIPI-CSI (низкая задержка, прямо в RAM) vs USB-UVC (проще, но +задержка).
Pi 5 / RK3588 / BeagleY-AI дают 2× CSI (стерео/обзор).

**Задержка видео (glass-to-glass):** аналоговый FPV ~20 мс; WebRTC на роботах ~150–180 мс
по сети, <80 мс в локалке; основной вклад — сама камера и USB-шина (~100 мс), не WebRTC.
ESP32-S3+OV2640: VGA JPEG ~15–25 FPS, QVGA ~25–30 FPS, 100–300 мс локально; проект
`esp32-cam-fpv` выжимает 34–44 FPS @50–80 мс собственной радиолинковкой (не обычный Wi-Fi).

---

## 6. Приоритет: Питание/батарея

| Плата | Idle | Под нагрузкой | Пик | БП |
|---|---|---|---|---|
| ESP32-S3 | ~24 мА (active), ~8 мкА (deep-sleep)* | 70–120 мА (Wi-Fi) | ~350 мА (TX) | 5V/0.5A |
| Pi Zero 2 W | ~0.6 Вт (~100 мА) | ~2.5 Вт | ~500 мА | 5V/1.2A |
| Pi 4 | ~3 Вт | ~6.4 Вт | ~1.25 A | 5V/3A |
| Pi 5 | ~3 Вт | ~8.8 Вт | **до 3.4 A** | **5V/5A (27 Вт) PD** |
| Jetson Orin Nano | ~5 Вт | 7–15 Вт (до 25 Super) | ~3 A+ | 5V/4A barrel |

\* *Цифры deep-sleep из даташита недостижимы на готовых девкитах — регулятор/USB-мост/LED жгут 5–15 мА. Во время стрима камеры экономии нет — закладывайте сотни мА постоянно.*

⚠️ **Pi 5 — отдельный кейс:** официально требует **5V/5A**; на обычном 5V/3A он
ограничивает USB до 600 мА (USB-камеры могут не определяться), пики до 3.4 A. На
повербанке/Li-ion дешёвые DC-DC часто не держат пики → brownout/ребут.

**Размер/вес:** Pi Zero 2 W (65×30 мм, ~11 г) — легчайший; Pi 4/5 — 85×56 мм (~46 г);
Jetson Orin Nano — крупный, нужен активный кулер; Milk-V Duo/Luckfox — граммы.

---

## 7. Реальный опыт: OSS-проекты и грабли

**Проекты-ориентиры (GitHub/Hackster):**
- **SunFounder PiCar-X** (Pi + Robot HAT) — AI-машинка, pan/tilt-камера, line/face/sign detection. Грабли: рассинхрон `robot-hat` ↔ примеры, неверные PWM-константы серв (исправлено в форках).
- **Donkey Car** (Pi 3/4 или Jetson Nano) — self-driving RC, deep-learning автопилот. Грабли: всплески лага управления по Wi-Fi до 1–30 с; малая Li-Po садится на неровностях.
- **NVIDIA JetBot / JetRacer** (Jetson Nano) — collision avoidance, road following. Грабли: токовый бюджет (Nano в 5W-режим), неправильный micro-USB кабель блокирует Ethernet.
- **RoboFoundry «Cheapest ROS2 Robot»** (ESP32 ~$6, опц. Pi) — diff-drive micro-ROS без дорогого SBC. Боль — заставить micro-ROS работать поверх Wi-Fi/UDP.
- **linorobot2** (ESP32/Teensy/RP2040 + SBC) — эталонный гибридный стек ROS2; много форков под конкретные ESP32 из-за настройки micro-ROS transport.
- **Luckfox Pico** vision (RV1106 + SC3336, ~$23) — дешёвый Linux+NPU узел, `opencv-mobile`. Грабли: USB-камера ≠ CSI (нужен V4L2), SDK только Ubuntu 22.04.

**Сквозные грабли реального опыта:**
- ESP32 + Wi-Fi не тянет несжатое видео; FPV-проекты пишут свою радиолинковку.
- OV2640 даёт битые кадры при `quality < 8`; RGB только на низком разрешении (мало RAM).
- micro-ROS поверх Wi-Fi/UDP — нетривиальная настройка transport.
- Pi 5 без 5A-БП троттлит USB; Hailo без PCIe Gen3 теряет половину FPS.

---

## 8. Рыночный контекст 2026 (важно для цен)

С конца 2025 — острый **дефицит DRAM** (датацентры скупают HBM). Контрактные цены DRAM
выросли на ~90–95% в Q1 2026. Итог: платы с памятью ≥2 ГБ резко подорожали. **Pi 5 16GB:
MSRP $120 → ~$205–305** (повышения дек.2025/фев.2026/апр.2026). Введён бюджетный **Pi 5
1GB за $45**. Платы с распаянной памятью (Pi Zero 2 W, Luckfox, Milk-V) подорожали мало.
**Вывод по ценам:** при планировании бюджета закладывайте волатильность; RK3588/Pi 5 с
большой RAM сейчас дороже исторического MSRP, цены сильно зависят от продавца.

---

## 9. Выводы и рекомендации для «Деньгожуй»

**Архитектурный принцип:** не искать одну «идеальную» плату, а строить **гибрид MCU + SBC**.
Текущий XIAO ESP32-S3 Sense отлично остаётся «спинным мозгом» (моторы, энкодеры, мик,
realtime) — его не нужно выбрасывать.

**Кандидаты в «мозг»/компаньон для зрения — 3 варианта под разный аппетит:**

1. **Минимальный шаг, бюджетно, мобильно — Raspberry Pi Zero 2 W (~$15) или Luckfox/Milk-V (~$8–25).**
   Pi Zero 2 W: лёгкий (11 г), ~0.6 Вт, полноценный Linux+OpenCV, USB-связь с ESP32. Подходит
   для базового зрения и WebRTC-стрима без тяжёлого AI. Luckfox/Milk-V — копеечный Linux+NPU
   узел «камера+0.5–1 TOPS» для лёгкой детекции прямо на краю.

2. **Сбалансированный, под ROS2 + реальное зрение — Raspberry Pi 5 + AI HAT+ (Hailo-8, 26 TOPS).**
   ~5 Вт у ускорителя, отличная экосистема Pi/ROS2, YOLO реально 30–60 FPS. Минусы: нужен
   **5V/5A** БП, нет HW H.264-энкодера (видео — софтом или вынести на ESP32-P4), цена RAM
   плавает. Альтернатива без NVIDIA/Hailo — **RK3588** (Orange Pi 5 / Radxa Rock 5C, 6 TOPS,
   + мощный 8K-видеокодек) — лучший по видео, но сложнее конвертация моделей.

3. **Максимальная производительность AI — NVIDIA Jetson Orin Nano Super ($249, 67 TOPS).**
   Если автономия/несколько камер/тяжёлые сети: CUDA/TensorRT, любые YOLO 50–120 FPS.
   Минусы: 7–25 Вт, активное охлаждение, крупнее, **нет NVENC** (видео кодируется софтом).

**Если хочется остаться в MCU-мире** (без Linux, ближе к нынешней Arduino-прошивке) —
единственный осмысленный апгрейд это **ESP32-P4 + C6** (MIPI-CSI + аппаратный H.264 1080p30),
но это не даёт настоящего CV/YOLO и тащит за собой бета-экосистему (см. отдельный разбор
[esp32p4c61-tiny.md](esp32p4c61-tiny.md)).

**Практический путь для проекта:**
- Шаг 1 (дёшево, быстро): ESP32-S3 остаётся, добавить **Pi Zero 2 W** по USB как Linux-компаньон
  для зрения/WebRTC. Освоить связку MCU↔SBC и (опц.) micro-ROS.
- Шаг 2 (если зрение в реальном времени станет ядром): перейти на **Pi 5 + Hailo** или
  **RK3588**, ESP32 оставить как motor-контроллер по micro-ROS.
- Jetson — только при серьёзной автономии с запасом по питанию.

---

## Источники

**Камера/видео/кодеки:**
- Raspberry Pi — [H.264 encoding performance on Pi 5 (whitepaper)](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-010033-WP-1-H.264%20encoding%20performance%20on%20Raspberry%20Pi%205_series%20computers.pdf), [Camera docs](https://www.raspberrypi.com/documentation/computers/camera_software.html); [HN: Pi 5 no HW video encode](https://news.ycombinator.com/item?id=38068801)
- NVIDIA — [Software Encode in Orin Nano (нет NVENC)](https://docs.nvidia.com/jetson/archives/r36.2/DeveloperGuide/SD/Multimedia/SoftwareEncodeInOrinNano.html), [Orin NX HW encode](https://forums.developer.nvidia.com/t/hardware-encode-question/297159)
- Rockchip RK3588 — [datasheet (8K VPU)](https://wiki.friendlyelec.com/wiki/images/e/ee/Rockchip_RK3588_Datasheet_V1.6-20231016.pdf), [CNX](https://www.cnx-software.com/2021/12/16/rockchip-rk3588-datasheet-sbc-coming-soon/)
- Espressif — [ESP32-P4](https://www.espressif.com/en/products/socs/esp32-p4); [esp32-cam-fpv (реальный FPS/задержка)](https://github.com/jeanlemotan/esp32-cam-fpv)
- [Transitive Robotics — WebRTC latency breakdown](https://transitiverobotics.com/blog/webrtc-latency-breakdown/), [RidgeRun — Jetson glass-to-glass](https://developer.ridgerun.com/wiki/index.php?title=Jetson_glass_to_glass_latency)

**AI/NPU/бенчмарки:**
- NVIDIA — [Jetson Orin Nano Super (67 TOPS, $249)](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/nano-super-developer-kit/), [Orin NX 100 TOPS](https://www.notebookcheck.net/NVIDIA-Jetson-Orin-NX-16-GB-Module-launches-worldwide-with-100-TOPS-AI-performance.687003.0.html); [MDPI — YOLOv8 on Orin NX](https://www.mdpi.com/2073-431X/15/2/74)
- Seeed — [YOLOv8 on RPi5 + AI Kit бенчмарк](https://wiki.seeedstudio.com/benchmark_on_rpi5_and_cm4_running_yolov8s_with_rpi_ai_kit/); [Forum — AI HAT+ деградация FPS / PCIe Gen3](https://forums.raspberrypi.com/viewtopic.php?t=392015)
- [RK3588 NPU бенчмарки](https://tinycomputers.io/posts/rockchip-rk3588-npu-benchmarks.html); [Coral Edge TPU benchmarks](https://www.coral.ai/docs/edgetpu/benchmarks/); Espressif [ESP-DL](https://github.com/espressif/esp-dl) / [esp-detection](https://github.com/espressif/esp-detection)

**ROS / micro-ROS / архитектура:**
- [ROS2 Jazzy on Raspberry Pi](https://docs.ros.org/en/jazzy/How-To-Guides/Installing-on-Raspberry-Pi.html); [micro-XRCE-DDS](https://micro.ros.org/docs/concepts/middleware/Micro_XRCE-DDS/); [Bosch — micro-ROS](https://www.bosch.com/stories/bringing-robotics-middleware-onto-tiny-microcontrollers/)
- [Pi GPIO servo jitter](https://forums.raspberrypi.com/viewtopic.php?t=296543)

**SBC/MCU спецификации и цены:**
- [Raspberry Pi 5](https://www.raspberrypi.com/products/raspberry-pi-5/), [Pi Zero 2 W power](https://www.cnx-software.com/2021/12/09/raspberry-pi-zero-2-w-power-consumption/); [DRAM-кризис: Tom's Hardware](https://www.tomshardware.com/raspberry-pi/raspberry-pi-5-price-increases-drastically-as-ai-shortage-bites-16gb-version-now-usd205-second-price-increase-in-three-months-over-70-percent-more-expensive-than-original-msrp), [raspberrypi.com news](https://www.raspberrypi.com/news/more-memory-driven-price-rises/), [The Register](https://www.theregister.com/2026/02/02/raspberry_pi_ram_shortage_price_hike/)
- Orange Pi 5 Plus, Radxa [Rock 5B](https://docs.radxa.com/en/rock5/rock5b/getting-started/introduction) / [Rock 5C](https://www.cnx-software.com/2024/05/18/radxa-rock-5c-lite-sbc-rockchip-rk3588s2-rk3582-soc-wifi-6-raspberry-pi-pcie-ffc-connector/), [Luckfox Pico](https://www.cnx-software.com/2024/02/29/luckfox-pico-pro-pico-max-rockchip-rv1106-boards-100m-ethernet-5mp-camera/), [Milk-V Duo 256M](https://www.electronics-lab.com/milk-v-duo-256m-is-an-sg2002-powered-multi-architecture-sbc-priced-at-7-99/), [BeagleY-AI](https://www.cnx-software.com/2024/03/28/beagley-ai-sbc-features-ti-am67a-vision-processor-with-4-tops-ai-accelerators/), [Khadas VIM4](https://www.khadas.com/vim4)
- Espressif [ESP32-S3](https://www.espressif.com/en/products/socs/esp32-s3) / [ESP32-C6](https://www.espressif.com/en/products/socs/esp32-c6); [RP2350/Pico 2](https://www.raspberrypi.com/news/rp2350-the-brains-of-raspberry-pi-pico-2/); [Teensy 4.1](https://www.pjrc.com/store/teensy41.html)

**OSS-проекты роботов:**
- [SunFounder PiCar-X](https://github.com/sunfounder/picar-x), [Donkey Car](https://github.com/autorope/donkeycar) ([лаг Wi-Fi](https://github.com/autorope/donkeycar/issues/46)), [NVIDIA JetBot](https://github.com/NVIDIA-AI-IOT/jetbot) ([питание](https://github.com/nvidia-ai-iot/jetbot/wiki/hardware-setup)), [JetRacer](https://github.com/NVIDIA-AI-IOT/jetracer)
- [linorobot2](https://github.com/linorobot/linorobot2) + [hardware (ESP32)](https://github.com/hippo5329/linorobot2_hardware), [RoboFoundry ESP32 ROS2](https://robofoundry.medium.com/building-cheapest-ros2-robot-using-esp32-part-1-hardware-build-af0044de68ce), [Waveshare UGV Beast ROS2](https://www.waveshare.com/ugv-beast-ros2-kit.htm), [Yahboom MicroROS-Pi5](https://category.yahboom.net/products/microros-pi5)
- [ESP32 diff-drive micro-ROS (Hackster)](https://www.hackster.io/amal-shaji/differential-drive-robot-using-ros2-and-esp32-aae289), [Luckfox vision SDK](https://github.com/LuckfoxTECH/luckfox-pico)

**Питание:**
- [Raspberry Pi power consumption 2026](https://raspberry.tips/en/raspberrypi-tutorials/raspberry-pi-power-consumption-update-2026-all-models-compared), [Jetson Orin Nano power](https://edgeaistack.ai/blog/jetson-orin-nano-power-consumption/), [ESP32-S3 current](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/current-consumption-measurement-modules.html)

---

*Примечание о достоверности: ключевые «контринтуитивные» факты (отсутствие HW H.264-энкодера
у Pi 5, вырезанный NVENC у Orin Nano, ценовой скачок из-за DRAM-дефицита) подтверждены
несколькими независимыми источниками, включая первоисточники (whitepaper Raspberry Pi,
документация NVIDIA). Пиковые FPS NPU (Hailo 400+, RK3588 54) — чистый ускоритель при
batch=1; реальный сквозной FPS на роботе в 1.5–3× ниже. Розничные цены 2026 волатильны
из-за дефицита памяти.*
