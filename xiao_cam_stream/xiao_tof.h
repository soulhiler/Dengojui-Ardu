#pragma once
/**
 * VL53L7CX — мультизонный ToF (матрица 4×4/8×8, FoV 60°, до ~3.5 м) + scan360.
 * Библиотека: «STM32duino VL53L7CX» (arduino-cli lib install "STM32duino VL53L7CX").
 * Включение: XIAO_TOF_ENABLE в drive_config.h. LPn модуля держать HIGH:
 * перемычка на 3.3 V или XIAO_TOF_LPN_PIN. INT не используется (поллинг).
 *
 * Совместимость со старым VL53L0X-контрактом: ключи телеметрии (tof_mm и др.)
 * и /scan360 не изменились; tof_mm — минимум по центральной полосе зон
 * (то, во что робот может въехать). Полная сетка расстояний: GET /tof.
 *
 * init_sensor() грузит в сенсор ~84 КБ прошивки по I2C — старт ToF занимает
 * ~2–3 с на 400 кГц; это нормально, не таймаут.
 */

#include <Arduino.h>
#include "drive_config.h"

#if XIAO_TOF_ENABLE

#include <Wire.h>
#include <vl53l7cx_class.h>
#include "xiao_drive.h"

#ifndef XIAO_TOF_LPN_PIN
#define XIAO_TOF_LPN_PIN -1
#endif
#ifndef XIAO_TOF_I2C_RST_PIN
#define XIAO_TOF_I2C_RST_PIN -1
#endif

enum XiaoTofProfile : uint8_t {
  XIAO_TOF_BALANCED = 0, /* 4×4, 30 Гц */
  XIAO_TOF_FAST = 1,     /* 4×4, 60 Гц */
  XIAO_TOF_ACCURATE = 2, /* 8×8, 15 Гц — полная сетка (scan360, /tof) */
  XIAO_TOF_LONG = 3,     /* 4×4, 10 Гц, интеграция 80 мс — максимум дальности */
};

struct XiaoScanPoint {
  uint16_t ang;
  uint16_t mm;
  uint8_t valid;
};
#if XIAO_AUDIO_ENABLE
#include "xiao_motor_audio.h"
#endif
static bool gTofOk = false;
static bool gTofNeedReinit = false;
static bool gTofHasTarget = false;
static bool gTofAuto = true;
static uint16_t gTofMm = 0;
static uint32_t gTofCount = 0;
static XiaoTofProfile gTofProfile = XIAO_TOF_BALANCED;
static uint8_t gTofFilterTap = 3;
static bool gTofFilterReset = false;
static VL53L7CX gTof(&Wire, XIAO_TOF_LPN_PIN, XIAO_TOF_I2C_RST_PIN);
/* ResultsData крупный (метаданные на все 64 зоны) — статически, не на стеке loopTask. */
static VL53L7CX_ResultsData gTofResults;
static uint8_t gTofZones = VL53L7CX_RESOLUTION_4X4; /* 16 или 64 */
static int16_t gTofGrid[VL53L7CX_RESOLUTION_8X8];   /* мм по зонам, -1 = нет цели */

static bool xiaoTofValidMm(uint16_t mm) {
  return mm >= 20 && mm <= 3500;
}

static const char *xiaoTofProfileTag(XiaoTofProfile p) {
  switch (p) {
    case XIAO_TOF_FAST:
      return "fast";
    case XIAO_TOF_ACCURATE:
      return "acc";
    case XIAO_TOF_LONG:
      return "long";
    default:
      return "bal";
  }
}

static uint16_t xiaoMedian3(uint16_t a, uint16_t b, uint16_t c) {
  if (a > b) {
    uint16_t t = a;
    a = b;
    b = t;
  }
  if (b > c) {
    uint16_t t = b;
    b = c;
    c = t;
  }
  if (a > b) {
    uint16_t t = a;
    a = b;
    b = t;
  }
  return b;
}

