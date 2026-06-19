#!/usr/bin/env python3
"""spatial.vo — Этап B.1: RGB-D визуальная одометрия (относительная 6-DOF поза).

У нас уже есть МЕТРИЧЕСКАЯ глубина на кадр (Depth Anything × ToF, см. depth_fusion).
Значит для позы между кадрами не нужен моно-SLAM с неоднозначным масштабом —
делаем RGB-D одометрию:
  1) ORB-фичи в двух кадрах + матч (OpenCV);
  2) по глубине поднимаем матчи в 3D (метры) в каждом кадре;
  3) Umeyama (Kabsch) + RANSAC -> жёсткое (R, t): cam_k -> cam_{k-1};
  4) накопление T_world; IMU даёт надёжный поворот (можно подменить yaw).

Ядро (umeyama, RANSAC, матрицы поз) — ЧИСТЫЙ numpy, тестируется без OpenCV
(--selftest). OpenCV нужен только для ORB на реальных кадрах (ленивый импорт).

Кадр координат: x-вправо, y-вверх, z-вперёд (как depth_fusion/tof_cloud).
"""
from __future__ import annotations

import math
import sys

import numpy as np


# ───────────────────────── ядро: жёсткое выравнивание 3D-3D ─────────────────────────

def umeyama(A: np.ndarray, B: np.ndarray):
    """Жёсткое (R, t) без масштаба (метрика уже есть): B ≈ R·A + t.
    A, B — (N,3) соответствующие 3D-точки. Кабш/Umeyama через SVD."""
    ca = A.mean(axis=0)
    cb = B.mean(axis=0)
    AA = A - ca
    BB = B - cb
    H = AA.T @ BB
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    t = cb - R @ ca
    return R, t


def umeyama_ransac(A: np.ndarray, B: np.ndarray, thresh_m: float = 0.05,
                   iters: int = 200, min_inliers: int = 6, seed: int = 12345):
    """RANSAC-обёртка: устойчиво к ложным матчам/выбросам глубины.
    Возвращает (R, t, inlier_mask) или None."""
    n = len(A)
    if n < 3:
        return None
    rng = np.random.default_rng(seed)
    best_R, best_t, best_in = None, None, None
    best_cnt = -1
    thr2 = thresh_m * thresh_m
    for _ in range(iters):
        idx = rng.choice(n, 3, replace=False)
        try:
            R, t = umeyama(A[idx], B[idx])
        except np.linalg.LinAlgError:
            continue
        res = B - (A @ R.T + t)
        d2 = np.einsum("ij,ij->i", res, res)
        mask = d2 < thr2
        cnt = int(mask.sum())
        if cnt > best_cnt:
            best_cnt, best_R, best_t, best_in = cnt, R, t, mask
    if best_cnt < min_inliers:
        return None
    R, t = umeyama(A[best_in], B[best_in])     # рефит по инлайерам
    return R, t, best_in


def estimate_translation_ransac(A: np.ndarray, B: np.ndarray, R: np.ndarray,
                                thresh_m: float = 0.05, iters: int = 200,
                                min_inliers: int = 6, seed: int = 123):
    """Трансляция t при ИЗВЕСТНОМ повороте R (из IMU): B ≈ R·A + t.
    Намного устойчивее полного 6-DOF — IMU уже дал надёжный курс, а из RGB-D
    матчей берём только сдвиг. RANSAC по одной точке (t = B_i − R·A_i).
    Возвращает (t, inlier_mask) или None."""
    n = len(A)
    if n < 1:
        return None
    cand = B - (A @ R.T)                 # (N,3): кандидат t от каждой точки
    rng = np.random.default_rng(seed)
    best_t, best_in, best_cnt = None, None, -1
    thr2 = thresh_m * thresh_m
    for _ in range(iters):
        t = cand[rng.integers(n)]
        res = cand - t
        d2 = np.einsum("ij,ij->i", res, res)
        mask = d2 < thr2
        cnt = int(mask.sum())
        if cnt > best_cnt:
            best_cnt, best_t, best_in = cnt, t, mask
    if best_cnt < min_inliers:
        return None
    return cand[best_in].mean(axis=0), best_in    # рефит = среднее по инлайерам


def R_yaw(rad: float) -> np.ndarray:
    """Поворот вокруг вертикали (y-вверх) на угол rad. cam_k → cam_{k-1}."""
    c, s = math.cos(rad), math.sin(rad)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


# ───────────────────────── матрицы поз 4×4 ─────────────────────────

