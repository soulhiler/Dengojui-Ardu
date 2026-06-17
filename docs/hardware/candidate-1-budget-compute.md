# Кандидат 1: бюджетно-мобильный Linux-компаньон (Pi Zero 2 W / Luckfox / Milk-V)

> Первый кандидат из обзора [research-boards-2026.md](research-boards-2026.md): добавить к
> текущему XIAO ESP32-S3 **маленький Linux-компьютер** для зрения/стрима/сети, оставив ESP32
> «спинным мозгом» (моторы, энкодеры, мик, realtime). Дёшево, легко, мало ест.
> Вердикт: лучший первый шаг к зрению — **Raspberry Pi Zero 2 W** (экосистема/простота);
> **Luckfox Pico Ultra W** — если нужен аппаратный H.264 + NPU в граммах; **Milk-V Duo** —
> абсолютный минимум размера/цены.

## Что это (и зачем в проекте)

Класс «micro-SBC»: полноценный Linux (Python, OpenCV, сеть) в форм-факторе чуть больше
самого ESP32. Не замена контроллеру, а **компаньон по USB/UART**: ESP32 продолжает крутить
привод и отдавать телеметрию, а Linux-плата берёт на себя то, что MCU не тянет — обработку
изображения, детекцию, WebRTC/RTSP-стрим, удобный сетевой стек, логику на Python.

## Требования проекта (чек-лист тира)

- Камера/зрение: своя CSI/USB-камера ИЛИ приём кадров. Цель — базовое CV (детекция движения,
  цвета, лица, простая детекция объектов) и низколатентный стрим. Полный YOLO 30 FPS — НЕ сюда.
- Связь с ESP32: USB-serial или UART (команды привода, телеметрия). Опц. micro-ROS.
- Wi-Fi: на борту (для управления/стрима).
- Питание: единицы ватт, лёгкий, от того же аккумулятора робота.

## Три подварианта

| | **Pi Zero 2 W** | **Luckfox Pico Ultra W** | **Milk-V Duo 256M** |
|---|---|---|---|
| SoC | BCM2710A1, 4×A53 1 ГГц | RV1106, 1×A7 + RISC-V | SG2002, A53 + RISC-V C906 |
| RAM | 512 МБ LPDDR2 | 256 МБ DDR3L | 256 МБ |
| **NPU** | **нет** | **0.5 TOPS int8 / 1 int4** | **1 TOPS int8** |
| ISP / HW-видеокодек | слабый (без HW H.264 enc) | **ISP3.2 5MP HDR + H.264/H.265 enc** | ISP + H.264/H.265 enc |
| Камера | 1× CSI (mini) | 1× MIPI-CSI | 1× MIPI-CSI |
| Радио | Wi-Fi 4, BT4.2 | **Wi-Fi 6, BT5.2** | нет (через модуль) |
| Сеть | нет Ethernet | опц. 100M/PoE | опц. 100M |
| ОС/софт | **Raspberry Pi OS / Ubuntu, огромная экосистема** | Buildroot/Ubuntu (RV1106 SDK) | Buildroot |
| Размер / вес | 65×30 мм, ~11 г | очень малый | ~21×26 мм, граммы |
| Питание | ~0.6 / 2.5 Вт | <2 Вт | ~0.2–1 Вт |
| Цена 2026 | ~$15 | ~$20–30 (W) | ~$8 |

## Что реально умеет каждый (по приоритетам)

### On-board AI / зрение
- **Pi Zero 2 W:** нет NPU, всё на 4×A53. TensorFlow Lite + MobileNet — **3–8 FPS**; SSD
  MobileNet V2 320×320 или EfficientDet-Lite 320 — для «>10 FPS» сценариев на пределе.
  С USB-ускорителем **Google Coral — 15–20 FPS**. Узкое место — **512 МБ RAM** (для AI впритык).
  Подходит: детекция движения/цвета/лиц, простая классификация, трекинг.
- **Luckfox / Milk-V:** есть **NPU (0.5–1 TOPS)** → YOLOv5 на низком разрешении — единицы FPS,
  лёгкая детекция/распознавание, плюс аппаратный кодек видео. Это «зрение на самом краю» за ~$10.
- **Вывод тира:** для лёгкого CV хватает всех; для чуть более серьёзной детекции выгоднее
  Luckfox/Milk-V (NPU) либо Pi Zero 2 W + Coral.

### Камера / видео
- **Pi Zero 2 W:** CSI-камера, MJPEG-стрим **1280×720@30 работает хорошо**; 1080p@30 реально
  ~15 FPS, задержка ~1–2 с на старом Zero W (на Zero 2 W за счёт 4 ядер заметно лучше). **Нет
  HW H.264-энкодера** → H.264 софтом грузит CPU; для низкой задержки практичнее MJPEG или WebRTC.
- **Luckfox / Milk-V:** есть **аппаратный H.264/H.265** + ISP — это их сильная сторона: качественный
  сжатый поток при копеечном CPU, законченное IP-камера-решение.
- **Вывод тира:** если важен именно качественный/сжатый видеопоток — Luckfox/Milk-V; если проще
  и «как у всех» (mjpg-streamer/picamera2) — Pi Zero 2 W.

### ROS / робототехника
- На всех троих полноценный ROS2 тяжёл (256–512 МБ RAM). Реалистично: лёгкий ROS2 на Pi Zero 2 W
  возможен, но впритык; **micro-ROS Agent** для связи с ESP32 — ок. Luckfox/Milk-V — скорее как
  vision-узел, чем ROS-хост.
- Моторику в любом случае оставляем на ESP32 (realtime).

