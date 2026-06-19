"""
spatial.depth_fusion — этап 2: плотная МЕТРИЧЕСКАЯ глубина из RGB + 64 ToF-точек.

Идея (см. docs/hardware/camera-tof-spatial-model.md): моно-сеть (Depth Anything)
даёт ОТНОСИТЕЛЬНУЮ глубину на весь кадр; наши 64 ToF-зоны задают МЕТРИЧЕСКИЙ
масштаб через аффинный fit в обратной глубине: 1/Z = s·D + b (s,b — МНК 2×2 по
валидным зонам, опц. RANSAC). Профильный paper под наш сетап (VL53L5CX+Depth
Anything, MMS): AbsRel ~0.14.

Тяжёлое (моно-сеть, плотная карта) — на сервере с GPU/Jetson; здесь оно под
ленивым импортом torch. Ядро — `fit_scale_shift` — чистый Python, тестируется без
зависимостей. Бэкенд глубины подключаемый: DepthAnythingBackend (lazy torch) или
SynthBackend (тест без железа/сети).
"""
from __future__ import annotations

import math


# ───────────────────────── ядро: аффинный fit масштаба ─────────────────────────

def fit_scale_shift(rel_at_zones, tof_mm_at_zones, ransac_iters: int = 0):
    """
    Подгоняет 1/Z_метр = s·rel + b по парам (относит. глубина зоны, ToF мм).
    rel_at_zones, tof_mm_at_zones — списки одной длины; None/<=0 в ToF = пропуск.
    Возвращает (s, b, n_inliers) или None если валидных точек < 2.
    Решение МНК 2×2 (нормальные уравнения) в пространстве ОБРАТНОЙ глубины
    (так моно-disparity линейна метрической обратной глубине).
    """
    pairs = []
    for rel, mm in zip(rel_at_zones, tof_mm_at_zones):
        if mm is None or mm <= 0 or rel is None:
            continue
        inv_z = 1000.0 / mm  # 1/метр
        pairs.append((float(rel), inv_z))
    if len(pairs) < 2:
        return None

    def solve(ps):
        # минимизируем Σ(s·r + b − y)²  → 2×2 нормальные уравнения
        n = len(ps)
        sr = sum(r for r, _ in ps)
        syy = sum(y for _, y in ps)
        srr = sum(r * r for r, _ in ps)
        sry = sum(r * y for r, y in ps)
        det = n * srr - sr * sr
        if abs(det) < 1e-12:
            return None
        s = (n * sry - sr * syy) / det
        b = (syy - s * sr) / n
        return s, b

    if ransac_iters <= 0 or len(pairs) < 4:
        sol = solve(pairs)
        return (sol[0], sol[1], len(pairs)) if sol else None

    # RANSAC: устойчив к выбросам ToF (мультипас/тёмные цели)
    import random
    best = None
    best_in = -1
    for _ in range(ransac_iters):
        sample = random.sample(pairs, 2)
        sol = solve(sample)
        if not sol:
            continue
        s, b = sol
        # порог инлайера: медианный остаток (грубо)
        res = [abs(s * r + b - y) for r, y in pairs]
        thr = max(0.05, _median(res))  # 1/м
        inl = [p for p, e in zip(pairs, res) if e <= thr]
        if len(inl) > best_in:
            refit = solve(inl) or sol
            best = (refit[0], refit[1], len(inl))
            best_in = len(inl)
    return best


def _median(v):
    if not v:
        return 0.0
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def metric_from_rel(rel_value: float, s: float, b: float):
    """Относительная глубина пикселя → метры (через 1/Z = s·rel + b). None если <=0."""
    inv_z = s * rel_value + b
    if inv_z <= 1e-6:
        return None
    return 1.0 / inv_z


# ───────────────────────── подключаемые бэкенды глубины ─────────────────────────

class SynthBackend:
    """Тестовый бэкенд: относительная глубина = градиент (без torch/сети)."""

    def infer(self, w: int, h: int):
        # «дальше к верху кадра» — правдоподобный относительный disparity-профиль
        return [[1.0 - 0.6 * (j / max(1, h - 1)) for _ in range(w)] for j in range(h)]


class DepthAnythingBackend:
    """Depth Anything V2 через transformers (ленивый импорт; нужен torch на сервере).
       infer(image_pil) -> 2D-список относительной глубины (disparity, больше=ближе)."""

    def __init__(self, model_id: str = "depth-anything/Depth-Anything-V2-Small-hf", device: str = "cpu"):
        self.model_id = model_id
        self.device = device
        self._pipe = None

    def _ensure(self):
        if self._pipe is None:
            import os
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # xet-загрузка ломала preprocessor
            from transformers import pipeline  # ленивый: не нужен для тестов/без GPU
            # use_fast=False: у модели нет fast (torchvision) image-processor (transformers 5.x).
            self._pipe = pipeline("depth-estimation", model=self.model_id,
                                  device=self.device, use_fast=False)
        return self._pipe

    def infer(self, image_pil):
        import numpy as np
        out = self._ensure()(image_pil)
        d = np.asarray(out["depth"], dtype="float32")
        return d  # numpy 2D; пользователь сэмплит по зонам


