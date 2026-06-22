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


WALL_PCT = 0.88         # перцентиль радиусов в секторе как «дальняя стена» (робастно к выбросам)


def contour_radii(cells: dict, cell_m: float = 0.06, nbins: int = 120, center=None, smooth=3,
                  pct: float = WALL_PCT):
    """Радиальный профиль СТЕН: в каждом секторе радиус стены = `pct`-перцентиль радиусов
    занятых ячеек (не абсолютный максимум). Это отбрасывает 1–2 самых дальних выброса
    глубины, но СОХРАНЯЕТ разреженные дальние стены (важно: робот к ним не подъезжал, там
    мало вокселей). Затем межсекторное медианное сглаживание гасит одиночные сектора.
    Возвращает (radii, center, coverage). Переиспользуется контуром и классификацией стена/нутро."""
    if not cells:
        return [0.0] * nbins, (0.0, 0.0), 0.0
    if center is None:
        cx = sum(gx for gx, _ in cells) / len(cells) * cell_m
        cz = sum(gz for _, gz in cells) / len(cells) * cell_m
    else:
        cx, cz = center
    sect = [[] for _ in range(nbins)]                       # радиусы занятых ячеек по секторам
    for (gx, gz) in cells:
        x, z = gx * cell_m, gz * cell_m
        sect[_angle_bin(x, z, cx, cz, nbins)].append(math.hypot(x - cx, z - cz))
    radii = [None] * nbins
    for b in range(nbins):
        rs = sorted(sect[b])
        if rs:
            radii[b] = rs[min(len(rs) - 1, int(pct * len(rs)))]   # робастный «дальний»
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


def _perp_dist(p, a, b):
    (px, pz), (ax, az), (bx, bz) = p, a, b
    dx, dz = bx - ax, bz - az
    L = math.hypot(dx, dz)
    if L < 1e-9:
        return math.hypot(px - ax, pz - az)
    return abs((px - ax) * dz - (pz - az) * dx) / L


