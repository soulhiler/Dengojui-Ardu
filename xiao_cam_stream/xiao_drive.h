#pragma once
/**
 * Привод и сенсоры шасси для XIAO (PWM, энкодеры, опционально УЗ / бампер).
 * HTTP: GET /drive?l=-255..255&r=...  stop=1  ·  /control?drive=0|1
 */

#include <Arduino.h>
#include "drive_config.h"

#if XIAO_DRIVE_ENABLE

#ifndef DRIVE_PWM_CH_L
#define DRIVE_PWM_CH_L 2
#endif
#ifndef DRIVE_PWM_CH_R
#define DRIVE_PWM_CH_R 3
#endif

struct XiaoDriveState {
  bool hw_ok = false;
  bool enabled = true;
  bool watchdog_stop = false;
  int16_t cmd_l = 0;
  int16_t cmd_r = 0;
  int32_t enc_l = 0;
  int32_t enc_r = 0;
  uint16_t us_cm = 0;
  uint8_t bumper = 0;
  uint32_t last_cmd_ms = 0;
};

static XiaoDriveState gDrive;
static volatile int32_t gEncL = 0;
static volatile int32_t gEncR = 0;

static void IRAM_ATTR encIsrL() {
  if (digitalRead(DRIVE_ENC_L_A) == digitalRead(DRIVE_ENC_L_B)) {
    gEncL++;
  } else {
    gEncL--;
  }
}

static void IRAM_ATTR encIsrR() {
  if (digitalRead(DRIVE_ENC_R_A) == digitalRead(DRIVE_ENC_R_B)) {
    gEncR++;
  } else {
    gEncR--;
  }
}

static inline int16_t driveClamp16(int v, int lo, int hi) {
  if (v < lo) {
    return static_cast<int16_t>(lo);
  }
  if (v > hi) {
    return static_cast<int16_t>(hi);
  }
  return static_cast<int16_t>(v);
}

#if XIAO_DRIVE_DRIVER_TB6612
static void driveMotorTb6612(uint8_t pwmPin, uint8_t in1, uint8_t in2, int16_t cmd) {
  cmd = driveClamp16(cmd, -255, 255);
  const uint8_t spd = static_cast<uint8_t>(abs(cmd));
  if (cmd > 0) {
    digitalWrite(in1, HIGH);
    digitalWrite(in2, LOW);
  } else if (cmd < 0) {
    digitalWrite(in1, LOW);
    digitalWrite(in2, HIGH);
  } else {
    digitalWrite(in1, LOW);
    digitalWrite(in2, LOW);
  }
  analogWrite(pwmPin, spd);
}
#else
static void driveMotorSignMag(uint8_t pwmPin, uint8_t dirPin, int16_t cmd) {
  cmd = driveClamp16(cmd, -255, 255);
  digitalWrite(dirPin, cmd >= 0 ? HIGH : LOW);
  analogWrite(pwmPin, static_cast<uint8_t>(abs(cmd)));
}
#endif

static void driveApplyMotors() {
  if (!gDrive.enabled || gDrive.watchdog_stop) {
#if XIAO_DRIVE_DRIVER_TB6612
    driveMotorTb6612(DRIVE_L_PWM, DRIVE_L_IN1, DRIVE_L_IN2, 0);
    driveMotorTb6612(DRIVE_R_PWM, DRIVE_R_IN1, DRIVE_R_IN2, 0);
#else
    driveMotorSignMag(DRIVE_L_PWM, DRIVE_L_DIR, 0);
    driveMotorSignMag(DRIVE_R_PWM, DRIVE_R_DIR, 0);
#endif
    return;
  }
#if XIAO_DRIVE_DRIVER_TB6612
  driveMotorTb6612(DRIVE_L_PWM, DRIVE_L_IN1, DRIVE_L_IN2, gDrive.cmd_l);
  driveMotorTb6612(DRIVE_R_PWM, DRIVE_R_IN1, DRIVE_R_IN2, gDrive.cmd_r);
#else
  driveMotorSignMag(DRIVE_L_PWM, DRIVE_L_DIR, gDrive.cmd_l);
  driveMotorSignMag(DRIVE_R_PWM, DRIVE_R_DIR, gDrive.cmd_r);
#endif
}

static void drivePwmInit() {
  /* esp32-arduino 3.x: разрешение/частота задаются на каждый PWM-пин (камера уже заняла LEDC0). */
  analogWriteResolution(DRIVE_L_PWM, DRIVE_PWM_BITS);
  analogWriteResolution(DRIVE_R_PWM, DRIVE_PWM_BITS);
  analogWriteFrequency(DRIVE_L_PWM, DRIVE_PWM_FREQ_HZ);
  analogWriteFrequency(DRIVE_R_PWM, DRIVE_PWM_FREQ_HZ);
}

static void driveEncInit() {
#if DRIVE_ENC_L_A > 0 && DRIVE_ENC_L_B > 0
  pinMode(DRIVE_ENC_L_A, INPUT_PULLUP);
  pinMode(DRIVE_ENC_L_B, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(DRIVE_ENC_L_A), encIsrL, CHANGE);
#endif
#if DRIVE_ENC_R_A > 0 && DRIVE_ENC_R_B > 0
  pinMode(DRIVE_ENC_R_A, INPUT_PULLUP);
  pinMode(DRIVE_ENC_R_B, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(DRIVE_ENC_R_A), encIsrR, CHANGE);
#endif
}