# ───────────────────────── плотное облако (numpy) ─────────────────────────

def densify_to_points(rel_depth, tof_grid, res, w, h, intr, cfg_flip_h=True, cfg_flip_v=True,
                      ransac_iters=100):
    """
    rel_depth: 2D (h×w) относительная глубина (numpy или список списков).
    tof_grid: список res*res мм (-1 = нет). intr: (fx,fy,cx,cy) калиброванной камеры.
    Возвращает список (x,y,z) метров в кадре камеры (без цвета — цвет берёт вызывающий).
    Требует numpy (на сервере). Масштаб подгоняется по ToF-зонам (fit_scale_shift).
    """
    import numpy as np
    rd = np.asarray(rel_depth, dtype="float32")
    fx, fy, cx, cy = intr

    # относит. глубина в центрах ToF-зон + соответствующие мм
    rel_z, mm_z = [], []
    for r in range(res):
        for c in range(res):
            mm = tof_grid[r * res + c]
            fc = (c + 0.5) / res
            fr = (r + 0.5) / res
            if cfg_flip_h:
                fc = 1.0 - fc
            if cfg_flip_v:
                fr = 1.0 - fr
            u = min(w - 1, int(fc * w))
            v = min(h - 1, int(fr * h))
            rel_z.append(float(rd[v, u]))
            mm_z.append(mm)
    fit = fit_scale_shift(rel_z, mm_z, ransac_iters=ransac_iters)
    if not fit:
        return []
    s, b, _ = fit

    inv_z = s * rd + b
    inv_z = np.where(inv_z > 1e-3, inv_z, np.nan)
    Z = 1.0 / inv_z
    Z = np.where((Z > 0.05) & (Z < 6.0), Z, np.nan)  # отсечь нефизичное

    ys, xs = np.where(~np.isnan(Z))
    out = []
    for v, u in zip(ys.tolist(), xs.tolist()):
        z = float(Z[v, u])
        x = (u - cx) * z / fx
        y = (v - cy) * z / fy
        out.append((x, y, z))
    return out


# ───────────────────── склейка: кадр → плотные МИРОВЫЕ точки ─────────────────────

