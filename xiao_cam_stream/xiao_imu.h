#pragma once
/**
 * IMU BNO085 (Bosch BNO080/085/086) — инерционный датчик курса/ориентации.
 *
 * Подключение: ТА ЖЕ шина I2C, что у ToF — SDA=D9 (GPIO8), SCL=D10 (GPIO9).
 * Адрес BNO085 = 0x4A (Adafruit) или 0x4B (SparkFun / ADR→3V3) — прошивка
 * пробует оба. ToF VL53L7CX = 0x29, конфликта нет.
 * Питание VIN→3V3, GND→GND. RST опционально на свободный GPIO (D7=44) для
 * аппаратного сброса; INT не нужен (поллинг).
 *
 * Режим: Game Rotation Vector — фьюжн гиро+акселерометр БЕЗ магнитометра.
 * Магнитометр в роботе вреден (моторы/токи/феррит искажают), поэтому mag-free —
 * гладкий курс без магнитных скачков (рекомендация SlimeVR / исследования проекта).
 *
 * Библиотека: «SparkFun BNO08x Cortex Based IMU» (НЕ Adafruit — у того
 * Adafruit_Sensor.h конфликтует с sensor_t драйвера камеры ESP32).
 *
 * Отдаёт в /telemetry: imu_ok, imu_yaw/pitch/roll (°), imu_count.
 * Курс imu_yaw читает PoseEstimator (spatial/world_service.py) для позы модели
 * пространства — turntable-скан и одометрия курса становятся автоматическими.
 *
 * Включение: XIAO_IMU_ENABLE в drive_config.h.
 */
#include <Arduino.h>
#include <Wire.h>
#include "drive_config.h"

#ifndef XIAO_IMU_ENABLE
#define XIAO_IMU_ENABLE 0
#endif

#if XIAO_IMU_ENABLE

#include <SparkFun_BNO08x_Arduino_Library.h>

#ifndef XIAO_IMU_RST_PIN
#define XIAO_IMU_RST_PIN -1   /* -1 = софт-сброс; иначе GPIO аппаратного RST */
#endif
#ifndef XIAO_IMU_ADDR
#define XIAO_IMU_ADDR 0x4A    /* пробуем и 0x4A, и 0x4B */
#endif
#ifndef XIAO_IMU_REPORT_MS
#define XIAO_IMU_REPORT_MS 20  /* период отчёта (мс) → 50 Гц */
#endif

static constexpr float XIAO_RAD2DEG = 57.29578f;

static BNO08x gImu;
static bool gImuOk = false;
static float gImuYawDeg = 0.0f, gImuPitchDeg = 0.0f, gImuRollDeg = 0.0f;
static uint32_t gImuCount = 0;

static void xiaoImuEnableReports() {
  gImu.enableGameRotationVector(XIAO_IMU_REPORT_MS);
}

static bool xiaoImuTryBegin() {
  /* Брейкауты BNO085 бывают на 0x4A (Adafruit) и 0x4B (SparkFun) — пробуем оба. */
  if (gImu.begin(XIAO_IMU_ADDR, Wire, -1, XIAO_IMU_RST_PIN)) {
    return true;
  }
  const uint8_t alt = (XIAO_IMU_ADDR == 0x4A) ? 0x4B : 0x4A;
  return gImu.begin(alt, Wire, -1, XIAO_IMU_RST_PIN);
}

static inline void xiaoImuInit() {
  /* Шину Wire обычно уже поднял ToF (Wire.begin(SDA,SCL)). Если ToF выключен —
     поднимем сами. */
#if !XIAO_TOF_ENABLE
  Wire.begin(XIAO_TOF_SDA, XIAO_TOF_SCL);
  Wire.setClock(400000L);
#endif
  gImuOk = xiaoImuTryBegin();
  if (gImuOk) {
    xiaoImuEnableReports();
    Serial.println(F("imu: BNO08x OK (Game Rotation Vector, без магнитометра)"));
  } else {
    Serial.println(F("imu: BNO08x не найден (addr 0x4A/0x4B? SDA=D9/SCL=D10? 3V3?) — ретрай в loop"));
  }
}

static inline void xiaoImuTick() {
  if (!gImuOk) {
    static uint32_t lastRetry = 0;
    const uint32_t now = millis();
    if (now - lastRetry > 2000u) {
      lastRetry = now;
      gImuOk = xiaoImuTryBegin();
      if (gImuOk) {
        xiaoImuEnableReports();
        Serial.println(F("imu: BNO08x подключился"));
      }
    }
    return;
  }
  if (gImu.wasReset()) {
    xiaoImuEnableReports();  /* после сброса сенсора заново включить отчёт */
  }
  /* Вычитать накопленные события (не больше 8 за тик, чтобы не зависнуть). */
  for (uint8_t i = 0; i < 8 && gImu.getSensorEvent(); ++i) {
    if (gImu.getSensorEventID() == SENSOR_REPORTID_GAME_ROTATION_VECTOR) {
      gImuYawDeg = gImu.getYaw() * XIAO_RAD2DEG;     /* курс вокруг вертикали, −180..+180 */
      gImuPitchDeg = gImu.getPitch() * XIAO_RAD2DEG;
      gImuRollDeg = gImu.getRoll() * XIAO_RAD2DEG;
      gImuCount++;
    }
  }
}

static inline void xiaoImuAppendTelemetry(String &j, bool &comma) {
  auto appendU = [&](const char *k, uint32_t v) {
    if (comma) j += ',';
    comma = true;
    j += '"'; j += k; j += "\":"; j += v;
  };
  auto appendF = [&](const char *k, float v) {
    if (comma) j += ',';
    comma = true;
    j += '"'; j += k; j += "\":"; j += String(v, 2);
  };
  appendU("imu_ok", gImuOk ? 1u : 0u);
  if (gImuOk) {
    appendF("imu_yaw", gImuYawDeg);
    appendF("imu_pitch", gImuPitchDeg);
    appendF("imu_roll", gImuRollDeg);
    appendU("imu_count", gImuCount);
  }
}

#else  /* !XIAO_IMU_ENABLE */

static inline void xiaoImuInit() {}
static inline void xiaoImuTick() {}
static inline void xiaoImuAppendTelemetry(String &, bool &) {}

#endif
