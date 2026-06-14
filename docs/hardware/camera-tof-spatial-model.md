# Камера + ToF → модель пространства в реальном времени, хранение и переиспользование

> Как из нашей **моно-камеры (OV на XIAO ESP32-S3)** + **VL53L7CX (8×8 ToF)** +
> одометрии/IMU построить пространственную модель в реальном времени, копить её,
> хранить на сервере и переиспользовать роботом позже. Decision-oriented: что
> реально работает на каком железе, в каком формате хранить.
>
> Сверено с ECCV/IEEE/JFR-публикациями, доками RTAB-Map/OctoMap/Open3D/ORB-SLAM3,
> исходниками. 403-блокировки → часть из выжимок, несущее подтверждено ≥2 источниками.
> ⚠ Главный честный вывод: **полноценная реал-тайм-модель — это работа на SBC/сервере,
> не на ESP32.** XIAO остаётся источником данных (камера+ToF+одометрия по сети).

## TL;DR — конвейер по уровням

| Уровень | Глубина | Модель | Хранение/переиспользование |
|---------|---------|--------|---------------------------|
| **A. Дёшево, edge** | ToF-only 8×8 (+ классич. фильтр) | облако точек по кадрам (Ferrolho-стиль) на ПК | сырые кадры → сервер, склейка офлайн |
| **B. Цветная модель** | моно-глубина (Depth Anything) + ToF для масштаба → цветное облако (Open3D) | накопление + ICP на сервере | PCD/PLY/меш на сервере |
| **C. Карта для навигации** 🎯 | RGB-D (из B) + одометрия + IMU | **RTAB-Map** (графовый SLAM) | **`.db` (SQLite) на сервере** → робот грузит в режиме локализации |

Рекомендация: **C через RTAB-Map** — единственный путь, где «построить → сохранить → переиспользовать» это first-class фича из коробки.

## Шаг 1. Камера + ToF → плотная/цветная глубина

Проблема: ToF даёт **только 64 точки** глубины в узком конусе; камера — плотную картинку без метрики. Нужно их поженить.

**Варианты (от тяжёлого к лёгкому):**
- **DELTAR (ECCV 2022) / CFPNet** — профильные сети **ровно под VL53L5CX 8×8 + RGB**, дают качество «как commodity RGB-D». Но это **GPU-методы** (~20М параметров, ~50 мс на 2080Ti) — только сервер с GPU, не edge. CFPNet REL 0.103 / RMSE 0.43 м на реальном датасете ZJU-L5.
- **Моно-глубина + ToF для масштаба (рекомендуется для нас):** сеть **Depth Anything V2 / MiDaS** даёт *относительную* глубину (disparity) из одного RGB; наши 64 ToF-точки фиксируют **метрический масштаб**. Математика тривиальна и закрыта: аффинный fit в обратной глубине `Z_inv = s·D + b`, где s,b — **закрытое решение МНК 2×2** (нормальные уравнения, как `compute_scale_and_shift` в MiDaS); шумные зоны — RANSAC (~100 итераций). 64 точки достаточно (в литературе хватает ~100 якорей, выигрыш насыщается к ~300).
  - **🎯 Профильный paper ровно под нас:** «Self-Supervised Enhancement for Depth from a Lightweight ToF Sensor» (arXiv 2506.13444) — **VL53L5CX 8×8 + Depth Anything** через Median-of-Median Scaling: реальная точность **AbsRel ≈0.14, RMSE ≈0.5 м, δ<1.25 ≈82%**. Это честная планка простого масштабирования. Дозаполняющая сеть (VI-Depth SML) роняет RMSE до ~0.14 м, но дороже по компьюту.
  - **Перф (важно для выбора железа):** Depth Anything Small — на **Jetson AGX Orin ~12 мс (real-time)**, **Orin Nano/NX ~10 FPS (впритык)**; **на RK3588 (Orange Pi 5) трансформер-глубина сейчас СЛОМАНА на NPU** (RKNN даёт garbled-выход, opset≤16, бывает медленнее CPU); **на Pi 5 CPU ~1–3 FPS** (не real-time, нужен AI-HAT/Hailo). Вывод: **онлайн моно-глубину гнать на сервере с GPU или Jetson Orin**, не на Orange Pi 5 NPU.
