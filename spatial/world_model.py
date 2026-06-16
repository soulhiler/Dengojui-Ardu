"""
spatial.world_model — ПЕРСИСТЕНТНАЯ модель пространства (воксель + цвет + уверенность).

Накопитель мировой модели: каждый кадр вливается, уверенность зоны растёт
(log-odds от повторных наблюдений), цвет усредняется бегущим средним, при
желании старое затухает (decay). Сохраняется на диск (gzip-json), переживает
рестарт — модель «копится навсегда» и уточняется.

Координаты: x — вправо, y — вверх, z — вперёд, метры (как spatial.tof_cloud).
Зависимостей нет (чистый stdlib): работает на любом ПК/сервере.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import time
from dataclasses import dataclass, field

# log-odds занятости вокселя
L_HIT = 0.85          # прибавка за наблюдение «занято»
L_MIN = -2.0
L_MAX = 5.0
L_CONFIDENT = 1.5     # порог «уверенно занято» (≈ 2+ согласованных наблюдения)
_COLOR_SAT = 25       # насыщение бегущего среднего цвета (после N наблюдений цвет стабилен)


@dataclass
class WorldModel:
    """Воксельная карта: ключ (i,j,k) -> [logodds, r, g, b, n, last_t]."""
    voxel_m: float = 0.05
    vox: dict = field(default_factory=dict)
    updated: float = 0.0

    def _key(self, x: float, y: float, z: float):
        m = self.voxel_m
        return (round(x / m), round(y / m), round(z / m))

    def integrate_point(self, x, y, z, rgb, t):
        k = self._key(x, y, z)
        v = self.vox.get(k)
        if v is None:
            self.vox[k] = [L_HIT, float(rgb[0]), float(rgb[1]), float(rgb[2]), 1, t]
        else:
            v[0] = min(L_MAX, v[0] + L_HIT)
            n = v[4] + 1
            a = 1.0 / min(n, _COLOR_SAT)  # бегущее среднее цвета
            v[1] += (rgb[0] - v[1]) * a
            v[2] += (rgb[1] - v[2]) * a
            v[3] += (rgb[2] - v[3]) * a
            v[4] = n
            v[5] = t
        self.updated = t

    def integrate_frame(self, pts) -> int:
        """pts: итерабельно (x, y, z, (r,g,b)) в МИРОВЫХ координатах."""
        t = time.time()
        n = 0
        for (x, y, z, rgb) in pts:
            self.integrate_point(x, y, z, rgb, t)
            n += 1
        return n

    def decay(self, amount: float = 0.1):
        """Опц. затухание уверенности (динамичная сцена). Удаляет «выдохшиеся» воксели."""
        dead = []
        for k, v in self.vox.items():
            v[0] -= amount
            if v[0] <= L_MIN:
                dead.append(k)
        for k in dead:
            del self.vox[k]

    def confident_points(self, l_thresh: float = L_CONFIDENT, cap: int = 8000):
        """Список (x, y, z, r, g, b, logodds) для уверенно занятых вокселей."""
        out = []
        m = self.voxel_m
        for (i, j, k), v in self.vox.items():
            if v[0] >= l_thresh:
                out.append((i * m, j * m, k * m, int(v[1]), int(v[2]), int(v[3]), round(v[0], 2)))
        if len(out) > cap:
            step = len(out) // cap + 1
            out = out[::step]
        return out

    def score_world_points(self, pts, l_thresh: float = L_CONFIDENT, radius: int = 1):
        """Совпадение скана с картой (для релокализации курса по карте).
        Для каждой МИРОВОЙ точки (x, y, z, ...) берём макс. log-odds уверенно
        занятого вокселя в окрестности ±radius и суммируем. Чем выше score —
        тем лучше точки ложатся на уже известную занятую геометрию.
        Возвращает (score, hits)."""
        m = self.voxel_m
        score = 0.0
        hits = 0
        for p in pts:
            ci = round(p[0] / m)
            cj = round(p[1] / m)
            ck = round(p[2] / m)
            best = 0.0
            for di in range(-radius, radius + 1):
                for dj in range(-radius, radius + 1):
                    for dk in range(-radius, radius + 1):
                        v = self.vox.get((ci + di, cj + dj, ck + dk))
                        if v is not None and v[0] >= l_thresh and v[0] > best:
                            best = v[0]
            if best > 0.0:
                score += best
                hits += 1
        return score, hits

    def likelihood_score(self, pts, sigma_m: float = 0.06, radius: int = 2,
                         l_min: float = 0.6):
        """Likelihood-field скоринг скана по карте (Thrun, Probabilistic Robotics).
        Вместо жёсткого «попал/не попал» (score_world_points) — ГЛАДКОЕ поле:
        для каждой мировой точки берём макс. по окрестности ±radius величину
        occ_logodds · exp(-d²/2σ²), где d — расстояние до занятого вокселя.
        Шире бассейн сходимости, суб-вокс. точность, учитывает ВСЕ наблюдённые
        воксели с весом по уверенности (мягкий порог l_min, не только confident).
        Возвращает (score, hits)."""
        m = self.voxel_m
        inv2s2 = 1.0 / (2.0 * sigma_m * sigma_m)
        score = 0.0
        hits = 0
        for p in pts:
            ci = round(p[0] / m)
            cj = round(p[1] / m)
            ck = round(p[2] / m)
            best = 0.0
            for di in range(-radius, radius + 1):
                for dj in range(-radius, radius + 1):
                    for dk in range(-radius, radius + 1):
                        v = self.vox.get((ci + di, cj + dj, ck + dk))
                        if v is not None and v[0] >= l_min:
                            d2 = (di * di + dj * dj + dk * dk) * m * m
                            w = v[0] * math.exp(-d2 * inv2s2)
                            if w > best:
                                best = w
            if best > 0.0:
                score += best
                hits += 1
        return score, hits

    def occupancy_2d(self, cell_m: float = 0.10, l_thresh: float = L_CONFIDENT):
        """Проекция на пол (плоскость XZ) -> {(gx,gz): max_logodds} для навигации."""
        grid = {}
        m = self.voxel_m
        for (i, j, k), v in self.vox.items():
            if v[0] >= l_thresh:
                gx = round(i * m / cell_m)
                gz = round(k * m / cell_m)
                cur = grid.get((gx, gz), -9.0)
                if v[0] > cur:
                    grid[(gx, gz)] = v[0]
        return grid

    def stats(self) -> dict:
        if not self.vox:
            return {"voxels": 0, "confident": 0, "max_logodds": 0.0, "updated": self.updated}
        conf = sum(1 for v in self.vox.values() if v[0] >= L_CONFIDENT)
        return {
            "voxels": len(self.vox),
            "confident": conf,
            "max_logodds": round(max(v[0] for v in self.vox.values()), 2),
            "updated": self.updated,
            "voxel_m": self.voxel_m,
        }

    def save(self, path: str) -> int:
        rows = [
            [list(k), round(v[0], 3), int(v[1]), int(v[2]), int(v[3]), v[4], round(v[5], 1)]
            for k, v in self.vox.items()
        ]
        data = {"voxel_m": self.voxel_m, "updated": self.updated, "vox": rows}
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="ascii") as f:
            json.dump(data, f)
        os.replace(tmp, path)  # атомарная замена — не бьём файл при крэше
        return len(rows)

    def load(self, path: str) -> int:
        if not os.path.exists(path):
            return 0
        with gzip.open(path, "rt", encoding="ascii") as f:
            data = json.load(f)
        self.voxel_m = data.get("voxel_m", self.voxel_m)
        self.updated = data.get("updated", 0.0)
        self.vox = {}
        for row in data.get("vox", []):
            self.vox[tuple(row[0])] = [row[1], float(row[2]), float(row[3]), float(row[4]), row[5], row[6]]
        return len(self.vox)

    def clear(self):
        self.vox = {}
        self.updated = time.time()

    def write_ply(self, path: str, l_thresh: float = L_CONFIDENT) -> int:
        pts = self.confident_points(l_thresh, cap=10 ** 9)
        with open(path, "w", encoding="ascii") as f:
            f.write("ply\nformat ascii 1.0\n")
            f.write(f"element vertex {len(pts)}\n")
            f.write("property float x\nproperty float y\nproperty float z\n")
            f.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
            f.write("end_header\n")
            for x, y, z, r, g, b, _ in pts:
                f.write(f"{x:.4f} {y:.4f} {z:.4f} {r} {g} {b}\n")
        return len(pts)