def intrinsics_from_fov(w: int, h: int, hfov_deg: float, vfov_deg: float | None = None):
    """Грубые интринсики камеры из FoV (нет калибровки): fx=(w/2)/tan(hfov/2).
    Квадратный пиксель по умолчанию (fy=fx). OV2640 со штатной линзой ~65° HFoV."""
    fx = (w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    fy = fx if vfov_deg is None else (h / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)
    return (fx, fy, w / 2.0, h / 2.0)


def frame_to_dense_world(jpeg_bytes, tof: dict, pose, backend, cam_hfov_deg: float = 65.0,
                         flip_h: bool = True, flip_v: bool = True, max_points: int = 8000,
                         weight: float = 0.5, ransac_iters: int = 100):
    """RGB-кадр + ToF + поза → генератор плотных МИРОВЫХ точек (wx, wy, wz, (r,g,b), w).

    Видео несёт ГЕОМЕТРИЮ: моно-сеть (backend) даёт относит. глубину на весь кадр,
    64 ToF-зоны задают МЕТРИЧЕСКИЙ масштаб (fit_scale_shift), цвет берём из кадра,
    поза переносит в мир. w<1 — плотные точки менее точны, чем ToF (мягче копятся).
    Нужны numpy + PIL. Кадр координат: x-вправо, y-вверх, z-вперёд (как tof_cloud)."""
    import io
    import numpy as np
    from PIL import Image
    from tof_cloud import apply_pose

    res = int(tof.get("res", 8))
    grid = tof.get("grid")
    if not grid or len(grid) < res * res:
        return
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    w, h = img.size
    rel = backend.infer(w, h) if isinstance(backend, SynthBackend) else backend.infer(img)
    rd = np.asarray(rel, dtype="float32")
    if rd.shape != (h, w):  # сеть могла вернуть свой размер — растянем под кадр
        pil = Image.fromarray(rd).resize((w, h))
        rd = np.asarray(pil, dtype="float32")

    fx, fy, cx, cy = intrinsics_from_fov(w, h, cam_hfov_deg)

    # масштаб по ToF-зонам (rel в центре зоны ↔ мм)
    rel_z, mm_z = [], []
    for r in range(res):
        for c in range(res):
            mm = grid[r * res + c]
            fc = (c + 0.5) / res
            fr = (r + 0.5) / res
            if flip_h:
                fc = 1.0 - fc
            if flip_v:
                fr = 1.0 - fr
            u = min(w - 1, int(fc * w))
            v = min(h - 1, int(fr * h))
            rel_z.append(float(rd[v, u]))
            mm_z.append(mm)
    fit = fit_scale_shift(rel_z, mm_z, ransac_iters=ransac_iters)
    if not fit:
        return
    s, b, _ = fit

    inv_z = s * rd + b
    Z = np.where(inv_z > 1e-3, 1.0 / np.maximum(inv_z, 1e-6), np.nan)
    Z = np.where((Z > 0.05) & (Z < 6.0), Z, np.nan)  # отсечь нефизичное
    ys, xs = np.where(~np.isnan(Z))
    n = len(xs)
    if n == 0:
        return
    if n > max_points:                              # равномерно прорядить
        idx = np.linspace(0, n - 1, max_points).astype(int)
        ys, xs = ys[idx], xs[idx]
    px = img.load()
    for v, u in zip(ys.tolist(), xs.tolist()):
        z = float(Z[v, u])
        x = (u - cx) * z / fx
        y = (cy - v) * z / fy                        # v вниз → y вверх
        wx, wy, wz = apply_pose(x, y, z, pose)
        yield (wx, wy, wz, px[u, v], weight)


def dense_points_from_depth(Z, rgb_pil, intr, pose, max_points: int = 8000, weight: float = 0.5):
    """Готовая метрическая карта глубины Z[H,W] (метры, NaN=нет) + RGB-кадр + поза →
    генератор плотных МИРОВЫХ точек (wx, wy, wz, (r,g,b), w). Без повторного инференса
    (для VO-drive: Z уже посчитана в metric_depth_map). intr=(fx,fy,cx,cy)."""
    import numpy as np
    from tof_cloud import apply_pose
    fx, fy, cx, cy = intr
    ys, xs = np.where(~np.isnan(Z))
    n = len(xs)
    if n == 0:
        return
    if n > max_points:
        idx = np.linspace(0, n - 1, max_points).astype(int)
        ys, xs = ys[idx], xs[idx]
    px = rgb_pil.load()
    for v, u in zip(ys.tolist(), xs.tolist()):
        z = float(Z[v, u])
        x = (u - cx) * z / fx
        y = (cy - v) * z / fy
        wx, wy, wz = apply_pose(x, y, z, pose)
        yield (wx, wy, wz, px[u, v], weight)


def metric_depth_map(jpeg_bytes, tof: dict, backend, cam_hfov_deg: float = 65.0,
                     flip_h: bool = True, flip_v: bool = True, ransac_iters: int = 100):
    """RGB-кадр + ToF → (depth_m [H,W] метры, gray [H,W] uint8, intr) — для VO (vo.py).
    Та же метрика по ToF-зонам, что frame_to_dense_world, но возвращаем КАРТУ глубины
    (а не облако): VO поднимает ORB-фичи в 3D по этой карте. Нужны numpy + PIL."""
    import io
    import numpy as np
    from PIL import Image

    res = int(tof.get("res", 8))
    grid = tof.get("grid")
    if not grid or len(grid) < res * res:
        return None
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    w, h = img.size
    rel = backend.infer(w, h) if isinstance(backend, SynthBackend) else backend.infer(img)
    rd = np.asarray(rel, dtype="float32")
    if rd.shape != (h, w):
        rd = np.asarray(Image.fromarray(rd).resize((w, h)), dtype="float32")
    intr = intrinsics_from_fov(w, h, cam_hfov_deg)
    rel_z, mm_z = [], []
    for r in range(res):
        for c in range(res):
            mm = grid[r * res + c]
            fc = (c + 0.5) / res
            fr = (r + 0.5) / res
            if flip_h:
                fc = 1.0 - fc
            if flip_v:
                fr = 1.0 - fr
            u = min(w - 1, int(fc * w))
            v = min(h - 1, int(fr * h))
            rel_z.append(float(rd[v, u]))
            mm_z.append(mm)
    fit = fit_scale_shift(rel_z, mm_z, ransac_iters=ransac_iters)
    if not fit:
        return None
    s, b, _ = fit
    inv_z = s * rd + b
    Z = np.where(inv_z > 1e-3, 1.0 / np.maximum(inv_z, 1e-6), np.nan).astype("float32")
    gray = np.asarray(img.convert("L"), dtype="uint8")
    return Z, gray, intr


if __name__ == "__main__":
    # self-test ядра без зависимостей
    rel = [0.9, 0.8, 0.7, 0.6, None, 0.5]
    tof = [1000, 1100, 1250, 1500, -1, 1900]  # мм
    f = fit_scale_shift(rel, tof)
    assert f, "fit failed"
    s, b, n = f
    # проверим обратную проверку: метр из rel ~ ToF
    z = metric_from_rel(0.9, s, b)
    print(f"fit s={s:.4f} b={b:.4f} n={n}; rel0.9 -> {z:.3f} м (ожидали ~1.0)")
    assert 0.8 < z < 1.2, "scale off"
    print("depth_fusion self-test OK")
