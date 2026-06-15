#pragma once
/**
 * MPU6050 (GY-521) — 6-осевой IMU (акселерометр + гироскоп), I2C.
 * Делит шину с VL53L7CX (адреса 0x68 vs 0x29 — без конфликта). Лишних пинов нет:
 * SDA→D9(GPIO8), SCL→D10(GPIO9), VCC→3V3, GND→GND. Включение: XIAO_IMU_ENABLE.
 *
 * Драйвер прямой (Wire, без внешней библиотеки — CI не нужна доп. зависимость).
 * Даёт КУРС (yaw) для ориентации робота: yaw — интеграл gyro-Z с авто-вычетом
 * смещения (калибровка на старте, робот стоит ~1 c); pitch/roll — из акселя
 * (гравитация), абсолютные. Без магнитометра yaw медленно дрейфует — ограничивается
 * энкодерами/Wi-Fi-якорем на сервере (см. PoseEstimator, docs/roadmap.md).
 *
 * Телеметрия: imu_ok, imu_yaw, imu_pitch, imu_roll (°), imu_gz (°/с), imu_temp_c.
 * Сброс курса: xiaoImuZeroYaw() (вызывается из /control?imuzero=1).
 */
#include <Arduino.h>
#include "drive_config.h"

#if defined(XIAO_IMU_ENABLE) && XIAO_IMU_ENABLE

#include <Wire.h>

#ifndef XIAO_IMU_SDA
#define XIAO_IMU_SDA 8   /* та же шина, что ToF (D9) */
#endif
#ifndef XIAO_IMU_SCL
#define XIAO_IMU_SCL 9   /* D10 */
#endif
#ifndef XIAO_IMU_ADDR
#define XIAO_IMU_ADDR 0x68  /* 0x68 (AD0=0) или 0x69 (AD0=1) */
#endif

/* Регистры MPU6050 */
#define MPU_REG_SMPLRT_DIV   0x19
#define MPU_REG_CONFIG       0x1A
#define MPU_REG_GYRO_CONFIG  0x1B
#define MPU_REG_ACCEL_CONFIG 0x1C
#define MPU_REG_PWR_MGMT_1   0x6B
#define MPU_REG_WHO_AM_I     0x75
#define MPU_REG_ACCEL_XOUT_H 0x3B

/* ±250 °/с → 131 LSB/(°/с); ±2 g → 16384 LSB/g (дефолтные диапазоны) */
static constexpr float kImuGyroLsb = 131.0f;
static constexpr float kImuAccLsb = 16384.0f;

static bool gImuOk = false;
static float gImuYaw = 0.0f;    /* °, интеграл gyro-Z, с вычетом смещения */
static float gImuPitch = 0.0f;  /* °, из акселя */
static float gImuRoll = 0.0f;   /* °, из акселя */
static float gImuGz = 0.0f;     /* °/с, текущая скорость рыскания (после bias) */
static float gImuTempC = 0.0f;
static float gImuGzBias = 0.0f; /* смещение нуля gyro-Z, °/с */
static uint32_t gImuLastUs = 0;

