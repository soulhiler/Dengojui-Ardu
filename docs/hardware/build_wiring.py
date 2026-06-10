"""Схема пайки «XIAO Деньгожуй» — schemdraw, стиль KiCad.
Сигнальные связи показаны NET-LABELS (теги одинакового имени = соединены),
прямыми проводами — только короткие 2-pin связи (питание, моторы).
Источник пинов: xiao_cam_stream/drive_config.h.

ВНИМАНИЕ — АКТУАЛЬНОСТЬ СИГНАЛЬНОЙ РАЗВОДКИ:
  Этот файл показывает СТАРЫЙ вариант сигналов (OLED по SPI на D6/D7/D8).
  Актуальная сигнальная разводка ядра 1.2.x — ToF VL53L0X (см.
  `docs/hardware/wiring-xiao-motor-tof.md`). Здесь файл сохранён прежде всего
  ради ЦЕПИ ЗАЩИТЫ ПИТАНИЯ (BMS / PPTC / AO3401 / Schottky SS14 / SMAJ5.0A /
  USBLC6-2SC6 + конденсаторы) — она применима к ЛЮБОЙ сигнальной разводке.

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

ЗАЩИТЫ В ЭТОЙ СХЕМЕ (Уровень 1, must-have):
  - BMS 2S на батарее (защита Li-Ion от перезаряд/переразряд/КЗ — !пожар!)
  - PPTC 1.5 A на BAT+ (КЗ-предохранитель)
  - AO3401 P-MOSFET (защита от обратной полярности батареи)
  - SS14 Schottky на 5V XIAO (защита от обратки USB VBUS)
  - SMAJ5.0A TVS на 5V XIAO (crowbar — пробой при >6.5 В замыкает шину)
  - USBLC6-2SC6 на USB D+/D− (защита USB-PHY от ESD)
  - 470мкФ + 100нФ после Buck (поглощение просадок при пиках)
  - 1000мкФ + 100нФ на BAT+ (поглощение выбросов от моторов)

============================================================================
"""
import os
import schemdraw
import schemdraw.elements as elm
import matplotlib

matplotlib.rcParams["font.family"] = ["Arial", "DejaVu Sans"]
matplotlib.rcParams["font.size"] = 9

