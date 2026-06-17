# spatial/ — модель пространства из камеры + ToF (XIAO)

Прототип построения 3D-модели помещения из данных XIAO ESP32-S3:
**камера (`/capture`) + мультизонный ToF VL53L7CX (`/tof`)** → цветное облако точек.

Архитектура (по `docs/hardware/camera-tof-spatial-model.md`): **XIAO — источник,
этот код на ПК/сервере — строит и копит модель.** Тяжёлый ML на плату не лезет.

## Этапы

| Этап | Что | Статус |
|------|-----|--------|
| **1. Разреженное цветное облако** | 8×8 ToF-зоны → 3D, покраска пикселем камеры, накопление в PLY | ✅ этот код |
| 2. Плотная глубина | моно-глубина (Depth Anything) + ToF для масштаба → плотное облако | ⏳ план |
| 3. Карта для навигации | курс IMU (MPU6050) + **релокализация курса по карте** (де-дрейф гиро, `relocalize_yaw`) | 🟡 курс/релок есть; полный SLAM (RTAB-Map, позиция x/z) — нужен SBC + энкодеры |

Этап 1 работает **на текущем железе без покупок** и проверяет весь тракт
данных. Точность/плотность скромные (64 зоны), но это рабочая «стройка
пространства» и фундамент под этапы 2–3.

## Запуск

```bash
pip install -r spatial/requirements.txt   # pillow (цвет из камеры); без него — колормап по дистанции

# Один снимок текущего вида:
py -3 spatial/build_model.py --ip 192.168.1.50 --frames 1 --out room.ply

# Turntable / scan: повернуть робота между кадрами (скрипт подскажет):
py -3 spatial/build_model.py --ip 192.168.1.50 --frames 8 --yaw-step 45 --out room.ply

# Сохранять сырые кадры на сервер (для офлайн-пересборки):
py -3 spatial/build_model.py --ip 192.168.1.50 --frames 8 --yaw-step 45 \
    --session-dir sessions/today --out room.ply

# Пересобрать из сохранённой сессии (хранение на сервере = просто каталог):
py -3 spatial/build_model.py --offline sessions/today --out room.ply

# Тест без железа:
py -3 spatial/build_model.py --synth --frames 8 --yaw-step 45 --out test.ply
```

Открыть `*.ply` — MeshLab / CloudCompare / Open3D (`o3d.io.read_point_cloud`).

## Файлы

| Файл | Назначение |
|------|------------|
| `tof_cloud.py` | геометрия: зона→3D-точка, покраска, поза, воксель-облако, запись PLY |
| `xiao_client.py` | HTTP-забор `/capture` + `/tof` (+ `/telemetry`) с платы |
| `build_model.py` | CLI: живой/офлайн/synth, накопление, PLY |
| `world_model.py` | персистентная воксельная карта (log-odds + цвет), `score_world_points` для матча |
| `world_service.py` | автономный фоновый ingest + `PoseEstimator` (курс IMU) + `relocalize_yaw` |
| `calibrate_live.py` | проверка/калибровка конвейера (см. ниже) |

## Автономный сервис + ориентация по карте (`world_service.py`)

Дашборд запускает `WorldService` — фоновый поток сам тянет кадры, оценивает позу
(курс из IMU MPU6050, знак калиброван `IMU_YAW_SIGN`), вливает в персистентную
`WorldModel` и сохраняет на диск. Модель копится навсегда и переживает рестарт.

**Релокализация курса по карте** (`relocalize_yaw`) — карта **используется для
ориентации**: раз в N кадров текущий ToF-скан сопоставляется с накопленной картой
перебором поправки курса (поза ~1-DOF — turntable), и найденная невязка мягко
(демпфирование, `reloc_gain`) де-дрейфит гироскоп. Срабатывает только при
**различимой геометрии** (`reloc_min_margin`: плоская стена рот-неоднозначна →
пропуск; угол/мебель → коррекция) и достаточной карте (`reloc_min_conf`). В
дашборде: кнопка «Релок по карте» (`/world/relocalize`), статус `reloc_*`.

## Проверка/калибровка (`calibrate_live.py`)

