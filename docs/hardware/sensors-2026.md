# Исследование: сенсоры для робота «Деньгожуй» (2026)

> Обзор доступных на рынке сенсоров под мобильного робота, привязанный к этапам
> автономности. Что включить **уже сейчас на ESP32-S3**, что добавить **с Raspberry Pi**,
> что избыточно. Категории: расстояние/препятствия, 2D-LiDAR, движение/одометрия, контакт/линия,
> питание/среда/звук, 3D-глубина. Метод — параллельное исследование (5 направлений) + проверка
> ключевых фактов. Дата: июнь 2026.

## Как читать (привязка к проекту)

Робот: дифф-привод TB6612 + энкодеры, камера, PDM-микрофон, ESP32-S3 (Arduino), перспектива
Pi Zero 2 W / Pi 5. Граница важна: **ESP32 тянет «лёгкие» сенсоры (I²C/UART/digital) для
реактивного поведения**; полноценная навигация/SLAM/глубина — это уже уровень Pi/Jetson с ROS2.

### ⚡ Что уже разведено в прошивке (быстрые включения)

Из `xiao_cam_stream/drive_config.h` и `xiao_drive.h`:
- **Бампер** (`DRIVE_BUMPER_PIN_1/2`, сейчас `=0`) — логика `INPUT_PULLUP` + активный-LOW + вывод в
  телеметрию (`bumper`) **уже написана**. Включить = вписать номер GPIO + микропереключатель.
  ⚠️ В коде нет антидребезга — добавить debounce 20–50 мс.
- **Ультразвук HC-SR04** (`DRIVE_US_*`, чтение через `pulseIn`) — разведён, но нужен **делитель
  ECHO 5 В→3.3 В** (отмечено в `drive_config.h.example`) и 2 GPIO.
- **Энкодеры** уже работают (D6–D9, квадратурные) — это готовый слой одометрии скорости.

### ⚠️ Особенности ESP32-S3 для сенсоров
- **ADC2 не работает при активном Wi-Fi** (а у нас камера-стрим) → избегать аналоговых линеек
  (QTR-8A, Sharp), брать **цифровые/I²C**. ADC1 — лишь несколько каналов.
- Многие сенсоры садятся на **одну I²C-шину** (ToF, IMU, INA, BME, BH1750) — экономия пинов.
- Пины камеры (10–18, 38–48), мик (41–42), LED 21 — не трогать.

---

## A. Расстояние / препятствия (точечные + зональные)

| Датчик | Тип | Дальность | FOV | Интерфейс | Питание | ESP32 | Цена 2026 |
|---|---|---|---|---|---|---|---|
| **HC-SR04+** | УЗ 40 кГц | 2–400 см | ~15° | Trig/Echo | 3–5.5 В | да (нативно 3.3 В) | $3–6 |
| **US-100** | УЗ + темп-компенс. | 2–450 см | ~15° | **UART** / Trig-Echo | 2.4–5.5 В | да (UART) | $4–8 |
| **VL53L0X** | лазер ToF | 5–200 см | ~25° | I²C | 3.3 В | отлично | $4–8 |
| **VL53L1X** | лазер ToF | 4 см–4 м | 15–27° прогр. | I²C | 3.3 В | отлично | $6–12 |
| **VL53L5CX / L8CX** | ToF **8×8 зон** | 2 см–4 м | 65° диаг. | I²C (L8: +SPI) | 3.3 В | да | $15–28 |
| **TF-Luna** | 1D-LiDAR ToF | 0.2–8 м | 2° | UART/I²C | 3.7–5.2 В | отлично | $18–30 |
| **TFmini-S** | 1D-LiDAR ToF | 0.1–12 м | 2° | UART/I²C | 5 В | да | $40–50 |
| Sharp GP2Y0A21 | ИК-триангуляция | 10–80 см | узкий | **аналог** | 5 В | ⚠️ ADC2/Wi-Fi | $5–10 |

**Рекомендация:** для реактивного объезда на ESP32 — **VL53L5CX (8×8, 65°)** спереди как грубая
«карта глубины» 64 зоны (ловит низкие препятствия и края столов; не слепнет на свету как ИК Sharp)
+ кольцо **US-100** или **VL53L1X** по бокам. ToF (I²C, 3.3 В) — главный выбор: не боятся
акустических помех от моторов. **Не брать** Sharp (аналог, ADC2 конфликтует с Wi-Fi, плохо с
чёрным/глянцем). Грабли ToF: зеркала и чёрные поглощающие поверхности занижают сигнал; трактовать
таймаут как «препятствие/обрыв».

