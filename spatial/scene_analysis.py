#!/usr/bin/env python3
"""spatial.scene_analysis — разбор воксельной карты на СЛОИ сцены.

Трёхэтапная задача:
  Этап 1 — КОНТУР помещения (статичный каркас: стены, силуэт комнаты).
  Этап 2 — ОБЪЕКТЫ внутри контура (в т.ч. мебель — статичные крупные объекты).
  Этап 3 — ДИНАМИКА: какие объекты двигались (по miss-подписи и диффу проходов).

Здесь реализован общий разбор пола (floor_cells) и Этап 1 (room_contour).
Объекты и динамика — отдельными функциями (object_clusters, classify_dynamics),
подключаются по мере готовности. Зависимостей нет (stdlib), как и world_model.

Координаты: x — вправо, y — вверх, z — вперёд (метры). Контур считаем в плоскости
пола XZ. Запуск:  python spatial/scene_analysis.py [карта.json.gz]
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from world_model import WorldModel, L_CONFIDENT, MAX_RANGE_M  # noqa: E402


# ───────────────────────── чистка ─────────────────────────

def clean(model: WorldModel, max_range: float = MAX_RANGE_M, prune: bool = True,
          origin=(0.0, 0.0, 0.0)) -> dict:
    """Готовит карту к разбору: убирает выбросы глубины (дальше max_range от origin)
    и спекл-одиночки. Старые карты строились без гейта — тут чистим постфактум."""
    m = model.voxel_m
    ox, oy, oz = origin
    dead = []
    for (i, j, k), v in model.vox.items():
        x, y, z = i * m, j * m, k * m
        if math.sqrt((x - ox) ** 2 + (y - oy) ** 2 + (z - oz) ** 2) > max_range:
            dead.append((i, j, k))
    for k in dead:
        del model.vox[k]
    pruned = model.prune_isolated() if prune else 0
    return {"range_dropped": len(dead), "speckle_pruned": pruned, "left": len(model.vox)}


# ───────────────────────── проекция на пол ─────────────────────────

def floor_cells(model: WorldModel, cell_m: float = 0.06,
                l_thresh: float = L_CONFIDENT) -> dict:
    """Проекция уверенных вокселей на пол (XZ). Для каждой ячейки агрегируем:
      logodds(max), hits, miss, цвет(взвеш.), y_min/y_max(высота столба), count.
    hits/miss → подпись статика/динамики (Этап 3); y-разброс → стена/мебель (Этап 2)."""
    m = model.voxel_m
    cells = {}
    for (i, j, k), v in model.vox.items():
        lo = v[0]
        if lo < l_thresh:
            continue
        gx = round(i * m / cell_m)
        gz = round(k * m / cell_m)
        y = j * m
        c = cells.get((gx, gz))
        if c is None:
            cells[(gx, gz)] = {
                "lo": lo, "hits": v[4], "miss": v[6],
                "r": v[1] * lo, "g": v[2] * lo, "b": v[3] * lo, "w": lo,
                "ymin": y, "ymax": y, "n": 1,
            }
        else:
            c["lo"] = max(c["lo"], lo)
            c["hits"] += v[4]
            c["miss"] += v[6]
            c["r"] += v[1] * lo
            c["g"] += v[2] * lo
            c["b"] += v[3] * lo
            c["w"] += lo
            c["ymin"] = min(c["ymin"], y)
            c["ymax"] = max(c["ymax"], y)
            c["n"] += 1
    return cells


def cell_color(c: dict):
    w = c["w"] or 1.0
    return (int(c["r"] / w), int(c["g"] / w), int(c["b"] / w))


# ───────────────────────── Этап 1: контур ─────────────────────────

def _fill_gaps(radii, nbins):
    """Круговая линейная интерполяция пропущенных секторов (None) по ближайшим данным."""
    idx = [b for b in range(nbins) if radii[b] is not None]
    if not idx:
        return radii
    out = list(radii)
    for b in range(nbins):
        if out[b] is not None:
            continue
        # ближайший заполненный слева и справа (по кругу)
        lo = next((idx[k] for k in range(len(idx) - 1, -1, -1) if idx[k] <= b), idx[-1] - nbins)
        hi = next((idx[k] for k in range(len(idx)) if idx[k] >= b), idx[0] + nbins)
        rl = radii[lo % nbins]
        rh = radii[hi % nbins]
        span = (hi - lo) or 1
        out[b] = rl + (rh - rl) * (b - lo) / span
    return out


def _median_smooth(radii, nbins, win=2):
    """Круговой медианный фильтр радиусов — гасит одиночные выбросы дальности."""
    if win < 1:
        return radii
    out = [0.0] * nbins
    for b in range(nbins):
        w = sorted(radii[(b + d) % nbins] for d in range(-win, win + 1))
        out[b] = w[len(w) // 2]
    return out


def room_contour(cells: dict, cell_m: float = 0.06, nbins: int = 120, center=None, smooth=2):
    """Силуэт помещения радиальным обходом из центра: на каждый угловой сектор берём
    САМУЮ ДАЛЬНЮЮ занятую ячейку (это стена; ближняя мебель её не подменяет, а если
    мебель ЗАСЛОНЯЕТ стену — контур честно проваливается внутрь = окклюзия). Пропуски
    интерполируются, радиальный профиль сглаживается медианой (денойз глубины).

    Возвращает: polygon [(x,z)...] по секторам, coverage (доля секторов С ДАННЫМИ —
    насколько периметр реально отснят, до интерполяции), center (cx,cz)."""
    if not cells:
        return [], 0.0, (0.0, 0.0)
    if center is None:
        cx = sum(gx for gx, _ in cells) / len(cells) * cell_m
        cz = sum(gz for _, gz in cells) / len(cells) * cell_m
    else:
        cx, cz = center
    radii = [None] * nbins
    for (gx, gz), c in cells.items():
        x, z = gx * cell_m, gz * cell_m
        r = math.hypot(x - cx, z - cz)
        a = math.atan2(z - cz, x - cx)
        b = int((a + math.pi) / (2 * math.pi) * nbins) % nbins
        if radii[b] is None or r > radii[b]:
            radii[b] = r
    coverage = sum(1 for r in radii if r is not None) / nbins
    radii = _median_smooth(_fill_gaps(radii, nbins), nbins, smooth)
    polygon = []
    for b in range(nbins):
        a = -math.pi + 2 * math.pi * (b + 0.5) / nbins
        polygon.append((cx + radii[b] * math.cos(a), cz + radii[b] * math.sin(a)))
    return polygon, coverage, (cx, cz)


def contour_metrics(polygon, center):
    """Габариты и периметр контура (для отчёта)."""
    if len(polygon) < 3:
        return {"span_x": 0.0, "span_z": 0.0, "perimeter": 0.0, "points": len(polygon)}
    xs = [p[0] for p in polygon]
    zs = [p[1] for p in polygon]
    per = 0.0
    for a in range(len(polygon)):
        x0, z0 = polygon[a]
        x1, z1 = polygon[(a + 1) % len(polygon)]
        per += math.hypot(x1 - x0, z1 - z0)
    return {"span_x": max(xs) - min(xs), "span_z": max(zs) - min(zs),
            "perimeter": per, "points": len(polygon)}


# ───────────────────────── self-test (stdlib) ─────────────────────────

def selftest() -> bool:
    """Синтетика: квадратное «кольцо стен» 2×2 м (центр 0) + мебельный блок внутри.
    Контур должен повторить квадрат (span≈2 м, высокое покрытие), мебель не должна
    раздувать контур (она ближе к центру, чем стены)."""
    m = WorldModel(voxel_m=0.05)
    rgb = (180, 180, 180)
    # стены: кольцо по периметру [-1..1] м, шаг 0.05
    step = 0.05
    n = int(2.0 / step) + 1
    for t in range(n):
        c = -1.0 + t * step
        for (x, z) in [(-1.0, c), (1.0, c), (c, -1.0), (c, 1.0)]:
            for _ in range(4):
                m.integrate_point(x, 0.0, z, rgb, 1.0)
    # мебель: блок внутри около (0.3, 0.2)
    for dx in range(4):
        for dz in range(4):
            for _ in range(4):
                m.integrate_point(0.3 + dx * step, 0.0, 0.2 + dz * step, (120, 90, 60), 1.0)

    cells = floor_cells(m, cell_m=0.06)
    poly, cov, ctr = room_contour(cells, cell_m=0.06, center=(0.0, 0.0))
    mt = contour_metrics(poly, ctr)
    span_ok = 1.7 <= mt["span_x"] <= 2.3 and 1.7 <= mt["span_z"] <= 2.3
    cover_ok = cov >= 0.9
    # контур не должен «прилипнуть» к мебели: макс. радиус ~ к стене (>0.9 м), не к мебели
    rmax = max(math.hypot(x, z) for (x, z) in poly) if poly else 0
    wall_ok = rmax > 0.9
    ok = span_ok and cover_ok and wall_ok
    print("SCENE-ANALYSIS selftest: контур span=%.2f×%.2f м покрытие=%.0f%% rmax=%.2f -> %s"
          % (mt["span_x"], mt["span_z"], cov * 100, rmax, "OK" if ok else "FAIL"))
    return ok


def _report(path: str):
    m = WorldModel()
    if not m.load(path):
        print("карта не найдена:", path)
        return
    before = len(m.vox)
    cl = clean(m)
    cells = floor_cells(m)
    poly, cov, ctr = room_contour(cells)
    mt = contour_metrics(poly, ctr)
    print("карта %s: вокселей %d -> %d (выброшено дальних %d, спекл %d)"
          % (os.path.basename(path), before, cl["left"], cl["range_dropped"], cl["speckle_pruned"]))
    print("ячеек пола: %d | центр (%.2f, %.2f)" % (len(cells), ctr[0], ctr[1]))
    print("КОНТУР: точек %d, габарит %.2f×%.2f м, периметр %.2f м, покрытие периметра %.0f%%"
          % (mt["points"], mt["span_x"], mt["span_z"], mt["perimeter"], cov * 100))
    if cov < 0.6:
        print("  ⚠ покрытие низкое — комната отснята лишь частично (нужен объезд периметра)")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--selftest" in sys.argv or not args:
        ok = selftest()
        if not args:
            sys.exit(0 if ok else 1)
    for p in args:
        _report(p)