```bash
py -3 spatial/calibrate_live.py --reloctest   # релокализация курса (детерм., без платы)
py -3 spatial/calibrate_live.py --simtest     # знак курса (детерм., без платы)
py -3 spatial/calibrate_live.py --selftest    # save/load round-trip (без платы)
py -3 spatial/calibrate_live.py <IP> 12       # живой прогон: поток позы/точек/occupancy
py -3 spatial/calibrate_live.py <IP> 20 --service   # реальный WorldService + reloc
py -3 spatial/calibrate_live.py <IP> --track  # калибровка знака по объекту при повороте
```

## Допущения и калибровка (важно)

- **FoV сенсора** по умолчанию 60°/ось (L7CX) — флаг `--fov`, при кривой
  геометрии подстроить.
- Дистанция зоны трактуется **перпендикулярной** (z); линза **зеркалит** сцену
  (учтено флагами `flip_h/flip_v` в `CloudConfig`).
- **Поза кадра** — пока ручная (yaw-step turntable). Реальная одометрия/курс —
  этап 3 (BNO085 + энкодеры → RTAB-Map). Без точной позы многокадровая склейка
  копит дрейф — это нормально для MVP.
- Покраска — дальнепольное приближение co-located камеры+ToF; на близком плане
  ломается параллаксом. Строгая калибровка камера↔ToF — этап 2.

## Дальше (этап 2)

`build_model.py` устроен так, что покраску/глубину легко заменить: вместо
64 зон → плотная глубина из Depth Anything, масштабированная теми же ToF-точками
(аффинный МНК), и плотное цветное облако. См. конвейер в
`docs/hardware/camera-tof-spatial-model.md`.

## Этап 2/A — плотная глубина из ВИДЕО (`depth_fusion.py`, `dense_fuse.py`)

**Видео несёт геометрию, а не только цвет.** Моно-сеть (Depth Anything V2) →
относит. глубина; 64 ToF-зоны задают метрический масштаб аффинным fit
`1/Z = s·rel + b` (`fit_scale_shift`, МНК 2×2, опц. RANSAC); `frame_to_dense_world()`
→ **тысячи цветных метрических МИРОВЫХ точек/кадр** (вместо 64 ToF-зон), готовых
к вливанию в `WorldModel`. Интринсики камеры из FoV (`intrinsics_from_fov`,
OV2640 ~65°). Бэкенд подключаемый: `SynthBackend` (без torch — форма синтет.,
цвет/масштаб/поза реальные) или `DepthAnythingBackend` (нужен `pip install torch
transformers`; GPU/Jetson желательно).

Драйвер `dense_fuse.py` (грабит RGB+ToF → плотный воксель-мир, сравнение
sparse↔dense, PLY):
```bash
py -3 spatial/dense_fuse.py --selftest                 # без платы и torch
py -3 spatial/dense_fuse.py <IP> --frames 1            # synth на живом кадре
py -3 spatial/dense_fuse.py <IP> --frames 6 --backend depthanything
```
Проверено на железе: sparse ≈40 → **dense ≈8000 точек/кадр (×200)**. Для НАСТОЯЩЕЙ
геометрии комнаты — поставить `torch`+`transformers` и `--backend depthanything`.
Самотест ядра: `py -3 spatial/depth_fusion.py`.

## Этап 3 — Wi-Fi-якорь позиции (`wifi_anchor.py`, `wifi_collect.py`)

Радиокарта строится **в координатах позы** робота без обмера: на ходу скан AP
(прошивка `GET /wifiscan`) + поза (PoseEstimator) → `WifiMap`. Матчинг **SE-WKNN**
(корреляция Спирмена по рангу силы AP — устойчив к дрейфу уровня). Даёт грубый
абсолютный фикс (x,z) → сброс дрейфа одометрии/IMU. Wi-Fi = якорь позиции, не курс.

```bash
py -3 spatial/wifi_collect.py --ip <IP> --map home.wifi --collect 30   # построить карту
py -3 spatial/wifi_collect.py --ip <IP> --map home.wifi --locate       # фикс позиции
py -3 spatial/wifi_collect.py --synth                                   # тест без железа
```
⚠ `/wifiscan` (синхронный скан) кратко роняет STA-связь — дёргать редко/на стоянке.