### ⚠️ Покровное стекло для ToF (VL53Lxx) — нельзя ставить «любое»

ToF-дальномеры ST очень чувствительны к защитному стеклу. Если ставите ToF за стекло корпуса:
- **ИК-прозрачность на 940 нм:** пропускание **>85% (VL53L1X)** / **>87% (VL53L5CX/L7CX)** в полосе
  930–950 нм. Обычное тонированное/ИК-cut стекло режет сигнал в ноль (берите боросиликат/D263,
  тонкое 0.3–0.5 мм; «чёрное» в видимом, но ИК-прозрачное — годится).
- **Минимальный воздушный зазор**, стекло не касается сенсора и не далеко; для **VL53L7CX**
  (8×8, 90°) зазор **<0.4 мм** или прокладка-стенка — иначе крайние зоны слепнут.
- **Не перекрывать апертуру** TX/RX; между «глазами» — матовая чёрная перегородка (против
  внутреннего crosstalk через стекло).
- **После монтажа — калибровка crosstalk** (`CalibrateXtalk`; у L7CX — **по зонам**), мишень белая
  на известной дистанции. Иначе близкие расстояния врут.

Разница по типам: одиночнозонные (VL53L0X/L1X) — проще, одна калибровка; многозонные
(VL53L5/L7/L8CX) — строже к зазору и требуют по-зонной Xtalk-калибровки. ST: AN5231 (L1X),
AN5856 (L5CX), AN4907 (L0X), UM3038 (L7CX).

## B. 2D-LiDAR (SLAM-навигация — нужен Raspberry Pi + ROS2)

| LiDAR | Принцип | Дальность | Скан | Интерфейс | На свету | Цена 2026 |
|---|---|---|---|---|---|---|
| **RPLIDAR C1** | **DTOF** | 0.05–12 м | 10 Гц | UART→USB | хорошо (IP54) | **$69** |
| **LDROBOT LD19/LD06** | **DTOF** | 0.02–12 м | 5–13 Гц | UART | до ~30 КЛк | $65–99 |
| RPLIDAR A1M8 | триангуляция | 0.15–12 м | 5.5–10 Гц | UART→USB | ⚠️ слепнет | $99 |
| LDROBOT LD20/D300 | DTOF | 0.03–25 м | 10 Гц | UART | улучшено | $130–145 |
| RPLIDAR A2 | триангуляция | 0.2–16 м | 10–15 Гц | UART→USB | ⚠️ слепнет | $229 |

**Рекомендация:** для SLAM — **RPLIDAR C1 ($69)** или **LDROBOT LD19 ($65–99)**: оба DTOF (лучше
на свету, чем триангуляционные A1/YDLIDAR), 12 м, прямой ROS2-драйвер, работают со **slam_toolbox**
(штатный 2D-SLAM в ROS2 Jazzy). LD19 удобнее питать/читать по UART прямо с Pi-GPIO; C1 — дешевле и
IP54. Нужны TF `odom→base_link` (отсюда важны наши энкодеры) + `LaserScan`. На ESP32 SLAM не крутить.

## C. Движение / ориентация / одометрия

| Сенсор | DOF | Аппаратный fusion | Интерфейс | Заметка | Цена 2026 |
|---|---|---|---|---|---|
| **BNO085/086** | 9 | **да** (кватернион, drift 0.5°/мин, автокалибровка) | I²C/**UART-RVC**/SPI | ⚠️ I²C плохо с ESP32 → **UART-RVC/SPI** | $20–30 |
| BNO055 | 9 | да (100 Гц) | I²C | теряет калибровку при выключении | $25–35 |
| ICM-20948 | 9 | DMP (плохо докум.) | I²C/SPI | замена снятого MPU9250 | $15–18 |
| BMI270 | 6 | нет | I²C/SPI | низкий дрейф, нужен внешний mag | $8–12 |
| MPU6050 | 6 | DMP примитив | I²C | только прототип | $2–4 |
| **AS5600** | — | — | I²C/PWM | магнитный энкодер 4096/об (фикс. адрес → мультиплексор на 2 колеса) | $3–6 |
| PMW3901 | — | — | SPI | оптический поток (нужна текстура пола) | $16–20 |
| NEO-6M / M8N | — | — | UART | GPS 2.5 м CEP (улица); RTK нет | $5–20 |
| ZED-F9P (RTK) | — | — | UART/I²C | см-точность, нужны RTCM-коррекции | модуль ~$150–200 |

