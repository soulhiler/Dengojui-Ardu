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
L_MISS = 0.4          # убавка за наблюдение «свободно» (луч прошёл насквозь). < L_HIT:
#                       реально занятая ячейка, видимая чаще занятой, чем свободной, выживает.
L_MIN = -2.0
L_MAX = 5.0
L_CONFIDENT = 1.5     # порог «уверенно занято» (≈ 2+ согласованных наблюдения)
MAX_RANGE_M = 6.0     # гейт выбросов: точки дальше масштаба комнаты — мусор глубины
_COLOR_SAT = 25       # насыщение бегущего среднего цвета (после N наблюдений цвет стабилен)


@dataclass
class WorldModel:
    """Воксельная карта: ключ (i,j,k) -> [logodds, r, g, b, hits, last_t, miss].
    hits — сколько раз воксель наблюдался занятым, miss — сколько раз луч прошёл
    сквозь него как сквозь свободное место. Пара hits/miss + last_t = временна́я
    подпись для различения СТАТИКА (стены, мебель) и ДИНАМИКИ (движущиеся объекты)."""
    voxel_m: float = 0.05
    vox: dict = field(default_factory=dict)
    updated: float = 0.0

    def _key(self, x: float, y: float, z: float):
        m = self.voxel_m
        return (round(x / m), round(y / m), round(z / m))

    def integrate_point(self, x, y, z, rgb, t, w: float = 1.0):
        """w — вес наблюдения (0..1] по уверенности зоны (из ToF sigma): чистая
        точка прибавляет полный L_HIT, шумная — меньше → уверенно занятые воксели
        копятся от хорошей геометрии быстрее, мусор не «дорастает» до confident."""
        add = L_HIT * w
        k = self._key(x, y, z)
        v = self.vox.get(k)
        if v is None:
            self.vox[k] = [min(L_MAX, add), float(rgb[0]), float(rgb[1]), float(rgb[2]), 1, t, 0]
        else:
            v[0] = min(L_MAX, v[0] + add)
            n = v[4] + 1
            a = 1.0 / min(n, _COLOR_SAT)  # бегущее среднее цвета
            v[1] += (rgb[0] - v[1]) * a
            v[2] += (rgb[1] - v[2]) * a
            v[3] += (rgb[2] - v[3]) * a
            v[4] = n
            v[5] = t
        self.updated = t

    def integrate_frame(self, pts) -> int:
        """pts: итерабельно в МИРОВЫХ координатах — (x, y, z, rgb) или
        (x, y, z, rgb, w), где w — вес наблюдения по уверенности (см. integrate_point)."""
        t = time.time()
        n = 0
        for p in pts:
            w = p[4] if len(p) > 4 else 1.0
            self.integrate_point(p[0], p[1], p[2], p[3], t, w)
            n += 1
        return n

    def integrate_ray(self, origin, x, y, z, rgb, t=None, w: float = 1.0,
                      max_range: float = MAX_RANGE_M) -> bool:
        """Наблюдение лучом из камеры `origin`=(ox,oy,oz) в мировую точку (x,y,z).
        КАРВИНГ: воксели вдоль луча (свободное место) ослабляются (miss++, logodds−=L_MISS),
        конечная точка усиливается (hit). Это даёт «забывание»: уехавший объект, сквозь
        чьё место теперь виден дальний фон, постепенно стирается → база для детекта движения.
        Гейт выбросов: точки дальше max_range игнорируются (мусор глубины). True — точка учтена."""
        t = time.time() if t is None else t
        ox, oy, oz = origin
        dx, dy, dz = x - ox, y - oy, z - oz
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist > max_range or dist < 1e-6:
            return False
        m = self.voxel_m
        hit_key = self._key(x, y, z)
        steps = int(dist / (m * 0.7))               # ~1.4 сэмпла на воксель
        carved = set()
        for s in range(1, steps):                    # пропускаем origin
            f = s / steps
            if f * dist > dist - m:                   # стоп за ~1 воксель до поверхности
                break
            k = self._key(ox + dx * f, oy + dy * f, oz + dz * f)
            if k == hit_key or k in carved:
                continue
            carved.add(k)
            v = self.vox.get(k)
            if v is not None:                         # ослабляем только наблюдённые ячейки
                v[0] -= L_MISS
                v[6] += 1
                v[5] = t
                if v[0] <= L_MIN:
                    del self.vox[k]
        self.integrate_point(x, y, z, rgb, t, w)
        return True

    def integrate_frame_rays(self, origin, pts) -> int:
        """Как integrate_frame, но с карвингом луча из общей точки камеры `origin`."""
        t = time.time()
        n = 0
        for p in pts:
            w = p[4] if len(p) > 4 else 1.0
            if self.integrate_ray(origin, p[0], p[1], p[2], p[3], t, w):
                n += 1
        return n

    def prune_isolated(self, l_thresh: float = L_CONFIDENT, min_neighbors: int = 1) -> int:
        """Денойз: удаляет уверенные воксели-одиночки (спекл глубины) — без занятых
        соседей в 26-окрестности. Возвращает число удалённых."""
        keys = set(self.vox.keys())
        dead = []
        for (i, j, k), v in self.vox.items():
            if v[0] < l_thresh:
                continue
            nb = 0
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for dk in (-1, 0, 1):
                        if di == dj == dk == 0:
                            continue
                        if (i + di, j + dj, k + dk) in keys:
                            nb += 1
                if nb >= min_neighbors:
                    break
            if nb < min_neighbors:
                dead.append((i, j, k))
        for k in dead:
            del self.vox[k]
        return len(dead)

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
            [list(k), round(v[0], 3), int(v[1]), int(v[2]), int(v[3]), v[4], round(v[5], 1), v[6]]
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
            miss = row[7] if len(row) > 7 else 0          # старые карты — без miss
            self.vox[tuple(row[0])] = [row[1], float(row[2]), float(row[3]), float(row[4]),
                                       row[5], row[6], miss]
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


