# Ориентация и построение пространства: конечные решения (2026)

> Куда двигать робота за пределы реактивного объезда: **ориентация** (курс/поза)
> и **построение карты пространства** (occupancy grid / SLAM). Под наше железо
> (XIAO ESP32-S3 + камера + энкодеры + VL53L7CX) и в целом. Decision-oriented:
> не теория, а «что купить и что запустить».
>
> Сверено с ST, Nav2/ROS2-доками, академией (Thrun, LOAM) и десятком реальных
> DIY-сборок (EN/CN/JP). 403-блокировки ST/форумов → часть фактов из выжимок,
> но несущие выводы подтверждены ≥2 независимыми источниками.

## TL;DR — финальный стек по уровню амбиций

| Хочу | Ориентация | Пространство | Железо сверх текущего |
|------|-----------|--------------|----------------------|
| **A. Реактивный объезд + «снимки» комнаты** | гиро+энкодеры на ESP32 | log-odds локальная сетка в PSRAM + spin-to-scan на стопах | **+BNO085** (~$20) |
| **B. Локальная навигация (costmap)** | BNO085 + EKF | Nav2 rolling costmap (ToF→виртуальный лазер) | +BNO085, +Orange Pi 4ГБ |
| **C. Настоящая карта комнаты с замыканием петель** | BNO085 + EKF | **slam_toolbox + 360° LiDAR** | +BNO085, +LD06/LD19 (~$70), +Pi 4/5 или Orange Pi 5 |

Ключевое: **forward-ToF строит только локальную сетку, не полную карту**. Для карты комнаты нужен 360° обзор — это либо дешёвый LiDAR, либо честно остаться на уровне A/B.

## 1. Ориентация (курс / поза) — финал

- **Купить BNO085** (брейкаут Adafruit/SparkFun, ~$20). Делает фьюжн **на чипе** и отдаёт кватернион по I2C — не нужно писать фильтр. Это «просто работает».
- **Режим Game Rotation Vector** (гиро+акс, **без магнитометра**) — гладкий курс без магнитных скачков. **Магнитометр в роботе вреден**: моторы, токи, феррит дают hard/soft-iron искажения; DIY-сообщество (SlimeVR) перешло на mag-free.
- **Курс довести энкодерами:** на дифф-приводе **доминирует ошибка курса** (θ), а не дистанции (классика D.Anderson: убрал ошибку курса — ошибку пути можно игнорировать). Гиро иммунен к проскальзыванию, но дрейфует; энкодеры точны коротко, но врут на слипе — **сверять угловую скорость гиро и энкодеров**, гиро — сквозь слип, энкодеры — ограничивают долгий дрейф гиро.
- **Точность:** курс ~1–3° за прогон, медленный остаточный дрейф ограничен энкодерами — для масштаба комнаты достаточно.
- **Бюджетная альтернатива:** MPU6050 (~$3) + библиотека **xioTechnologies Fusion** (Mahony-класс, со встроенной коррекцией смещения гиро на остановках). Больше кода, чуть больше дрейф.
- **Камеру для курса не использовать** (visual odometry тяжелее и копит ошибку) — она для перцепции.
- **Если будет SBC + ROS2:** `robot_localization` **ekf_node**, `two_d_mode: true`; правило — фьюзить **ориентацию yaw из IMU + vx/yaw-rate из одометрии**, не абсолютный yaw из двух источников.

**Что НЕ делать:** магнитометр для indoor-курса; EKF аттитюда на ESP32; камера как основной курс.

## 2. Построение пространства — финал по уровням компьюта

### Минимальный жизнеспособный пайплайн (работает даже на ESP32)
**Log-odds occupancy grid** — математика тривиальна (сложения на луч):
1. 8×8 ToF → свернуть в 8 «лучей» (минимум по столбцу) = виртуальный лазер на 60°.
2. IMU yaw + энкодеры → поза (x, y, θ).
3. На каждый луч — растеризация (Bresenham) в фикс-размерную робоцентричную сетку, log-odds free/occupied.
4. Порог → occupancy grid для навигации.

В ROS это `nav2_costmap_2d` (rolling window); без ROS — ~100 строк C. Запоминать: log-odds выбран ровно за дешевизну и устойчивость к разреженным лучам ToF.

