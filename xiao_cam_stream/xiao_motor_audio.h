#pragma once
/** Звук через обмотки TB6612: ЗНАКОПЕРЕМЕННЫЙ ток (вперёд/назад симметрично, среднее ≈ 0)
 *  — мотор «поёт» на частоте тона, но НЕ вращается. Громкость = доля драйва в полупериоде
 *  (gAudioPct). Раньше был однонаправленный PWM (постоянная составляющая → мотор ехал). */

#include <Arduino.h>
#include "drive_config.h"

#if XIAO_DRIVE_ENABLE && XIAO_AUDIO_ENABLE

#include "xiao_drive.h"

struct XiaoNote {
  uint16_t hz;
  uint16_t ms;
};

static const XiaoNote XIAO_MELODY1[] = {
    {523, 170}, {659, 170}, {784, 220}, {659, 170}, {523, 220}, {0, 120}, {784, 220}, {659, 220}};
static const XiaoNote XIAO_MELODY2[] = {
    {440, 220}, {440, 120}, {523, 220}, {587, 220}, {523, 220}, {440, 220}, {392, 240}, {0, 120}};
static const XiaoNote XIAO_MELODY3[] = {
    {659, 160}, {622, 160}, {659, 160}, {784, 260}, {0, 100}, {784, 160}, {880, 220}, {988, 260}};
static const XiaoNote XIAO_MELODY_PRIVET[] = {
    {330, 70}, {460, 70}, {620, 80}, {540, 70}, {0, 30}, {760, 90}, {980, 120}, {860, 90}, {0, 35},
    {700, 120}, {520, 160}, {1120, 70}, {0, 70}};
static const XiaoNote XIAO_MELODY_PRIVET2[] = {
    {1450, 40}, {900, 45}, {520, 70}, {620, 70}, {0, 25}, {700, 80}, {980, 90}, {1220, 80}, {900, 70}, {0, 30},
    {1600, 45}, {780, 75}, {620, 85}, {0, 70}};

static bool gAudioActive = false;
// Пины активного мотора (звук гоним ЗНАКОПЕРЕМЕННО через его обмотку).
static uint8_t gAudInA = DRIVE_L_IN1;
static uint8_t gAudInB = DRIVE_L_IN2;
static uint8_t gAudPwm = DRIVE_L_PWM;
static const XiaoNote *gMelody = nullptr;
static uint8_t gMelodyLen = 0;
static uint8_t gMelodyIndex = 0;
static unsigned long gMelodyNextAt = 0;
static uint8_t gAudioPct = 10;
static uint16_t gWaveHz = 0;
static uint16_t gHalfUs = 0;       // полупериод тона
static uint16_t gOnUs = 0;         // длительность ДРАЙВА в каждом полупериоде (∝ громкость)
static uint8_t gPhase = 0;         // 0 вперёд-драйв, 1 тормоз, 2 назад-драйв, 3 тормоз
static uint32_t gPhaseEndUs = 0;

bool xiaoAudioIsActive() {
  return gAudioActive;
}

static uint8_t xiaoAudioParseChannel(const char *s) {
  if (!s || !*s) {
    return DRIVE_L_PWM;
  }
  if (s[0] == 'B' || s[0] == 'b' || s[0] == '2' || s[0] == 'r' || s[0] == 'R') {
    return DRIVE_R_PWM;
  }
  return DRIVE_L_PWM;
}

// Выбрать активный мотор; второй — выключить.
static void xiaoAudioArmPath(uint8_t pwmPin) {
  if (pwmPin == DRIVE_R_PWM) {
    gAudInA = DRIVE_R_IN1;
    gAudInB = DRIVE_R_IN2;
    gAudPwm = DRIVE_R_PWM;
    digitalWrite(DRIVE_L_IN1, LOW);
    digitalWrite(DRIVE_L_IN2, LOW);
    analogWrite(DRIVE_L_PWM, 0);
  } else {
    gAudInA = DRIVE_L_IN1;
    gAudInB = DRIVE_L_IN2;
    gAudPwm = DRIVE_L_PWM;
    digitalWrite(DRIVE_R_IN1, LOW);
    digitalWrite(DRIVE_R_IN2, LOW);
    analogWrite(DRIVE_R_PWM, 0);
  }
  pinMode(gAudInA, OUTPUT);
  pinMode(gAudInB, OUTPUT);
  pinMode(gAudPwm, OUTPUT);
  digitalWrite(gAudPwm, LOW);
}