# ───────────────────────── self-test (stdlib) ─────────────────────────

def selftest() -> bool:
    """Проверяем карвинг свободного пространства и «забывание» уехавшего объекта.
    Сцена: камера в (0,0,0) смотрит вдоль +Z. Стена на z=2.0. Объект на z=1.0.
    Сначала видим объект (он ближе стены). Потом объект «уезжает» — лучи проходят
    сквозь его место до стены → его воксель должен ослабнуть и исчезнуть, а стена жить."""
    m = WorldModel(voxel_m=0.05)
    origin = (0.0, 0.0, 0.0)
    rgb = (200, 200, 200)
    obj_key = m._key(0.0, 0.0, 1.0)
    wall_key = m._key(0.0, 0.0, 2.0)

    # фаза 1: объект на месте — 5 наблюдений объекта (луч до z=1.0)
    for _ in range(5):
        m.integrate_ray(origin, 0.0, 0.0, 1.0, rgb, t=1.0)
    obj_after_seen = m.vox.get(obj_key)
    seen_ok = obj_after_seen is not None and obj_after_seen[4] >= 5

    # фаза 2: объект уехал — 12 наблюдений ДАЛЬНЕЙ стены (луч идёт СКВОЗЬ место объекта)
    for _ in range(12):
        m.integrate_ray(origin, 0.0, 0.0, 2.0, rgb, t=2.0)
    obj_after_gone = m.vox.get(obj_key)
    wall = m.vox.get(wall_key)

    # «забыт» = больше НЕ уверенно занят (logodds < порога) либо удалён совсем;
    # и miss-счётчик зафиксировал прохождения сквозь место объекта.
    forgotten = (obj_after_gone is None or obj_after_gone[0] < L_CONFIDENT) \
        and (obj_after_gone is None or obj_after_gone[6] >= 10)
    wall_alive = wall is not None and wall[0] >= L_CONFIDENT
    wall_misses_low = wall is not None and wall[6] == 0    # сквозь стену не светили

    # гейт выбросов: точка за 50 м — игнор
    gated = m.integrate_ray(origin, 0.0, 0.0, 50.0, rgb) is False

    # денойз: одиночный уверенный воксель удаляется
    m2 = WorldModel(voxel_m=0.05)
    for _ in range(3):
        m2.integrate_point(5.0, 0.0, 0.0, rgb, 1.0)       # одиночка, уверенный
    pruned = m2.prune_isolated() == 1

    ok = seen_ok and forgotten and wall_alive and wall_misses_low and gated and pruned
    objlo = ("%.2f" % obj_after_gone[0]) if obj_after_gone else "удалён"
    objmiss = obj_after_gone[6] if obj_after_gone else "—"
    print("WORLD-MODEL selftest: объект виден=%s -> забыт=%s (logodds=%s, miss=%s) | "
          "стена жива=%s (miss=%s) | гейт=%s денойз=%s -> %s"
          % (seen_ok, forgotten, objlo, objmiss, wall_alive, wall[6] if wall else "—",
             gated, pruned, "OK" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if selftest() else 1)