### По тирам железа
- **ESP32-S3 (240 МГц, PSRAM):** настоящего SLAM НЕТ. Только dead-reckoning + локальная log-odds сетка. Роль — **узел I/O** (моторы, энкодеры, IMU, ToF), отдающий данные на компаньон через **micro-ROS**.
- **Pi Zero 2W (512 МБ):** для standalone slam_toolbox **не годится** (slam_toolbox ~600 МБ + Nav2 ~400 МБ). Даже сборка ROS2-пакетов превышает 512 МБ. Тупик для onboard-SLAM.
- **Orange Pi Zero 2W (4 ГБ):** жизнеспособно, но впритык — ros_base без GUI, RViz на ноуте.
- **Pi 4 (4 ГБ) — практический минимум** для slam_toolbox+Nav2 (70–90% CPU); **Pi 5 / Orange Pi 5 — комфортно onboard**.
- **Hector SLAM** — без одометрии, но требует плотного 360° скан-матчинга, деградирует на разреженном ToF. **Cartographer** тяжелее slam_toolbox — избегать на слабых платах. **slam_toolbox** — дефолт ROS2 при наличии LiDAR.

### Spin-to-scan (наше вращение на 360°)
Даёт **снимок** комнаты из точки, но **хуже даже дешёвого LiDAR** и **не даёт SLAM на ходу**: точки копятся секундами, любая ошибка позы за оборот смазывает скан (motion distortion), курс плывёт за медленное вращение. Годится для «штампа» локальной сетки на остановках; сшивать снимки между стопами — копит дрейф одометрии, без замыкания петель.

## 3. Решение «докупать ли 360° LiDAR»

**Да — если нужна глобально-согласованная карта.** Это **самый большой скачок качества** за ~$70. slam_toolbox/Hector/Cartographer построены вокруг плотного 360° скан-матчинга, который forward-ToF в принципе не даёт.

Дешёвые варианты (все с ROS2-драйверами, ~12 м, DTOF):
- **LD19 / STL-19P** (~$65–90): 10 Гц, 0.03–12 м, ROS2 SDK. STL-19P — улучшенный drop-in LD19.
- **LD06** (~$70): практически LD19.
- **RPLIDAR C1/A1** (~$99): Slamtec, самая широкая опен-сорс поддержка.

⚠ **slam_toolbox + LD06 НЕ влезет в Pi Zero 2W 512 МБ** — нужна 4-ГБ-плата (Orange Pi Zero 2W 4ГБ впритык / Pi 4 / Pi 5). Без ROS: **BreezySLAM/CoreSLAM** (tinySLAM, <200 строк C) — легче slam_toolbox, но тоже хочет 360° скан.

**Лестница апгрейда над 2D-лидаром** (если плоского среза мало) — в `lidar-landscape-2026.md`: 2D LD06/LD19 (~$70) → 3D Unitree L2 (~$395) → стандарт робототехники Livox Mid-360 (~$899, FAST-LIO из коробки). Для нашего класса робота потолок здравого смысла — 2D; выше — другой бюджет/питание.

**Правило решения:**
- Реактивный объезд + снимки, минимум денег → остаёмся на ToF + log-odds + spin-to-scan.
- Реальная карта с петлями → **LD19/LD06 (~$70) + 4-ГБ SBC + slam_toolbox**. ESP32-S3 — узел моторов/энкодеров/IMU через micro-ROS.

## 4. Реальные сборки-референсы (копировать архитектуру)

**🎯 Самый близкий к нам — Ferrolho `VL53L5CX-BNO08X-viewer`** (Hackaday, фев 2026, ~$30):
**ESP32 + VL53L5CX (8×8 ToF, родственник нашего L7CX) + BNO085** → real-time **3D-скан комнаты**. ESP32 стримит JSON по serial; Python-хост делает point cloud, **RANSAC-подгонку плоскостей**, EMA-фильтр, накопление карты. Это **ровно наш стек после покупки BNO085** и ровно «стройка пространства». Не полный SLAM (нет замыкания петель), но рабочий 3D-сканер малых помещений. → https://github.com/ferrolho/VL53L5CX-BNO08X-viewer

