"""Схема пайки для ТЕСТА МОТОРОВ через Arduino UNO + TB6612FNG.

Назначение: пока ждём новые XIAO ESP32-S3 Sense с AliExpress (1 неделя) —
проверить работоспособность TB6612 и моторов через временный контроллер
Arduino UNO. Это упрощённая схема: без камеры, без OLED, без микрофона,
без WiFi. Только моторы.

Источник пинов: arduino_motor_test/arduino_motor_test.ino.

============================================================================
БЕЗОПАСНОСТЬ ПИТАНИЯ — ПРОТОКОЛ (КРАТКО, развёрнуто в основной схеме)
============================================================================

  ШАГ 1. Батарея ОТКЛЮЧЕНА → подключи USB → прошей.
  ШАГ 2. ОТКЛЮЧИ USB.
  ШАГ 3. Подключи батарею (через защитный блок) → тестируй.
  ШАГ 4. Менять скетч → шаг 1.

  НИКОГДА USB+батарея одновременно. См. docs/dev-log.md, инцидент 2026-05-26.

============================================================================
"""
import os
import schemdraw
import schemdraw.elements as elm
import matplotlib

matplotlib.rcParams["font.family"] = ["Arial", "DejaVu Sans"]
matplotlib.rcParams["font.size"] = 9

# Цвета по функции
C_VM = "#E53935"      # 7.4 В батарея / VM моторов
C_GND = "#212121"     # GND
C_5V = "#FB8C00"      # 5 В питание логики
C_PWM = "#7CB342"     # PWM
C_DIR = "#0288D1"     # направление IN1/IN2/STBY
C_MOT = "#455A64"     # выходы моторов AO/BO
C_PROT = "#6D4C41"    # ЗАЩИТНЫЕ КОМПОНЕНТЫ
C_NOTE = "#5E35B1"    # пояснительные метки

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_BASE = os.path.join(HERE, "wiring-arduino-motor-test")


def pins_vert(labels_top_to_bottom, anchors, side, total):
    return [
        elm.IcPin(name=lbl, side=side, anchorname=anc, slot=f"{total - i}/{total}")
        for i, (lbl, anc) in enumerate(zip(labels_top_to_bottom, anchors))
    ]