// Состояние моста активного мотора: drive=false → тормоз (PWM low, ток не идёт);
// drive=true → драйв в сторону fwd (знакопеременность даёт звук без вращения).
static inline void xiaoAudioBridge(bool fwd, bool drive) {
  if (!drive) {
    digitalWrite(gAudPwm, LOW);
    return;
  }
  digitalWrite(gAudInA, fwd ? HIGH : LOW);
  digitalWrite(gAudInB, fwd ? LOW : HIGH);
  digitalWrite(gAudPwm, HIGH);
}

static void xiaoAudioSetWave(uint16_t hz) {
  if (hz < 80) {                       // тишина: мост в нейтраль
    gWaveHz = 0;
    gHalfUs = 0;
    gOnUs = 0;
    digitalWrite(gAudPwm, LOW);
    digitalWrite(gAudInA, LOW);
    digitalWrite(gAudInB, LOW);
    return;
  }
  if (hz > 5000) {
    hz = 5000;
  }
  gWaveHz = hz;
  uint16_t periodUs = static_cast<uint16_t>(1000000UL / hz);
  if (periodUs < 100) {
    periodUs = 100;
  }
  gHalfUs = periodUs / 2;
  // Драйв занимает gAudioPct% каждого полупериода (симметрично вперёд/назад → среднее 0).
  gOnUs = static_cast<uint16_t>((uint32_t)gHalfUs * gAudioPct / 100);
  if (gOnUs < 8) {
    gOnUs = 8;
  }
  if (gOnUs > gHalfUs) {
    gOnUs = gHalfUs;
  }
  gPhase = 0;
  xiaoAudioBridge(true, true);         // старт: вперёд-драйв
  gPhaseEndUs = micros() + gOnUs;
}

// 4 фазы за период: вперёд-драйв → тормоз → назад-драйв → тормоз. Знакопеременный ток
// (среднее ≈ 0) → мотор «поёт» на gWaveHz, но НЕ вращается. Громкость ∝ gOnUs.
static void xiaoAudioWaveTick() {
  if (!gAudioActive || gWaveHz == 0) {
    return;
  }
  const uint32_t nowUs = micros();
  if (static_cast<int32_t>(nowUs - gPhaseEndUs) < 0) {
    return;
  }
  gPhase = static_cast<uint8_t>((gPhase + 1) & 3);
  const uint16_t brakeUs = static_cast<uint16_t>(gHalfUs - gOnUs);
  switch (gPhase) {
    case 0: xiaoAudioBridge(true, true);   gPhaseEndUs += gOnUs;   break;  // вперёд-драйв
    case 1: xiaoAudioBridge(true, false);  gPhaseEndUs += brakeUs; break;  // тормоз
    case 2: xiaoAudioBridge(false, true);  gPhaseEndUs += gOnUs;   break;  // назад-драйв
    default: xiaoAudioBridge(false, false); gPhaseEndUs += brakeUs; break; // тормоз
  }
}

void xiaoAudioStop() {
  gAudioActive = false;
  gMelody = nullptr;
  gWaveHz = 0;
  digitalWrite(gAudPwm, LOW);
  digitalWrite(gAudInA, LOW);
  digitalWrite(gAudInB, LOW);
  xiaoDriveStop();
}