**Рекомендация:** IMU по умолчанию — **BNO085** (готовый кватернион на борту, не нужно писать
фильтр Маджвика на ESP32), но подключать по **UART-RVC или SPI**, не I²C (его I²C нарушает спеку и
плохо дружит с ESP32-S3). Магнетометр — самое слабое звено рядом с моторами: калибровать
hard/soft-iron **в собранном роботе**, держать дальше от моторов/Li-Po; в сильных помехах mag
отключают и берут курс из энкодеров+GPS. **Одометрия:** энкодеры (дистанция) + IMU (повороты)
сливаются через `robot_localization` (EKF) на Pi; на улице +GPS через `navsat_transform_node`.

## D. Контакт / линия / обрыв

| Сенсор | Назначение | Интерфейс | ESP32 пины | Цена 2026 |
|---|---|---|---|---|
| **Микропереключатель/бампер** | столкновение | digital | 1 GPIO | $0.1–0.5 |
| **QTR-8RC** (Pololu) | линия, 8 каналов | **digital RC** | 8 GPIO | $9.95 |
| 5-канальный TCRT-модуль | линия (бюджет) | digital/analog | 5 GPIO | $3–6 |
| TCRT5000 (одиночный) | линия/край | digital/analog | 1 на канал | ~$0.85 |
| **VL53L0X (вниз)** | обрыв/cliff | I²C | шина | $5–15 |
| Hall A3144 / геркон | скорость/концевик | digital | 1 GPIO | $0.3–1 |
| Индуктивный/ёмкостный | металл/спец | digital | ⚠️ 6–36 В | $1–15 |

**Рекомендация (минимум для line-following + защита):** (1) **бампер на микропереключателях** —
включается прямо сейчас в прошивке (+ добавить debounce); (2) линия — **QTR-8RC цифровой** (не
аналоговый 8A: ADC2/Wi-Fi конфликт); (3) защита от падения — **VL53L0X вниз** по краям (таймаут =
обрыв над чёрным/глянцевым полом). Индуктивный/ёмкостный — мимо (высокое питание, только металл).

## E. Питание / среда / звук

| Сенсор | Измеряет | Интерфейс | Заметка | Цена 2026 |
|---|---|---|---|---|
| **INA226** | V/I/P батареи | I²C | 16-бит, до 36 В; сменить шунт под токи моторов | $3–6 |
| **INA3221** | V/I, **3 канала** | I²C | батарея + 2 мотора одним чипом | $3–6 |
| INA219 | V/I/P | I²C | 12-бит, проще | $1–5 |
| ACS712 | I (hall, изоляция) | аналог | ⚠️ шумный, ADC2/Wi-Fi | $2–4 |
| **DS18B20** | T (зонд) | 1-Wire | на радиатор драйвера моторов | $1–3 |
| BME280 / AHT20 | T+влажн.(+давл.) | I²C | самонагрев BME 1–2 °C | $2–5 |
| BH1750 | освещённость (люкс) | I²C | день/ночь/тень | $1–2 |
| INMP441 | I²S-микрофон | I²S | лучше согласован с ESP32, чем SPH0645 | $3–5 |
| ReSpeaker XVF3800 | **направление звука** (DOA) | USB/I²S | DOA/beamforming на XMOS, +XIAO ESP32S3 | $54.50 |

**Рекомендация:** главное — **мониторинг батареи**: **INA226** (или **INA3221** для раздельного
контроля батарея+моторы) по I²C на линию Li-ion → V/I/P и оценка разряда. Избегать ACS712 для
точного учёта (шум). Опц.: **DS18B20** на радиатор драйвера (тепловая защита), **BME280/BH1750**
для среды. Звук уже закрыт PDM-микрофоном; направление звука экономично решается только готовым
**ReSpeaker XVF3800**.

## F. 3D / глубина + готовые киты (нужен Pi/Jetson, не ESP32)

