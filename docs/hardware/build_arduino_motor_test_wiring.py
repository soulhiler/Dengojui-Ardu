"""Схема пайки для ТЕСТА МОТОРОВ через Arduino UNO + TB6612FNG.

Назначение: пока ждём новые XIAO ESP32-S3 Sense с AliExpress (1 неделя) —
проверить работоспособность TB6612 и моторов через временный контроллер
Arduino UNO. Это упрощённая схема: без камеры, без OLED, без микрофона,
без WiFi. Только моторы.

Источник пинов: arduino_motor_test/arduino_motor_test.ino.

============================================================================
БЕЗОПАСНОСТЬ ПИТАНИЯ (читать ПЕРЕД пайкой/первым включением)
============================================================================

ПРАВИЛО 1 (САМОЕ ВАЖНОЕ — повторяется из основной схемы):
НИКОГДА не подключай USB к ПК и батарею 7.4 В одновременно.
Хотя ATmega328 на Arduino UNO существенно устойчивее ESP32-S3 к петле
земли (USB-UART «жертвенный» — даже умерев, не убьёт основной чип),
СОЗДАВАТЬ ТАКУЮ СИТУАЦИЮ ВСЁ РАВНО НЕЛЬЗЯ. Один раз уже потеряли XIAO
этим путём — см. docs/dev-log.md, инцидент 2026-05-26.

ПРОТОКОЛ РАБОТЫ С ARDUINO + БАТАРЕЯ:
  1. Отключаешь батарею от схемы.
  2. Подключаешь USB к Arduino, прошиваешь скетч.
  3. ОТКЛЮЧАЕШЬ USB.
  4. Подключаешь батарею (через защитный блок) — на Buck (для Arduino Vin)
     и на TB6612 VM (для моторов).
  5. Тестируешь.
  6. Хочешь поменять скетч → отключаешь батарею → подключаешь USB → ...

ПРАВИЛО 2. ДО подачи питания на Arduino: измерь мультиметром выход
Buck — должно быть 5.0–5.5 В (Vin Arduino терпит 7–12 В, но через
встроенный LDO лучше дать сразу чистые 5 В).

ПРАВИЛО 3. Защиты Уровня 1 (BMS / PPTC / AO3401 / Schottky / TVS /
USBLC6) — те же, что в основной схеме build_wiring.py. Не игнорировать.

============================================================================
"""
import os
import schemdraw
import schemdraw.elements as elm
import matplotlib

matplotlib.rcParams["font.family"] = ["Arial", "DejaVu Sans"]
matplotlib.rcParams["font.size"] = 9

# Цвета по функции (те же что в основной схеме)
C_VM = "#E53935"
C_GND = "#212121"
C_5V = "#FB8C00"
C_PWM = "#7CB342"
C_DIR = "#0288D1"
C_MOT = "#455A64"
C_PROT = "#6D4C41"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_BASE = os.path.join(HERE, "wiring-arduino-motor-test")


def pins_vert(labels_top_to_bottom, anchors, side, total):
    return [
        elm.IcPin(name=lbl, side=side, anchorname=anc, slot=f"{total - i}/{total}")
        for i, (lbl, anc) in enumerate(zip(labels_top_to_bottom, anchors))
    ]