- **Классика на CPU (Pi-friendly, но не доказано для 64 точек):** морфологическое дозаполнение (IP-Basic) и joint-bilateral/guided filter (OpenCV `ximgproc`). Без обучения, дёшево, но валидировано на плотном LiDAR (тысячи точек), не на 64 — для нас спекулятивно.
- **Базовый факт (Ma & Karaman, ICRA 2018):** RGB + всего 20 разреженных точек уже бьют «RGB-only» на ~32% RMSE, без обрыва качества на малом числе. (⚠ у них точки случайны по кадру, наш ToF — кластер в центре, реально будет хуже.)

**Калибровка камера↔ToF:** интринсики OV — шахматка OpenCV (`calibrateCamera`); лучи зон — **таблицы ST `VL53L5_Zone_Pitch8x8`/`Yaw8x8`** (углы НЕ равномерны); линза **зеркалит** картинку (зона 0 = верх-право сцены — учесть). Экстринсик — плоскостной/PnP (DELTAR-метод). При со-расположении камеры и ToF в дальней зоне отображение зон→пиксели почти фиксированное (homography-приближение), на близи ломается параллаксом.

**Цветное облако (XYZRGB):** `Open3D` — `RGBDImage.create_from_color_and_depth(..., convert_rgb_to_intensity=False)` (⚠ дефолт `True` убивает цвет) → `PointCloud.create_from_rgbd_image(rgbd, intrinsic)`. Бэк-проекция: `X=(u-cx)·Z/fx, Y=(v-cy)·Z/fy, Z=depth`. Если глубина и RGB с разных сенсоров — сначала `cv2.rgbd.registerDepth` (выровнять в кадр камеры по экстринсику). **Живая визуализация edge→сервер→оператор:** **Rerun** (`rr.Points3D` + `rr.Pinhole`; viewer по gRPC с другой машины — `rr.connect_grpc()`) — самый низкий порог; альтернативы — Foxglove (WebSocket+браузер) или three.js. Ferrolho использует Viser (web 3D на localhost:8080).

## Шаг 2. Реал-тайм 3D-модель (что копит и строит)

| Инструмент | Что даёт | Железо | Формат |
|-----------|----------|--------|--------|
| **RTAB-Map** 🎯 | графовый RGB-D SLAM: позы + петли + occupancy/облако/octomap/меш | Pi 4/5 CPU (медленно, ~5–6 Гц на Pi3; Jetson комфортно) | **SQLite `.db`** |
| **ORB-SLAM3** | визуальный SLAM, точная локализация (ATE ~0.1 м) | Pi 4/5 CPU-only | `.osa` (разреженный feature-map) |
| **OctoMap** | 3D-октодерево occupied/free/unknown | Pi/Orange Pi CPU, ~3–5 см | `.bt`/`.ot` |
| **voxblox** (ETH) | TSDF + **ESDF** (готов для планирования) | **CPU-only** (Orange Pi 5 — лучший CPU-выбор) | protobuf + mesh PLY |
| **Open3D ScalableTSDF** | объёмная реконструкция → меш | CPU headless (Pi/Orange Pi, `BUILD_GUI=OFF`) | PLY/OBJ |
| **nvblox** (NVIDIA) | TSDF/ESDF/mesh + Nav2-costmap | **только GPU/Jetson Orin** | `.nvblx` |

- **TSDF-выбор:** на CPU-плате (Orange Pi 5) — **voxblox** (даёт ESDF — расстояние до препятствия на воксель, прямо для планировщика) или Open3D ScalableTSDF (чистый меш офлайн). **nvblox** — только если есть Jetson Orin (GPU). KinectFusion/nvblox на Pi/Orange Pi не идут (нет CUDA).
- **Сборка/ограничение облака:** копить сырые кадры нельзя (640×480 = ~300k точек/кадр). Open3D `voxel_down_sample` (размер карты ∝ площади/воксель², не числу кадров) + `statistical_outlier_removal` после каждого слияния. Склейка кадров — **по позам из SLAM** (frame-to-frame ICP копит дрейф); ICP (point-to-plane/GICP/colored) — для локального уточнения и петель. Формат: **PCD binary** (быстро + поля PCL) или **PLY** (совместимость/просмотр).

