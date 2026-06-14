"""
spatial.tof_cloud — геометрия: 8×8 ToF-сетка VL53L7CX → цветное облако точек.

Проекция зоны в 3D и покраска пикселем камеры. Сервер-сайд, без железа.
Допущения (см. docs/hardware/camera-tof-spatial-model.md, раздел калибровки):
  * Дистанция зоны трактуется как ПЕРПЕНДИКУЛЯРНАЯ (z), не радиальная
    (поведение VL53L5/7CX по сообществу); x = z·tan(ah), y = z·tan(av).
  * FoV по оси — приближение (по умолч. 60° для L7CX); калибруется флагом --fov.
  * Покраска — дальнепольное приближение co-located камеры+ToF: зона (r,c)
    отображается в фиксированный регион кадра. На близи ломается параллаксом.
Система координат: x — вправо, y — вверх, z — вперёд (камерный кадр), метры.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class CloudConfig:
    fov_h_deg: float = 60.0   # горизонтальный FoV сенсора (L7CX ~60°/ось)
    fov_v_deg: float = 60.0   # вертикальный FoV
    flip_h: bool = True       # линза зеркалит сцену по горизонтали (зона 0 = право)
    flip_v: bool = True       # и по вертикали (зона 0 = верх)
    min_mm: int = 20          # отбрасывать ближе (кросстолк <60см ненадёжен, но 20 — край)
    max_mm: int = 3500        # дальше — за пределами L7CX


def _zone_angles(idx: int, res: int, fov_deg: float, flip: bool) -> float:
    """Угол центра зоны idx (0..res-1) в радианах относительно оси."""
    frac = (idx + 0.5) / res - 0.5          # -0.5..+0.5
    if flip:
        frac = -frac
    return math.radians(frac * fov_deg)


def grid_to_points(grid: list[int], res: int, cfg: CloudConfig):
    """
    Сетка мм (row-major, -1 = нет цели) → список (zone_row, zone_col, x, y, z) в метрах.
    Возвращает валидные точки (по min/max и target!=-1).
    """
    pts = []
    for r in range(res):
        av = _zone_angles(r, res, cfg.fov_v_deg, cfg.flip_v)
        for c in range(res):
            mm = grid[r * res + c]
            if mm is None or mm < 0 or mm < cfg.min_mm or mm > cfg.max_mm:
                continue
            ah = _zone_angles(c, res, cfg.fov_h_deg, cfg.flip_h)
            z = mm / 1000.0
            x = z * math.tan(ah)
            y = z * math.tan(av)
            pts.append((r, c, x, y, z))
    return pts


def zone_pixel(r: int, c: int, res: int, w: int, h: int, cfg: CloudConfig):
    """Пиксель кадра для зоны (r,c) — дальнепольное отображение зоны в регион."""
    fc = (c + 0.5) / res
    fr = (r + 0.5) / res
    if cfg.flip_h:
        fc = 1.0 - fc
    if cfg.flip_v:
        fr = 1.0 - fr
    u = min(w - 1, max(0, int(fc * w)))
    v = min(h - 1, max(0, int(fr * h)))
    return u, v


@dataclass
class Pose:
    """Поза кадра: поворот вокруг вертикали (yaw, рад) + сдвиг (м)."""
    yaw: float = 0.0
    tx: float = 0.0
    ty: float = 0.0
    tz: float = 0.0


def apply_pose(x: float, y: float, z: float, p: Pose):
    """Камерную точку → в мировой кадр по позе (yaw вокруг оси y=вверх)."""
    cy, sy = math.cos(p.yaw), math.sin(p.yaw)
    wx = cy * x + sy * z + p.tx
    wz = -sy * x + cy * z + p.tz
    wy = y + p.ty
    return wx, wy, wz


@dataclass
class PointCloud:
    """Накопитель цветного облака с воксельным прорежением."""
    voxel_m: float = 0.03
    _vox: dict = field(default_factory=dict)   # (i,j,k) -> (x,y,z,r,g,b)

    def add(self, x, y, z, rgb):
        key = (round(x / self.voxel_m), round(y / self.voxel_m), round(z / self.voxel_m))
        self._vox[key] = (x, y, z, rgb[0], rgb[1], rgb[2])

    def __len__(self):
        return len(self._vox)

    def write_ply(self, path: str):
        rows = list(self._vox.values())
        with open(path, "w", encoding="ascii") as f:
            f.write("ply\nformat ascii 1.0\n")
            f.write(f"element vertex {len(rows)}\n")
            f.write("property float x\nproperty float y\nproperty float z\n")
            f.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
            f.write("end_header\n")
            for x, y, z, r, g, b in rows:
                f.write(f"{x:.4f} {y:.4f} {z:.4f} {int(r)} {int(g)} {int(b)}\n")
        return len(rows)