with schemdraw.Drawing(show=False) as d:
    d.config(unit=2.2, fontsize=10)

    # ===================== Источники питания + защиты =====================

    bat = elm.Ic(
        pins=[
            elm.IcPin(name="+", side="right", anchorname="plus", slot="2/2"),
            elm.IcPin(name="−", side="right", anchorname="minus", slot="1/2"),
        ],
        label="Батарея\n2S Li-Ion 7.4 В",
        lblloc="top", lblofst=0.3,
        w=2.4, h=1.6, plblofst=0.15, fontsize=10,
    ).at((1, 14))
    d += bat

    bms = elm.Ic(
        pins=[
            elm.IcPin(name="B+", side="left", anchorname="binp", slot="2/2"),
            elm.IcPin(name="B−", side="left", anchorname="binn", slot="1/2"),
            elm.IcPin(name="P+", side="right", anchorname="poutp", slot="2/2"),
            elm.IcPin(name="P−", side="right", anchorname="poutn", slot="1/2"),
        ],
        label="BMS 2S\n5–10 A",
        lblloc="top", lblofst=0.3,
        w=2.0, h=1.6, plblofst=0.15, fontsize=9, color=C_PROT,
    ).at((5, 14))
    d += bms

    rev = elm.Ic(
        pins=[
            elm.IcPin(name="IN+", side="left", anchorname="inp", slot="2/2"),
            elm.IcPin(name="IN−", side="left", anchorname="inn", slot="1/2"),
            elm.IcPin(name="OUT+", side="right", anchorname="outp", slot="2/2"),
            elm.IcPin(name="OUT−", side="right", anchorname="outn", slot="1/2"),
        ],
        label="PPTC 1.5A\n+ AO3401\n(reverse-polarity)",
        lblloc="top", lblofst=0.3,
        w=2.4, h=1.6, plblofst=0.15, fontsize=9, color=C_PROT,
    ).at((9, 14))
    d += rev

    buck = elm.Ic(
        pins=[
            elm.IcPin(name="IN+", side="left", anchorname="inp", slot="2/2"),
            elm.IcPin(name="IN−", side="left", anchorname="inn", slot="1/2"),
            elm.IcPin(name="OUT+ 5В", side="right", anchorname="outp", slot="2/2"),
            elm.IcPin(name="OUT−", side="right", anchorname="outn", slot="1/2"),
        ],
        label="DC-DC Buck\n(на 5.0 В!)",
        lblloc="top", lblofst=0.3,
        w=2.6, h=1.6, plblofst=0.15, fontsize=9,
    ).at((13, 14))
    d += buck

    # ===================== Моторы =====================

    ml = elm.Ic(
        pins=[
            elm.IcPin(name="M+", side="left", anchorname="mp", slot="2/2"),
            elm.IcPin(name="M−", side="left", anchorname="mn", slot="1/2"),
        ],
        label="Мотор L",
        lblloc="top", lblofst=0.3,
        w=1.8, h=1.6, plblofst=0.15, fontsize=10,
    ).at((15, 10))
    d += ml

    mr = elm.Ic(
        pins=[
            elm.IcPin(name="M+", side="left", anchorname="mp", slot="2/2"),
            elm.IcPin(name="M−", side="left", anchorname="mn", slot="1/2"),
        ],
        label="Мотор R",
        lblloc="top", lblofst=0.3,
        w=1.8, h=1.6, plblofst=0.15, fontsize=10,
    ).at((15, 6))
    d += mr

    # ===================== TB6612FNG =====================
    tb_left = pins_vert(
        ["PWMA", "AIN2", "AIN1", "STBY", "BIN1", "BIN2", "PWMB", "GND"],
        ["pwma", "ain2", "ain1", "stby", "bin1", "bin2", "pwmb", "gndb"],
        side="left", total=8,
    )
    tb_right = pins_vert(
        ["VM (+100мкФ)", "VCC", "GND", "AO1", "AO2", "BO2", "BO1", "GND"],
        ["vm", "vcc", "gndt", "ao1", "ao2", "bo2", "bo1", "gndt2"],
        side="right", total=8,
    )
    tb = elm.Ic(
        pins=tb_left + tb_right,
        label="TB6612FNG",
        lblloc="top", lblofst=0.4,
        w=3.6, h=8, plblofst=0.2, fontsize=11,
    ).at((11, 0.5))
    d += tb

    # ===================== Arduino UNO =====================
    # Реалистичный пинаут UNO: слева power+GND, справа цифровые D2..D13.
    # Здесь обобщённо: левая сторона — питание/земля, правая — цифровые
    # пины (только те, что нам нужны).
    uno_left = pins_vert(
        ["Vin", "5V", "GND", "GND", "USB-B"],
        ["vin", "v5", "gnd1", "gnd2", "usb"],
        side="left", total=5,
    )
    uno_right = pins_vert(
        ["D2 (AIN1)", "D3~ (PWMA)", "D4 (AIN2)", "D5~ (PWMB)",
         "D7 (BIN1)", "D8 (BIN2)", "D9~ (STBY)"],
        ["d2", "d3", "d4", "d5", "d7", "d8", "d9"],
        side="right", total=7,
    )
    uno = elm.Ic(
        pins=uno_left + uno_right,
        label="Arduino UNO\n(ATmega328P, 5V)",
        lblloc="top", lblofst=0.4,
        w=3.4, h=7, plblofst=0.2, fontsize=11,
    ).at((4, 1.5))
    d += uno

    # ===================== ПРЯМЫЕ ПРОВОДА — питание =====================
    d += elm.Wire("-", color=C_VM, lw=2.5).at(bat.plus).to(bms.binp)
    d += elm.Wire("-", color=C_GND, lw=2.5).at(bat.minus).to(bms.binn)
    d += elm.Wire("-", color=C_VM, lw=2.5).at(bms.poutp).to(rev.inp)
    d += elm.Wire("-", color=C_GND, lw=2.5).at(bms.poutn).to(rev.inn)
    d += elm.Wire("-", color=C_VM, lw=2.5).at(rev.outp).to(buck.inp)
    d += elm.Wire("-", color=C_GND, lw=2.5).at(rev.outn).to(buck.inn)

    # ===================== Моторы: TB6612 AO/BO → motors =====================
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.ao1).to(ml.mp)
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.ao2).to(ml.mn)
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.bo1).to(mr.mp)
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.bo2).to(mr.mn)

    # ===================== NET-LABELS =====================
    def add_label(anchor, text, color, direction, length=0.5):
        line = elm.Line(color=color, lw=1.6).at(anchor)
        if direction == "left":   line.left(length)
        elif direction == "right": line.right(length)
        elif direction == "up":    line.up(length)
        elif direction == "down":  line.down(length)
        d.add(line)
        d.add(elm.Tag().label(text, color=color, fontsize=9).at(line.end))

    # --- Управление моторами: Arduino ↔ TB6612 ---
    # PWMA=D3, AIN1=D2, AIN2=D4, PWMB=D5, BIN1=D7, BIN2=D8, STBY=D9
    add_label(uno.d3, "PWMA", C_PWM, "right")
    add_label(uno.d2, "AIN1", C_DIR, "right")
    add_label(uno.d4, "AIN2", C_DIR, "right")
    add_label(uno.d5, "PWMB", C_PWM, "right")
    add_label(uno.d7, "BIN1", C_DIR, "right")
    add_label(uno.d8, "BIN2", C_DIR, "right")
    add_label(uno.d9, "STBY", C_DIR, "right")

    add_label(tb.pwma, "PWMA", C_PWM, "left")
    add_label(tb.ain1, "AIN1", C_DIR, "left")
    add_label(tb.ain2, "AIN2", C_DIR, "left")
    add_label(tb.pwmb, "PWMB", C_PWM, "left")
    add_label(tb.bin1, "BIN1", C_DIR, "left")
    add_label(tb.bin2, "BIN2", C_DIR, "left")
    add_label(tb.stby, "STBY", C_DIR, "left")

    # --- +5V net (Buck OUT → Arduino Vin + Arduino 5V → TB6612 VCC) ---
    add_label(buck.outp, "+5V", C_5V, "right")
    add_label(uno.vin, "+5V", C_5V, "left")
    add_label(uno.v5, "+5V", C_5V, "left")
    add_label(tb.vcc, "+5V", C_5V, "right")

    # --- BAT_prot+ net (выход RP-FET → TB6612 VM) ---
    add_label(rev.outp, "BAT_prot+", C_VM, "down")
    add_label(tb.vm, "BAT_prot+", C_VM, "right")

    # --- GND net (общая шина) ---
    add_label(buck.outn, "GND", C_GND, "down")
    add_label(uno.gnd1, "GND", C_GND, "left")
    add_label(uno.gnd2, "GND", C_GND, "left")
    add_label(tb.gndt, "GND", C_GND, "right")
    add_label(tb.gndt2, "GND", C_GND, "right")
    add_label(tb.gndb, "GND", C_GND, "left")

    # --- USB пометка ---
    d.add(elm.Tag().label("USB-B к ПК ТОЛЬКО для прошивки!\n(батарея в это время ОТКЛЮЧЕНА)",
                           color=C_PROT, fontsize=8).at((uno.usb[0] - 1.5, uno.usb[1])))

    # ===================== Легенда =====================
    legend = elm.Ic(
        pins=[],
        label=(
            "СХЕМА ДЛЯ ТЕСТА МОТОРОВ ЧЕРЕЗ ARDUINO UNO\n"
            "(временная — пока ждём новые XIAO ESP32-S3 Sense)\n"
            "\n"
            "ТЕГИ С ОДИНАКОВЫМ ИМЕНЕМ = СОЕДИНЕНЫ ОБЩЕЙ ШИНОЙ (стиль KiCad/Altium):\n"
            "+5V · BAT_prot+ · GND · PWMA · PWMB · AIN1 · AIN2 · BIN1 · BIN2 · STBY\n"
            "\n"
            "ЦВЕТ ПРОВОДА = ФУНКЦИЯ:\n"
            "красный — VM/батарея 7.4В   ·   оранж. — 5В питание логики\n"
            "зелёный — PWM   ·   синий — направление IN1/IN2/STBY\n"
            "серый — выходы моторов   ·   чёрный — GND   ·   коричневый — защиты\n"
            "\n"
            "ПОРЯДОК ПИТАНИЯ:  Bat → BMS → PPTC+RP-FET → Buck → +5V → Arduino Vin/5V → TB6612 VCC\n"
            "                                                  └→ TB6612 VM (через BAT_prot+)\n"
            "\n"
            "ПИНЫ Arduino UNO ↔ TB6612FNG:\n"
            "  D2→AIN1   D3~→PWMA   D4→AIN2   (левый мотор A)\n"
            "  D5~→PWMB  D7→BIN1    D8→BIN2   (правый мотор B)\n"
            "  D9~→STBY  (программный enable — LOW=драйвер отключён, HIGH=работает)\n"
            "  Arduino 5V → TB6612 VCC;  GND общий (Arduino + TB6612 + Battery −)\n"
            "  TB6612 VM ← BAT_prot+ (7.4 В после защит)\n"
            "\n"
            "ПРОТОКОЛ ПОДКЛЮЧЕНИЯ (СТРОГО ПО ШАГАМ — НЕ НАРУШАТЬ):\n"
            "  1. Батарея ОТКЛЮЧЕНА → подключить USB → прошить скетч.\n"
            "  2. ОТКЛЮЧИТЬ USB.\n"
            "  3. Подключить батарею (через защитный блок).\n"
            "  4. Тестировать. При необходимости менять скетч → шаг 1.\n"
            "НИКОГДА не подключай USB+батарею одновременно. Это убило XIAO\n"
            "(см. docs/dev-log.md, инцидент 2026-05-26)."
        ),
        lblloc="center", lblofst=0,
        w=22, h=6.5, fontsize=10,
    ).at((1, -5.5))
    d += legend

    # ===================== Сохранение =====================
    d.save(OUT_BASE + ".svg")
    d.save(OUT_BASE + ".pdf")
    d.save(OUT_BASE + ".png", dpi=200)

print("OK ->", OUT_BASE + ".pdf,.png,.svg")