static uint16_t xiaoMedian5(uint16_t *v) {
  for (uint8_t i = 0; i < 4; ++i) {
    for (uint8_t j = i + 1; j < 5; ++j) {
      if (v[i] > v[j]) {
        uint16_t t = v[i];
        v[i] = v[j];
        v[j] = t;
      }
    }
  }
  return v[2];
}

static uint16_t xiaoTofTickIntervalMs() {
  switch (gTofProfile) {
    case XIAO_TOF_FAST:
      return 14;
    case XIAO_TOF_ACCURATE:
      return 60;
    case XIAO_TOF_LONG:
      return 95;
    default:
      return 30;
  }
}

static void xiaoTofApplyProfile(XiaoTofProfile p, bool force = false) {
  if (!gTofOk) {
    return;
  }
  if (!force && p == gTofProfile) {
    return;
  }

  uint8_t res = VL53L7CX_RESOLUTION_4X4;
  uint8_t hz = 30; /* 4×4: до 60 Гц, 8×8: до 15 Гц */
  uint8_t mode = VL53L7CX_RANGING_MODE_CONTINUOUS;
  uint32_t integrationMs = 0; /* только для AUTONOMOUS */
  switch (p) {
    case XIAO_TOF_FAST:
      hz = 60;
      gTofFilterTap = 3;
      break;
    case XIAO_TOF_ACCURATE:
      res = VL53L7CX_RESOLUTION_8X8;
      hz = 15;
      gTofFilterTap = 3;
      break;
    case XIAO_TOF_LONG:
      hz = 10;
      mode = VL53L7CX_RANGING_MODE_AUTONOMOUS;
      integrationMs = 80;
      gTofFilterTap = 3;
      break;
    default:
      gTofFilterTap = 3;
      p = XIAO_TOF_BALANCED;
      break;
  }

  gTof.vl53l7cx_stop_ranging();
  /* Порядок по UM3038: resolution → ranging mode (+integration) → frequency → start. */
  bool ok = gTof.vl53l7cx_set_resolution(res) == 0;
  ok = ok && gTof.vl53l7cx_set_ranging_mode(mode) == 0;
  if (ok && integrationMs) {
    ok = gTof.vl53l7cx_set_integration_time_ms(integrationMs) == 0;
  }
  ok = ok && gTof.vl53l7cx_set_ranging_frequency_hz(hz) == 0;
  ok = ok && gTof.vl53l7cx_start_ranging() == 0;
  if (!ok) {
    gTofNeedReinit = true;
    Serial.println(F("tof: profile apply FAIL"));
    return;
  }
  gTofZones = res;
  gTofProfile = p;
  gTofFilterReset = true;
  Serial.print(F("tof profile: "));
  Serial.println(xiaoTofProfileTag(p));
}

/**
 * Разбор свежего кадра gTofResults: обновляет gTofGrid и возвращает минимум
 * по центральной полосе зон (средние строки матрицы — высота, куда едет
 * корпус; крайние строки часто видят пол/потолок). 0 = валидных целей нет.
 */
static uint16_t xiaoTofFrameCenterMin() {
  const uint8_t zones = gTofZones;
  const uint8_t side = (zones == VL53L7CX_RESOLUTION_8X8) ? 8 : 4;
  const uint8_t rowFrom = side / 4;        /* 4×4: 1, 8×8: 2 */
  const uint8_t rowTo = side - side / 4;   /* 4×4: 3, 8×8: 6 (не включая) */
  uint16_t best = 0;
  for (uint8_t z = 0; z < zones; ++z) {
    const uint16_t idx = static_cast<uint16_t>(z) * VL53L7CX_NB_TARGET_PER_ZONE;
    const uint8_t st = gTofResults.target_status[idx];
    const int16_t d = gTofResults.distance_mm[idx];
    const bool valid = gTofResults.nb_target_detected[z] > 0 && (st == 5 || st == 9) && d > 0 &&
                       xiaoTofValidMm(static_cast<uint16_t>(d));
    gTofGrid[z] = valid ? d : -1;
    const uint8_t row = z / side;
    if (valid && row >= rowFrom && row < rowTo) {
      if (!best || static_cast<uint16_t>(d) < best) {
        best = static_cast<uint16_t>(d);
      }
    }
  }
  return best;
}