def _rdp(pts, eps):
    """Ramer–Douglas–Peucker: прорежает ломаную, оставляя вершины-«углы» (отклонение > eps)."""
    if len(pts) < 3:
        return list(pts)
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        d = _perp_dist(pts[i], pts[0], pts[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return _rdp(pts[:idx + 1], eps)[:-1] + _rdp(pts[idx:], eps)
    return [pts[0], pts[-1]]


def room_walls(cells: dict, cell_m: float = 0.06, nbins: int = 120, center=None,
               eps: float = 0.12, min_wall_m: float = 0.4):
    """Стены комнаты из контура: спрямляем зубчатый силуэт (RDP) в прямые сегменты.
    Сегмент длиннее `min_wall_m` = СТЕНА (короткие — углы/проёмы/шум). Возвращает
    dict: polygon (сырой контур), vertices (углы), walls [(p0,p1,len)], coverage, center."""
    poly, coverage, ctr = room_contour(cells, cell_m, nbins, center)
    verts = _rdp(poly + [poly[0]], eps)                     # замыкаем перед прорежением
    walls = []
    for i in range(len(verts) - 1):
        p0, p1 = verts[i], verts[i + 1]
        L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if L >= min_wall_m:
            walls.append((p0, p1, L))
    return {"polygon": poly, "vertices": verts, "walls": walls,
            "coverage": coverage, "center": ctr}


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


# ───────────────────────── Этап 3: статика vs динамика ─────────────────────────

DYN_RATIO = 0.30      # порог «двигался» по одно-карточной miss-подписи


def label_dynamics(objs, dyn_ratio: float = DYN_RATIO):
    """Одно-карточная разметка: объект «двигался», если сквозь его место часто светили
    насквозь (miss/(hits+miss) высок) — т.е. он там был, но потом исчезал. Работает,
    когда карта строилась с карвингом луча (integrate_ray); на старых картах miss=0."""
    for o in objs:
        o["moved"] = o["dyn_ratio"] >= dyn_ratio
    return objs


def diff_maps(model_a, model_b, cell_m: float = 0.06, l_thresh: float = L_CONFIDENT,
              min_cells: int = 3):
    """Дифф ДВУХ проходов (должны быть в одной системе координат — релокализованы).
    Ячейка занята в A и пуста в B → объект УШЁЛ; пуста в A, занята в B → ПРИШЁЛ;
    занята в обоих → статика (стены/мебель на месте). Кластеры изменившихся ячеек =
    движущиеся объекты. Это надёжный детектор движения (не зависит от карвинга)."""
    ca = floor_cells(model_a, cell_m, l_thresh)
    cb = floor_cells(model_b, cell_m, l_thresh)
    ka, kb = set(ca), set(cb)
    vanished = ka - kb
    appeared = kb - ka
    stable = ka & kb
    moved = []
    for comp in _connected(vanished | appeared):
        if len(comp) < min_cells:
            continue
        van = sum(1 for k in comp if k in vanished)
        app = sum(1 for k in comp if k in appeared)
        gxs = [g[0] for g in comp]
        gzs = [g[1] for g in comp]
        moved.append({
            "cx": sum(gxs) / len(gxs) * cell_m, "cz": sum(gzs) / len(gzs) * cell_m,
            "cells": len(comp), "vanished": van, "appeared": app,
            "state": "ушёл" if app == 0 else ("пришёл" if van == 0 else "переместился"),
        })
    moved.sort(key=lambda o: o["cells"], reverse=True)
    return {"stable_cells": len(stable), "vanished_cells": len(vanished),
            "appeared_cells": len(appeared), "moved_objects": moved}


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
    # стены: кольцо по периметру [-1..1] м, шаг 0.05, ТОЛЩИНОЙ 3 вокселя внутрь
    # (реальная стена из глубины — полоса; для density-поддержки нужна толщина).
    step = 0.05
    n = int(2.0 / step) + 1
    for t in range(n):
        c = -1.0 + t * step
        for d in range(3):
            dd = d * step
            for (x, z) in [(-1.0 + dd, c), (1.0 - dd, c), (c, -1.0 + dd), (c, 1.0 - dd)]:
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

    # стены: квадратное кольцо должно спрямиться в немного длинных сегментов
    rw = room_walls(cells, cell_m=0.06, center=(0.0, 0.0))
    walls_ok = 3 <= len(rw["walls"]) <= 8 and any(w[2] > 1.5 for w in rw["walls"])

    ok = span_ok and cover_ok and wall_ok and obj_ok and near_ok and inside_ok and walls_ok
    print("SCENE-ANALYSIS selftest: контур %.2f×%.2f м (%.0f%%, rmax=%.2f) | стен=%d | объектов=%d "
          "мебель=%d центр(%.2f,%.2f) площадь=%.2fм² -> %s"
          % (mt["span_x"], mt["span_z"], cov * 100, rmax, len(rw["walls"]), len(objs), len(furn),
             furn[0]["cx"] if furn else 0, furn[0]["cz"] if furn else 0,
             furn[0]["area_m2"] if furn else 0, "OK" if ok else "FAIL"))
    return ok


def selftest_dynamics() -> bool:
    """Синтетика динамики: статичная стена (одинакова в обоих проходах) + объект,
    который ПЕРЕЕХАЛ из (0.5,0) в (-0.5,0). Дифф должен: стену не трогать, у (0.5,0)
    показать 'ушёл', у (-0.5,0) — 'пришёл'."""
    def build(obj_x):
        m = WorldModel(voxel_m=0.05)
        for t in range(41):                                # статичная стена z=+1.0
            x = -1.0 + t * 0.05
            for _ in range(4):
                m.integrate_point(x, 0.0, 1.0, (180, 180, 180), 1.0)
        for dx in range(6):                                # объект 6×6 у obj_x
            for dz in range(6):
                for _ in range(4):
                    m.integrate_point(obj_x + dx * 0.05, 0.0, dz * 0.05, (130, 90, 60), 1.0)
        return m

    a = build(0.5)
    b = build(-0.5)
    d = diff_maps(a, b, cell_m=0.06)
    mv = d["moved_objects"]
    left = [o for o in mv if o["state"] == "ушёл"]
    came = [o for o in mv if o["state"] == "пришёл"]
    left_ok = any(o["cx"] > 0.3 for o in left)
    came_ok = any(o["cx"] < -0.3 for o in came)
    stable_ok = d["stable_cells"] >= 10                    # стена осталась стабильной
    ok = left_ok and came_ok and stable_ok
    print("SCENE-ANALYSIS dynamics selftest: стабильно=%d ушёл=%d пришёл=%d "
          "(объект (0.5,0)->(-0.5,0)) -> %s"
          % (d["stable_cells"], len(left), len(came), "OK" if ok else "FAIL"))
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

    rw = room_walls(cells)
    print("СТЕНЫ: %d сегментов (после спрямления), углов %d"
          % (len(rw["walls"]), len(rw["vertices"]) - 1))
    for i, (p0, p1, L) in enumerate(sorted(rw["walls"], key=lambda w: -w[2])[:6]):
        print("  стена %d: (%.2f,%.2f)→(%.2f,%.2f) длина %.2f м" % (i, p0[0], p0[1], p1[0], p1[1], L))

    objs, _ = object_clusters(cells)
    print("ОБЪЕКТЫ внутри контура: %d (мебель/крупных %d)"
          % (len(objs), sum(1 for o in objs if o["kind"] == "мебель/крупный")))
    for i, o in enumerate(objs[:8]):
        print("  #%d %-13s центр(%.2f,%.2f) %.2f×%.2f м, площадь %.2f м², "
              "цвет#%02x%02x%02x, miss/(h+m)=%.0f%%"
              % (i, o["kind"], o["cx"], o["cz"], o["size_x"], o["size_z"], o["area_m2"],
                 o["color"][0], o["color"][1], o["color"][2], o["dyn_ratio"] * 100))


def _diff_report(path_a: str, path_b: str):
    a, b = WorldModel(), WorldModel()
    if not a.load(path_a) or not b.load(path_b):
        print("не загрузить одну из карт"); return
    clean(a); clean(b)
    d = diff_maps(a, b)
    print("ДИФФ %s vs %s: стабильно %d | исчезло %d | появилось %d ячеек"
          % (os.path.basename(path_a), os.path.basename(path_b),
             d["stable_cells"], d["vanished_cells"], d["appeared_cells"]))
    print("ДВИГАВШИЕСЯ объекты: %d" % len(d["moved_objects"]))
    for i, o in enumerate(d["moved_objects"][:10]):
        print("  #%d %-12s центр(%.2f,%.2f) ячеек=%d (исчез=%d появ=%d)"
              % (i, o["state"], o["cx"], o["cz"], o["cells"], o["vanished"], o["appeared"]))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--selftest" in sys.argv or not args:
        ok = selftest() and selftest_dynamics()
        if not args:
            sys.exit(0 if ok else 1)
    if len(args) == 2:                                     # дифф двух проходов (Этап 3)
        _diff_report(args[0], args[1])
    else:
        for p in args:
            _report(p)
