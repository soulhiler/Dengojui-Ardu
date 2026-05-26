"""Схема пайки «XIAO Деньгожуй» — schemdraw, стиль KiCad.
Сигнальные связи показаны NET-LABELS (теги одинакового имени = соединены),
прямыми проводами — только короткие 2-pin связи (питание, моторы).
Источник пинов: xiao_cam_stream/drive_config.h.

============================================================================
ВНИМАНИЕ — БЕЗОПАСНОСТЬ ПИТАНИЯ (читать ПЕРЕД пайкой/первым включением)
============================================================================

ПРАВИЛО 1 (САМОЕ ВАЖНОЕ). НИКОГДА не подключай USB к ПК и батарею 7.4 В
одновременно. Это создаёт «петлю земли» через общий минус: уравнивающий
ток между землёй ПК и землёй батареи течёт через USB GND, пробивает
ESD-защиту D+/D− и убивает USB-PHY (а часто и весь SoC) ESP32-S3.
Симптом — красный диод горит, чип «мёртв». Восстановить нельзя.
Это уже случилось один раз — см. `docs/dev-log.md`, инцидент 2026-05-26.

Если нужны оба источника одновременно (прошивка во время полевых
испытаний и т. п.) — ставь USB-isolator (ADuM3160) или ideal-diode
MOSFET-переключатель источников.

ПРАВИЛО 2. ДО первого подключения Buck-преобразователя к XIAO 5V pin
ОБЯЗАТЕЛЬНО измерь мультиметром выход Buck без нагрузки. Цель: 5.0 В
± 0.1 В. Buck-модули с AliExpress приходят с потенциометром в случайном
положении и могут выдавать 12+ В — это смерть XIAO с первой подачи.

ПРАВИЛО 3. Цветовая маркировка проводов питания, разная для разных
напряжений:
  - BAT+ (7.4 В) — толстый красный или жёлтый
  - Buck OUT+ (5 В) — тонкий красный или оранжевый
  - 3V3 — оранжевый (потоньше)
  - GND — ВСЕГДА чёрный, и никогда не использовать чёрный для других сетей

Желательно (не обязательно, но крайне рекомендуется):
  - Schottky-диод (SS14) последовательно между Buck OUT+ и XIAO 5V
    (анод к Buck) — защита от обратки с USB VBUS. Падение 0.3 В,
    Buck подкрутить на 5.3 В.
  - PPTC-предохранитель 1–2 А на BAT+ — защита от КЗ при пайке.
  - TVS-диоды на USB D+/D− (USBLC6-2SC6 или PRTR5V0U2X).

============================================================================
"""
import os
import schemdraw
import schemdraw.elements as elm
import matplotlib

matplotlib.rcParams["font.family"] = ["Arial", "DejaVu Sans"]
matplotlib.rcParams["font.size"] = 9

# Цвета по функции
C_VM = "#E53935"
C_GND = "#212121"
C_3V3 = "#FB8C00"
C_PWM = "#7CB342"
C_DIR = "#0288D1"
C_MOT = "#455A64"
C_SPI_D = "#8E24AA"
C_SPI_C = "#D81B60"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_BASE = os.path.join(HERE, "wiring-xiao-tb6612")


def pins_vert(labels_top_to_bottom, anchors, side, total):
    """Пины на вертикальной стороне сверху вниз (slot N=верх → 1=низ)."""
    return [
        elm.IcPin(name=lbl, side=side, anchorname=anc, slot=f"{total - i}/{total}")
        for i, (lbl, anc) in enumerate(zip(labels_top_to_bottom, anchors))
    ]