/** Авто-профиль: цель потеряна → LONG; едем с целью → FAST; стоим стабильно → BALANCED. */
static void xiaoTofAutoEvaluate(unsigned long now) {
  if (!gTofAuto || !gTofOk) {
    return;
  }

  static unsigned long lastEval = 0;
  static unsigned long lastSwitch = 0;
  static uint8_t missStreak = 0;
  static uint8_t hitStreak = 0;

  if (now - lastEval < 400) {
    return;
  }
  lastEval = now;

  XiaoDriveState ds{};
  xiaoDriveGetState(&ds);

  if (!gTofHasTarget) {
    if (missStreak < 250) {
      missStreak++;
    }
    hitStreak = 0;
  } else {
    if (hitStreak < 250) {
      hitStreak++;
    }
    missStreak = 0;
  }

  if (now - lastSwitch < 3000) {
    return;
  }

  const bool motors =
      ds.enabled && (ds.cmd_l > 25 || ds.cmd_l < -25 || ds.cmd_r > 25 || ds.cmd_r < -25);

  XiaoTofProfile want = gTofProfile;

  if (missStreak >= 12 && gTofProfile != XIAO_TOF_LONG) {
    want = XIAO_TOF_LONG;
  } else if (gTofProfile == XIAO_TOF_LONG && hitStreak >= 4) {
    want = XIAO_TOF_BALANCED;
  } else if (gTofHasTarget && motors && gTofProfile != XIAO_TOF_FAST) {
    want = XIAO_TOF_FAST;
  } else if (gTofHasTarget && !motors && hitStreak >= 8 && gTofProfile == XIAO_TOF_FAST) {
    want = XIAO_TOF_BALANCED;
  }

  if (want != gTofProfile) {
    xiaoTofApplyProfile(want);
    lastSwitch = now;
  }
}

static inline void xiaoTofInit() {
#if (XIAO_TOF_SDA == 19 || XIAO_TOF_SDA == 20 || XIAO_TOF_SCL == 19 || XIAO_TOF_SCL == 20)
#error "GPIO19/20 = USB D-/D+ on ESP32-S3 — COM пропадёт после Wire.begin"
#endif
  Wire.begin(XIAO_TOF_SDA, XIAO_TOF_SCL);
  Wire.setClock(400000L);
  delay(50);
  gTofOk = false;
  gTof.begin();
  /* init_sensor льёт прошивку в сенсор (~2–3 с); 2 попытки, не 3 — иначе долгий boot. */
  for (uint8_t attempt = 0; attempt < 2 && !gTofOk; ++attempt) {
    gTofOk = (gTof.init_sensor() == 0);
    if (!gTofOk) {
      delay(150);
    }
  }
  if (gTofOk) {
    for (uint8_t z = 0; z < VL53L7CX_RESOLUTION_8X8; ++z) {
      gTofGrid[z] = -1;
    }
    gTofProfile = XIAO_TOF_BALANCED;
    xiaoTofApplyProfile(XIAO_TOF_BALANCED, true);
  }
  Serial.println(gTofOk ? F("tof: VL53L7CX OK") : F("tof: VL53L7CX FAIL (LPn на 3.3V? SDA/SCL?)"));
}

