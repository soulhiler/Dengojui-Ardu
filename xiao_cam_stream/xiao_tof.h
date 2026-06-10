#pragma once
/**
 * VL53L0X + scan360 (порт с arduino_motor_test на XIAO ESP32-S3).
 * Требует библиотеку Pololu VL53L0X и XIAO_TOF_ENABLE в drive_config.h.
 */

#include <Arduino.h>
#include "drive_config.h"

#if XIAO_TOF_ENABLE

#include <Wire.h>
#include <VL53L0X.h>
#include "xiao_drive.h"

enum XiaoTofProfile : uint8_t {
  XIAO_TOF_BALANCED = 0,
  XIAO_TOF_FAST = 1,
  XIAO_TOF_ACCURATE = 2,
  XIAO_TOF_LONG = 3,
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
static VL53L0X gTof;

static bool xiaoTofValidMm(uint16_t mm) {
  return mm >= 20 && mm < 2000;
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
      return 22;
    case XIAO_TOF_ACCURATE:
      return 100;
    default:
      return 35;
  }
}

static void xiaoTofApplyProfile(XiaoTofProfile p, bool force = false) {
  if (!gTofOk) {
    return;
  }
  if (!force && p == gTofProfile) {
    return;
  }

  gTof.stopContinuous();
  switch (p) {
    case XIAO_TOF_FAST:
      gTof.setSignalRateLimit(0.25f);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodPreRange, 12);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodFinalRange, 10);
      gTof.setMeasurementTimingBudget(20000);
      gTof.startContinuous(22);
      gTofFilterTap = 3;
      break;
    case XIAO_TOF_ACCURATE:
      gTof.setSignalRateLimit(0.25f);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodPreRange, 12);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodFinalRange, 10);
      gTof.setMeasurementTimingBudget(100000);
      gTof.startContinuous(105);
      gTofFilterTap = 5;
      break;
    case XIAO_TOF_LONG:
      gTof.setSignalRateLimit(0.1f);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodPreRange, 18);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodFinalRange, 14);
      gTof.setMeasurementTimingBudget(33000);
      gTof.startContinuous(35);
      gTofFilterTap = 3;
      break;
    default:
      gTof.setSignalRateLimit(0.25f);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodPreRange, 12);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodFinalRange, 10);
      gTof.setMeasurementTimingBudget(33000);
      gTof.startContinuous(35);
      gTofFilterTap = 3;
      p = XIAO_TOF_BALANCED;
      break;
  }
  gTofProfile = p;
  gTofFilterReset = true;
  Serial.print(F("tof profile: "));
  Serial.println(xiaoTofProfileTag(p));
}

static void xiaoTofAutoEvaluate(unsigned long now) {
  if (!gTofAuto || !gTofOk) {
    return;
  }

  static unsigned long lastEval = 0;
  static unsigned long lastSwitch = 0;
  static uint8_t missStreak = 0;
  static uint8_t hitStreak = 0;
  static uint16_t samp[5] = {0, 0, 0, 0, 0};
  static uint8_t sampN = 0;

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
    sampN = 0;
  } else {
    if (hitStreak < 250) {
      hitStreak++;
    }
    missStreak = 0;
    if (sampN < 5) {
      samp[sampN++] = gTofMm;
    } else {
      for (uint8_t i = 1; i < 5; ++i) {
        samp[i - 1] = samp[i];
      }
      samp[4] = gTofMm;
    }
  }

  if (now - lastSwitch < 3000) {
    return;
  }

  uint16_t spread = 0;
  if (sampN >= 3) {
    uint16_t mn = samp[0];
    uint16_t mx = samp[0];
    for (uint8_t i = 1; i < sampN; ++i) {
      if (samp[i] < mn) {
        mn = samp[i];
      }
      if (samp[i] > mx) {
        mx = samp[i];
      }
    }
    spread = mx - mn;
  }

  const bool motors =
      ds.enabled && (ds.cmd_l > 25 || ds.cmd_l < -25 || ds.cmd_r > 25 || ds.cmd_r < -25);

  XiaoTofProfile want = gTofProfile;

  if (missStreak >= 12 && gTofProfile != XIAO_TOF_LONG) {
    want = XIAO_TOF_LONG;
  } else if (gTofProfile == XIAO_TOF_LONG && hitStreak >= 4) {
    want = XIAO_TOF_BALANCED;
  } else if (gTofHasTarget && spread > 45 && gTofProfile != XIAO_TOF_ACCURATE) {
    want = XIAO_TOF_ACCURATE;
  } else if (gTofHasTarget && spread <= 20 && motors && gTofProfile != XIAO_TOF_FAST) {
    want = XIAO_TOF_FAST;
  } else if (gTofHasTarget && spread <= 15 && !motors && hitStreak >= 8 &&
             (gTofProfile == XIAO_TOF_FAST || gTofProfile == XIAO_TOF_ACCURATE)) {
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
  for (uint8_t attempt = 0; attempt < 3 && !gTofOk; ++attempt) {
    gTofOk = gTof.init();
    if (!gTofOk) {
      delay(120);
    }
  }
  if (gTofOk) {
    gTof.setTimeout(200);
    gTofProfile = XIAO_TOF_BALANCED;
    xiaoTofApplyProfile(XIAO_TOF_BALANCED, true);
  }
  Serial.println(gTofOk ? F("tof: VL53L0X OK") : F("tof: VL53L0X FAIL"));
}

static inline void xiaoTofTick() {
  static unsigned long last = 0;
  static unsigned long lastRetry = 0;
  static uint8_t failStreak = 0;
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
    return;
  }
  if (gTofNeedReinit) {
    gTofNeedReinit = false;
    xiaoTofInit();
    return;
  }

  const uint16_t mm = gTof.readRangeContinuousMillimeters();
  if (gTof.timeoutOccurred()) {
    gTofHasTarget = false;
    gTofMm = 0;
    if (++failStreak >= 5) {
      gTofNeedReinit = true;
      failStreak = 0;
    }
    xiaoTofAutoEvaluate(now);
    return;
  }
  failStreak = 0;
  if (!xiaoTofValidMm(mm)) {
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

static bool xiaoTofReadSample(uint16_t &mmOut) {
  if (!gTofOk) {
    return false;
  }
  const uint16_t mm = gTof.readRangeContinuousMillimeters();
  if (gTof.timeoutOccurred() || !xiaoTofValidMm(mm)) {
    return false;
  }
  mmOut = mm;
  return true;
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
    delay(35);
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

#endif

