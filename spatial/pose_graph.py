#!/usr/bin/env python3
"""spatial.pose_graph — Этап B.3: замыкание петель (2D pose-graph SLAM, SE(2)).

Длинные проезды копят дрейф (VO + IMU). Когда робот ВОЗВРАЩАЕТСЯ в уже виденное
место (детект совпадения), добавляем ребро-«замыкание петли» — и оптимизация
РАСПРЕДЕЛЯЕТ накопленную ошибку по всей траектории, делая карту глобально
согласованной (конец сходится к началу).

Граф поз: узлы = позы кадров (x, y, θ ∈ SE(2)); рёбра = относительные позы:
  - одометрия (из vo.relative_pose_rgbd) между соседними кадрами;
  - замыкание петли между не-соседними кадрами (по совпадению места).
Оптимизация — Gauss-Newton (Grisetti «A Tutorial on Graph-Based SLAM»), чистый
numpy, без g2o/gtsam. Первый узел фиксирован (калибровка свободы).

Координаты плоские: x — вправо, y — вперёд (проекция нашего x-z на пол), θ — курс.
Ядро тестируется без робота (--selftest): квадратная петля с дрейфом → замыкание.
"""
from __future__ import annotations

import math
import sys

import numpy as np


# ───────────────────────── SE(2) ─────────────────────────

def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def v2t(p):
    """(x, y, θ) → 3×3 однородная матрица."""
    c, s = math.cos(p[2]), math.sin(p[2])
    return np.array([[c, -s, p[0]], [s, c, p[1]], [0, 0, 1.0]])


def t2v(T):
    """3×3 → (x, y, θ)."""
    return np.array([T[0, 2], T[1, 2], math.atan2(T[1, 0], T[0, 0])])


def compose(a, b):
    """Поза a, затем относительная b → мировая поза."""
    return t2v(v2t(a) @ v2t(b))


def between(a, b):
    """Относительная поза из a в b (a^-1 · b)."""
    return t2v(np.linalg.inv(v2t(a)) @ v2t(b))


# ───────────────────────── граф поз ─────────────────────────

class PoseGraph:
    """Узлы (позы) + рёбра (относительные измерения) + Gauss-Newton оптимизация."""

    def __init__(self):
        self.nodes = []          # список [x, y, θ]
        self.edges = []          # список (i, j, z=[x,y,θ], omega 3×3)

    def add_node(self, pose):
        self.nodes.append([float(pose[0]), float(pose[1]), float(pose[2])])
        return len(self.nodes) - 1

    def add_edge(self, i, j, z, info=None):
        omega = np.eye(3) if info is None else np.asarray(info, float)
        self.edges.append((i, j, np.asarray(z, float), omega))

    def _linear(self, xi, xj, z):
        """Невязка e и якобианы A=de/dxi, B=de/dxj для ребра (SE(2))."""
        thi, thij = xi[2], z[2]
        ci, si = math.cos(thi), math.sin(thi)
        RiT = np.array([[ci, si], [-si, ci]])
        dRiT = np.array([[-si, ci], [-ci, -si]])      # d(RiT)/dθi
        cij, sij = math.cos(thij), math.sin(thij)
        RijT = np.array([[cij, sij], [-sij, cij]])
        diff = np.array([xj[0] - xi[0], xj[1] - xi[1]])
        e_t = RijT @ (RiT @ diff - z[:2])
        e = np.array([e_t[0], e_t[1], _wrap(xj[2] - xi[2] - thij)])
        A = np.zeros((3, 3))
        A[:2, :2] = -RijT @ RiT
        A[:2, 2] = RijT @ (dRiT @ diff)
        A[2, 2] = -1.0
        B = np.zeros((3, 3))
        B[:2, :2] = RijT @ RiT
        B[2, 2] = 1.0
        return e, A, B

    def total_error(self) -> float:
        tot = 0.0
        for (i, j, z, om) in self.edges:
            e, _, _ = self._linear(self.nodes[i], self.nodes[j], z)
            tot += float(e @ om @ e)
        return tot

    def optimize(self, iters: int = 30, tol: float = 1e-6):
        n = len(self.nodes)
        X = np.array(self.nodes, float)
        for _ in range(iters):
            H = np.zeros((3 * n, 3 * n))
            b = np.zeros(3 * n)
            for (i, j, z, om) in self.edges:
                e, A, B = self._linear(X[i], X[j], z)
                ii, jj = slice(3 * i, 3 * i + 3), slice(3 * j, 3 * j + 3)
                H[ii, ii] += A.T @ om @ A
                H[ii, jj] += A.T @ om @ B
                H[jj, ii] += B.T @ om @ A
                H[jj, jj] += B.T @ om @ B
                b[ii] += A.T @ om @ e
                b[jj] += B.T @ om @ e
            H[0:3, 0:3] += np.eye(3) * 1e9          # фиксируем первый узел (свобода)
            try:
                dx = np.linalg.solve(H, -b)
            except np.linalg.LinAlgError:
                break
            X += dx.reshape(n, 3)
            X[:, 2] = (X[:, 2] + math.pi) % (2 * math.pi) - math.pi
            if np.linalg.norm(dx) < tol:
                break
        self.nodes = X.tolist()
        return self.total_error()


# ───────────────────────── self-test (numpy) ─────────────────────────

def selftest():
    true = [(0, 0, 0.0), (1, 0, math.pi / 2), (1, 1, math.pi),
            (0, 1, -math.pi / 2), (0, 0, 0.0)]            # квадрат, узел 4 ≈ узел 0
    rng = np.random.default_rng(3)
    pg = PoseGraph()
    est = [list(true[0])]
    odom = []
    for i in range(4):
        z = between(true[i], true[i + 1])
        zn = z + np.array([rng.normal(0, 0.05), rng.normal(0, 0.05), rng.normal(0, 0.04)])
        odom.append((i, i + 1, zn))
        est.append(list(compose(est[i], zn)))            # интегрируем шум → дрейф
    for p in est:
        pg.add_node(p)
    for (i, j, z) in odom:
        pg.add_edge(i, j, z)
    pg.add_edge(4, 0, between(true[4], true[0]), info=np.eye(3) * 10)   # ЗАМЫКАНИЕ петли

    drift_before = math.hypot(est[4][0] - est[0][0], est[4][1] - est[0][1])
    e0 = pg.total_error()
    e1 = pg.optimize(40)
    n4, n0 = pg.nodes[4], pg.nodes[0]
    drift_after = math.hypot(n4[0] - n0[0], n4[1] - n0[1])
    ok = e1 < e0 * 0.25 and drift_after < 0.05
    print("POSE-GRAPH selftest: ошибка %.3f→%.3f | замыкание петли %.3f→%.3f м -> %s"
          % (e0, e1, drift_before, drift_after, "OK" if ok else "FAIL"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if selftest() else 1)