- **RTAB-Map — ядро для нашей цели.** Графовый SLAM: узлы = кадры (поза+RGB+depth+визуальные слова+локальная сетка), рёбра = одометрия+петли (appearance-based, bag-of-words). Трёхуровневая память (STM/WM/LTM) выгружает старое на диск → держит реал-тайм на большой карте. Фьюзит **wheel-одометрию + IMU** (наш набор!). ROS2 ≥ Humble через `rtabmap_ros`, интеграция с Nav2.
- **ORB-SLAM3** — точнее по локализации, но карта **разреженная** (для релокализации, не для планирования пути); occupancy надо строить отдельно. Атлас мульти-карт, но save/load капризный (баги Boost-сериализации). Брать, если приоритет — метрическая локализация камеры.
- **OctoMap — слой представления, НЕ SLAM:** жрёт уже локализованные облака от фронтенда (RTAB-Map) → 3D-октодерево + down-projected 2D-сетка. CPU-only, лёгкий.

## Шаг 3. Хранение на сервере + переиспользование роботом

**Главный паттерн (RTAB-Map):**
1. **Картировать** (SLAM-режим), копя всё в один **`.db`** (SQLite: позы, RGB/depth сжато, визуальные слова, локальные сетки). Параметры хранятся внутри `.db`.
2. **Сохранить `.db` на сервер** (просто файл; можно в объектное хранилище/БД).
3. **Переиспользовать:** робот при старте тянет `.db`, открывает в **режиме локализации** `Mem/IncrementalMemory=false` (+ `Mem/InitWMWithAllNodes=true` — вся карта в RAM для мгновенной релокализации). Карта **не растёт**, публикуется `map→odom` (корректирует дрейф одометрии) — робот находит себя в прежней карте.
4. **Дополнять позже:** мульти-сессия на той же базе — при петле между новой и старой сессией карты **сливаются автоматически** (старт из узнаваемой «якорной» точки).

**Что хранить под задачу:**
- **Навигация 2D:** `map.pgm`+`map.yaml` (Nav2 `map_server`) или RTAB-Map `/grid_map`.
- **3D occupancy:** OctoMap **`.bt`** (компактно, occupied-only; `.ot` если нужны вероятности/цвет для дообновления).
- **Полная переиспользуемая база:** RTAB-Map `.db` (и карта, и сенсорные данные, и петли — единственное, что грузится обратно «как было»).
- **Просмотр/модель:** облако `.pcd`/`.ply`, меш `.obj` (`rtabmap-export`).

**Серверная архитектура (DIY-минимум → продвинуто):**
- **Минимум:** сервер хранит `.db`/`.bt` как файлы (или в S3/MinIO); робот по HTTP тянет при старте, RTAB-Map грузит в localization-режиме. Сырые кадры с XIAO (`/capture`+`/tof`+`/telemetry`) можно копить на сервере и реконструировать офлайн (как Ferrolho, но с накоплением).
- **ROS2-путь:** `rtabmap_ros` на SBC/сервере, micro-ROS-мост с XIAO (одометрия/IMU/ToF), Nav2 для навигации по сохранённой карте.
- **Флот/облако:** одна карта на несколько роботов; enterprise — AWS RoboMaker/Formant/Freedom (избыточно для нас).

## Что под наше железо конкретно

XIAO ESP32-S3 **не строит модель** — он источник: уже отдаёт `/stream` (камера), `/tof` (сетка), `/telemetry` (одометрия/энкодеры; +IMU после BNO085). Реалистичный путь:

1. **Сейчас, без покупок:** копить на сервере синхронные тройки **кадр+ToF-сетка+поза** по сети; на ПК — моно-глубина (Depth Anything) + ToF-масштаб → цветное облако (Open3D) → накопление ICP. Получаем 3D-модель помещения офлайн. Это «стройка пространства» на текущем железе.
2. **+BNO085 (~$20):** честный курс → одометрия годна для онлайн-склейки (как Ferrolho, наш точный аналог: ESP32+ToF+BNO085→3D).
3. **+SBC (4 ГБ, см. SBC-обзор):** **RTAB-Map** в RGB-D-режиме (глубина из шага 1) + одометрия+IMU → `.db` на сервере → робот переоткрывает в localization-режиме. Это полный цикл «построил → сохранил → переиспользовал».
4. **+360° LiDAR (опц., см. `lidar-landscape-2026.md`):** если нужна надёжная глобальная карта — RTAB-Map умеет и лидар, петли надёжнее камеры на бедных текстурой стенах.

## Честный реалистичный итог