static uint16_t driveReadUltrasonicCm() {
#if DRIVE_US_ENABLE && DRIVE_US_TRIG > 0 && DRIVE_US_ECHO > 0
  digitalWrite(DRIVE_US_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(DRIVE_US_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(DRIVE_US_TRIG, LOW);
  const uint32_t us = pulseIn(DRIVE_US_ECHO, HIGH, 25000);
  if (us == 0) {
    return 0;
  }
  return static_cast<uint16_t>(us / 58);
#else
  return 0;
#endif
}

static inline void xiaoDriveInit() {
  gDrive = XiaoDriveState{};
  gEncL = 0;
  gEncR = 0;

  drivePwmInit();

#if XIAO_DRIVE_DRIVER_TB6612
  pinMode(DRIVE_L_IN1, OUTPUT);
  pinMode(DRIVE_L_IN2, OUTPUT);
  pinMode(DRIVE_R_IN1, OUTPUT);
  pinMode(DRIVE_R_IN2, OUTPUT);
  if (DRIVE_STBY_PIN >= 0) {
    pinMode(DRIVE_STBY_PIN, OUTPUT);
    digitalWrite(DRIVE_STBY_PIN, HIGH);
  }
#else
  pinMode(DRIVE_L_DIR, OUTPUT);
  pinMode(DRIVE_R_DIR, OUTPUT);
#endif

  driveEncInit();

#if DRIVE_US_ENABLE && DRIVE_US_TRIG > 0
  pinMode(DRIVE_US_TRIG, OUTPUT);
  pinMode(DRIVE_US_ECHO, INPUT);
#endif
#if DRIVE_BUMPER_PIN_1 > 0
  pinMode(DRIVE_BUMPER_PIN_1, INPUT_PULLUP);
#endif
#if DRIVE_BUMPER_PIN_2 > 0
  pinMode(DRIVE_BUMPER_PIN_2, INPUT_PULLUP);
#endif

  driveApplyMotors();
  gDrive.hw_ok = true;
  gDrive.last_cmd_ms = millis();
  Serial.println(F("drive: init OK (motors+encoders, see drive_config.h)"));
}

static inline void xiaoDriveSetEnabled(bool on) {
  gDrive.enabled = on;
  if (!on) {
    gDrive.cmd_l = 0;
    gDrive.cmd_r = 0;
    gDrive.watchdog_stop = false;
    driveApplyMotors();
  }
}

static inline void xiaoDriveSetLr(int16_t l, int16_t r) {
  gDrive.cmd_l = driveClamp16(l, -255, 255);
  gDrive.cmd_r = driveClamp16(r, -255, 255);
  gDrive.watchdog_stop = false;
  gDrive.last_cmd_ms = millis();
  driveApplyMotors();
}

static inline void xiaoDriveStop() {
  gDrive.cmd_l = 0;
  gDrive.cmd_r = 0;
  gDrive.watchdog_stop = false;
  gDrive.last_cmd_ms = millis();
  driveApplyMotors();
}

static inline void xiaoDriveTick() {
  if (!gDrive.hw_ok) {
    return;
  }

  gDrive.enc_l = gEncL;
  gDrive.enc_r = gEncR;

#if DRIVE_BUMPER_PIN_1 > 0 || DRIVE_BUMPER_PIN_2 > 0
  uint8_t b = 0;
#if DRIVE_BUMPER_PIN_1 > 0
  if (digitalRead(DRIVE_BUMPER_PIN_1) == LOW) {
    b |= 1u;
  }
#endif
#if DRIVE_BUMPER_PIN_2 > 0
  if (digitalRead(DRIVE_BUMPER_PIN_2) == LOW) {
    b |= 2u;
  }
#endif
  gDrive.bumper = b;
  if (b != 0) {
    gDrive.cmd_l = 0;
    gDrive.cmd_r = 0;
    driveApplyMotors();
    return;
  }
#endif

  const uint32_t now = millis();
  if (gDrive.enabled && (now - gDrive.last_cmd_ms) > static_cast<uint32_t>(DRIVE_WATCHDOG_MS)) {
    if (gDrive.cmd_l != 0 || gDrive.cmd_r != 0) {
      gDrive.watchdog_stop = true;
      gDrive.cmd_l = 0;
      gDrive.cmd_r = 0;
      driveApplyMotors();
    }
  }

  static uint32_t s_usLast = 0;
  if (DRIVE_US_ENABLE && (now - s_usLast) > 120u) {
    s_usLast = now;
    gDrive.us_cm = driveReadUltrasonicCm();
  }
}

static inline void xiaoDriveGetState(XiaoDriveState *out) {
  if (out) {
    *out = gDrive;
  }
}

static inline void xiaoDriveAppendTelemetry(String &j, bool &comma) {
  if (!gDrive.hw_ok) {
    return;
  }
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
  auto appendI = [&](const char *k, int32_t v) {
    if (comma) {
      j += ',';
    }
    comma = true;
    j += '"';
    j += k;
    j += "\":";
    j += v;
  };
  appendU("drive_hw", 1u);
  appendU("drive_enabled", gDrive.enabled ? 1u : 0u);
  appendU("drive_watchdog", gDrive.watchdog_stop ? 1u : 0u);
  appendI("drive_cmd_l", gDrive.cmd_l);
  appendI("drive_cmd_r", gDrive.cmd_r);
  appendI("enc_l", gDrive.enc_l);
  appendI("enc_r", gDrive.enc_r);
  appendU("us_cm", gDrive.us_cm);
  appendU("bumper", gDrive.bumper);
}

#else /* !XIAO_DRIVE_ENABLE */

struct XiaoDriveState {};

static inline void xiaoDriveInit() {}
static inline void xiaoDriveTick() {}
static inline void xiaoDriveSetEnabled(bool) {}
static inline void xiaoDriveSetLr(int16_t, int16_t) {}
static inline void xiaoDriveStop() {}
static inline void xiaoDriveGetState(XiaoDriveState *) {}
static inline void xiaoDriveAppendTelemetry(String &, bool &) {}

#endif