def make_T(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = t
    return T


def invert_T(T: np.ndarray) -> np.ndarray:
    R = T[:3, :3]
    t = T[:3, 3]
    Ti = np.eye(4)
    Ti[:3, :3] = R.T
    Ti[:3, 3] = -R.T @ t
    return Ti


def yaw_of(R: np.ndarray) -> float:
    """Курс (вокруг оси y-вверх) из матрицы поворота, рад."""
    return math.atan2(R[0, 2], R[2, 2])


# ───────────────────────── OpenCV: фичи и поза по кадрам ─────────────────────────

def _lift(uv, depth, intr):
    """Пиксели (u,v) + карта глубины (метры) -> 3D (x,y,z) в кадре камеры.
    None-точки (нет/нефизичная глубина) отбрасываются. intr=(fx,fy,cx,cy)."""
    fx, fy, cx, cy = intr
    H, W = depth.shape
    out = []
    keep = []
    for k, (u, v) in enumerate(uv):
        iu, iv = int(round(u)), int(round(v))
        if iu < 0 or iv < 0 or iu >= W or iv >= H:
            continue
        z = float(depth[iv, iu])
        if not (0.05 < z < 8.0) or math.isnan(z):
            continue
        out.append(((u - cx) * z / fx, (cy - v) * z / fy, z))
        keep.append(k)
    return np.asarray(out, dtype="float64"), keep


class OrbMatcher:
    """ORB-фичи + матч (OpenCV, ленивый). Описатели бинарные -> Hamming + cross-check."""

    def __init__(self, nfeatures: int = 1200):
        import cv2
        self._cv2 = cv2
        self.orb = cv2.ORB_create(nfeatures=nfeatures)
        self.bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    def detect(self, img_gray):
        kp, des = self.orb.detectAndCompute(img_gray, None)
        pts = np.array([k.pt for k in kp], dtype="float64") if kp else np.zeros((0, 2))
        return pts, des

    def match(self, des1, des2, max_matches: int = 400):
        if des1 is None or des2 is None or len(des1) < 3 or len(des2) < 3:
            return []
        m = self.bf.match(des1, des2)
        m = sorted(m, key=lambda x: x.distance)[:max_matches]
        return [(x.queryIdx, x.trainIdx) for x in m]


def relative_pose_rgbd(prev, cur, intr, matcher, thresh_m: float = 0.05, R_prior=None):
    """Относительная поза cur->prev (4×4) по двум RGB-D кадрам.
    prev/cur: dict {gray: 2D-uint8, depth: 2D метров}. Возвращает (T, n_inliers) или None.
    T: точка из кадра cur в кадр prev (P_prev = T · P_cur).
    R_prior (из IMU) задан -> фиксируем поворот, оцениваем только трансляцию
    (устойчиво); иначе полный 6-DOF (umeyama)."""
    p_pts, p_des = matcher.detect(prev["gray"])
    c_pts, c_des = matcher.detect(cur["gray"])
    pairs = matcher.match(p_des, c_des)
    if len(pairs) < 8:
        return None
    p_uv = [p_pts[i] for i, _ in pairs]
    c_uv = [c_pts[j] for _, j in pairs]
    P3, pk = _lift(p_uv, prev["depth"], intr)      # 3D в кадре prev
    C3, ck = _lift(c_uv, cur["depth"], intr)        # 3D в кадре cur
    common = [k for k in range(len(pairs)) if k in set(pk) and k in set(ck)]
    if len(common) < 8:
        return None
    pi = {k: n for n, k in enumerate(pk)}
    ci = {k: n for n, k in enumerate(ck)}
    A = np.asarray([C3[ci[k]] for k in common])     # cur
    B = np.asarray([P3[pi[k]] for k in common])     # prev
    if R_prior is not None:
        sol = estimate_translation_ransac(A, B, R_prior, thresh_m=thresh_m)
        if not sol:
            return None
        t, mask = sol
        return make_T(R_prior, t), int(mask.sum())
    sol = umeyama_ransac(A, B, thresh_m=thresh_m)
    if not sol:
        return None
    R, t, mask = sol
    return make_T(R, t), int(mask.sum())


# ───────────────────────── self-test (numpy, без OpenCV) ─────────────────────────

def selftest():
    rng = np.random.default_rng(7)
    A = rng.uniform(-1.5, 1.5, size=(120, 3))
    th = math.radians(25.0)                          # известный поворот вокруг y
    R = np.array([[math.cos(th), 0, math.sin(th)],
                  [0, 1, 0],
                  [-math.sin(th), 0, math.cos(th)]])
    t = np.array([0.30, -0.05, 0.20])
    B = A @ R.T + t
    B += rng.normal(0, 0.004, B.shape)               # шум
    out = rng.choice(120, 25, replace=False)         # 25 выбросов
    B[out] += rng.uniform(-1, 1, (25, 3))
    sol = umeyama_ransac(A, B, thresh_m=0.05)
    assert sol, "RANSAC (6-DOF) не сошёлся"
    Rr, tr, mask = sol
    rot_err = math.degrees(abs(yaw_of(Rr) - th))
    t_err = float(np.linalg.norm(tr - t))
    # translation-only при известном R (путь с IMU-поворотом)
    solt = estimate_translation_ransac(A, B, R, thresh_m=0.05)
    assert solt, "RANSAC (только t) не сошёлся"
    tt, mt = solt
    t_only_err = float(np.linalg.norm(tt - t))
    ok = (rot_err < 1.0 and t_err < 0.03 and mask.sum() >= 90
          and t_only_err < 0.02 and mt.sum() >= 90)
    print("VO selftest: 6-DOF курс=%.2f° t=%.3fм инл=%d | t-only ошибка=%.3fм инл=%d -> %s"
          % (rot_err, t_err, mask.sum(), t_only_err, mt.sum(), "OK" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if selftest() else 1)
