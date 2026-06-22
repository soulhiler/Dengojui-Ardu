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


def _angle_bin(x, z, cx, cz, nbins):
    a = math.atan2(z - cz, x - cx)
    return int((a + math.pi) / (2 * math.pi) * nbins) % nbins


def contour_radii(cells: dict, cell_m: float = 0.06, nbins: int = 120, center=None, smooth=2):
    """Радиальный профиль контура: на каждый угловой сектор — самая дальняя занятая
    ячейка (стена). Пропуски интерполируются, профиль сглаживается медианой.
    Возвращает (radii[nbins], center(cx,cz), coverage). Переиспользуется и контуром,
    и классификацией «стена vs нутро» (Этап 2)."""
    if not cells:
        return [0.0] * nbins, (0.0, 0.0), 0.0
    if center is None:
        cx = sum(gx for gx, _ in cells) / len(cells) * cell_m
        cz = sum(gz for _, gz in cells) / len(cells) * cell_m
    else:
        cx, cz = center
    radii = [None] * nbins
    for (gx, gz) in cells:
        x, z = gx * cell_m, gz * cell_m
        r = math.hypot(x - cx, z - cz)
        b = _angle_bin(x, z, cx, cz, nbins)
        if radii[b] is None or r > radii[b]:
            radii[b] = r
    coverage = sum(1 for r in radii if r is not None) / nbins
    radii = _median_smooth(_fill_gaps(radii, nbins), nbins, smooth)
    return radii, (cx, cz), coverage


def room_contour(cells: dict, cell_m: float = 0.06, nbins: int = 120, center=None, smooth=2):
    """Силуэт помещения (полигон) из радиального профиля. Самая дальняя занятая ячейка
    в секторе = стена; ближняя мебель её не подменяет, а заслонённая стена честно даёт
    провал контура внутрь (окклюзия). Возвращает (polygon[(x,z)], coverage, center)."""
    radii, (cx, cz), coverage = contour_radii(cells, cell_m, nbins, center, smooth)
    polygon = []
    for b in range(nbins):
        a = -math.pi + 2 * math.pi * (b + 0.5) / nbins
        polygon.append((cx + radii[b] * math.cos(a), cz + radii[b] * math.sin(a)))
    return polygon, coverage, (cx, cz)


# ───────────────────────── Этап 2: объекты / мебель ─────────────────────────

def _connected(cell_set):
    """Связные компоненты на сетке (8-соседство). cell_set: множество (gx,gz)."""
    seen = set()
    comps = []
    for start in cell_set:
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        comp = []
        while stack:
            gx, gz = stack.pop()
            comp.append((gx, gz))
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if dx == 0 and dz == 0:
                        continue
                    nb = (gx + dx, gz + dz)
                    if nb in cell_set and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        comps.append(comp)
    return comps


