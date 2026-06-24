#pragma once
/** Звук через обмотки TB6612.
 *
 * ВАЖНО (ESP32): тон делаем АППАРАТНЫМ LEDC — `analogWriteFrequency(pwmPin, нота)` +
 * малая `analogWrite`-скважность. Старый порт с Arduino бил `digitalWrite` по пину в
 * loop() — на XIAO цикл медленный (камера/WiFi/ToF), бит-бэнг звуковых частот не успевал
 * → чистого тона нет, только постоянная составляющая → мотор КРУТИЛСЯ, а не пел.
 *
 * PWM-пин НИКОГДА не трогаем pinMode/digitalWrite (иначе отрывается LEDC и портится привод).
 * После звука возвращаем рабочую частоту DRIVE_PWM_FREQ_HZ — джойстик/езда не страдают.
 */

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
// Пины активного мотора.
static uint8_t gAudInA = DRIVE_L_IN1;
static uint8_t gAudInB = DRIVE_L_IN2;
static uint8_t gAudPwm = DRIVE_L_PWM;
static const XiaoNote *gMelody = nullptr;
static uint8_t gMelodyLen = 0;
static uint8_t gMelodyIndex = 0;
static unsigned long gMelodyNextAt = 0;
static uint8_t gAudioPct = 10;          // «громкость» 10..100 % → скважность тона

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

// Скважность тона из gain. Держим в «тоновом» окне (≈6..40 % из 255): у 0 и у 100 %
// переменной составляющей нет (нет звука), а большая скважность = большой средний ток
// (вращение). Малая скважность → слышимый тон, но мотор ниже порога страгивания.
static inline uint8_t xiaoAudioDuty() {
  const uint16_t lo = 16, hi = 100;     // ≈ 6 % .. 39 % из 255
  return static_cast<uint8_t>(lo + (uint16_t)(gAudioPct - 10) * (hi - lo) / 90);
}

// Выбрать активный мотор; второй заглушить. Направление фиксированное (как на стенде).
static void xiaoAudioArmPath(uint8_t pwmPin) {
  if (pwmPin == DRIVE_R_PWM) {
    gAudInA = DRIVE_R_IN1;
    gAudInB = DRIVE_R_IN2;
    gAudPwm = DRIVE_R_PWM;
    analogWrite(DRIVE_L_PWM, 0);
    digitalWrite(DRIVE_L_IN1, LOW);
    digitalWrite(DRIVE_L_IN2, LOW);
  } else {
    gAudInA = DRIVE_L_IN1;
    gAudInB = DRIVE_L_IN2;
    gAudPwm = DRIVE_L_PWM;
    analogWrite(DRIVE_R_PWM, 0);
    digitalWrite(DRIVE_R_IN1, LOW);
    digitalWrite(DRIVE_R_IN2, LOW);
  }
  digitalWrite(gAudInA, HIGH);
  digitalWrite(gAudInB, LOW);
}

// Сыграть ноту аппаратно: частота LEDC-карьера = частота ноты. Чисто, без зависимости
// от скорости loop(). hz<80 → пауза (скважность 0, рабочая частота возвращена).
static void xiaoAudioPlayNote(uint16_t hz) {
  if (hz < 80) {
    analogWrite(gAudPwm, 0);
    analogWriteFrequency(gAudPwm, DRIVE_PWM_FREQ_HZ);
    return;
  }
  if (hz > 5000) {
    hz = 5000;
  }
  analogWriteFrequency(gAudPwm, hz);
  analogWrite(gAudPwm, xiaoAudioDuty());
}

void xiaoAudioStop() {
  gAudioActive = false;
  gMelody = nullptr;
  // ВЕРНУТЬ PWM-пин в рабочий режим привода (частота 20 кГц, скважность 0). Иначе на
  // джойстике остаётся неверная частота карьера. LEDC при этом не отрывали.
  analogWrite(gAudPwm, 0);
  analogWriteFrequency(gAudPwm, DRIVE_PWM_FREQ_HZ);
  digitalWrite(gAudInA, LOW);
  digitalWrite(gAudInB, LOW);
  xiaoDriveStop();
}

static void xiaoAudioStartBeep(uint16_t hz, uint16_t ms, uint8_t pwmPin) {
  if (hz < 100) hz = 100;
  if (hz > 4000) hz = 4000;
  if (ms < 20) ms = 20;
  if (ms > 3000) ms = 3000;
  xiaoAudioStop();
  xiaoAudioArmPath(pwmPin);
  gAudioActive = true;
  gMelody = nullptr;
  xiaoAudioPlayNote(hz);
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
  xiaoAudioStop();
  xiaoAudioArmPath(pwmPin);
  gAudioActive = true;
  gMelody = mel;
  gMelodyLen = len;
  gMelodyIndex = 0;
  gMelodyNextAt = 0;        // первая нота — сразу в tick
}

// Без программного бит-бэнга: LEDC уже держит тон, тут только РАСПИСАНИЕ нот.
static inline void xiaoAudioTick() {
  if (!gAudioActive) {
    return;
  }
  const unsigned long now = millis();
  if (!gMelody) {                          // одиночный beep
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
  xiaoAudioPlayNote(n.hz);                 // hz==0 → пауза
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
  if (pct < 10) pct = 10;
  if (pct > 100) pct = 100;
  gAudioPct = pct;
  if (gAudioActive) {
    analogWrite(gAudPwm, xiaoAudioDuty());   // применить на лету (если играет нота)
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