static inline void xiaoTofTick() {
  static unsigned long last = 0;
  static unsigned long lastRetry = 0;
  static unsigned long lastFrame = 0;
  static uint16_t hist[5] = {0, 0, 0, 0, 0};
  static uint8_t histFill = 0;
  const unsigned long now = millis();
  if (now - last < xiaoTofTickIntervalMs()) {
    return;
  }
  last = now;
  if (gTofFilterReset) {
    histFill = 0;
    gTofFilterReset = false;
  }
  if (!gTofOk) {
    if (now - lastRetry >= 2000) {
      lastRetry = now;
      gTofNeedReinit = true;
    }
  }
  if (gTofNeedReinit) {
    gTofNeedReinit = false;
    xiaoTofInit();
    lastFrame = now;
    return;
  }
  if (!gTofOk) {
    return;
  }

  uint8_t ready = 0;
  if (gTof.vl53l7cx_check_data_ready(&ready) != 0) {
    /* Ошибка I2C — если кадров нет дольше 1.5 с, переинициализация. */
    if (now - lastFrame > 1500) {
      gTofNeedReinit = true;
      gTofHasTarget = false;
      gTofMm = 0;
    }
    return;
  }
  if (!ready) {
    if (lastFrame && now - lastFrame > 1500) {
      gTofNeedReinit = true;
      gTofHasTarget = false;
      gTofMm = 0;
    }
    return;
  }
  if (gTof.vl53l7cx_get_ranging_data(&gTofResults) != 0) {
    if (now - lastFrame > 1500) {
      gTofNeedReinit = true;
    }
    return;
  }
  lastFrame = now;

  const uint16_t mm = xiaoTofFrameCenterMin();
  if (!mm) {
    gTofHasTarget = false;
    gTofMm = 0;
    histFill = 0;
    xiaoTofAutoEvaluate(now);
    return;
  }
  if (histFill < gTofFilterTap) {
    hist[histFill++] = mm;
  } else {
    for (uint8_t i = 1; i < gTofFilterTap; ++i) {
      hist[i - 1] = hist[i];
    }
    hist[gTofFilterTap - 1] = mm;
  }
  gTofHasTarget = true;
  if (histFill >= gTofFilterTap) {
    if (gTofFilterTap >= 5) {
      uint16_t tmp[5];
      for (uint8_t i = 0; i < 5; ++i) {
        tmp[i] = hist[i];
      }
      gTofMm = xiaoMedian5(tmp);
    } else if (gTofFilterTap >= 3) {
      gTofMm = xiaoMedian3(hist[0], hist[1], hist[2]);
    } else {
      gTofMm = mm;
    }
  } else {
    gTofMm = mm;
  }
  gTofCount++;
  xiaoTofAutoEvaluate(now);
}

/** Блокирующее чтение одного свежего кадра (для scan360). */
static bool xiaoTofReadSample(uint16_t &mmOut) {
  if (!gTofOk) {
    return false;
  }
  const unsigned long t0 = millis();
  while (millis() - t0 < 400) {
    uint8_t ready = 0;
    if (gTof.vl53l7cx_check_data_ready(&ready) != 0) {
      return false;
    }
    if (ready) {
      if (gTof.vl53l7cx_get_ranging_data(&gTofResults) != 0) {
        return false;
      }
      const uint16_t mm = xiaoTofFrameCenterMin();
      if (!mm) {
        return false;
      }
      mmOut = mm;
      return true;
    }
    delay(5);
  }
  return false;
}

static uint16_t xiaoTofReadAvgMm() {
  uint32_t sum = 0;
  uint8_t n = 0;
  for (uint8_t i = 0; i < 3; ++i) {
    uint16_t mm = 0;
    if (xiaoTofReadSample(mm)) {
      sum += mm;
      n++;
    }
  }
  return n ? static_cast<uint16_t>(sum / n) : 0;
}

static constexpr uint8_t XIAO_SCAN_STEPS_DEFAULT = 30;
static constexpr uint16_t XIAO_SCAN_TURN_MS = 165;
static constexpr int16_t XIAO_SCAN_TURN_L = -130;
static constexpr int16_t XIAO_SCAN_TURN_R = 130;

static void xiaoScanTurnStep() {
  xiaoDriveSetLr(XIAO_SCAN_TURN_L, XIAO_SCAN_TURN_R);
  delay(XIAO_SCAN_TURN_MS);
  xiaoDriveStop();
  delay(70);
}