with schemdraw.Drawing(show=False) as d:
    d.config(unit=2.4, fontsize=10)

    # =========================================================================
    # ВЕРХНИЙ ЭТАЖ (y=14): защитная силовая цепочка Bat → BMS → RP-FET → Buck
    # Зазоры между компонентами — не меньше 2 unit, чтобы провода не теснились.
    # =========================================================================

    bat = elm.Ic(
        pins=[
            elm.IcPin(name="+", side="right", anchorname="plus", slot="2/2"),
            elm.IcPin(name="−", side="right", anchorname="minus", slot="1/2"),
        ],
        label="Батарея\n2S Li-Ion 7.4 В",
        lblloc="top", lblofst=0.4,
        w=2.6, h=1.8, plblofst=0.15, fontsize=10,
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
        lblloc="top", lblofst=0.4,
        w=2.2, h=1.8, plblofst=0.15, fontsize=9, color=C_PROT,
    ).at((6, 14))
    d += bms

    rev = elm.Ic(
        pins=[
            elm.IcPin(name="IN+", side="left", anchorname="inp", slot="2/2"),
            elm.IcPin(name="IN−", side="left", anchorname="inn", slot="1/2"),
            elm.IcPin(name="OUT+", side="right", anchorname="outp", slot="2/2"),
            elm.IcPin(name="OUT−", side="right", anchorname="outn", slot="1/2"),
        ],
        label="PPTC 1.5 A\n+ AO3401\n(rev-polarity)",
        lblloc="top", lblofst=0.4,
        w=2.6, h=1.8, plblofst=0.15, fontsize=9, color=C_PROT,
    ).at((11, 14))
    d += rev

    buck = elm.Ic(
        pins=[
            elm.IcPin(name="IN+", side="left", anchorname="inp", slot="2/2"),
            elm.IcPin(name="IN−", side="left", anchorname="inn", slot="1/2"),
            elm.IcPin(name="OUT+ 5 В", side="right", anchorname="outp", slot="2/2"),
            elm.IcPin(name="OUT−", side="right", anchorname="outn", slot="1/2"),
        ],
        label="DC-DC Buck\n(калибровать!\nна 5.0 В)",
        lblloc="top", lblofst=0.4,
        w=2.8, h=1.8, plblofst=0.15, fontsize=9,
    ).at((16, 14))
    d += buck

    # =========================================================================
    # СРЕДНИЙ ЭТАЖ (y=3..10): Arduino (слева) ↔ TB6612 (центр) ↔ Моторы (справа)
    # Зазоры:
    #   Arduino правая грань x=4.6 → TB6612 левая x=12 — зазор 7.4 unit (богато)
    #   TB6612 правая x=16 → Моторы x=18 — зазор 2 unit для коротких проводов
    # =========================================================================

    uno_left = pins_vert(
        ["Vin",  "5V",  "GND", "GND", "USB-B"],
        ["vin", "v5", "gnd1", "gnd2", "usb"],
        side="left", total=5,
    )
    uno_right = pins_vert(
        ["D2",  "D3 ~", "D4",  "D5 ~", "D7",  "D8",  "D9 ~"],
        ["d2",  "d3",   "d4",  "d5",   "d7",  "d8",  "d9"],
        side="right", total=7,
    )
    uno = elm.Ic(
        pins=uno_left + uno_right,
        label="Arduino UNO\n(ATmega328P, 5V)",
        lblloc="top", lblofst=0.5,
        w=3.6, h=7.5, plblofst=0.2, fontsize=11,
    ).at((1, 3))
    d += uno

    # TB6612 — пины КОРОТКИЕ (без "+100мкФ" в названии). Конденсатор покажем
    # отдельной меткой-нотой ниже.
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
        lblloc="top", lblofst=0.5,
        w=4.0, h=8.5, plblofst=0.2, fontsize=11,
    ).at((12, 2.5))
    d += tb

    # Моторы — справа от TB6612 с зазором 3 unit, чтобы провода AO/BO успевали
    # сделать L-route без наложений. ml поднимаем выше (на уровень AO1/AO2),
    # mr опускаем ниже (на уровень BO2/BO1). Лейбл компонента — слева от
    # пинов, чтобы не накладываться сверху на провода.
    ml = elm.Ic(
        pins=[
            elm.IcPin(name="M+", side="left", anchorname="mp", slot="2/2"),
            elm.IcPin(name="M−", side="left", anchorname="mn", slot="1/2"),
        ],
        label="Мотор L",
        lblloc="right", lblofst=0.5,
        w=1.6, h=1.4, plblofst=0.15, fontsize=10,
    ).at((20, 8.5))
    d += ml

    mr = elm.Ic(
        pins=[
            elm.IcPin(name="M+", side="left", anchorname="mp", slot="2/2"),
            elm.IcPin(name="M−", side="left", anchorname="mn", slot="1/2"),
        ],
        label="Мотор R",
        lblloc="right", lblofst=0.5,
        w=1.6, h=1.4, plblofst=0.15, fontsize=10,
    ).at((20, 3.5))
    d += mr

    # =========================================================================
    # ПРЯМЫЕ ПРОВОДА — питание (цепочка соседних компонентов в верхней строке)
    # =========================================================================
    d += elm.Wire("-", color=C_VM,  lw=2.5).at(bat.plus).to(bms.binp)
    d += elm.Wire("-", color=C_GND, lw=2.5).at(bat.minus).to(bms.binn)
    d += elm.Wire("-", color=C_VM,  lw=2.5).at(bms.poutp).to(rev.inp)
    d += elm.Wire("-", color=C_GND, lw=2.5).at(bms.poutn).to(rev.inn)
    d += elm.Wire("-", color=C_VM,  lw=2.5).at(rev.outp).to(buck.inp)
    d += elm.Wire("-", color=C_GND, lw=2.5).at(rev.outn).to(buck.inn)

    # =========================================================================
    # ПРЯМЫЕ ПРОВОДА — моторы (короткие, к соседним компонентам)
    # =========================================================================
    d += elm.Wire("-|", color=C_MOT, lw=1.8).at(tb.ao1).to(ml.mp)
    d += elm.Wire("-|", color=C_MOT, lw=1.8).at(tb.ao2).to(ml.mn)
    d += elm.Wire("-|", color=C_MOT, lw=1.8).at(tb.bo1).to(mr.mp)
    d += elm.Wire("-|", color=C_MOT, lw=1.8).at(tb.bo2).to(mr.mn)

    # =========================================================================
    # NET-LABELS — всё остальное через имена (KiCad-стиль, без длинных линий)
    # =========================================================================
    def add_label(anchor, text, color, direction, length=0.55):
        line = elm.Line(color=color, lw=1.5).at(anchor)
        if direction == "left":    line.left(length)
        elif direction == "right": line.right(length)
        elif direction == "up":    line.up(length)
        elif direction == "down":  line.down(length)
        d.add(line)
        d.add(elm.Tag().label(text, color=color, fontsize=9).at(line.end))

    # --- Управляющие сигналы Arduino ↔ TB6612 (одно имя = соединено) ---
    # Arduino правая колонка: метки вправо
    add_label(uno.d3, "PWMA", C_PWM, "right")
    add_label(uno.d2, "AIN1", C_DIR, "right")
    add_label(uno.d4, "AIN2", C_DIR, "right")
    add_label(uno.d5, "PWMB", C_PWM, "right")
    add_label(uno.d7, "BIN1", C_DIR, "right")
    add_label(uno.d8, "BIN2", C_DIR, "right")
    add_label(uno.d9, "STBY", C_DIR, "right")

    # TB6612 левая колонка: метки влево (тем же именем)
    add_label(tb.pwma, "PWMA", C_PWM, "left")
    add_label(tb.ain1, "AIN1", C_DIR, "left")
    add_label(tb.ain2, "AIN2", C_DIR, "left")
    add_label(tb.pwmb, "PWMB", C_PWM, "left")
    add_label(tb.bin1, "BIN1", C_DIR, "left")
    add_label(tb.bin2, "BIN2", C_DIR, "left")
    add_label(tb.stby, "STBY", C_DIR, "left")

    # --- +5V net (Buck OUT → Arduino Vin/5V → TB6612 VCC) ---
    # На TB6612 +5V уходит вверх (нет конфликта с моторами справа).
    add_label(buck.outp, "+5V", C_5V, "right")
    add_label(uno.vin, "+5V", C_5V, "left")
    add_label(uno.v5,  "+5V", C_5V, "left")
    add_label(tb.vcc,  "+5V", C_5V, "up", length=0.8)

    # --- BAT_prot+ net (выход RP-FET → TB6612 VM, силовое питание моторов 7.4 В) ---
    # На TB6612 VM уходит вверх с увеличенной длиной (зазор от +5V метки).
    add_label(rev.outp, "BAT_prot+", C_VM, "down")
    add_label(tb.vm,    "BAT_prot+", C_VM, "up", length=1.4)

    # --- GND net (общая шина) ---
    # На правой стороне TB6612 GND-метки уходят вниз (gndt2) и вверх (gndt),
    # чтобы не теснились с моторными проводами.
    add_label(buck.outn, "GND", C_GND, "down")
    add_label(uno.gnd1, "GND", C_GND, "left")
    add_label(uno.gnd2, "GND", C_GND, "left")
    add_label(tb.gndt,  "GND", C_GND, "up", length=0.5)
    add_label(tb.gndt2, "GND", C_GND, "down", length=0.5)
    add_label(tb.gndb,  "GND", C_GND, "left")

    # =========================================================================
    # ПОЯСНИТЕЛЬНЫЕ НОТЫ — там, где не помещаются на компонент
    # =========================================================================
    # 100мкФ конденсатор на VM TB6612 — нота СПРАВА от пина VM (не вверху,
    # чтобы не теснилось с метками BAT_prot+/+5V/GND).
    d.add(elm.Tag().label("VM: +100мкФ к GND", color=C_NOTE, fontsize=8).at(
        (tb.vm[0] + 2.6, tb.vm[1])))
    # 470мкФ + 5V защита (SS14+SMAJ) — нотой ПОД Buck.outp
    d.add(elm.Tag().label("после Buck:\n470мкФ + SS14 →\nSMAJ5.0A к GND",
                          color=C_PROT, fontsize=8).at(
        (buck.outp[0] + 0.6, buck.outp[1] - 1.5)))
    # USB ESD-защита — рядом с USB-B пином, СЛЕВА (вне области пинов Arduino)
    d.add(elm.Tag().label("USB-B: только для прошивки\nUSBLC6-2SC6 на D+/D-",
                          color=C_PROT, fontsize=8).at(
        (uno.usb[0] - 1.5, uno.usb[1])))

    # =========================================================================
    # ЛЕГЕНДЫ — три отдельных компактных блока, не сливающиеся в один кирпич
    # =========================================================================

    # Легенда 1: цвет = функция
    legend_color = elm.Ic(
        pins=[],
        label=(
            "ЦВЕТ ПРОВОДА = ФУНКЦИЯ\n"
            "  красный — 7.4 В батарея / VM моторов\n"
            "  оранжевый — 5 В питание логики\n"
            "  зелёный — PWM сигнал\n"
            "  синий — направление IN1/IN2/STBY\n"
            "  серый — выходы моторов AO/BO\n"
            "  чёрный — GND\n"
            "  коричневый — защитные компоненты"
        ),
        lblloc="center", lblofst=0,
        w=7.5, h=3.2, fontsize=9,
    ).at((1, -2))
    d += legend_color

    # Легенда 2: net-labels (стиль KiCad)
    legend_net = elm.Ic(
        pins=[],
        label=(
            "ТЕГИ С ОДИНАКОВЫМ ИМЕНЕМ = СОЕДИНЕНЫ\n"
            "(стиль KiCad/Altium — провода не рисуются)\n"
            "\n"
            "Сети в этой схеме:\n"
            "  +5V · BAT_prot+ · GND\n"
            "  PWMA · PWMB · AIN1 · AIN2\n"
            "  BIN1 · BIN2 · STBY"
        ),
        lblloc="center", lblofst=0,
        w=7.5, h=3.2, fontsize=9,
    ).at((9, -2))
    d += legend_net

    # Легенда 3: пинаут Arduino UNO ↔ TB6612
    legend_pin = elm.Ic(
        pins=[],
        label=(
            "ПИНАУТ Arduino UNO → TB6612FNG\n"
            "  D2 → AIN1     D3 ~ → PWMA   D4 → AIN2   (мотор A = L)\n"
            "  D5 ~ → PWMB   D7 → BIN1     D8 → BIN2   (мотор B = R)\n"
            "  D9 ~ → STBY   (программный enable)\n"
            "  Arduino 5V → TB6612 VCC; GND общий\n"
            "  TB6612 VM ← BAT_prot+ (7.4 В после защит)"
        ),
        lblloc="center", lblofst=0,
        w=11.0, h=3.2, fontsize=9,
    ).at((1, -6))
    d += legend_pin

    # Легенда 4: протокол питания (КРИТИЧНО)
    legend_safety = elm.Ic(
        pins=[],
        label=(
            "⚠ ПРОТОКОЛ ПИТАНИЯ — СТРОГО ПО ШАГАМ\n"
            "1) Батарея ОТКЛЮЧЕНА  → подключи USB → прошей.\n"
            "2) ОТКЛЮЧИ USB.\n"
            "3) Подключи батарею (через защитный блок) → тестируй.\n"
            "4) Менять скетч → шаг 1.\n"
            "НИКОГДА USB+батарея одновременно (убило XIAO 2026-05-26)."
        ),
        lblloc="center", lblofst=0,
        w=11.0, h=3.2, fontsize=9, color=C_PROT,
    ).at((13, -6))
    d += legend_safety

    # =========================================================================
    # Сохранение
    # =========================================================================
    d.save(OUT_BASE + ".svg")
    d.save(OUT_BASE + ".pdf")
    d.save(OUT_BASE + ".png", dpi=200)

print("OK ->", OUT_BASE + ".pdf,.png,.svg")