**Для ROS2-пути — `tof_imager_micro_ros`** (adityakamath): наш сенсор VL53L5CX/**L7CX** → micro-ROS → `PointCloud2`, готовый мост ESP32→ROS2. → https://github.com/adityakamath/tof_imager_micro_ros

| Сборка | Железо | Где карта | Достигнуто |
|--------|--------|----------|-----------|
| **Ferrolho viewer** 🎯 | **ESP32 + VL53L5CX + BNO085** | на ПК (Python) | **наш стек**: 3D-скан, RANSAC-плоскости, накопление |
| **MentorPi M1** (Hiwonder) | **Pi 5 + LD06 + ESP32 (micro-ROS)** | на Pi 5 | архитектура SBC+ESP32+LiDAR, slam_toolbox+Nav2 |
| **Minibot** | Pi 5 + RPLIDAR C1 + ESP32 | на Pi 5 | SLAM+Nav2 onboard, ESP32 по serial |
| **kaiaai / Maker's Pet** | ESP32 (micro-ROS firmware) + Pi/ПК | на SBC/ПК | платформа, 5+ LiDAR (LD19 и др.), Cartographer/Nav2 |
| **Lidarbot** (TheNoobInventor) | Pi 4 4ГБ + RPLIDAR A1 + MPU6050 + энкодеры | на ПК по сети | occupancy grid + Nav2, EKF-фьюжн |
| **Linorobot2** | Pi 4+ + LD06/LD19 + Teensy (micro-ROS) | на Pi/Jetson | модульный фреймворк; Pi 3B/1ГБ **недостаточно** |
| **BreezySLAM (AdroitAnandAI)** | Pi 3/4 + RPLIDAR A1 | на Pi (без ROS) | карта мультикомнаты, viz по MQTT |
| **antbern gridmap-slam** | ESP32 + самодельный поворотный LiDAR | на ПК (Java) | ⚠ «kind of works» — честно недопил |

Выводы сборок: **ESP32 = низкоуровневый узел (сенсоры/моторы/одометрия), карта/SLAM = на SBC или ПК.** Чистого SLAM на ESP32 в рабочих проектах нет. MentorPi/Minibot/kaiaai доказывают наш сплит. Pi 4 4ГБ — пол для slam_toolbox, Pi 5 — комфорт; **Pi Zero 2W в рабочих SLAM-сборках не встречается**. Для мультизонного ToF массовый паттерн — **3D point cloud + плоскости (Ferrolho), а не SLAM** — это и есть реалистичная «стройка пространства» на нашем железе сейчас.

**🏗 Эталонная архитектура — паттерн `linorobot2`** (если пойдём в ROS2): ESP32-S3 несёт **micro-ROS firmware** (подписка `/cmd_vel`, PID моторов, публикует `/odom` + `/imu`, опц. проброс LiDAR по UDP); SBC несёт ROS2 + `robot_localization` EKF (odom+IMU) → slam_toolbox → Nav2. Поддерживает LD06/LD19/STL27L из коробки. Ближайший **буквальный двойник нашего железа** — `PrwTsrt/microros_esp32_diffdrive` (**Yahboom ESP32-S3 + Orange Pi 5B**), плюс RoboFoundry build-log по той же связке. → https://github.com/linorobot/linorobot2 , https://github.com/hippo5329/linorobot2_hardware/wiki

**⚠ Честный пробел:** законченного DIY-робота, который строит **навигационную карту по мультизонному ToF**, в природе нет — только драйверы и 3D-сканеры (Ferrolho). Если цель — именно карта для навигации, мультизонный ToF не заменяет 360° LiDAR; ToF остаётся для реактивного объезда/cliff/3D-снимков. Сверхдешёвый вход в LiDAR-SLAM — салвадж-лидары **Xiaomi LDS02RR (~$15)** / 3irobotix Delta-2, которые ESP32 умеет принимать (Maker's Pet), SLAM — на SBC.

## 5. Что это значит для нашего робота — роадмап

1. **Сейчас (без покупок):** ToF → 8 лучей → **log-odds локальная сетка** в PSRAM XIAO + dead-reckoning (пока без IMU — только энкодеры). Радар-UI/`/tof` уже есть; добавить накопление сетки. Реактивный объезд + cliff по строкам (см. `vl53l7cx-best-practices.md`).
2. **Шаг 1 — ориентация (~$20):** **BNO085** на I2C XIAO (Game Rotation Vector). Сразу честный курс → нормальная одометрия → spin-to-scan снимки перестают «плыть». После этого мы = стек Ferrolho → можно повторить его **3D-скан комнаты** (point cloud + RANSAC-плоскости на ПК) почти даром, это и есть первая «стройка пространства».
3. **Шаг 2 — мозг (по выбору из SBC-обзора):** компаньон, ESP32 → micro-ROS узел (`/odom`, `/imu`, ToF как `LaserScan/PointCloud2`). Nav2 rolling costmap.
4. **Шаг 3 — настоящая карта (~$70 + 4ГБ SBC):** **LD19/LD06** + **Pi 4/5 или Orange Pi 5** + slam_toolbox. Это и есть «стройка пространства» с замыканием петель.

> Уточнение к SBC-обзору (`orange-pi-lineup-2026.md`): **для SLAM RAM решает.** Pi Zero 2W 512 МБ — тупик; целиться в **4 ГБ+** (Orange Pi Zero 2W 4ГБ впритык, надёжнее Pi 4 / Orange Pi 5). Это смещает выбор с «маленький Pi Zero 2W» в сторону 4-ГБ платы, если карта комнаты — цель.

## Источники

- BNO085 (фьюжн на чипе, report types): https://learn.adafruit.com/adafruit-9-dof-orientation-imu-fusion-breakout-bno085/report-types
- xioTechnologies Fusion (Mahony/Madgwick, drift-correction): https://github.com/xioTechnologies/Fusion
- SlimeVR IMU comparison (mag-free консенсус): https://github.com/SlimeVR/SlimeVR-Docs-Site/blob/main/src/diy/imu-comparison.md
- D.Anderson IMU-odometry (heading доминирует): http://www.geology.smu.edu/~dpa-www/robo/Encoder/imu_odo/
- ROS2 robot_localization ekf: https://docs.nav2.org/setup_guides/odom/setup_robot_localization.html
- Nav2 costmaps (rolling window): https://docs.nav2.org/configuration/packages/configuring-costmaps.html
- Occupancy grid log-odds (Thrun): http://robots.stanford.edu/papers/thrun.occ-journal.pdf
- Motion distortion вращающихся сенсоров: https://arxiv.org/pdf/2308.13694
- BreezySLAM / CoreSLAM: https://github.com/simondlevy/BreezySLAM
- awesome-2d-lidars (LD06/LD19/RPLIDAR): https://github.com/kaiaai/awesome-2d-lidars
- Lidarbot: https://github.com/TheNoobInventor/lidarbot
- Minibot (Pi5 + ESP32): https://github.com/YJ0528/minibot
- Linorobot2: https://github.com/linorobot/linorobot2
- MentorPi M1 (Pi5+LD06+ESP32): https://www.robotshop.com/products/hiwonder-mentorpi-m1-mecanum-wheel-ros2-robot
- Zbotic SLAM на Pi (RAM-бюджет): https://zbotic.in/slam-raspberry-pi-turtlebot3-navigation-mapping/
- Ferrolho VL53L5CX+BNO085 3D-скан (наш стек): https://github.com/ferrolho/VL53L5CX-BNO08X-viewer
- tof_imager_micro_ros (VL53L7CX→ROS2 PointCloud2): https://github.com/adityakamath/tof_imager_micro_ros
- kaiaai / Maker's Pet (ESP32 micro-ROS + ROS2): https://github.com/kaiaai/kaiaai
- MappingRover (Thrun occupancy grid на сонаре): https://github.com/stheophil/MappingRover
- linorobot2_hardware (ESP32-S3 micro-ROS firmware): https://github.com/hippo5329/linorobot2_hardware/wiki
- PrwTsrt ESP32-S3 + Orange Pi 5B (двойник железа): https://github.com/PrwTsrt/microros_esp32_diffdrive
- Maker's Pet — дешёвые LiDAR на ESP32 (Xiaomi LDS02RR ~$15): https://makerspet.com/