- **Камера+8×8-ToF фьюжн реален, но узкое место — 64 точки** в узком конусе: метрический масштаб моно-глубине дают, плотную модель «из коробки» — нет (DELTAR/CFPNet это GPU-сети). На edge — моно-глубина+ToF-масштаб; тяжёлое — на сервере с GPU.
- **Реал-тайм-модель = SBC/сервер**, не ESP32. XIAO — сенсорный узел.
- **«Построить→хранить→переиспользовать» решает RTAB-Map** (`.db` + localization mode + мульти-сессия) — это прямой ответ на запрос. ORB-SLAM3 — для точной релокализации, OctoMap — для 3D-occupancy представления.
- Готового DIY-робота, который строит **навигационную** модель именно по мультизонному ToF, в природе пока нет — это сканеры (Ferrolho, ToF-only) или лидарные SLAM-сборки; наш путь — гибрид: ToF+моно-глубина для модели, RTAB-Map для персистентности. **Прагматичный шорткат** для цветного плотного 3D: готовый RGB-D/ToF-модуль (Arducam RGBD-ToF, DFRobot) аппаратно выравнивает цвет с полной depth-картой — дешевле и проще, чем кастомное слияние моно+64-зоны (но это другой сенсор, не наш VL53L7CX).
- **Edge vs сервер по компьюту:** тяжёлое (DELTAR-фьюжн, моно-глубина в real-time) — **сервер с GPU или Jetson AGX Orin**; CPU-карта (Orange Pi 5 / Pi 4-5) тянет RTAB-Map/OctoMap/voxblox/ICP на даунсэмпленных облаках при умеренной частоте; XIAO — только источник. Канал не узкое место: ToF-сетка <1 КБ/кадр, RGB — JPEG с OV2640; узкое место — компьют, поэтому всё модельное на сервере/SBC.

## Источники

- DELTAR (VL53L5CX+RGB, ECCV 2022): https://arxiv.org/abs/2209.13362 · https://zju3dv.github.io/deltar/
- CFPNet (8×8 ToF+RGB): https://arxiv.org/html/2411.04480v1
- Ma & Karaman sparse-to-dense (ICRA 2018): https://github.com/fangchangma/sparse-to-dense.pytorch
- Монокулярная глубина + sparse scale (ICRA 2023): https://arxiv.org/abs/2303.12134
- Depth Anything V2: https://github.com/DepthAnything/Depth-Anything-V2 · Jetson-бенч: https://github.com/IRCVLab/Depth-Anything-for-Jetson-Orin
- ST per-zone pitch/yaw LUT: https://community.st.com/t5/imaging-sensors/vl53l5cx-multi-zone-sensor-get-x-y-z-of-points-relative-to/td-p/172929
- Open3D RGBD→cloud: https://www.open3d.org/docs/release/tutorial/geometry/rgbd_image.html
- Rerun (live 3D): https://github.com/rerun-io/rerun
- RTAB-Map (JFR 2024): https://arxiv.org/pdf/2403.06341 · wiki: https://github.com/introlab/rtabmap/wiki · ros: https://github.com/introlab/rtabmap_ros
- RTAB-Map мульти-сессия/локализация: https://github.com/introlab/rtabmap/wiki/Multi-session
- ORB-SLAM3 (T-RO 2021): https://arxiv.org/abs/2007.11898 · https://github.com/UZ-SLAMLab/ORB_SLAM3
- OctoMap (Autonomous Robots 2013): https://octomap.github.io/ · https://github.com/OctoMap/octomap_mapping
- Ferrolho ESP32+VL53L5CX+BNO085 (наш аналог): https://github.com/ferrolho/VL53L5CX-BNO08X-viewer
- tof_imager (ToF→ROS2 PointCloud2): https://github.com/adityakamath/tof_imager_ros
- MMS: VL53L5CX 8×8 + Depth Anything (наш сетап): https://arxiv.org/pdf/2506.13444
- VI-Depth (LS-выравнивание + дозаполнение, ICRA 2023): https://github.com/isl-org/VI-Depth
- MiDaS compute_scale_and_shift: https://github.com/isl-org/MiDaS/issues/28
- voxblox (TSDF+ESDF, CPU): https://github.com/ethz-asl/voxblox
- nvblox (Jetson Orin GPU): https://github.com/NVIDIA-ISAAC-ROS/isaac_ros_nvblox
- Open3D RGBD-integration (TSDF): https://www.open3d.org/docs/release/tutorial/pipelines/rgbd_integration.html
- RK3588 NPU трансформер-глубина (баг): https://github.com/airockchip/rknn-toolkit2/issues/322