| Сенсор | Что даёт | Дальность | Хост | ROS2 | Цена 2026 |
|---|---|---|---|---|---|
| **OAK-D Lite** | стерео depth + **AI на борту** (4 TOPS) | 0.2–19 м | Pi/Jetson | depthai-ros | $149 |
| **OAK-D Pro** | active stereo + IR + AI | 0.7–12 м | Pi/Jetson | depthai-ros | $299–399 |
| **RealSense D435i** | RGB+depth+IMU | 0.3–3 м | Pi 5/Jetson | realsense-ros | $199–249 |
| **RealSense D455** | +база 95 мм (точнее) | 0.6–6 м | Pi 5/Jetson | realsense-ros | $299–349 |
| **Orbbec Gemini 2** | active-IR стерео+IMU | 0.15–10 м | Pi/Jetson | OrbbecSDK_ROS2 | $234 |
| Arducam ToF | depth (CSI) | 0.2–4 м | Pi/Jetson | community | $30–50 |
| Готовые киты (Yahboom ROSMASTER, Waveshare UGV) | шасси+LiDAR+depth, SLAM из коробки | — | Pi 5/Jetson | ROS2 | $500–1500 |

> **Статус RealSense (проверено):** линейку **не закрыли**. В июле 2025 RealSense выделилась из
> Intel в независимую компанию ($50M Series A, партнёрство с NVIDIA). D435i/D455 выпускаются и
> поддерживаются; EOL в 2021 коснулся только LiDAR L515 и face-аутентификации.

**Рекомендация:** depth нужен для семантики и низких/высоких препятствий, которые 2D-LiDAR не видит;
идеал — **LiDAR (навигация) + depth (восприятие)**. Под Pi — **OAK-D Lite** (AI на борту снимает
нагрузку с CPU) или **RealSense D435i / Orbbec Gemini 2**; под Jetson — **OAK-D Pro / D455**.
**Для текущего ESP32-робота depth-камеры и ROS2-киты избыточны и несовместимы** — потолок ESP32 это
**VL53L5CX/L8CX (8×8 I²C)**; настоящее зрение/SLAM = апгрейд платформы до Pi 5 / Jetson.

---

## Рекомендованные комплекты по этапам

> ⚠️ **По пинам:** на голом XIAO ESP32-S3 привод уже занял 10 из 11 пэдов (свободен только D10),
> и дефолтные пины I²C/UART/SPI заняты. Набор «Этап 1» физически требует реорганизации шины
> (I²C-расширители) или выноса сенсоров на Pi — детали в [pinout-wiring-2026.md](pinout-wiring-2026.md).

**Этап 1 — реактивный ESP32 (сейчас, дёшево, минимум пинов):**
- Бампер (микропереключатели) — *включить в прошивке + debounce*.
- **VL53L5CX (8×8)** спереди + 1–2 **VL53L1X** по бокам (всё I²C).
- **VL53L0X вниз** ×2 — защита от падения.
- **INA226/INA3221** — мониторинг батареи (I²C).
- **BNO085** (UART-RVC/SPI) — курс/повороты.
- (опц.) **QTR-8RC** — если нужен line-following; **DS18B20** на драйвер.

**Этап 2 — навигация с Pi (когда добавите Pi Zero/Pi 5 + ROS2):**
- **RPLIDAR C1 ($69)** или **LD19** + `slam_toolbox` + Nav2.
- Энкодеры+BNO085 → одометрия через `robot_localization` (EKF).
- (опц.) **OAK-D Lite / RealSense D435i** — depth/семантика.

**Этап 3 — улица/расширение:**
- GPS **NEO-M8N** (или RTK **ZED-F9P** для см-точности) + `navsat_transform_node`.
- LiDAR с большей дальностью (LD20 25 м) при открытых пространствах.

---

## Источники

