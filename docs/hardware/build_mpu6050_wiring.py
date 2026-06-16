"""Схема подключения IMU MPU6050 (GY-521) к XIAO ESP32-S3 — schemdraw, стиль KiCad.

Ключевая идея: MPU6050 вешается на ТУ ЖЕ шину I2C, что и ToF VL53L7CX
(SDA=D9/GPIO8, SCL=D10/GPIO9). Адреса разные (MPU6050 0x68, ToF 0x29) —
конфликта нет. Net-labels с одинаковым именем = соединены (как KiCad/Altium).

Это «нарисованная» версия таблицы из wiring-xiao-motor-tof.md.

Запуск: python docs/hardware/build_mpu6050_wiring.py
"""
import os
import schemdraw
import schemdraw.elements as elm
import matplotlib

matplotlib.rcParams["font.family"] = ["Arial", "DejaVu Sans"]
matplotlib.rcParams["font.size"] = 9

C_PWR = "#FB8C00"   # 3V3
C_GND = "#212121"   # GND
C_SDA = "#8E24AA"   # I2C данные
C_SCL = "#D81B60"   # I2C клок
C_NC = "#9aa3ad"    # не подключать

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_BASE = os.path.join(HERE, "wiring-xiao-mpu6050-gy521")


def pins_vert(labels, anchors, side, total):
    return [
        elm.IcPin(name=lbl, side=side, anchorname=anc, slot=f"{total - i}/{total}")
        for i, (lbl, anc) in enumerate(zip(labels, anchors))
    ]


with schemdraw.Drawing(show=False) as d:
    d.config(unit=2.4, fontsize=10)

    # XIAO — левая колонка пинов (как на плате: 5V/GND/3V3/D10/D9/D8/D7)
    xi_left = pins_vert(
        ["5V", "GND", "3V3", "D10 (SCL)", "D9 (SDA)", "D8 (ToF LPn)", "D7 (своб.)"],
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
        w=3.4, h=7.5, plblofst=0.2, fontsize=11,
    ).at((1, 2))
    d += xiao

    # MPU6050 / GY-521 — 8-pin брейкаут, пины справа (порядок как на плате)
    imu = elm.Ic(
        pins=pins_vert(
            ["VCC", "GND", "SCL", "SDA", "XDA", "XCL", "AD0", "INT"],
            ["vcc", "gnd", "scl", "sda", "xda", "xcl", "ad0", "intp"],
            side="right", total=8,
        ),
        label="IMU MPU6050\n(GY-521, без магнит.)",
        lblloc="top", lblofst=0.4,
        w=2.6, h=6.6, plblofst=0.2, fontsize=10,
    ).at((9, 3.2))
    d += imu

    # ToF VL53L7CX — уже подключён, показываем что делит шину
    tof = elm.Ic(
        pins=pins_vert(
            ["VIN", "GND", "SCL", "SDA", "LPn"],
            ["vin", "gnd", "scl", "sda", "lpn"],
            side="right", total=5,
        ),
        label="ToF VL53L7CX\n(уже на шине)",
        lblloc="top", lblofst=0.4,
        w=2.6, h=4.6, plblofst=0.2, fontsize=10,
    ).at((9, -4.0))
    d += tof

    def tag(anchor, text, color, direction, length=0.55):
        line = elm.Line(color=color, lw=1.6).at(anchor)
        getattr(line, direction)(length)
        d.add(line)
        d.add(elm.Tag().label(text, color=color, fontsize=9).at(line.end))

    # XIAO — теги
    tag(xiao.v33, "3V3", C_PWR, "left")
    tag(xiao.gnd, "GND", C_GND, "left")
    tag(xiao.d10, "SCL", C_SCL, "left")
    tag(xiao.d9, "SDA", C_SDA, "left")
    tag(xiao.d8, "ToF_LPn", "#6D4C41", "left")

    # MPU6050 — теги (те же имена = соединено)
    tag(imu.vcc, "3V3", C_PWR, "right")
    tag(imu.gnd, "GND", C_GND, "right")
    tag(imu.scl, "SCL", C_SCL, "right")
    tag(imu.sda, "SDA", C_SDA, "right")
    tag(imu.ad0, "GND", C_GND, "right")  # AD0→GND = адрес 0x68
    d.add(elm.Tag().label("= адрес 0x68", color="#6b7785", fontsize=8).at(
        (imu.ad0[0] + 2.5, imu.ad0[1])))
    d.add(elm.Tag().label("XDA — не подключать", color=C_NC, fontsize=8).at(
        (imu.xda[0] + 0.6, imu.xda[1])))
    d.add(elm.Tag().label("XCL — не подключать", color=C_NC, fontsize=8).at(
        (imu.xcl[0] + 0.6, imu.xcl[1])))
    d.add(elm.Tag().label("INT — не подключать", color=C_NC, fontsize=8).at(
        (imu.intp[0] + 0.6, imu.intp[1])))

    # ToF — теги (та же шина)
    tag(tof.vin, "3V3", C_PWR, "right")
    tag(tof.gnd, "GND", C_GND, "right")
    tag(tof.scl, "SCL", C_SCL, "right")
    tag(tof.sda, "SDA", C_SDA, "right")
    tag(tof.lpn, "ToF_LPn", "#6D4C41", "right")

    legend = elm.Ic(
        pins=[],
        label=(
            "ПОДКЛЮЧЕНИЕ MPU6050 (GY-521) К XIAO (общая шина I2C с ToF)\n"
            "Теги с одинаковым именем = соединены (стиль KiCad).\n"
            "\n"
            "  VCC -> XIAO 3V3            SDA -> XIAO D9 (GPIO8)\n"
            "  GND -> XIAO GND            SCL -> XIAO D10 (GPIO9)\n"
            "  AD0 -> GND (адрес 0x68; на 3V3 -> 0x69, тогда XIAO_IMU_ADDR 0x69)\n"
            "  XDA / XCL / INT -> НЕ подключать (всё по поллингу основной шины)\n"
            "\n"
            "ToF VL53L7CX уже сидит на той же SDA/SCL — MPU6050 просто добавляется\n"
            "параллельно. Адреса разные: MPU6050 0x68, ToF 0x29 — НЕ конфликтуют.\n"
            "Прошивка: XIAO_IMU_ENABLE 1 в drive_config.h, драйвер xiao_imu.h (прямой Wire).\n"
            "\n"
            "Питание VCC от 3.3 В (логика MPU6050 НЕ 5V-tolerant; на GY-521 свой\n"
            "регулятор, но корми 3V3, чтобы уровни I2C были 3.3 В).\n"
            "Гироскоп калибрует ноль на старте — НЕ двигать ~0.6 с после включения.\n"
            "Магнитометра нет — для курса робота это плюс (моторы/токи не искажают)."
        ),
        lblloc="center", lblofst=0,
        w=20, h=5.4, fontsize=10,
    ).at((1, -11))
    d += legend

    d.save(OUT_BASE + ".svg")
    d.save(OUT_BASE + ".pdf")
    d.save(OUT_BASE + ".png", dpi=200)

print("OK ->", OUT_BASE + ".pdf,.png,.svg")