### Питание / батарея
- Все три — единицы ватт, без проблем с питанием (в отличие от Pi 5 с его 5V/5A). Pi Zero 2 W
  ~0.6 Вт в простое; Luckfox/Milk-V — доли ватта. Лёгкие, для мобильной платформы идеальны.

## Как это встраивается в «Деньгожуй» (архитектура)

```
┌ Linux-компаньон (Pi Zero 2 W / Luckfox) ┐      камера → CV/детекция
│  Python + OpenCV, Wi-Fi, WebRTC/RTSP     │      стрим на телефон/ПК
│  (опц. micro-ROS Agent)                  │
└──────────── USB-serial / UART ───────────┘
                    │  команды /drive, приём телеметрии
┌ XIAO ESP32-S3 Sense (как сейчас) ────────┐
│  привод TB6612 + энкодеры, PDM-мик,       │  realtime, как есть
│  BLE-телеметрия                           │
└───────────────────────────────────────────┘
```

Минимальное изменение текущего кода: ESP32 уже отдаёт `/drive`, `/telemetry`, `/control` по
HTTP и PCM по TCP — Linux-плата может ходить к нему как обычный клиент по Wi-Fi, либо через
USB-serial добавить прямой канал. Камеру для зрения вешаем на Linux-плату (CSI/USB).

## Реальный опыт и грабли

- **Pi Zero 2 W, AI:** 512 МБ RAM — главный потолок; «надёжное object-tracking под вопросом»
  без оптимизации/ускорителя. Берите квантованные модели и низкое разрешение.
- **Pi Zero 2 W, видео:** USB 2.0 и CPU ограничивают FPS на высоком разрешении; выбирайте
  720p30 или 1080p15. Нет аппаратного H.264.
- **Luckfox:** USB-камера (UVC) **не идёт через CSI-функцию** — код адаптировать под V4L2/OpenCV;
  официальный SDK собирается/тестируется только на **Ubuntu 22.04**; одно ядро A7 — слабовато
  для тяжёлого общего Linux-софта (но NPU/ISP это компенсируют для зрения).
- **Milk-V Duo 256M:** нет встроенного радио — Wi-Fi через модуль; экосистема меньше, чем у Pi.
- Общий момент: micro-ROS поверх Wi-Fi/UDP — нетривиальная настройка transport (если пойдёте в ROS).

## Вердикт и рекомендация (внутри тира)

- **Дефолт — Raspberry Pi Zero 2 W (~$15).** Максимально простой вход в зрение/стрим: гигантская
  экосистема (Raspberry Pi OS, picamera2, OpenCV, mjpg-streamer, WebRTC), лёгкий, дешёвый,
  минимум сюрпризов. Берёт на себя CV-логику и сеть, ESP32 остаётся как есть. Для серьёзной
  детекции добавить USB-Coral.
- **Если нужен качественный сжатый видеопоток + NPU в граммах — Luckfox Pico Ultra W (~$20–30).**
  Аппаратный H.264/H.265 + ISP 5MP + 1 TOPS NPU + Wi-Fi 6. Платите за это более сложным SDK
  и слабым одним A7-ядром для общих задач.
- **Абсолютный минимум размера/цены — Milk-V Duo 256M (~$8).** Если плата нужна крошечная и
  только под одну vision-задачу; готовьтесь к Buildroot и внешнему Wi-Fi.

**Рекомендуемый первый шаг для проекта:** взять **Pi Zero 2 W**, подключить к ESP32-S3 по USB,
поднять камеру + WebRTC/MJPEG-стрим и базовую детекцию на OpenCV. Это даёт зрение и нормальный
стрим при минимальных затратах и без перестройки текущей прошивки. Если упрётесь в
производительность AI — следующий шаг это кандидат 2 (Pi 5 + Hailo / RK3588) из обзора.

## Источники

- Raspberry Pi — [Pi Zero 2 W (специф.)](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/), [power consumption](https://www.cnx-software.com/2021/12/09/raspberry-pi-zero-2-w-power-consumption/)
- Pi Zero 2 W зрение/AI — [Real-Time Object Detection on Pi Zero 2W (ResearchGate)](https://www.researchgate.net/publication/399120906_Real-Time_Object_Detection_Using_Raspberry_Pi_Zero_2W_An_Optimized_Approach), [forum: object detection](https://forums.raspberrypi.com/viewtopic.php?t=385516), [rpi-object-detection (GitHub)](https://github.com/automaticdai/rpi-object-detection)
- Pi Zero MJPEG-стрим — [mjpg-streamer setup](https://krystof.io/mjpg-streamer-on-a-raspberry-pi-zero-w-with-a-usb-webcam-streaming-setup/), [picamera2 MJPEG (issue #708)](https://github.com/raspberrypi/picamera2/issues/708), [CSI MJPEG practical case](https://prometeo.blog/en/practical-case-mjpeg-on-raspberry-pi-zero-w-csi-camera/)
- Luckfox — [Pico Ultra (W) специф.](https://www.luckfox.com/EN-Luckfox-Pico-Ultra), [wiki RV1106](https://wiki.luckfox.com/Luckfox-Pico-RV1106/), [opencv-mobile](https://wiki.luckfox.com/Luckfox-Pico-Plus-Mini/opencv-mobile/), [SDK](https://github.com/LuckfoxTECH/luckfox-pico), [USB≠CSI (форум)](https://forums.luckfox.com/viewtopic.php?t=1618)
- Milk-V — [Duo 256M (SG2002)](https://www.electronics-lab.com/milk-v-duo-256m-is-an-sg2002-powered-multi-architecture-sbc-priced-at-7-99/), [SG2002](https://milkv.io/chips/sg2002)
- Coral ускоритель — [Edge TPU benchmarks](https://www.coral.ai/docs/edgetpu/benchmarks/)