/** Возвращает число точек; заполняет out[0..max-1]. */
static uint8_t xiaoTofRunScan360(uint8_t steps, XiaoScanPoint *out, uint8_t maxOut) {
  if (!gTofOk || steps < 4 || !out || maxOut == 0) {
    return 0;
  }
  if (steps > maxOut) {
    steps = maxOut;
  }

#if XIAO_AUDIO_ENABLE
  extern void xiaoAudioStop();
  xiaoAudioStop();
#endif

  const bool autoPrev = gTofAuto;
  gTofAuto = false;
  xiaoTofApplyProfile(XIAO_TOF_ACCURATE, true);
  xiaoDriveStop();
  delay(200);

  for (uint8_t i = 0; i < steps; ++i) {
    const uint16_t ang = static_cast<uint16_t>(i) * (360 / steps);
    delay(60);
    const uint16_t mm = xiaoTofReadAvgMm();
    out[i].ang = ang;
    out[i].mm = mm;
    out[i].valid = xiaoTofValidMm(mm) ? 1 : 0;
    if (i + 1 < steps) {
      xiaoScanTurnStep();
    }
  }

  xiaoDriveStop();
  gTofAuto = autoPrev;
  xiaoTofApplyProfile(XIAO_TOF_BALANCED, true);
  return steps;
}

static inline bool xiaoTofIsOk() {
  return gTofOk;
}
static inline bool xiaoTofHasTarget() {
  return gTofHasTarget;
}
static inline uint16_t xiaoTofMm() {
  return gTofMm;
}
static inline uint32_t xiaoTofCount() {
  return gTofCount;
}
static inline bool xiaoTofAutoOn() {
  return gTofAuto;
}
static inline const char *xiaoTofProfileStr() {
  return xiaoTofProfileTag(gTofProfile);
}

static inline void xiaoTofAppendTelemetry(String &j, bool &comma) {
  auto appendU = [&](const char *k, uint32_t v) {
    if (comma) {
      j += ',';
    }
    comma = true;
    j += '"';
    j += k;
    j += "\":";
    j += v;
  };
  auto appendS = [&](const char *k, const char *v) {
    if (comma) {
      j += ',';
    }
    comma = true;
    j += '"';
    j += k;
    j += "\":\"";
    j += v;
    j += '"';
  };
  appendU("tof_ok", gTofOk ? 1u : 0u);
  appendU("tof_valid", gTofHasTarget ? 1u : 0u);
  appendU("tof_mm", gTofMm);
  appendU("tof_count", gTofCount);
  appendS("tof_profile", xiaoTofProfileTag(gTofProfile));
  appendU("tof_auto", gTofAuto ? 1u : 0u);
  appendU("tof_res", (gTofZones == VL53L7CX_RESOLUTION_8X8) ? 8u : 4u);
}

/** GET /tof: вся сетка зон (мм, -1 = нет цели), порядок — строками сверху вниз. */
static inline void xiaoTofGridJson(String &j) {
  const uint8_t side = (gTofZones == VL53L7CX_RESOLUTION_8X8) ? 8 : 4;
  j = F("{\"ok\":");
  j += gTofOk ? '1' : '0';
  j += F(",\"res\":");
  j += side;
  j += F(",\"mm\":");
  j += gTofMm;
  j += F(",\"profile\":\"");
  j += xiaoTofProfileTag(gTofProfile);
  j += F("\",\"grid\":[");
  const uint8_t zones = side * side;
  for (uint8_t z = 0; z < zones; ++z) {
    if (z) {
      j += ',';
    }
    j += gTofGrid[z];
  }
  j += F("]}");
}

#else /* !XIAO_TOF_ENABLE */

struct XiaoScanPoint {
  uint16_t ang;
  uint16_t mm;
  uint8_t valid;
};

static inline void xiaoTofInit() {}
static inline void xiaoTofTick() {}
static inline bool xiaoTofIsOk() {
  return false;
}
static inline bool xiaoTofHasTarget() {
  return false;
}
static inline uint16_t xiaoTofMm() {
  return 0;
}
static inline uint32_t xiaoTofCount() {
  return 0;
}
static inline bool xiaoTofAutoOn() {
  return false;
}
static inline const char *xiaoTofProfileStr() {
  return "off";
}
static inline uint8_t xiaoTofRunScan360(uint8_t, XiaoScanPoint *, uint8_t) {
  return 0;
}
static inline void xiaoTofAppendTelemetry(String &, bool &) {}
static inline void xiaoTofGridJson(String &j) {
  j = F("{\"ok\":0,\"error\":\"tof off\"}");
}

#endif