static void xiaoAudioStartBeep(uint16_t hz, uint16_t ms, uint8_t pwmPin) {
  if (hz < 100) {
    hz = 100;
  }
  if (hz > 4000) {
    hz = 4000;
  }
  if (ms < 20) {
    ms = 20;
  }
  if (ms > 3000) {
    ms = 3000;
  }
  xiaoDriveStop();
  xiaoAudioStop();
  xiaoAudioArmPath(pwmPin);            // выбирает мотор + настраивает пины
  gAudioActive = true;
  xiaoAudioSetWave(hz);
  gMelody = nullptr;
  gMelodyNextAt = millis() + ms + 10;
}

static void xiaoAudioStartMelody(uint8_t id, uint8_t pwmPin) {
  const XiaoNote *mel = nullptr;
  uint8_t len = 0;
  if (id == 1) {
    mel = XIAO_MELODY1;
    len = sizeof(XIAO_MELODY1) / sizeof(XIAO_MELODY1[0]);
  } else if (id == 2) {
    mel = XIAO_MELODY2;
    len = sizeof(XIAO_MELODY2) / sizeof(XIAO_MELODY2[0]);
  } else if (id == 3) {
    mel = XIAO_MELODY3;
    len = sizeof(XIAO_MELODY3) / sizeof(XIAO_MELODY3[0]);
  } else if (id == 9) {
    mel = XIAO_MELODY_PRIVET;
    len = sizeof(XIAO_MELODY_PRIVET) / sizeof(XIAO_MELODY_PRIVET[0]);
  } else if (id == 10) {
    mel = XIAO_MELODY_PRIVET2;
    len = sizeof(XIAO_MELODY_PRIVET2) / sizeof(XIAO_MELODY_PRIVET2[0]);
  }
  if (!mel || len == 0) {
    return;
  }
  xiaoDriveStop();
  xiaoAudioStop();
  xiaoAudioArmPath(pwmPin);            // выбирает мотор + настраивает пины
  gAudioActive = true;
  gMelody = mel;
  gMelodyLen = len;
  gMelodyIndex = 0;
  gMelodyNextAt = 0;
  xiaoAudioSetWave(0);
}

static inline void xiaoAudioTick() {
  if (!gAudioActive) {
    return;
  }
  xiaoAudioWaveTick();
  const unsigned long now = millis();
  if (!gMelody) {
    if (now >= gMelodyNextAt) {
      xiaoAudioStop();
    }
    return;
  }
  if (now < gMelodyNextAt) {
    return;
  }
  if (gMelodyIndex >= gMelodyLen) {
    xiaoAudioStop();
    return;
  }
  const XiaoNote n = gMelody[gMelodyIndex++];
  if (n.hz == 0) {
    xiaoAudioSetWave(0);
  } else {
    xiaoAudioSetWave(n.hz);
  }
  gMelodyNextAt = now + n.ms + 25;
}

static inline void xiaoAudioBeepHttp(uint16_t hz, uint16_t ms, const char *ch) {
  xiaoAudioStartBeep(hz, ms, xiaoAudioParseChannel(ch));
}

static inline void xiaoAudioMelodyHttp(uint8_t id, const char *ch) {
  if (id == 0) {
    xiaoAudioStop();
    return;
  }
  xiaoAudioStartMelody(id, xiaoAudioParseChannel(ch));
}

static inline void xiaoAudioSetGain(uint8_t pct) {
  if (pct < 10) {
    pct = 10;
  }
  if (pct > 100) {
    pct = 100;
  }
  gAudioPct = pct;
  if (gAudioActive && gWaveHz) {
    xiaoAudioSetWave(gWaveHz);
  }
}

#else

static inline bool xiaoAudioIsActive() {
  return false;
}
static inline void xiaoAudioStop() {}
static inline void xiaoAudioTick() {}
static inline void xiaoAudioBeepHttp(uint16_t, uint16_t, const char *) {}
static inline void xiaoAudioMelodyHttp(uint8_t, const char *) {}
static inline void xiaoAudioSetGain(uint8_t) {}

#endif