static bool xiaoImuWrite(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(XIAO_IMU_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

/* Чтение n байт начиная с reg. Возвращает число прочитанных. */
static uint8_t xiaoImuRead(uint8_t reg, uint8_t *buf, uint8_t n) {
  Wire.beginTransmission(XIAO_IMU_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) {
    return 0;
  }
  const uint8_t got = Wire.requestFrom((int)XIAO_IMU_ADDR, (int)n);
  for (uint8_t i = 0; i < got && i < n; ++i) {
    buf[i] = Wire.read();
  }
  return got;
}

/* Сырой кадр: ax,ay,az,temp,gx,gy,gz (int16, big-endian). */
static bool xiaoImuReadRaw(int16_t *a, int16_t *t, int16_t *g) {
  uint8_t b[14];
  if (xiaoImuRead(MPU_REG_ACCEL_XOUT_H, b, 14) < 14) {
    return false;
  }
  a[0] = (int16_t)((b[0] << 8) | b[1]);
  a[1] = (int16_t)((b[2] << 8) | b[3]);
  a[2] = (int16_t)((b[4] << 8) | b[5]);
  *t = (int16_t)((b[6] << 8) | b[7]);
  g[0] = (int16_t)((b[8] << 8) | b[9]);
  g[1] = (int16_t)((b[10] << 8) | b[11]);
  g[2] = (int16_t)((b[12] << 8) | b[13]);
  return true;
}

/** Калибровка нуля gyro-Z: усреднить N замеров, робот должен стоять. */
static void xiaoImuCalibrateBias(uint16_t samples = 200) {
  int16_t a[3], g[3], t;
  double acc = 0.0;
  uint16_t n = 0;
  for (uint16_t i = 0; i < samples; ++i) {
    if (xiaoImuReadRaw(a, &t, g)) {
      acc += g[2] / kImuGyroLsb;
      n++;
    }
    delay(3);
  }
  gImuGzBias = n ? (float)(acc / n) : 0.0f;
  Serial.print(F("imu: gyroZ bias = "));
  Serial.print(gImuGzBias, 3);
  Serial.println(F(" deg/s"));
}

static inline void xiaoImuZeroYaw() { gImuYaw = 0.0f; }

static inline void xiaoImuInit() {
#if !defined(XIAO_TOF_ENABLE) || !XIAO_TOF_ENABLE
  /* Если ToF выключен — шину поднимаем сами (иначе её уже поднял ToF). */
  Wire.begin(XIAO_IMU_SDA, XIAO_IMU_SCL);
  Wire.setClock(400000L);
#endif
  delay(20);
  uint8_t who = 0;
  xiaoImuRead(MPU_REG_WHO_AM_I, &who, 1);
  /* MPU6050 → 0x68; клоны иногда 0x70/0x72/0x98 — не блокируем по WHO_AM_I жёстко. */
  if (!xiaoImuWrite(MPU_REG_PWR_MGMT_1, 0x01)) {  /* выйти из сна, clock = gyro X */
    gImuOk = false;
    Serial.println(F("imu: MPU6050 не отвечает (адрес 0x68? питание 3V3? SDA/SCL?)"));
    return;
  }
  delay(10);
  xiaoImuWrite(MPU_REG_SMPLRT_DIV, 0x04);   /* 1 кГц/(1+4) = 200 Гц */
  xiaoImuWrite(MPU_REG_CONFIG, 0x03);       /* DLPF ~44 Гц — режет вибрацию моторов */
  xiaoImuWrite(MPU_REG_GYRO_CONFIG, 0x00);  /* ±250 °/с */
  xiaoImuWrite(MPU_REG_ACCEL_CONFIG, 0x00); /* ±2 g */
  delay(20);
  gImuOk = true;
  Serial.print(F("imu: MPU6050 OK (WHO_AM_I=0x"));
  Serial.print(who, HEX);
  Serial.println(F("), калибрую нуль гиро — не двигай ~0.6 с…"));
  xiaoImuCalibrateBias();
  gImuLastUs = micros();
}

static inline void xiaoImuTick() {
  if (!gImuOk) {
    return;
  }
  static uint32_t last = 0;
  const uint32_t now = millis();
  if (now - last < 5) {  /* ~200 Гц предел; реально ограничен loop */
    return;
  }
  last = now;
  int16_t a[3], g[3], t;
  if (!xiaoImuReadRaw(a, &t, g)) {
    return;
  }
  const uint32_t us = micros();
  float dt = (us - gImuLastUs) * 1e-6f;
  gImuLastUs = us;
  if (dt <= 0 || dt > 0.5f) {
    dt = 0.005f;  /* защита от выброса dt */
  }

  gImuTempC = t / 340.0f + 36.53f;  /* формула из даташита MPU6050 */

  const float gz = g[2] / kImuGyroLsb - gImuGzBias;  /* °/с */
  gImuGz = gz;
  /* Зона нечувствительности: гасит дрейф интеграла на стоянке (шум гиро). */
  if (gz > 0.3f || gz < -0.3f) {
    gImuYaw += gz * dt;
  }
  if (gImuYaw >= 360.0f) gImuYaw -= 360.0f;
  if (gImuYaw < 0.0f) gImuYaw += 360.0f;

  /* pitch/roll из гравитации (абсолютные, не дрейфуют). */
  const float ax = a[0] / kImuAccLsb, ay = a[1] / kImuAccLsb, az = a[2] / kImuAccLsb;
  gImuRoll = atan2f(ay, az) * 57.2958f;
  gImuPitch = atan2f(-ax, sqrtf(ay * ay + az * az)) * 57.2958f;
}

static inline bool xiaoImuIsOk() { return gImuOk; }
static inline float xiaoImuYaw() { return gImuYaw; }

static inline void xiaoImuAppendTelemetry(String &j, bool &comma) {
  auto add = [&](const char *k, float v, unsigned int dp) {
    if (comma) j += ',';
    comma = true;
    j += '"'; j += k; j += "\":"; j += String(v, dp);
  };
  if (comma) j += ',';
  comma = true;
  j += F("\"imu_ok\":");
  j += gImuOk ? '1' : '0';
  if (gImuOk) {
    add("imu_yaw", gImuYaw, 1);
    add("imu_pitch", gImuPitch, 1);
    add("imu_roll", gImuRoll, 1);
    add("imu_gz", gImuGz, 2);
    add("imu_temp_c", gImuTempC, 1);
  }
}

#else  /* !XIAO_IMU_ENABLE */

static inline void xiaoImuInit() {}
static inline void xiaoImuTick() {}
static inline bool xiaoImuIsOk() { return false; }
static inline float xiaoImuYaw() { return 0.0f; }
static inline void xiaoImuZeroYaw() {}
static inline void xiaoImuAppendTelemetry(String &, bool &) {}

#endif
