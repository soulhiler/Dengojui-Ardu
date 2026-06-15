"""
spatial.wifi_anchor — этап 3: Wi-Fi-якорь позиции против дрейфа.

Метод (docs/hardware/wifi-localization-method.md): робот на ходу пишет вектор RSSI
всех видимых AP, тегируя его СВОЕЙ координатой из world_model → радиокарта строится
сама, без ручного обмера. Потом по новому скану k-NN даёт грубый АБСОЛЮТНЫЙ фикс
(x,z) в координатах модели → сбрасывает дрейф одометрии/IMU и восстанавливает позу
после рестарта/«похищения». Wi-Fi = якорь ПОЗИЦИИ, не курс (курс — IMU+энкодеры).

Матчинг — SE-WKNN: сходство по РАНГУ силы AP (корреляция Спирмена), а не по
абсолютному RSSI → устойчиво к дрейфу уровня/устройства (изящный трюк из обзора).
Чистый Python, без зависимостей. Скан AP даёт прошивка (`GET /wifiscan`).
"""
from __future__ import annotations

import json
import math


def _ranks(vals):
    """Ранги значений (средний ранг при равенстве) — для корреляции Спирмена."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(a, b):
    """Ранговая корреляция Спирмена двух списков одной длины (в [-1,1])."""
    n = len(a)
    if n < 2:
        return 0.0
    ra, rb = _ranks(a), _ranks(b)
    ma = sum(ra) / n
    mb = sum(rb) / n
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    va = math.sqrt(sum((ra[i] - ma) ** 2 for i in range(n)))
    vb = math.sqrt(sum((rb[i] - mb) ** 2 for i in range(n)))
    if va < 1e-9 or vb < 1e-9:
        return 0.0
    return cov / (va * vb)


def similarity(query: dict, sample: dict) -> float:
    """SE-WKNN сходство скана query и отпечатка sample (оба {bssid: rssi})."""
    common = [k for k in query if k in sample]
    if len(common) < 2:
        # мало общих AP — слабое сходство, грубо по доле перекрытия
        union = set(query) | set(sample)
        return 0.05 * (len(common) / len(union)) if union else 0.0
    qa = [query[k] for k in common]
    sa = [sample[k] for k in common]
    rho = _spearman(qa, sa)                 # ранговое сходство (устойчиво к дрейфу)
    overlap = len(common) / len(set(query) | set(sample))
    return max(0.0, (rho + 1.0) / 2.0) * overlap


class WifiMap:
    """Радиокарта в координатах world_model: точки (x, z, scan{bssid:rssi})."""

    def __init__(self):
        self.pts = []   # list of (x, z, scan)

    def add(self, x: float, z: float, scan: dict, min_aps: int = 2):
        if not scan or len(scan) < min_aps:
            return False
        self.pts.append((float(x), float(z), {str(k): int(v) for k, v in scan.items()}))
        return True

    def __len__(self):
        return len(self.pts)

    def locate(self, scan: dict, k: int = 3):
        """k-NN по SE-WKNN → (x, z, confidence) или None. confidence = сумма весов k лучших."""
        if not self.pts or not scan:
            return None
        scored = [(similarity(scan, s), x, z) for (x, z, s) in self.pts]
        scored.sort(key=lambda t: t[0], reverse=True)
        top = [t for t in scored[:k] if t[0] > 0.0]
        if not top:
            return None
        wsum = sum(w for w, _, _ in top)
        if wsum <= 0:
            return None
        x = sum(w * px for w, px, _ in top) / wsum
        z = sum(w * pz for w, _, pz in top) / wsum
        return (x, z, wsum / len(top))   # conf 0..1 (среднее лучших весов)

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pts": self.pts}, f)
        return len(self.pts)

    def load(self, path: str):
        import os
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.pts = [(p[0], p[1], p[2]) for p in d.get("pts", [])]
        return len(self.pts)


def scan_from_wifiscan(js: dict) -> dict:
    """Ответ прошивки GET /wifiscan ({aps:[{bssid,rssi},...]}) → {bssid: rssi}."""
    out = {}
    for ap in (js or {}).get("aps", []):
        b = ap.get("bssid")
        r = ap.get("rssi")
        if b and isinstance(r, (int, float)):
            out[str(b)] = int(r)
    return out


if __name__ == "__main__":
    m = WifiMap()
    # три «места» с разными отпечатками
    m.add(0.0, 0.0, {"ap1": -40, "ap2": -70, "ap3": -85})
    m.add(2.0, 0.0, {"ap1": -65, "ap2": -50, "ap3": -80})
    m.add(0.0, 2.0, {"ap1": -80, "ap2": -75, "ap3": -45})
    # запрос рядом с местом 1, но весь уровень «просел» на 8 дБ (дрейф) — ранги те же
    q = {"ap1": -48, "ap2": -78, "ap3": -93}
    loc = m.locate(q, k=1)
    assert loc, "no match"
    x, z, conf = loc
    print(f"матч → x={x:.2f} z={z:.2f} conf={conf:.2f} (ожидали ~0,0)")
    assert abs(x) < 0.5 and abs(z) < 0.5, "SE-WKNN не устойчив к дрейфу уровня"
    # сохранение/загрузка
    import tempfile, os
    p = os.path.join(tempfile.gettempdir(), "wifimap.json")
    m.save(p)
    m2 = WifiMap(); n = m2.load(p)
    assert n == 3 and m2.locate(q, k=1)
    print("wifi_anchor self-test OK")