with schemdraw.Drawing(show=False) as d:
    d.config(unit=2.2, fontsize=10)

    # ===================== Компоненты =====================

    bat = elm.Ic(
        pins=[
            elm.IcPin(name="+", side="bottom", anchorname="plus", slot="1/2"),
            elm.IcPin(name="−", side="bottom", anchorname="minus", slot="2/2"),
        ],
        label="Батарея 7.4 В",
        lblloc="top", lblofst=0.3,
        w=2.4, h=1.4, plblofst=0.15, fontsize=10,
    ).at((1, 13))
    d += bat

    buck = elm.Ic(
        pins=[
            elm.IcPin(name="IN+", side="left", anchorname="inp", slot="2/2"),
            elm.IcPin(name="IN−", side="left", anchorname="inn", slot="1/2"),
            elm.IcPin(name="OUT+ 5В", side="right", anchorname="outp", slot="2/2"),
            elm.IcPin(name="OUT−", side="right", anchorname="outn", slot="1/2"),
        ],
        label="DC-DC Buck 5В",
        lblloc="top", lblofst=0.3,
        w=3.0, h=1.6, plblofst=0.15, fontsize=10,
    ).at((6, 13))
    d += buck

    ml = elm.Ic(
        pins=[
            elm.IcPin(name="M+", side="left", anchorname="mp", slot="2/2"),
            elm.IcPin(name="M−", side="left", anchorname="mn", slot="1/2"),
        ],
        label="Мотор L",
        lblloc="top", lblofst=0.3,
        w=1.8, h=1.6, plblofst=0.15, fontsize=10,
    ).at((14, 11))
    d += ml

    mr = elm.Ic(
        pins=[
            elm.IcPin(name="M+", side="left", anchorname="mp", slot="2/2"),
            elm.IcPin(name="M−", side="left", anchorname="mn", slot="1/2"),
        ],
        label="Мотор R",
        lblloc="top", lblofst=0.3,
        w=1.8, h=1.6, plblofst=0.15, fontsize=10,
    ).at((14, 7))
    d += mr

    # TB6612 — вертикально 2 кол × 8
    tb_left = pins_vert(
        ["PWMA", "AIN2", "AIN1", "STBY", "BIN1", "BIN2", "PWMB", "GND"],
        ["pwma", "ain2", "ain1", "stby", "bin1", "bin2", "pwmb", "gndb"],
        side="left", total=8,
    )
    tb_right = pins_vert(
        ["VM", "VCC", "GND", "AO1", "AO2", "BO2", "BO1", "GND"],
        ["vm", "vcc", "gndt", "ao1", "ao2", "bo2", "bo1", "gndt2"],
        side="right", total=8,
    )
    tb = elm.Ic(
        pins=tb_left + tb_right,
        label="TB6612FNG",
        lblloc="top", lblofst=0.4,
        w=3.2, h=8, plblofst=0.2, fontsize=11,
    ).at((10, 1))
    d += tb

    # XIAO — вертикально 2 кол × 7
    xi_left = pins_vert(
        ["5V", "GND", "3V3", "D10", "D9", "D8", "D7"],
        ["v5", "gnd", "v33", "d10", "d9", "d8", "d7"],
        side="left", total=7,
    )
    xi_right = pins_vert(
        ["D0", "D1", "D2", "D3", "D4", "D5", "D6"],
        ["d0", "d1", "d2", "d3", "d4", "d5", "d6"],
        side="right", total=7,
    )
    xiao = elm.Ic(
        pins=xi_left + xi_right,
        label="XIAO ESP32-S3 Sense",
        lblloc="top", lblofst=0.4,
        w=3.0, h=7, plblofst=0.2, fontsize=11,
    ).at((4, 2))
    d += xiao

    # OLED — вертикально 1 кол × 7, пины на ПРАВОЙ стороне
    ol_right = pins_vert(
        ["GND", "VCC", "D0/SCK", "D1/MOSI", "RES", "DC", "CS"],
        ["gnd", "vcc", "sck", "mosi", "res", "dc", "cs"],
        side="right", total=7,
    )
    oled = elm.Ic(
        pins=ol_right,
        label="OLED 0.96″\nSSD1306 SPI",
        lblloc="top", lblofst=0.5,
        w=2.4, h=7, plblofst=0.2, fontsize=10,
    ).at((17.5, 2))
    d += oled

    # ===================== ПРЯМЫЕ ПРОВОДА (2-pin, короткие) =====================
    # Питание моторов: BAT+ → TB6612 VM (горизонтально через верх)
    d += elm.Wire("-|", color=C_VM, lw=2.5).at(bat.plus).to(tb.vm)
    # BAT+ → BUCK IN+ (короткий вправо)
    d += elm.Wire("-|", color=C_VM, lw=2).at(bat.plus).to(buck.inp)
    # BUCK OUT+ → XIAO 5V (через верх XIAO)
    d += elm.Wire("-|", color=C_VM, lw=2).at(buck.outp).to(xiao.v5)
    # Моторы: TB6612 AO/BO → motors
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.ao1).to(ml.mp)
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.ao2).to(ml.mn)
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.bo1).to(mr.mp)
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.bo2).to(mr.mn)

    # ===================== NET-LABELS для дальних связей =====================
    # Стиль KiCad: у XIAO-пина тег с именем сигнала, у TB6612/OLED — такой же.
    # Одно имя = соединены.

    def add_label(anchor, text, color, direction, length=0.5):
        line = elm.Line(color=color, lw=1.6).at(anchor)
        if direction == "left":
            line.left(length)
        elif direction == "right":
            line.right(length)
        elif direction == "up":
            line.up(length)
        elif direction == "down":
            line.down(length)
        d.add(line)
        d.add(elm.Tag().label(text, color=color, fontsize=9).at(line.end))

    # --- Управление моторами: XIAO ↔ TB6612 ---
    # PWMA = D5, AIN1 = D0, AIN2 = D1, PWMB = D4, BIN1 = D2, BIN2 = D3
    # XIAO правая колонка (тег вправо)
    add_label(xiao.d5, "PWMA", C_PWM, "right")
    add_label(xiao.d0, "AIN1", C_DIR, "right")
    add_label(xiao.d1, "AIN2", C_DIR, "right")
    add_label(xiao.d4, "PWMB", C_PWM, "right")
    add_label(xiao.d2, "BIN1", C_DIR, "right")
    add_label(xiao.d3, "BIN2", C_DIR, "right")
    # TB6612 левая колонка (тег влево, такие же имена)
    add_label(tb.pwma, "PWMA", C_PWM, "left")
    add_label(tb.ain1, "AIN1", C_DIR, "left")
    add_label(tb.ain2, "AIN2", C_DIR, "left")
    add_label(tb.pwmb, "PWMB", C_PWM, "left")
    add_label(tb.bin1, "BIN1", C_DIR, "left")
    add_label(tb.bin2, "BIN2", C_DIR, "left")

    # --- SPI: XIAO ↔ OLED ---
    # XIAO LEFT col: D8 SCK, D10 MOSI, D7 DC; XIAO RIGHT col: D6 CS
    add_label(xiao.d8, "SCK", C_SPI_D, "left")
    add_label(xiao.d10, "MOSI", C_SPI_D, "left")
    add_label(xiao.d7, "DC", C_SPI_C, "left")
    add_label(xiao.d6, "CS", C_SPI_C, "right")
    # OLED — правая колонка с тегами вправо (same names)
    add_label(oled.sck, "SCK", C_SPI_D, "right")
    add_label(oled.mosi, "MOSI", C_SPI_D, "right")
    add_label(oled.dc, "DC", C_SPI_C, "right")
    add_label(oled.cs, "CS", C_SPI_C, "right")

    # --- 3V3 net labels ---
    add_label(xiao.v33, "3V3", C_3V3, "left")
    add_label(tb.vcc, "3V3", C_3V3, "right")
    add_label(tb.stby, "3V3", C_3V3, "left")
    add_label(oled.vcc, "3V3", C_3V3, "right")
    add_label(oled.res, "3V3", C_3V3, "right")

    # --- GND net labels ---
    add_label(bat.minus, "GND", C_GND, "down")
    add_label(buck.inn, "GND", C_GND, "left")
    add_label(buck.outn, "GND", C_GND, "right")
    add_label(tb.gndt, "GND", C_GND, "right")
    add_label(tb.gndt2, "GND", C_GND, "right")
    add_label(tb.gndb, "GND", C_GND, "left")
    add_label(xiao.gnd, "GND", C_GND, "left")
    add_label(oled.gnd, "GND", C_GND, "right")

    # ===================== Легенда (внизу, горизонтальная) =====================
    legend = elm.Ic(
        pins=[],
        label=(
            "УСЛОВНЫЕ ОБОЗНАЧЕНИЯ\n"
            "Теги с одинаковым именем соединены общей шиной (стиль KiCad/Altium):\n"
            "3V3 · GND · PWMA · PWMB · AIN1 · AIN2 · BIN1 · BIN2 · SCK · MOSI · DC · CS\n"
            "\n"
            "ЦВЕТ ПРОВОДА = ФУНКЦИЯ:\n"
            "красный — VM/5В питание   ·   зелёный — PWM   ·   синий — направление IN1/IN2\n"
            "серый — выходы моторов AO/BO   ·   фиолет. — SPI данные (SCK,MOSI)\n"
            "розовый — SPI управление (DC,CS)   ·   оранж. — 3V3 шина   ·   чёрный — GND"
        ),
        lblloc="center", lblofst=0,
        w=18, h=4, fontsize=11,
    ).at((1, -3))
    d += legend

    # ===================== Сохранение =====================
    d.save(OUT_BASE + ".svg")
    d.save(OUT_BASE + ".pdf")
    d.save(OUT_BASE + ".png", dpi=200)

print("OK ->", OUT_BASE + ".pdf,.png,.svg")
