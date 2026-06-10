#!/usr/bin/env python3
"""Веб-панель управления UNO+TB6612 (не грузит Canvas IDE)."""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pip install pyserial", file=sys.stderr)
    sys.exit(1)

HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>UNO моторы</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; margin: 16px; max-width: 520px;
    background: #1e1e1e; color: #e0e0e0; user-select: none; }
  h1 { font-size: 1.2rem; margin: 0 0 6px; }
  .hint { font-size: 12px; color: #999; margin-bottom: 10px; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; }
  button { padding: 10px 14px; font-size: 14px; cursor: pointer;
    border: 1px solid #444; border-radius: 6px; background: #2d2d2d; color: #eee; }
  button, input { touch-action: manipulation; pointer-events: auto; }
  .cmd-link { display: inline-block; padding: 10px 14px; font-size: 14px; cursor: pointer;
    border: 1px solid #444; border-radius: 6px; background: #2d2d2d; color: #eee; text-decoration: none; }
  button.danger { background: #8b2222; min-width: 72px; }
  .speed button.active { outline: 2px solid #3794ff; }
  input[type=range] { flex: 1; min-width: 120px; }
  .readout { font-family: ui-monospace, monospace; font-size: 14px; margin: 8px 0; }
  .joystick {
    position: relative; width: 240px; height: 240px; margin: 12px auto;
    border-radius: 50%; background: #252526;
    border: 2px solid #444; box-shadow: inset 0 0 24px #111;
    touch-action: none;
  }
  .joystick::before {
    content: ""; position: absolute; inset: 50% auto auto 50%;
    width: 2px; height: 100%; margin-left: -1px; margin-top: -50%;
    background: #333; pointer-events: none;
  }
  .joystick::after {
    content: ""; position: absolute; inset: 50% auto auto 50%;
    width: 100%; height: 2px; margin-left: -50%; margin-top: -1px;
    background: #333; pointer-events: none;
  }
  .joy-knob {
    position: absolute; left: 50%; top: 50%; width: 72px; height: 72px;
    margin: -36px 0 0 -36px; border-radius: 50%;
    background: #0e639c;
    border: 2px solid #6eb3f7; cursor: grab;
  }
  .joy-knob.dragging { cursor: grabbing; z-index: 3; }
  .joy-knob { z-index: 3; }
  .joy-label {
    position: absolute; font-size: 12px; font-weight: 600; color: #8ab4e8;
    pointer-events: none; z-index: 1; letter-spacing: 0.02em;
  }
  .joy-label.fwd { top: 10px; left: 50%; transform: translateX(-50%); }
  .joy-label.back { bottom: 10px; left: 50%; transform: translateX(-50%); }
  .joy-label.left { left: 10px; top: 50%; transform: translateY(-50%); }
  .joy-label.right { right: 10px; top: 50%; transform: translateY(-50%); }
  #status { font-size: 13px; color: #aaa; }
  .radar-wrap {
    margin: 14px 0 18px; padding: 12px;
    background: #161616; border: 1px solid #333; border-radius: 10px;
  }
  .radar-title { font-size: 0.95rem; margin: 0 0 8px; color: #8ab4e8; font-weight: 600; }
  .radar-caption { font-size: 11px; color: #777; margin: 0 0 8px; }
  #radar {
    display: block; width: 100%; max-width: 320px; height: auto; margin: 0 auto;
    border-radius: 8px; background: #0d1117;
  }
  .radar-readout {
    font-family: ui-monospace, monospace; font-size: 15px;
    margin: 10px 0 8px; text-align: center;
  }
  .radar-readout strong { color: #3dff9a; font-size: 1.25em; }
  .radar-meta { font-size: 11px; color: #666; text-align: center; margin-top: 4px; }
  .map-wrap {
    margin: 14px 0; padding: 12px;
    background: #161616; border: 1px solid #333; border-radius: 10px;
  }
  .map-wrap h2 { font-size: 0.95rem; margin: 0 0 8px; color: #8ab4e8; }
  #mapGrid {
    display: block; width: 100%; max-width: 320px; height: auto; margin: 0 auto;
    image-rendering: pixelated; background: #0a0a0a; border-radius: 6px;
  }
  .map-meta { font-size: 11px; color: #777; margin: 8px 0; text-align: center; }
  button.scan { background: #1a4d3a; border-color: #3dff9a; }
  button.scan:disabled { opacity: 0.5; cursor: wait; }
  .sys-wrap {
    margin: 14px 0; padding: 12px;
    background: #161616; border: 1px solid #333; border-radius: 10px;
    font-size: 12px;
  }
  .sys-wrap h2 { font-size: 0.95rem; margin: 0 0 8px; color: #8ab4e8; }
  .sys-table { width: 100%; border-collapse: collapse; margin: 6px 0; }
  .sys-table th, .sys-table td {
    text-align: left; padding: 4px 6px; border-bottom: 1px solid #2a2a2a;
    vertical-align: top; font-family: ui-monospace, monospace; font-size: 11px;
  }
  .sys-table th { color: #888; font-weight: 600; }
  .tag-ok { color: #3dff9a; }
  .tag-busy { color: #ff8a65; }
  .tag-panel { color: #6eb3f7; }
  .sys-hint { color: #666; margin: 4px 0 8px; }
</style>
</head>
<body>
<h1>Управление моторами</h1>
<p class="hint">Круговой джойстик: вверх/вниз — езда, влево/вправо — поворот. Отпусти — стоп.</p>
<p id="status">…</p>
<section class="sys-wrap" aria-label="Порты и процессы">
  <h2>Система · COM и плата</h2>
  <p class="sys-hint">Панель: <span id="sysPanelPort" class="tag-panel">—</span> · PID <span id="sysPanelPid">—</span></p>
  <h3 style="font-size:12px;color:#aaa;margin:10px 0 4px;">COM-порты</h3>
  <table class="sys-table" id="sysPortsTbl"><thead><tr><th>Порт</th><th>Статус</th><th>Устройство</th></tr></thead><tbody></tbody></table>
  <h3 style="font-size:12px;color:#aaa;margin:10px 0 4px;">Процессы (serial / python / arduino)</h3>
  <table class="sys-table" id="sysProcTbl"><thead><tr><th>PID</th><th>Имя</th><th>Команда</th></tr></thead><tbody></tbody></table>
  <h3 style="font-size:12px;color:#aaa;margin:10px 0 4px;">Пины Arduino UNO (прошивка)</h3>
  <table class="sys-table" id="sysPinsTbl"><thead><tr><th>Пин</th><th>Назначение</th></tr></thead><tbody></tbody></table>
  <p class="sys-hint" id="sysUpdated">…</p>
</section>
<section class="radar-wrap" aria-label="Радар VL53L0X">
  <h2 class="radar-title">Лазерный дальномер</h2>
  <p class="radar-caption">Сектор обзора ~27° · дальность до 2 м · ось по центру</p>
  <canvas id="radar" width="320" height="210"></canvas>
  <p class="radar-readout">До преграды: <strong id="radarDist">—</strong></p>
  <p class="radar-meta">Замеров: <span id="tofCount">0</span> · режим: <span id="tofProfile">auto</span>
    <button type="button" id="btnTofReset" style="margin-left:8px;padding:4px 10px;font-size:12px;">Сброс</button></p>
</section>
<section class="map-wrap" aria-label="Карта сканирования">
  <h2>Карта (скан 360°)</h2>
  <p class="map-meta">Поворот на месте моторами · ~30 точек · сетка 5 см</p>
  <canvas id="mapGrid" width="240" height="240"></canvas>
  <p class="map-meta" id="mapStatus">Нажми «Скан 360°» — робот повернётся и построит карту</p>
  <div class="row" style="justify-content:center;">
    <button type="button" class="scan" id="btnScan360">Скан 360°</button>
    <button type="button" id="btnMapClear">Очистить карту</button>
  </div>
</section>
<div class="row">
  <span>Макс. скорость</span>
  <input type="range" id="maxSpd" min="40" max="255" value="180">
  <span id="maxLbl">180</span>
</div>
<div class="row">
  <span>Сила мелодии</span>
  <input type="range" id="audGain" min="10" max="100" value="10">
  <span id="audLbl">10%</span>
</div>
<div class="row speed" id="speeds"></div>
<div class="joystick" id="joy" aria-label="Джойстик движения">
  <span class="joy-label fwd">Вперёд</span>
  <span class="joy-label back">Назад</span>
  <span class="joy-label left">Влево</span>
  <span class="joy-label right">Вправо</span>
  <div class="joy-knob" id="knob"></div>
</div>
<p class="readout">L: <span id="lv">0</span> &nbsp; R: <span id="rv">0</span></p>
<div class="row">
  <button class="danger" id="btnStop">Стоп</button>
  <button id="btnBeep">Beep A</button>
  <button id="btnBeepB">Beep B</button>
  <button id="btnMel1">Mel1 A</button>
  <button id="btnMel1B">Mel1 B</button>
  <button id="btnMel2">Mel2 A</button>
  <button id="btnSayPrivet">Say Privet</button>
  <button id="btnSayPrivet2">Say Privet v2</button>
  <button id="btnMelStop">Mel stop</button>
</div>
<div class="row">
  <a class="cmd-link" target="cmdsink" href="/cmd?line=beep%20880%20250%20A&q=1">HTTP Beep A</a>
  <a class="cmd-link" target="cmdsink" href="/cmd?line=beep%20880%20250%20B&q=1">HTTP Beep B</a>
  <a class="cmd-link" target="cmdsink" href="/cmd?line=melody%201%20A&q=1">HTTP Mel1 A</a>
  <a class="cmd-link" target="cmdsink" href="/cmd?line=melody%20stop&q=1">HTTP Mel stop</a>
</div>
<iframe name="cmdsink" style="display:none;"></iframe>
<script>
const joy = document.getElementById('joy');
const knob = document.getElementById('knob');
const maxSpdEl = document.getElementById('maxSpd');
const maxLbl = document.getElementById('maxLbl');
const audGainEl = document.getElementById('audGain');
const audLbl = document.getElementById('audLbl');
let maxSpeed = 180;
let dragging = false;
let lastLine = '';
let joyL = 0;
let joyR = 0;
let driveTimer = null;
const DEAD = 0.12;
const DRIVE_KEEPALIVE_MS = 180;

maxSpdEl.oninput = () => {
  maxSpeed = +maxSpdEl.value;
  maxLbl.textContent = maxSpeed;
  document.querySelectorAll('.speed button').forEach(b => b.classList.remove('active'));
};
audGainEl.oninput = () => { audLbl.textContent = audGainEl.value + '%'; };

[80, 120, 180, 220, 255].forEach(v => {
  const b = document.createElement('button');
  b.textContent = v;
  if (v === maxSpeed) b.classList.add('active');
  b.onclick = () => {
    maxSpeed = v; maxSpdEl.value = v; maxLbl.textContent = v;
    document.querySelectorAll('.speed button').forEach(x =>
      x.classList.toggle('active', +x.textContent === v));
  };
  document.getElementById('speeds').appendChild(b);
});

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function mix(nx, ny) {
  const mag = Math.hypot(nx, ny);
  if (mag < DEAD) return { l: 0, r: 0 };
  const k = Math.min(1, mag);
  const fx = (nx / mag) * k;
  const fy = (-ny / mag) * k;
  const l = Math.round(maxSpeed * clamp(fy + fx, -1, 1));
  const r = Math.round(maxSpeed * clamp(fy - fx, -1, 1));
  return { l, r };
}

function lineFor(l, r) {
  if (l === 0 && r === 0) return 'stop';
  return 'L ' + l + ' R ' + r;
}

function pushDrive(l, r, force) {
  joyL = l;
  joyR = r;
  document.getElementById('lv').textContent = l;
  document.getElementById('rv').textContent = r;
  const line = lineFor(l, r);
  if (!force && line === lastLine) return;
  lastLine = line;
  fetch('/cmd?line=' + encodeURIComponent(line) + '&q=1').catch(() => {});
}

function setKnob(dx, dy, R) {
  const mag = Math.hypot(dx, dy);
  if (mag > R) { dx = dx * R / mag; dy = dy * R / mag; }
  knob.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
  const nx = dx / R;
  const ny = dy / R;
  const { l, r } = mix(nx, ny);
  pushDrive(l, r);
}

function centerKnob() {
  knob.style.transform = 'translate(0,0)';
  lastLine = '';
  pushDrive(0, 0);
}

function ptrPos(e) {
  const r = joy.getBoundingClientRect();
  return { x: e.clientX - (r.left + r.width / 2), y: e.clientY - (r.top + r.height / 2), R: r.width * 0.36 };
}

function start(e) {
  dragging = true;
  knob.classList.add('dragging');
  e.preventDefault();
  if (e.target.setPointerCapture) e.target.setPointerCapture(e.pointerId);
  if (driveTimer) clearInterval(driveTimer);
  driveTimer = setInterval(() => {
    if (!dragging) return;
    pushDrive(joyL, joyR, true);
  }, DRIVE_KEEPALIVE_MS);
  move(e);
}

function move(e) {
  if (!dragging) return;
  const p = ptrPos(e);
  setKnob(p.x, p.y, p.R);
}

function end(e) {
  if (!dragging) return;
  dragging = false;
  if (driveTimer) { clearInterval(driveTimer); driveTimer = null; }
  knob.classList.remove('dragging');
  if (e.target.releasePointerCapture) try { e.target.releasePointerCapture(e.pointerId); } catch (_) {}
  centerKnob();
  fetch('/cmd?line=stop').catch(() => {});
}

joy.addEventListener('pointerdown', start);
window.addEventListener('pointermove', move);
window.addEventListener('pointerup', end);
window.addEventListener('pointercancel', end);
const RADAR = { fovDeg: 27, maxMm: 1200 };

function drawRadar(mm) {
  const canvas = document.getElementById('radar');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h - 16;
  const maxR = h - 28;
  const halfFov = (RADAR.fovDeg / 2) * Math.PI / 180;
  const a0 = -Math.PI / 2 - halfFov;
  const a1 = -Math.PI / 2 + halfFov;
  const ac = -Math.PI / 2;

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#0d1117';
  ctx.fillRect(0, 0, w, h);

  // сетка дальности
  const rings = [250, 500, 750, 1000, 1200];
  rings.forEach(ringMm => {
    const r = maxR * (ringMm / RADAR.maxMm);
    ctx.strokeStyle = '#2a2f36';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, r, a0, a1);
    ctx.stroke();
    ctx.fillStyle = '#5a6270';
    ctx.font = '10px ui-monospace, monospace';
    const lx = cx + (r + 2) * Math.cos(a0);
    const ly = cy + (r + 2) * Math.sin(a0);
    ctx.fillText((ringMm / 1000).toFixed(2).replace(/\\.00$/, '') + 'm', lx + 2, ly + 4);
  });

  // сектор обзора
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.arc(cx, cy, maxR, a0, a1);
  ctx.closePath();
  const grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, maxR);
  grd.addColorStop(0, 'rgba(0, 180, 120, 0.18)');
  grd.addColorStop(1, 'rgba(0, 80, 60, 0.04)');
  ctx.fillStyle = grd;
  ctx.fill();
  ctx.strokeStyle = 'rgba(0, 200, 130, 0.45)';
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // границы сектора
  ctx.strokeStyle = 'rgba(0, 200, 130, 0.25)';
  ctx.setLineDash([3, 5]);
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + maxR * Math.cos(a0), cy + maxR * Math.sin(a0));
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + maxR * Math.cos(a1), cy + maxR * Math.sin(a1));
  ctx.stroke();
  ctx.setLineDash([]);

  // центральная ось
  ctx.strokeStyle = '#3a4550';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + maxR * Math.cos(ac), cy + maxR * Math.sin(ac));
  ctx.stroke();

  const valid = mm != null && mm >= 20 && mm < 8190;
  const elDist = document.getElementById('radarDist');
  if (valid) {
    const dist = Math.min(mm, RADAR.maxMm);
    const beamR = maxR * (dist / RADAR.maxMm);
    const bx = cx + beamR * Math.cos(ac);
    const by = cy + beamR * Math.sin(ac);

    // луч до цели
    ctx.strokeStyle = 'rgba(61, 255, 154, 0.85)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(bx, by);
    ctx.stroke();

    // зона попадания на дистанции
    const hitHalf = beamR * Math.tan(halfFov);
    ctx.fillStyle = 'rgba(61, 255, 154, 0.12)';
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(bx - hitHalf, by);
    ctx.lineTo(bx + hitHalf, by);
    ctx.closePath();
    ctx.fill();

    // преграда
    ctx.shadowColor = '#3dff9a';
    ctx.shadowBlur = 12;
    ctx.fillStyle = '#3dff9a';
    ctx.beginPath();
    ctx.arc(bx, by, 9, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 2;
    ctx.stroke();

    if (elDist) elDist.textContent = dist + ' mm';
  } else if (elDist) {
    elDist.textContent = 'нет цели';
  }

  // датчик
  ctx.fillStyle = '#0e639c';
  ctx.strokeStyle = '#6eb3f7';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(cx, cy, 7, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#8ab4e8';
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('VL53L0X', cx, cy + 22);
  ctx.textAlign = 'left';
}

function tofIsValid(j) {
  if (j.tof_valid === false) return false;
  if (j.tof_valid === true) return true;
  const mm = j.tof_mm;
  return mm != null && mm >= 20 && mm < 8190;
}

function formatTofProfile(p, auto) {
  const names = { fast: 'быстро', bal: 'баланс', acc: 'точно', long: 'дальний' };
  const label = names[p] || p || '—';
  return auto ? label + ' · авто' : label + ' · ручной';
}

function updateTofUi(j) {
  document.getElementById('tofCount').textContent = j.tof_count ?? 0;
  const el = document.getElementById('tofProfile');
  if (el) el.textContent = formatTofProfile(j.tof_profile, j.tof_auto !== false);
  drawRadar(tofIsValid(j) ? j.tof_mm : null);
}

const MAP_W = 80;
const MAP_H = 80;
const CELL_MM = 50;
const mapLog = new Float32Array(MAP_W * MAP_H);
const mapCanvas = document.getElementById('mapGrid');
const mapCtx = mapCanvas.getContext('2d');
const mapImg = mapCtx.createImageData(MAP_W, MAP_H);
const ROBOT_CX = 40;
const ROBOT_CY = 40;

function mapIdx(x, y) {
  if (x < 0 || y < 0 || x >= MAP_W || y >= MAP_H) return -1;
  return y * MAP_W + x;
}

function bresenham(x0, y0, x1, y1, fn) {
  let dx = Math.abs(x1 - x0);
  let dy = -Math.abs(y1 - y0);
  const sx = x0 < x1 ? 1 : -1;
  const sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  for (;;) {
    fn(x0, y0);
    if (x0 === x1 && y0 === y1) break;
    const e2 = 2 * err;
    if (e2 >= dy) { err += dy; x0 += sx; }
    if (e2 <= dx) { err += dx; y0 += sy; }
  }
}

function mapAddLog(x, y, delta) {
  const i = mapIdx(x, y);
  if (i < 0) return;
  mapLog[i] = Math.max(-4, Math.min(4, mapLog[i] + delta));
}

function mapRay(angleDeg, mm, valid) {
  const rad = (angleDeg - 90) * Math.PI / 180;
  const cells = Math.min(mm / CELL_MM, 38);
  const x1 = Math.round(ROBOT_CX + Math.cos(rad) * cells);
  const y1 = Math.round(ROBOT_CY + Math.sin(rad) * cells);
  const pts = [];
  bresenham(ROBOT_CX, ROBOT_CY, x1, y1, (x, y) => pts.push([x, y]));
  for (let i = 0; i < pts.length - 1; i++) {
    mapAddLog(pts[i][0], pts[i][1], -0.45);
  }
  if (valid && pts.length) {
    const end = pts[pts.length - 1];
    mapAddLog(end[0], end[1], 0.85);
  }
}

function drawMapGrid() {
  const d = mapImg.data;
  for (let i = 0; i < mapLog.length; i++) {
    const v = mapLog[i];
    let r, g, b;
    if (v > 0.35) { r = 230; g = 230; b = 230; }
    else if (v < -0.35) { r = 40; g = 200; b = 120; }
    else { r = 35; g = 38; b = 42; }
    const p = i * 4;
    d[p] = r; d[p + 1] = g; d[p + 2] = b; d[p + 3] = 255;
  }
  const off = document.createElement('canvas');
  off.width = MAP_W;
  off.height = MAP_H;
  off.getContext('2d').putImageData(mapImg, 0, 0);
  mapCtx.imageSmoothingEnabled = false;
  mapCtx.clearRect(0, 0, mapCanvas.width, mapCanvas.height);
  mapCtx.drawImage(off, 0, 0, mapCanvas.width, mapCanvas.height);
  const sc = mapCanvas.width / MAP_W;
  mapCtx.fillStyle = '#0e639c';
  mapCtx.beginPath();
  mapCtx.arc(ROBOT_CX * sc, ROBOT_CY * sc, 5, 0, Math.PI * 2);
  mapCtx.fill();
}

function clearMap() {
  mapLog.fill(0);
  drawMapGrid();
  document.getElementById('mapStatus').textContent = 'Карта очищена';
}

function applyScanPoints(points) {
  let n = 0;
  for (const p of points) {
    if (p.valid) {
      mapRay(p.ang, p.mm, true);
      n++;
    }
  }
  drawMapGrid();
  document.getElementById('mapStatus').textContent =
    'Скан: ' + points.length + ' лучей · стен: ' + n;
}

async function runScan360() {
  const btn = document.getElementById('btnScan360');
  btn.disabled = true;
  document.getElementById('mapStatus').textContent = 'Скан… робот поворачивается (~30–60 с)';
  try {
    const r = await fetch('/scan360');
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || 'scan failed');
    applyScanPoints(j.points || []);
    document.getElementById('status').textContent = 'OK scan360';
  } catch (e) {
    document.getElementById('mapStatus').textContent = 'Ошибка: ' + e.message;
    document.getElementById('status').textContent = 'ERR scan';
  } finally {
    btn.disabled = false;
  }
}

document.getElementById('btnScan360').onclick = () => runScan360();
document.getElementById('btnMapClear').onclick = () => clearMap();
drawMapGrid();

async function sendCmd(line) {
  try {
    const r = await fetch('/cmd?line=' + encodeURIComponent(line) + '&q=1');
    const j = await r.json();
    document.getElementById('status').textContent = j.ok ? ('OK: ' + line) : ('ERR: ' + (j.error || 'unknown'));
  } catch (_) {
    document.getElementById('status').textContent = 'ERR: panel offline';
  }
}
document.getElementById('btnStop').onclick = () => { dragging = false; centerKnob(); sendCmd('stop'); };
document.getElementById('btnBeep').onclick = () => sendCmd('beep 880 250 A');
document.getElementById('btnBeepB').onclick = () => sendCmd('beep 880 250 B');
document.getElementById('btnMel1').onclick = () => sendCmd('melody 1 A');
document.getElementById('btnMel1B').onclick = () => sendCmd('melody 1 B');
document.getElementById('btnMel2').onclick = () => sendCmd('melody 2 A');
document.getElementById('btnSayPrivet').onclick = () => sendCmd('say privet A');
document.getElementById('btnSayPrivet2').onclick = () => sendCmd('say privet2 A');
document.getElementById('btnMelStop').onclick = () => sendCmd('melody stop');
document.getElementById('btnTofReset').onclick = () => sendCmd('tofreset');
audGainEl.onchange = () => sendCmd('again ' + audGainEl.value);

drawRadar(null);

const BOARD_PINS = [
  ['D2','AIN1 мотор A'], ['D3','PWMA'], ['D4','AIN2'], ['D5','PWMB'],
  ['D7','BIN1 мотор B'], ['D8','BIN2'], ['D9','STBY драйвер'],
  ['D6','OLED RES'], ['D10','OLED CS'], ['D11','OLED MOSI'], ['D12','OLED DC'], ['D13','OLED SCK'],
  ['A4','I2C SDA (VL53L0X)'], ['A5','I2C SCL'], ['A0','напряжение VM']
];

function renderBoardPins() {
  const tb = document.querySelector('#sysPinsTbl tbody');
  tb.innerHTML = BOARD_PINS.map(([p, n]) => '<tr><td>' + p + '</td><td>' + n + '</td></tr>').join('');
}
renderBoardPins();

function renderSys(j) {
  document.getElementById('sysPanelPort').textContent = j.panel_port || '—';
  document.getElementById('sysPanelPid').textContent = j.panel_pid ?? '—';
  const pt = document.querySelector('#sysPortsTbl tbody');
  pt.innerHTML = (j.ports || []).map(p => {
    let st = p.busy ? '<span class="tag-busy">занят</span>' : '<span class="tag-ok">свободен</span>';
    if (p.panel) st = '<span class="tag-panel">панель</span>';
    return '<tr><td>' + p.device + '</td><td>' + st + '</td><td>' + (p.description || '') + '</td></tr>';
  }).join('') || '<tr><td colspan="3">нет портов</td></tr>';
  const pr = document.querySelector('#sysProcTbl tbody');
  pr.innerHTML = (j.processes || []).map(p => {
    const cmd = (p.cmd || '').length > 80 ? (p.cmd.slice(0, 77) + '…') : (p.cmd || '');
    return '<tr><td>' + p.pid + '</td><td>' + p.name + '</td><td>' + cmd + '</td></tr>';
  }).join('') || '<tr><td colspan="3">нет подходящих процессов</td></tr>';
  document.getElementById('sysUpdated').textContent = 'Обновлено: ' + (j.ts || '');
}

function refreshSystem() {
  fetch('/system').then(r => r.json()).then(renderSys).catch(() => {});
}
refreshSystem();
setInterval(refreshSystem, 3000);

fetch('/status').then(r => r.json()).then(j => {
  document.getElementById('status').textContent = 'Порт ' + j.port + ' · джойстик ~20 Гц';
  updateTofUi(j);
  sendCmd('again ' + audGainEl.value);
}).catch(() => {});

setInterval(() => {
  fetch('/status').then(r => r.json()).then(updateTofUi).catch(() => {});
}, 250);
</script>
</body>
</html>
"""


def find_port(hint: str | None) -> str:
    if hint:
        return hint
    for p in list_ports.comports():
        d = (p.description or "") + (p.hwid or "")
        if "CH340" in d or "Arduino" in d:
            return p.device
    ports = [p.device for p in list_ports.comports()]
    if len(ports) == 1:
        return ports[0]
    raise SystemExit("Укажи порт: py -3 tools/uno_motor_web.py COM3")


def open_serial(port: str, baud: int) -> serial.Serial:
    try:
        ser = serial.Serial(port, baud, timeout=0.25)
    except serial.SerialException as e:
        msg = str(e).lower()
        if "access is denied" in msg or "permission" in msg or "отказано" in msg:
            raise SystemExit(
                f"{port} занят.\n"
                "Закрой: Arduino Serial Monitor, второй терминал с uno_motor_web.py,\n"
                "монитор порта. Потом снова: .\\tools\\start_uno_motor_panel.ps1"
            ) from e
        raise
    time.sleep(2.0)
    while ser.in_waiting:
        ser.readline()
    return ser


def is_drive_cmd(line: str) -> bool:
    s = line.strip()
    return s == "stop" or s.startswith("L ")


class SerialBus:
    def __init__(self, port: str, baud: int) -> None:
        self.port = port
        self.ser = open_serial(port, baud)
        self._q: queue.Queue = queue.Queue()
        self._io_lock = threading.Lock()
        self._tel_lock = threading.Lock()
        self._tel: dict = {"tof_valid": None, "tof_mm": None, "tof_count": None}
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _transact(self, line: str, *, wait_reply: bool, timeout: float = 0.22) -> list[str]:
        out: list[str] = []
        with self._io_lock:
            try:
                self.ser.write((line.strip() + "\n").encode("ascii"))
                self.ser.flush()
            except serial.SerialException as e:
                raise RuntimeError(
                    f"{self.port} недоступен (закрой другие программы на COM)"
                ) from e
            if not wait_reply:
                time.sleep(0.003)
                while self.ser.in_waiting:
                    self.ser.readline()
                return []
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self.ser.in_waiting:
                    out.append(
                        self.ser.readline().decode("utf-8", errors="replace").rstrip()
                    )
                else:
                    time.sleep(0.008)
        return [x for x in out if x]

    def run_scan360(self, steps: int = 30) -> list[dict]:
        points: list[dict] = []
        with self._io_lock:
            while self.ser.in_waiting:
                self.ser.readline()
            cmd = f"scan360 {steps}" if steps != 30 else "scan360"
            self.ser.write((cmd + "\n").encode("ascii"))
            self.ser.flush()
            deadline = time.time() + 120.0
            while time.time() < deadline:
                if not self.ser.in_waiting:
                    time.sleep(0.02)
                    continue
                line = self.ser.readline().decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                if line.startswith("SCAN ang="):
                    try:
                        parts = {}
                        for tok in line.split():
                            if "=" in tok:
                                k, v = tok.split("=", 1)
                                parts[k] = v
                        points.append(
                            {
                                "ang": int(parts["ang"]),
                                "mm": int(parts["mm"]),
                                "valid": parts.get("valid", "0") == "1",
                            }
                        )
                    except (KeyError, ValueError):
                        pass
                elif "OK scan done" in line:
                    break
                elif line.startswith("ERR scan"):
                    raise RuntimeError(line)
        return points

    def _handle(self, prio: int, line: str, wait_reply: bool, evt: threading.Event | None, holder: list[str] | None) -> None:
        try:
            reply = self._transact(line, wait_reply=wait_reply)
            if holder is not None:
                holder.extend(reply)
        except Exception as exc:
            if holder is not None:
                holder.append(f"ERR {exc}")
        finally:
            if evt is not None:
                evt.set()

    def _drain_queue(self) -> bool:
        batch: list[tuple] = []
        try:
            while True:
                batch.append(self._q.get_nowait())
        except queue.Empty:
            pass
        if not batch:
            return False
        batch.sort(key=lambda x: x[0])
        for item in batch:
            self._handle(*item)
        return True

    def _run(self) -> None:
        last_tof = 0.0
        while not self._stop.is_set():
            if self._drain_queue():
                continue
            now = time.time()
            if now - last_tof >= 0.28:
                last_tof = now
                try:
                    lines = self._transact("tof?", wait_reply=True)
                    valid, mm, cnt, profile, auto = parse_tof(lines)
                    with self._tel_lock:
                        self._tel = {
                            "tof_valid": valid,
                            "tof_mm": mm,
                            "tof_count": cnt,
                            "tof_profile": profile,
                            "tof_auto": auto,
                        }
                except Exception:
                    pass
            time.sleep(0.012)

    def _enqueue(self, line: str, *, priority: int, wait_reply: bool) -> list[str]:
        evt = threading.Event() if wait_reply else None
        holder: list[str] | None = [] if wait_reply else None
        self._q.put((priority, line, wait_reply, evt, holder))
        if evt is not None:
            evt.wait(timeout=0.4)
            return holder
        return []

    def send_drive(self, line: str) -> None:
        self._enqueue(line, priority=0, wait_reply=False)

    def send(self, line: str, *, quick: bool = False) -> list[str]:
        if quick and is_drive_cmd(line):
            self.send_drive(line)
            return []
        prio = 2 if line.strip() != "tof?" else 1
        return self._enqueue(line, priority=prio, wait_reply=True)

    def telemetry(self) -> dict:
        with self._tel_lock:
            return dict(self._tel)

    def close(self) -> None:
        self._stop.set()
        try:
            self.send_drive("stop")
        except Exception:
            pass
        time.sleep(0.05)
        self.ser.close()


BOARD_PINS_DOC = [
    ("D2", "AIN1 мотор A"),
    ("D3", "PWMA"),
    ("D4", "AIN2"),
    ("D5", "PWMB"),
    ("D7", "BIN1 мотор B"),
    ("D8", "BIN2"),
    ("D9", "STBY TB6612"),
    ("D6", "OLED RES"),
    ("D10", "OLED CS"),
    ("D11", "OLED MOSI"),
    ("D12", "OLED DC"),
    ("D13", "OLED SCK"),
    ("A4", "I2C SDA (VL53L0X)"),
    ("A5", "I2C SCL"),
    ("A0", "VM напряжение"),
]


def probe_port_free(device: str) -> bool | None:
    try:
        s = serial.Serial(device, timeout=0.15)
        s.close()
        return True
    except serial.SerialException:
        return False
    except Exception:
        return None


def list_serial_processes() -> list[dict]:
    if sys.platform != "win32":
        return []
    keywords = (
        "python",
        "arduino",
        "serial",
        "uno_motor",
        "java.exe",
        "javaw",
    )
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []
        raw = json.loads(r.stdout)
        if isinstance(raw, dict):
            raw = [raw]
        out: list[dict] = []
        for item in raw:
            name = (item.get("Name") or "").lower()
            cmd = item.get("CommandLine") or ""
            cmd_l = cmd.lower()
            if not any(k in name or k in cmd_l for k in keywords):
                continue
            out.append(
                {
                    "pid": item.get("ProcessId"),
                    "name": item.get("Name") or "?",
                    "cmd": cmd,
                }
            )
        out.sort(key=lambda x: (x.get("name") or "", x.get("pid") or 0))
        return out
    except Exception:
        return []


def collect_system_info(panel_port: str) -> dict:
    ports: list[dict] = []
    for p in list_ports.comports():
        dev = p.device
        is_panel = dev == panel_port
        busy = None if is_panel else probe_port_free(dev)
        ports.append(
            {
                "device": dev,
                "description": (p.description or "").strip(),
                "hwid": (p.hwid or "").strip()[:80],
                "panel": is_panel,
                "busy": False if is_panel else (busy is False),
                "free": True if busy else (False if busy is False else None),
            }
        )
    return {
        "panel_port": panel_port,
        "panel_pid": os.getpid(),
        "ports": ports,
        "processes": list_serial_processes(),
        "board_pins": [{"pin": a, "role": b} for a, b in BOARD_PINS_DOC],
        "ts": time.strftime("%H:%M:%S"),
    }


def parse_tof(lines: list[str]) -> tuple[bool | None, int | None, int | None, str | None, bool | None]:
    for ln in lines:
        if "tof=" in ln and "mm=" in ln and "count=" in ln:
            try:
                # OK tof=1 valid=1 mm=123 count=45 profile=bal auto=1
                valid = None
                if "valid=" in ln:
                    valid_s = ln.split("valid=", 1)[1].split(" ", 1)[0]
                    valid = valid_s.strip().startswith("1")
                mm_s = ln.split("mm=", 1)[1].split(" ", 1)[0]
                cnt_part = ln.split("count=", 1)[1]
                cnt_s = cnt_part.split(" ", 1)[0]
                profile = None
                auto = None
                if "profile=" in ln:
                    profile = ln.split("profile=", 1)[1].split(" ", 1)[0].strip()
                if "auto=" in ln:
                    auto = ln.split("auto=", 1)[1].strip().startswith("1")
                return valid, int(mm_s.strip()), int(cnt_s.strip()), profile, auto
            except Exception:
                continue
    return None, None, None, None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", help="COM3")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port-http", type=int, default=8765)
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    port = find_port(args.port)
    bus = SerialBus(port, args.baud)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *a: object) -> None:
            pass

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path == "/scan360":
                qs = parse_qs(u.query)
                steps_s = (qs.get("steps") or ["30"])[0]
                try:
                    steps = max(8, min(72, int(steps_s)))
                except ValueError:
                    steps = 30
                try:
                    points = bus.run_scan360(steps)
                    self._json(200, {"ok": True, "points": points, "steps": steps})
                except Exception as e:
                    self._json(500, {"ok": False, "error": str(e)})
                return
            if u.path == "/system":
                self._json(200, {"ok": True, **collect_system_info(bus.port)})
                return
            if u.path == "/status":
                tel = bus.telemetry()
                self._json(
                    200,
                    {
                        "ok": True,
                        "port": bus.port,
                        "tof_valid": tel.get("tof_valid"),
                        "tof_mm": tel.get("tof_mm"),
                        "tof_count": tel.get("tof_count"),
                        "tof_profile": tel.get("tof_profile"),
                        "tof_auto": tel.get("tof_auto"),
                    },
                )
                return
            if u.path == "/cmd":
                qs = parse_qs(u.query)
                line = (qs.get("line") or ["stop"])[0]
                quick = (qs.get("q") or ["0"])[0] in ("1", "true", "yes")
                try:
                    reply = bus.send(line, quick=quick)
                    self._json(200, {"ok": True, "reply": reply})
                except Exception as e:
                    self._json(500, {"ok": False, "error": str(e)})
                return
            self.send_error(404)

    httpd = HTTPServer((args.host, args.port_http), Handler)
    url = f"http://{args.host}:{args.port_http}/"
    print(f"Панель: {url}")
    print(f"Serial: {port} · Ctrl+C — выход")
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        bus.send_drive("stop")
    finally:
        httpd.server_close()
        bus.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