**Расстояние/LiDAR:** ST [VL53L5CX](https://www.st.com/en/imaging-and-photonics-solutions/vl53l5cx.html) / [L1X](https://www.st.com/en/imaging-and-photonics-solutions/vl53l1x.html) / [L0X](https://www.st.com/en/imaging-and-photonics-solutions/vl53l0x.html) / [L8CX](https://www.st.com/en/imaging-and-photonics-solutions/vl53l8cx.html); Benewake [TF-Luna](https://en.benewake.com/TFLuna/index.html) / [TFmini-S](https://en.benewake.com/TFminiS/index.html); [SLAMTEC C1](https://www.slamtec.com/en/c1) / [A1](https://www.slamtec.com/en/lidar/a1) / [A2](https://www.slamtec.com/en/lidar/a2); [LD06 ArduPilot](https://ardupilot.org/copter/docs/common-ld06.html), [awesome-2d-lidars](https://github.com/kaiaai/awesome-2d-lidars); [Adafruit US-100](https://www.adafruit.com/product/4019); [Pololu Sharp GP2Y0A21](https://www.pololu.com/product/136); [slam_toolbox (Jazzy)](https://docs.ros.org/en/jazzy/p/slam_toolbox/)

**IMU/одометрия:** [BNO085 (Adafruit)](https://learn.adafruit.com/adafruit-9-dof-orientation-imu-fusion-breakout-bno085/overview), [BNO055 datasheet](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bno055-ds000.pdf), [ICM-20948 (TDK)](https://invensense.tdk.com/products/motion-tracking/9-axis/icm-20948/), [AS5600 (Seeed)](https://wiki.seeedstudio.com/Grove-12-bit-Magnetic-Rotary-Position-Sensor-AS5600/), [PMW3901+ESP32](https://circuitdigest.com/microcontroller-projects/interfacing-pmw3901-optical-flow-sensor-with-esp32), [NEO-6M](https://www.waveshare.com/wiki/UART_GPS_NEO-6M), [ZED-F9P](https://www.u-blox.com/en/product/zed-f9p-module), [robot_localization (Nav2)](https://docs.nav2.org/setup_guides/odom/setup_robot_localization.html)

**Контакт/линия:** [TCRT5000](https://www.utmel.com/components/tcrt5000-ir-sensor-datasheet-pinout-and-circuit?id=697), [Pololu QTR-8RC](https://www.pololu.com/product/961) + [User's Guide](https://www.pololu.com/docs/0j12/all), [VL53L0X (Adafruit)](https://www.adafruit.com/product/3317), [A3144 Hall](https://components101.com/sensors/a3144-hall-effect-sensor)

**Питание/среда/звук:** [INA226 (TI)](https://www.ti.com/product/INA226), [INA260 (Adafruit)](https://www.adafruit.com/product/4226), [ACS712 (Allegro)](https://www.allegromicro.com/en/products/sense/current-sensor-ics/zero-to-fifty-amp-integrated-conductor-sensor-ics/acs712), [BME280/DHT/DS18B20 сравнение](https://randomnerdtutorials.com/dht11-vs-dht22-vs-lm35-vs-ds18b20-vs-bme280-vs-bmp180/), [BH1750 (Adafruit)](https://www.adafruit.com/product/4681), [INMP441 vs SPH0645](https://www.atomic14.com/videos/posts/3g7l5bm7fZ8), [ReSpeaker XVF3800 (Seeed)](https://www.seeedstudio.com/ReSpeaker-XVF3800-4-Mic-Array-With-XIAO-ESP32S3-p-6489.html)

**3D/глубина:** [RealSense спин-аут (Intel Capital)](https://www.intelcapital.com/realsense-completes-spin-out-from-intel-raises-50-million-to-accelerate-ai-powered-vision-for-robotics-and-biometrics/), [CNBC](https://www.cnbc.com/2025/07/11/intel-ai-robotics-realsense.html), [RealSense D455 spec](https://www.intel.com/content/www/us/en/products/sku/205847/intel-realsense-depth-camera-d455/specifications.html), [OAK-D Pro (Luxonis)](https://shop.luxonis.com/products/oak-d-pro), [Orbbec Gemini 2](https://www.orbbec.com/products/stereo-vision-camera/gemini-2/) + [ROS2](https://github.com/orbbec/OrbbecSDK_ROS2), [Arducam ToF](https://www.arducam.com/time-of-flight-camera-for-raspberry-pi.html), [Yahboom ROSMASTER R2](https://category.yahboom.net/products/rosmaster-r2)

---

*Примечание о достоверности: ключевой факт (спин-аут RealSense из Intel, июль 2025, $50M, NVIDIA)
подтверждён несколькими независимыми источниками. Цены 2026 — ориентир розницы (±15% по региону).
Привязки к прошивке (бампер/ультразвук уже разведены, ADC2/Wi-Fi конфликт) проверены по коду репозитория.*