# Цвета по функции
C_VM = "#E53935"        # VM/5В питание (силовое)
C_GND = "#212121"       # GND
C_3V3 = "#FB8C00"       # 3V3 шина
C_PWM = "#7CB342"       # PWM
C_DIR = "#0288D1"       # направление IN1/IN2
C_MOT = "#455A64"       # выходы моторов
C_SPI_D = "#8E24AA"     # SPI данные
C_SPI_C = "#D81B60"     # SPI управление
C_PROT = "#6D4C41"      # ЗАЩИТЫ (коричневый, выделен в схеме)

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

    # ===================== Источники питания =====================

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

    # ----- BMS (Battery Management System) — защита Li-Ion -----
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

    # ----- PPTC + AO3401 reverse-polarity FET (объединённый защитный блок) -----
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

    # ----- Buck DC-DC -----
    buck = elm.Ic(
        pins=[
            elm.IcPin(name="IN+", side="left", anchorname="inp", slot="2/2"),
            elm.IcPin(name="IN−", side="left", anchorname="inn", slot="1/2"),
            elm.IcPin(name="OUT+ 5.3В", side="right", anchorname="outp", slot="2/2"),
            elm.IcPin(name="OUT−", side="right", anchorname="outn", slot="1/2"),
        ],
        label="DC-DC Buck\n(калибровать\nна 5.3 В!)",
        lblloc="top", lblofst=0.3,
        w=2.6, h=1.6, plblofst=0.15, fontsize=9,
    ).at((13, 14))
    d += buck

    # ----- 5V output protection: SS14 + SMAJ5.0A + 470µF -----
    prot5v = elm.Ic(
        pins=[
            elm.IcPin(name="IN", side="left", anchorname="inp", slot="2/2"),
            elm.IcPin(name="GND", side="left", anchorname="inn", slot="1/2"),
            elm.IcPin(name="5.0В", side="right", anchorname="outp", slot="2/2"),
            elm.IcPin(name="GND", side="right", anchorname="outn", slot="1/2"),
        ],
        label="5V защита\n470мкФ + 100нФ\n→ SS14 →\nSMAJ5.0A к GND",
        lblloc="top", lblofst=0.3,
        w=2.6, h=1.8, plblofst=0.15, fontsize=8, color=C_PROT,
    ).at((17, 14))
    d += prot5v

    # USB ESD protection — реализована как net-label у XIAO (USBLC6-2SC6
    # ставится физически между USB-кабелем и пинами XIAO; провод тут
    # символический, поэтому отдельный блок не рисуем — указание в легенде).

    # ===================== Моторы =====================

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
    ).at((10, 1))
    d += tb

    # ===================== XIAO ESP32-S3 Sense =====================
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

    # ===================== OLED =====================
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
    ).at((19, 2))
    d += oled

    # ===================== ПРЯМЫЕ ПРОВОДА — питание (последовательная цепочка) =====================
    # 1) Батарея → BMS
    d += elm.Wire("-", color=C_VM, lw=2.5).at(bat.plus).to(bms.binp)
    d += elm.Wire("-", color=C_GND, lw=2.5).at(bat.minus).to(bms.binn)

    # 2) BMS → PPTC+RP-FET
    d += elm.Wire("-", color=C_VM, lw=2.5).at(bms.poutp).to(rev.inp)
    d += elm.Wire("-", color=C_GND, lw=2.5).at(bms.poutn).to(rev.inn)

    # 3) PPTC+RP-FET → Buck IN
    d += elm.Wire("-", color=C_VM, lw=2.5).at(rev.outp).to(buck.inp)
    d += elm.Wire("-", color=C_GND, lw=2.5).at(rev.outn).to(buck.inn)

    # 4) Buck OUT → 5V protection
    d += elm.Wire("-", color=C_VM, lw=2).at(buck.outp).to(prot5v.inp)
    d += elm.Wire("-", color=C_GND, lw=2).at(buck.outn).to(prot5v.inn)

    # 5) 5V и BAT_prot+ — через NET-LABELS (одна метка = провод соединён,
    #    стиль KiCad). Так избегаем длинных пересекающихся линий через всю схему.

    # ===================== Моторы: TB6612 AO/BO → motors =====================
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.ao1).to(ml.mp)
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.ao2).to(ml.mn)
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.bo1).to(mr.mp)
    d += elm.Wire("-", color=C_MOT, lw=1.8).at(tb.bo2).to(mr.mn)

    # ===================== NET-LABELS для сигналов и шин =====================
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
    add_label(xiao.d5, "PWMA", C_PWM, "right")
    add_label(xiao.d0, "AIN1", C_DIR, "right")
    add_label(xiao.d1, "AIN2", C_DIR, "right")
    add_label(xiao.d4, "PWMB", C_PWM, "right")
    add_label(xiao.d2, "BIN1", C_DIR, "right")
    add_label(xiao.d3, "BIN2", C_DIR, "right")
    add_label(tb.pwma, "PWMA", C_PWM, "left")
    add_label(tb.ain1, "AIN1", C_DIR, "left")
    add_label(tb.ain2, "AIN2", C_DIR, "left")
    add_label(tb.pwmb, "PWMB", C_PWM, "left")
    add_label(tb.bin1, "BIN1", C_DIR, "left")
    add_label(tb.bin2, "BIN2", C_DIR, "left")

    # --- SPI: XIAO ↔ OLED ---
    add_label(xiao.d8, "SCK", C_SPI_D, "left")
    add_label(xiao.d10, "MOSI", C_SPI_D, "left")
    add_label(xiao.d7, "DC", C_SPI_C, "left")
    add_label(xiao.d6, "CS", C_SPI_C, "right")
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

    # --- +5V net labels (после 5V-защиты идёт на XIAO 5V) ---
    add_label(prot5v.outp, "+5V", C_VM, "right")
    add_label(xiao.v5, "+5V", C_VM, "left")

    # --- BAT_prot+ net labels (выход RP-FET идёт на Buck IN+ и на TB6612 VM) ---
    # На Buck IN+ уже идёт прямой провод (соседние компоненты), а на TB6612 VM —
    # через net-label, чтобы не тянуть длинный провод через всю схему.
    add_label(rev.outp, "BAT_prot+", C_VM, "down")
    add_label(tb.vm, "BAT_prot+", C_VM, "right")

    # --- USBLC6-2SC6 — символическая метка у XIAO (физически — между USB-кабелем
    #     и пинами USB на плате XIAO; на схеме не имеет нумерованных пинов) ---
    d.add(elm.Tag().label("[!] USBLC6-2SC6 на USB D+/D-",
                           color=C_PROT, fontsize=8).at((xiao.v5[0] - 1.2, xiao.v5[1] + 0.6)))

    # --- GND net labels (одна общая шина) ---
    add_label(prot5v.outn, "GND", C_GND, "right")
    add_label(buck.outn, "GND", C_GND, "down")
    add_label(tb.gndt, "GND", C_GND, "right")
    add_label(tb.gndt2, "GND", C_GND, "right")
    add_label(tb.gndb, "GND", C_GND, "left")
    add_label(xiao.gnd, "GND", C_GND, "left")
    add_label(oled.gnd, "GND", C_GND, "right")

    # ===================== Легенда (внизу) =====================
    legend = elm.Ic(
        pins=[],
        label=(
            "УСЛОВНЫЕ ОБОЗНАЧЕНИЯ\n"
            "Теги с одинаковым именем = соединены общей шиной (стиль KiCad/Altium):\n"
            "3V3 · GND · PWMA · PWMB · AIN1 · AIN2 · BIN1 · BIN2 · SCK · MOSI · DC · CS\n"
            "\n"
            "ЦВЕТ ПРОВОДА = ФУНКЦИЯ:\n"
            "красный — VM / 5В питание  ·  зелёный — PWM  ·  синий — направление IN1/IN2\n"
            "серый — выходы моторов AO/BO  ·  фиолет. — SPI данные (SCK,MOSI)\n"
            "розовый — SPI управление (DC,CS)  ·  оранж. — 3V3 шина  ·  чёрный — GND\n"
            "коричневый — ЗАЩИТНЫЕ КОМПОНЕНТЫ\n"
            "\n"
            "ПОРЯДОК ПИТАНИЯ (сверху схемы):  Bat → BMS → PPTC+RP-FET → Buck → 5V-защита → XIAO\n"
            "Ответвление от BAT_prot+ (после RP-FET): на TB6612 VM с конденсатором 100мкФ к GND.\n"
            "\n"
            "БЕЗОПАСНОСТЬ ПИТАНИЯ (КРИТИЧНО):\n"
            "1) НИКОГДА USB+батарея одновременно — петля земли убивает USB-PHY ESP32-S3.\n"
            "2) ДО подключения Buck к XIAO 5V — измерить мультиметром: 5.0В ± 0.1В.\n"
            "3) BAT+ толстый красный/жёлтый; 5В тонкий красный/оранжевый; GND только чёрный.\n"
            "Полный разбор инцидента 2026-05-26: docs/dev-log.md."
        ),
        lblloc="center", lblofst=0,
        w=22, h=5.5, fontsize=10,
    ).at((1, -4.5))
    d += legend

    # ===================== Сохранение =====================
    d.save(OUT_BASE + ".svg")
    d.save(OUT_BASE + ".pdf")
    d.save(OUT_BASE + ".png", dpi=200)

print("OK ->", OUT_BASE + ".pdf,.png,.svg")