def object_clusters(cells: dict, cell_m: float = 0.06, nbins: int = 120, center=None,
                    wall_margin_m: float = 0.20, min_cells: int = 4,
                    furniture_area_m2: float = 0.06):
    """Этап 2: объекты ВНУТРИ контура (стены и пристенок исключаем по радиусу), затем
    кластеризация связных ячеек. Каждый объект — словарь: центр (x,z), габарит, площадь
    следа, высота (ymin..ymax), цвет, hits/miss (для Этапа 3), класс (мебель/мелкий).

    Мебель = крупный статичный объект (площадь следа >= furniture_area_m2). «Двигался ли
    он» решает Этап 3 — мебель тоже может переместиться."""
    radii, (cx, cz), _ = contour_radii(cells, cell_m, nbins, center)
    interior = {}
    for (gx, gz), c in cells.items():
        x, z = gx * cell_m, gz * cell_m
        r = math.hypot(x - cx, z - cz)
        b = _angle_bin(x, z, cx, cz, nbins)
        if r < radii[b] - wall_margin_m:          # внутри, не у стены
            interior[(gx, gz)] = c
    objs = []
    for comp in _connected(set(interior)):
        if len(comp) < min_cells:
            continue
        gxs = [g[0] for g in comp]
        gzs = [g[1] for g in comp]
        r = g_ = bl = 0.0
        hits = miss = 0
        wsum = 0.0
        ymin = ymax = None
        for k in comp:
            c = interior[k]
            r += c["r"]; g_ += c["g"]; bl += c["b"]; wsum += c["w"]
            hits += c["hits"]; miss += c["miss"]
            ymin = c["ymin"] if ymin is None else min(ymin, c["ymin"])
            ymax = c["ymax"] if ymax is None else max(ymax, c["ymax"])
        wsum = wsum or 1.0
        area = len(comp) * cell_m * cell_m
        objs.append({
            "cx": (sum(gxs) / len(gxs)) * cell_m, "cz": (sum(gzs) / len(gzs)) * cell_m,
            "size_x": (max(gxs) - min(gxs) + 1) * cell_m,
            "size_z": (max(gzs) - min(gzs) + 1) * cell_m,
            "cells": len(comp), "area_m2": area,
            "y_min": ymin, "y_max": ymax, "height": (ymax - ymin) if ymin is not None else 0.0,
            "color": (int(r / wsum), int(g_ / wsum), int(bl / wsum)),
            "hits": hits, "miss": miss,
            "dyn_ratio": miss / (hits + miss) if (hits + miss) else 0.0,
            "kind": "мебель/крупный" if area >= furniture_area_m2 else "мелкий",
            "_comp": comp,
        })
    objs.sort(key=lambda o: o["area_m2"], reverse=True)
    return objs, (cx, cz)


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
    # мебель: блок 8×8 внутри около (0.3, 0.2) — крупный статичный объект
    for dx in range(8):
        for dz in range(8):
            for _ in range(4):
                m.integrate_point(0.3 + dx * step, 0.0, 0.2 + dz * step, (120, 90, 60), 1.0)

    cells = floor_cells(m, cell_m=0.06)
    poly, cov, ctr = room_contour(cells, cell_m=0.06, center=(0.0, 0.0))
    mt = contour_metrics(poly, ctr)
    span_ok = 1.7 <= mt["span_x"] <= 2.3 and 1.7 <= mt["span_z"] <= 2.3
    cover_ok = cov >= 0.9
    rmax = max(math.hypot(x, z) for (x, z) in poly) if poly else 0
    wall_ok = rmax > 0.9                                   # контур у стены, не у мебели

    objs, _ = object_clusters(cells, cell_m=0.06, center=(0.0, 0.0))
    furn = [o for o in objs if o["kind"] == "мебель/крупный"]
    obj_ok = len(furn) >= 1
    near_ok = obj_ok and 0.2 <= furn[0]["cx"] <= 0.8 and 0.1 <= furn[0]["cz"] <= 0.7
    # мебель не должна попасть в стену: её центр заметно ближе центра, чем стена
    inside_ok = obj_ok and math.hypot(furn[0]["cx"], furn[0]["cz"]) < 0.9

    ok = span_ok and cover_ok and wall_ok and obj_ok and near_ok and inside_ok
    print("SCENE-ANALYSIS selftest: контур %.2f×%.2f м (%.0f%%, rmax=%.2f) | объектов=%d "
          "мебель=%d центр(%.2f,%.2f) площадь=%.2fм² -> %s"
          % (mt["span_x"], mt["span_z"], cov * 100, rmax, len(objs), len(furn),
             furn[0]["cx"] if furn else 0, furn[0]["cz"] if furn else 0,
             furn[0]["area_m2"] if furn else 0, "OK" if ok else "FAIL"))
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

    objs, _ = object_clusters(cells)
    print("ОБЪЕКТЫ внутри контура: %d (мебель/крупных %d)"
          % (len(objs), sum(1 for o in objs if o["kind"] == "мебель/крупный")))
    for i, o in enumerate(objs[:8]):
        print("  #%d %-13s центр(%.2f,%.2f) %.2f×%.2f м, площадь %.2f м², "
              "цвет#%02x%02x%02x, miss/(h+m)=%.0f%%"
              % (i, o["kind"], o["cx"], o["cz"], o["size_x"], o["size_z"], o["area_m2"],
                 o["color"][0], o["color"][1], o["color"][2], o["dyn_ratio"] * 100))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--selftest" in sys.argv or not args:
        ok = selftest()
        if not args:
            sys.exit(0 if ok else 1)
    for p in args:
        _report(p)
