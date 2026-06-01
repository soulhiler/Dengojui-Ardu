/**
 * UNO + TB6612 + OLED + sound via motor coil (tone on PWM pin).
 * Serial 115200
 *
 * Drive:
 *   D2 AIN1, D3 PWMA, D4 AIN2 — motor A
 *   D5 PWMB, D7 BIN1, D8 BIN2 — motor B
 *   D9 STBY
 *
 * OLED 4SPI:
 *   D0=SCK=D13, D1=MOSI=D11, CS=D10, DC=D12, RES=D6
 *
 * Commands:
 *   L <n> R <n>
 *   stop
 *   stby 0|1
 *   beep <freqHz> <ms> [A|B]
 *   melody <1|2|3> [A|B]
 *   melody stop
 *   x
 */

#include <U8x8lib.h>
#include <Wire.h>
#include <VL53L0X.h>

constexpr uint8_t PIN_AIN1 = 2;
constexpr uint8_t PIN_PWMA = 3;
constexpr uint8_t PIN_AIN2 = 4;
constexpr uint8_t PIN_PWMB = 5;
constexpr uint8_t PIN_BIN1 = 7;
constexpr uint8_t PIN_BIN2 = 8;
constexpr uint8_t PIN_STBY = 9;

constexpr uint8_t OLED_CS = 10;
constexpr uint8_t OLED_DC = 12;
constexpr uint8_t OLED_CLK = 13;
constexpr uint8_t OLED_MOSI = 11;
constexpr uint8_t OLED_RST_HW = 6;

constexpr uint8_t PIN_VBAT = A0;
constexpr float VDIV_SCALE = 3.0f;
constexpr uint8_t OLED_USE_SH1106 = 1;
constexpr unsigned long OLED_MS = 400;

#if OLED_USE_SH1106
U8X8_SH1106_128X64_NONAME_4W_SW_SPI gOled(OLED_CLK, OLED_MOSI, OLED_CS, OLED_DC, OLED_RST_HW);
#else
U8X8_SSD1306_128X64_NONAME_4W_SW_SPI gOled(OLED_CLK, OLED_MOSI, OLED_CS, OLED_DC, OLED_RST_HW);
#endif

struct Note {
  uint16_t hz;
  uint16_t ms;
};

static const Note MELODY1[] = {
    {523, 170}, {659, 170}, {784, 220}, {659, 170}, {523, 220}, {0, 120}, {784, 220}, {659, 220}};
static const Note MELODY2[] = {
    {440, 220}, {440, 120}, {523, 220}, {587, 220}, {523, 220}, {440, 220}, {392, 240}, {0, 120}};
static const Note MELODY3[] = {
    {659, 160}, {622, 160}, {659, 160}, {784, 260}, {0, 100}, {784, 160}, {880, 220}, {988, 260}};
// Речеподобный шаблон под "при-вет!" для мотора-пищалки.
static const Note MELODY_PRIVET[] = {
    {330, 70}, {460, 70}, {620, 80}, {540, 70}, {0, 30},   // "при"
    {760, 90}, {980, 120}, {860, 90}, {0, 35},             // "ве"
    {700, 120}, {520, 160}, {1120, 70}, {0, 70}};          // "т!"
// Версия 2: больше участков в зоне 1-2 кГц (лучше читаются согласные).
static const Note MELODY_PRIVET2[] = {
    {1450, 40}, {900, 45}, {520, 70}, {620, 70}, {0, 25},  // "при"
    {700, 80}, {980, 90}, {1220, 80}, {900, 70}, {0, 30},  // "ве"
    {1600, 45}, {780, 75}, {620, 85}, {0, 70}};             // "т!"

static int16_t gCmdL = 0;
static int16_t gCmdR = 0;
static bool gDriverOn = true;
static uint8_t gCurrentPct = 100;  // 20..100% лимит мощности (псевдо-ограничение тока)
static uint8_t gAudioPct = 10;     // 10..100% сила звука; для этих моторов 10% обычно самый чистый тон
static float gVmV = 0.0f;
static unsigned long gLastOled = 0;

static bool gAudioActive = false;
static uint8_t gTonePin = PIN_PWMA;
static const Note *gMelody = nullptr;
static uint8_t gMelodyLen = 0;
static uint8_t gMelodyIndex = 0;
static unsigned long gMelodyNextAt = 0;
static char gAudioLabel[12] = "silent";
static uint16_t gWaveHz = 0;
static uint16_t gWavePeriodUs = 0;
static uint16_t gWaveOnUs = 0;
static uint32_t gWaveNextEdgeUs = 0;
static bool gWaveHigh = false;
static bool gTofOk = false;
static bool gTofNeedReinit = false;
static bool gTofHasTarget = false;
static bool gTofAuto = true;
static uint16_t gTofMm = 0;
static uint32_t gTofCount = 0;
static VL53L0X gTof;

enum TofProfile : uint8_t {
  TOF_PROFILE_BALANCED = 0,
  TOF_PROFILE_FAST = 1,
  TOF_PROFILE_ACCURATE = 2,
  TOF_PROFILE_LONG = 3,
};

static TofProfile gTofProfile = TOF_PROFILE_BALANCED;
static uint8_t gTofFilterTap = 3;
static bool gTofFilterReset = false;

static bool tofValidMm(uint16_t mm) {
  return mm >= 20 && mm < 2000;
}

static const char *tofProfileTag(TofProfile p) {
  switch (p) {
    case TOF_PROFILE_FAST:
      return "fast";
    case TOF_PROFILE_ACCURATE:
      return "acc";
    case TOF_PROFILE_LONG:
      return "long";
    default:
      return "bal";
  }
}

static uint16_t median3(uint16_t a, uint16_t b, uint16_t c) {
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

static uint16_t median5(uint16_t *v) {
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

static uint16_t tofTickIntervalMs() {
  switch (gTofProfile) {
    case TOF_PROFILE_FAST:
      return 22;
    case TOF_PROFILE_ACCURATE:
      return 100;
    default:
      return 35;
  }
}

void tofApplyProfile(TofProfile p, bool force = false) {
  if (!gTofOk) return;
  if (!force && p == gTofProfile) return;

  gTof.stopContinuous();
  switch (p) {
    case TOF_PROFILE_FAST:
      gTof.setSignalRateLimit(0.25f);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodPreRange, 12);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodFinalRange, 10);
      gTof.setMeasurementTimingBudget(20000);
      gTof.startContinuous(22);
      gTofFilterTap = 3;
      break;
    case TOF_PROFILE_ACCURATE:
      gTof.setSignalRateLimit(0.25f);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodPreRange, 12);
      gTof.setVcselPulsePeriod(VL53L0X::VcselPeriodFinalRange, 10);
      gTof.setMeasurementTimingBudget(100000);
      gTof.startContinuous(105);
      gTofFilterTap = 5;
      break;
    case TOF_PROFILE_LONG:
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
      p = TOF_PROFILE_BALANCED;
      break;
  }
  gTofProfile = p;
  gTofFilterReset = true;
  Serial.print(F("tof profile: "));
  Serial.println(tofProfileTag(p));
}

void tofAutoEvaluate(unsigned long now) {
  if (!gTofAuto || !gTofOk) return;

  static unsigned long lastEval = 0;
  static unsigned long lastSwitch = 0;
  static uint8_t missStreak = 0;
  static uint8_t hitStreak = 0;
  static uint16_t samp[5] = {0, 0, 0, 0, 0};
  static uint8_t sampN = 0;

  if (now - lastEval < 400) return;
  lastEval = now;

  if (!gTofHasTarget) {
    if (missStreak < 250) missStreak++;
    hitStreak = 0;
    sampN = 0;
  } else {
    if (hitStreak < 250) hitStreak++;
    missStreak = 0;
    if (sampN < 5) {
      samp[sampN++] = gTofMm;
    } else {
      for (uint8_t i = 1; i < 5; ++i) samp[i - 1] = samp[i];
      samp[4] = gTofMm;
    }
  }

  if (now - lastSwitch < 3000) return;

  uint16_t spread = 0;
  if (sampN >= 3) {
    uint16_t mn = samp[0];
    uint16_t mx = samp[0];
    for (uint8_t i = 1; i < sampN; ++i) {
      if (samp[i] < mn) mn = samp[i];
      if (samp[i] > mx) mx = samp[i];
    }
    spread = mx - mn;
  }

  const bool motors =
      (gCmdL > 25 || gCmdL < -25 || gCmdR > 25 || gCmdR < -25) && gDriverOn && !gAudioActive;

  TofProfile want = gTofProfile;

  if (missStreak >= 12 && gTofProfile != TOF_PROFILE_LONG) {
    want = TOF_PROFILE_LONG;
  } else if (gTofProfile == TOF_PROFILE_LONG && hitStreak >= 4) {
    want = TOF_PROFILE_BALANCED;
  } else if (gTofHasTarget && spread > 45 && gTofProfile != TOF_PROFILE_ACCURATE) {
    want = TOF_PROFILE_ACCURATE;
  } else if (gTofHasTarget && spread <= 20 && motors && gTofProfile != TOF_PROFILE_FAST) {
    want = TOF_PROFILE_FAST;
  } else if (gTofHasTarget && spread <= 15 && !motors && hitStreak >= 8 &&
             (gTofProfile == TOF_PROFILE_FAST || gTofProfile == TOF_PROFILE_ACCURATE)) {
    want = TOF_PROFILE_BALANCED;
  }

  if (want != gTofProfile) {
    tofApplyProfile(want);
    lastSwitch = now;
  }
}

void motorA(int16_t speed) {
  if (speed > 0) {
    digitalWrite(PIN_AIN1, HIGH);
    digitalWrite(PIN_AIN2, LOW);
  } else if (speed < 0) {
    digitalWrite(PIN_AIN1, LOW);
    digitalWrite(PIN_AIN2, HIGH);
    speed = -speed;
  } else {
    digitalWrite(PIN_AIN1, LOW);
    digitalWrite(PIN_AIN2, LOW);
  }
  analogWrite(PIN_PWMA, (uint8_t)constrain(speed, 0, 255));
}

void motorB(int16_t speed) {
  if (speed > 0) {
    digitalWrite(PIN_BIN1, HIGH);
    digitalWrite(PIN_BIN2, LOW);
  } else if (speed < 0) {
    digitalWrite(PIN_BIN1, LOW);
    digitalWrite(PIN_BIN2, HIGH);
    speed = -speed;
  } else {
    digitalWrite(PIN_BIN1, LOW);
    digitalWrite(PIN_BIN2, LOW);
  }
  analogWrite(PIN_PWMB, (uint8_t)constrain(speed, 0, 255));
}

void stopAudio() {
  noTone(PIN_PWMA);
  noTone(PIN_PWMB);
  digitalWrite(PIN_PWMA, LOW);
  digitalWrite(PIN_PWMB, LOW);
  gAudioActive = false;
  gMelody = nullptr;
  gMelodyLen = 0;
  gMelodyIndex = 0;
  gMelodyNextAt = 0;
  gWaveHz = 0;
  gWavePeriodUs = 0;
  gWaveOnUs = 0;
  gWaveNextEdgeUs = 0;
  gWaveHigh = false;
  strncpy(gAudioLabel, "silent", sizeof(gAudioLabel) - 1);
  gAudioLabel[sizeof(gAudioLabel) - 1] = '\0';
  // Возвращаем мост в штатное управление моторами.
  applyDrive();
}

void applyDrive() {
  if (!gDriverOn) {
    motorA(0);
    motorB(0);
    return;
  }
  int16_t sl = (int16_t)((int32_t)gCmdL * gCurrentPct / 100);
  int16_t sr = (int16_t)((int32_t)gCmdR * gCurrentPct / 100);
  motorA(sl);
  motorB(sr);
}

void enableDriver(bool on) {
  gDriverOn = on;
  digitalWrite(PIN_STBY, on ? HIGH : LOW);
  if (!on) {
    gCmdL = 0;
    gCmdR = 0;
    motorA(0);
    motorB(0);
    stopAudio();
  }
}

float readVmVolts() {
  uint32_t sum = 0;
  for (uint8_t i = 0; i < 8; i++) {
    sum += (uint16_t)analogRead(PIN_VBAT);
    delayMicroseconds(120);
  }
  return (sum / 8.0f) * (5.0f / 1023.0f) * VDIV_SCALE;
}

static void oledHardwareReset() {
  pinMode(OLED_RST_HW, OUTPUT);
  digitalWrite(OLED_RST_HW, LOW);
  delay(10);
  digitalWrite(OLED_RST_HW, HIGH);
  delay(10);
}

void oledPaint() {
  char line[20];
  gVmV = 0.9f * gVmV + 0.1f * readVmVolts();

  gOled.clearDisplay();
  snprintf(line, sizeof(line), "L:%4d R:%4d", gCmdL, gCmdR);
  gOled.drawString(0, 0, line);
  snprintf(line, sizeof(line), "VM %4.1fV", gVmV);
  gOled.drawString(0, 1, line);
  snprintf(line, sizeof(line), "I:%3u%% %s", gCurrentPct, gDriverOn ? "STBY ON" : "STBY OFF");
  gOled.drawString(0, 2, line);
  snprintf(line, sizeof(line), "snd:%s", gAudioLabel);
  gOled.drawString(0, 3, line);
#if OLED_USE_SH1106
  gOled.drawString(0, 4, "drv SH1106");
#else
  gOled.drawString(0, 4, "drv SSD1306");
#endif
  if (!gTofOk) {
    snprintf(line, sizeof(line), "TOF:offline");
  } else if (gTofHasTarget) {
    snprintf(line, sizeof(line), "TOF:%4u %s", (unsigned)gTofMm, tofProfileTag(gTofProfile));
  } else {
    snprintf(line, sizeof(line), "TOF:--- %s", tofProfileTag(gTofProfile));
  }
  gOled.drawString(0, 5, line);
}

void oledInit() {
  oledHardwareReset();
  gOled.begin();
  gOled.setPowerSave(0);
  gOled.setContrast(255);
  gOled.setFont(u8x8_font_chroma48medium8_r);
  Serial.print(F("oled ok "));
  Serial.println(OLED_USE_SH1106 ? F("SH1106") : F("SSD1306"));
  gVmV = readVmVolts();
  oledPaint();
}

int16_t parseSpeed(const char *s) {
  long v = strtol(s, nullptr, 10);
  if (v < -255) v = -255;
  if (v > 255) v = 255;
  return (int16_t)v;
}

uint8_t parseChannelPin(const char *s) {
  if (!s || !*s) return PIN_PWMA;
  if (s[0] == 'B' || s[0] == 'b' || s[0] == '2') return PIN_PWMB;
  return PIN_PWMA;
}

void armAudioPath(uint8_t pin) {
  // Для звука на PWM нужен заданный путь тока через обмотку:
  // IN1/IN2 фиксируем в одном направлении, а tone() модулирует PWM.
  if (pin == PIN_PWMB) {
    digitalWrite(PIN_BIN1, HIGH);
    digitalWrite(PIN_BIN2, LOW);
    digitalWrite(PIN_AIN1, LOW);
    digitalWrite(PIN_AIN2, LOW);
  } else {
    digitalWrite(PIN_AIN1, HIGH);
    digitalWrite(PIN_AIN2, LOW);
    digitalWrite(PIN_BIN1, LOW);
    digitalWrite(PIN_BIN2, LOW);
  }
}

void setWave(uint16_t hz) {
  if (hz < 80) {
    gWaveHz = 0;
    gWavePeriodUs = 0;
    gWaveOnUs = 0;
    digitalWrite(gTonePin, LOW);
    return;
  }
  if (hz > 5000) hz = 5000;
  gWaveHz = hz;
  gWavePeriodUs = (uint16_t)(1000000UL / hz);
  if (gWavePeriodUs < 100) gWavePeriodUs = 100;
  gWaveOnUs = (uint16_t)((uint32_t)gWavePeriodUs * gAudioPct / 100);
  if (gWaveOnUs < 15) gWaveOnUs = 15;
  if (gWaveOnUs > gWavePeriodUs - 15) gWaveOnUs = gWavePeriodUs - 15;
  gWaveHigh = false;
  digitalWrite(gTonePin, LOW);
  gWaveNextEdgeUs = micros() + 60;
}

void startBeep(uint16_t hz, uint16_t ms, uint8_t pin) {
  if (hz < 100) hz = 100;
  if (hz > 4000) hz = 4000;
  if (ms < 20) ms = 20;
  if (ms > 3000) ms = 3000;

  gCmdL = 0;
  gCmdR = 0;
  applyDrive();
  if (!gDriverOn) enableDriver(true);

  stopAudio();
  armAudioPath(pin);
  gAudioActive = true;
  gTonePin = pin;
  pinMode(gTonePin, OUTPUT);
  setWave(hz);
  gMelodyNextAt = millis() + ms + 10;
  snprintf(gAudioLabel, sizeof(gAudioLabel), "beep %u", hz);
}

void startMelody(uint8_t id, uint8_t pin) {
  const Note *mel = nullptr;
  uint8_t len = 0;
  if (id == 1) {
    mel = MELODY1;
    len = sizeof(MELODY1) / sizeof(MELODY1[0]);
  } else if (id == 2) {
    mel = MELODY2;
    len = sizeof(MELODY2) / sizeof(MELODY2[0]);
  } else if (id == 3) {
    mel = MELODY3;
    len = sizeof(MELODY3) / sizeof(MELODY3[0]);
  } else if (id == 9) {
    mel = MELODY_PRIVET;
    len = sizeof(MELODY_PRIVET) / sizeof(MELODY_PRIVET[0]);
  } else if (id == 10) {
    mel = MELODY_PRIVET2;
    len = sizeof(MELODY_PRIVET2) / sizeof(MELODY_PRIVET2[0]);
  }
  if (!mel || len == 0) return;

  gCmdL = 0;
  gCmdR = 0;
  applyDrive();
  if (!gDriverOn) enableDriver(true);

  stopAudio();
  armAudioPath(pin);
  gAudioActive = true;
  gTonePin = pin;
  pinMode(gTonePin, OUTPUT);
  gMelody = mel;
  gMelodyLen = len;
  gMelodyIndex = 0;
  gMelodyNextAt = 0;
  setWave(0);
  snprintf(gAudioLabel, sizeof(gAudioLabel), "mel %u", id);
}

void waveTick() {
  if (!gAudioActive || gWaveHz == 0) return;
  uint32_t nowUs = micros();
  if ((int32_t)(nowUs - gWaveNextEdgeUs) < 0) return;

  if (gWaveHigh) {
    digitalWrite(gTonePin, LOW);
    gWaveHigh = false;
    gWaveNextEdgeUs += (uint32_t)(gWavePeriodUs - gWaveOnUs);
  } else {
    digitalWrite(gTonePin, HIGH);
    gWaveHigh = true;
    gWaveNextEdgeUs += gWaveOnUs;
  }
}

void tofInit() {
  Wire.begin();
  Wire.setClock(400000L);  // быстрее I2C (стандарт 100 кГц)
  delay(50);
  gTofOk = false;
  for (uint8_t attempt = 0; attempt < 3 && !gTofOk; ++attempt) {
    gTofOk = gTof.init();
    if (!gTofOk) delay(120);
  }
  if (gTofOk) {
    gTof.setTimeout(200);
    gTofProfile = TOF_PROFILE_BALANCED;
    tofApplyProfile(TOF_PROFILE_BALANCED, true);
  }
  Serial.println(gTofOk ? F("tof: VL53L0X OK") : F("tof: VL53L0X FAIL"));
}

void tofTick() {
  static unsigned long last = 0;
  static unsigned long lastRetry = 0;
  static uint8_t failStreak = 0;
  static uint16_t hist[5] = {0, 0, 0, 0, 0};
  static uint8_t histFill = 0;
  const unsigned long now = millis();
  if (now - last < tofTickIntervalMs()) return;
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
  uint16_t mm = gTof.readRangeContinuousMillimeters();
  if (gTof.timeoutOccurred()) {
    gTofHasTarget = false;
    gTofMm = 0;
    if (++failStreak >= 5) {
      gTofNeedReinit = true;
      failStreak = 0;
    }
    tofAutoEvaluate(now);
    return;
  }
  failStreak = 0;
  if (!tofValidMm(mm)) {
    gTofHasTarget = false;
    gTofMm = 0;
    histFill = 0;
    tofAutoEvaluate(now);
    return;
  }
  if (histFill < gTofFilterTap) {
    hist[histFill++] = mm;
  } else {
    for (uint8_t i = 1; i < gTofFilterTap; ++i) hist[i - 1] = hist[i];
    hist[gTofFilterTap - 1] = mm;
  }
  gTofHasTarget = true;
  if (histFill >= gTofFilterTap) {
    if (gTofFilterTap >= 5) {
      uint16_t tmp[5];
      for (uint8_t i = 0; i < 5; ++i) tmp[i] = hist[i];
      gTofMm = median5(tmp);
    } else if (gTofFilterTap >= 3) {
      gTofMm = median3(hist[0], hist[1], hist[2]);
    } else {
      gTofMm = mm;
    }
  } else {
    gTofMm = mm;
  }
  gTofCount++;
  tofAutoEvaluate(now);
}

constexpr uint8_t SCAN_STEPS_DEFAULT = 30;
constexpr uint8_t SCAN_DEG_STEP = 12;
constexpr uint16_t SCAN_TURN_MS = 165;
constexpr int16_t SCAN_TURN_L = -130;
constexpr int16_t SCAN_TURN_R = 130;

bool tofReadSample(uint16_t &mmOut) {
  if (!gTofOk) return false;
  uint16_t mm = gTof.readRangeContinuousMillimeters();
  if (gTof.timeoutOccurred() || !tofValidMm(mm)) return false;
  mmOut = mm;
  return true;
}

uint16_t tofReadAvgMm() {
  uint32_t sum = 0;
  uint8_t n = 0;
  for (uint8_t i = 0; i < 3; ++i) {
    uint16_t mm = 0;
    if (tofReadSample(mm)) {
      sum += mm;
      n++;
    }
    delay(35);
  }
  return n ? (uint16_t)(sum / n) : 0;
}

void scanTurnStep() {
  gCmdL = SCAN_TURN_L;
  gCmdR = SCAN_TURN_R;
  applyDrive();
  delay(SCAN_TURN_MS);
  gCmdL = 0;
  gCmdR = 0;
  applyDrive();
  delay(70);
}

void runScan360(uint8_t steps) {
  if (!gTofOk || steps < 4) {
    Serial.println(F("ERR scan tof"));
    return;
  }
  stopAudio();
  const bool autoPrev = gTofAuto;
  gTofAuto = false;
  tofApplyProfile(TOF_PROFILE_ACCURATE, true);

  gCmdL = 0;
  gCmdR = 0;
  applyDrive();
  delay(200);

  Serial.println(F("SCAN begin"));
  for (uint8_t i = 0; i < steps; ++i) {
    const uint16_t ang = (uint16_t)i * (360 / steps);
    delay(60);
    const uint16_t mm = tofReadAvgMm();
    const bool valid = tofValidMm(mm);
    Serial.print(F("SCAN ang="));
    Serial.print(ang);
    Serial.print(F(" mm="));
    Serial.print(mm);
    Serial.print(F(" valid="));
    Serial.println(valid ? 1 : 0);
    if (i + 1 < steps) scanTurnStep();
  }

  gCmdL = 0;
  gCmdR = 0;
  applyDrive();
  gTofAuto = autoPrev;
  tofApplyProfile(TOF_PROFILE_BALANCED, true);
  Serial.println(F("OK scan done"));
}

void printI2CScan() {
  bool any = false;
  Serial.print(F("OK i2c:"));
  for (uint8_t addr = 1; addr < 127; ++addr) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      any = true;
      Serial.print(F(" 0x"));
      if (addr < 16) Serial.print('0');
      Serial.print(addr, HEX);
    }
    delay(1);
  }
  if (!any) Serial.print(F(" none"));
  Serial.println();
}

uint8_t i2cRead8(uint8_t addr, uint8_t reg, bool &ok) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) {
    ok = false;
    return 0;
  }
  if (Wire.requestFrom((int)addr, 1) != 1) {
    ok = false;
    return 0;
  }
  ok = true;
  return Wire.read();
}

void printTofDiag() {
  bool ok1 = false, ok2 = false, ok3 = false;
  uint8_t idModel = i2cRead8(0x29, 0xC0, ok1);  // VL53L0X: expected 0xEE
  uint8_t idRev = i2cRead8(0x29, 0xC2, ok2);    // revision id
  uint8_t sysRangeStart = i2cRead8(0x29, 0x00, ok3);
  Serial.print(F("OK tofdiag addr=0x29 model=0x"));
  if (idModel < 16) Serial.print('0');
  Serial.print(idModel, HEX);
  Serial.print(F(" rev=0x"));
  if (idRev < 16) Serial.print('0');
  Serial.print(idRev, HEX);
  Serial.print(F(" reg00=0x"));
  if (sysRangeStart < 16) Serial.print('0');
  Serial.print(sysRangeStart, HEX);
  Serial.print(F(" ok="));
  Serial.print((ok1 && ok2 && ok3) ? 1 : 0);
  Serial.println();
}

void audioTick() {
  if (!gAudioActive) return;
  unsigned long now = millis();
  // Важно: поддерживаем генерацию волны на каждом проходе loop(),
  // иначе в режиме мелодии сигнал "замирает" между проверками ноты.
  waveTick();

  if (!gMelody) {
    if (now >= gMelodyNextAt) {
      stopAudio();
    }
    return;
  }

  if (now < gMelodyNextAt) return;
  if (gMelodyIndex >= gMelodyLen) {
    stopAudio();
    return;
  }

  Note n = gMelody[gMelodyIndex++];
  if (n.hz == 0) {
    setWave(0);
  } else {
    setWave(n.hz);
  }
  gMelodyNextAt = now + n.ms + 25;
}

void emergencyStop() {
  gCmdL = 0;
  gCmdR = 0;
  enableDriver(false);
  gOled.clearDisplay();
  gOled.drawString(0, 2, "EMERGENCY STOP");
  gOled.drawString(0, 3, "Press RESET UNO");
  Serial.println(F("EMERGENCY STOP — нажмите Reset на Arduino"));
  while (true) {
    delay(500);
  }
}

void handleLine(char *line) {
  while (*line == ' ' || *line == '\t') line++;
  if (!*line) return;

  if (*line == 'x' || *line == 'X') emergencyStop();

  if (strncmp(line, "stop", 4) == 0) {
    stopAudio();
    gCmdL = 0;
    gCmdR = 0;
    applyDrive();
    Serial.println(F("OK stop"));
    return;
  }

  if (strncmp(line, "stby", 4) == 0) {
    const char *p = line + 4;
    while (*p == ' ') p++;
    enableDriver(*p != '0');
    Serial.print(F("OK stby="));
    Serial.println(gDriverOn ? 1 : 0);
    return;
  }

  if (strncmp(line, "tof?", 4) == 0 || strncmp(line, "sensor?", 7) == 0) {
    Serial.print(F("OK tof="));
    Serial.print(gTofOk ? 1 : 0);
    Serial.print(F(" valid="));
    Serial.print(gTofHasTarget ? 1 : 0);
    Serial.print(F(" mm="));
    Serial.print((unsigned)gTofMm);
    Serial.print(F(" count="));
    Serial.print((unsigned long)gTofCount);
    Serial.print(F(" profile="));
    Serial.print(tofProfileTag(gTofProfile));
    Serial.print(F(" auto="));
    Serial.println(gTofAuto ? 1 : 0);
    return;
  }

  if (strncmp(line, "tofauto", 7) == 0) {
    const char *p = line + 7;
    while (*p == ' ') p++;
    gTofAuto = (*p != '0');
    Serial.print(F("OK tofauto="));
    Serial.println(gTofAuto ? 1 : 0);
    return;
  }

  if (strncmp(line, "tofprofile", 10) == 0) {
    const char *p = line + 10;
    while (*p == ' ') p++;
    if (strncmp(p, "auto", 4) == 0) {
      gTofAuto = true;
      Serial.println(F("OK tofprofile=auto"));
      return;
    }
    gTofAuto = false;
    if (strncmp(p, "fast", 4) == 0) {
      tofApplyProfile(TOF_PROFILE_FAST, true);
    } else if (strncmp(p, "acc", 3) == 0) {
      tofApplyProfile(TOF_PROFILE_ACCURATE, true);
    } else if (strncmp(p, "long", 4) == 0) {
      tofApplyProfile(TOF_PROFILE_LONG, true);
    } else {
      tofApplyProfile(TOF_PROFILE_BALANCED, true);
    }
    Serial.print(F("OK tofprofile="));
    Serial.println(tofProfileTag(gTofProfile));
    return;
  }

  if (strncmp(line, "scan360", 7) == 0) {
    uint8_t steps = SCAN_STEPS_DEFAULT;
    const char *p = line + 7;
    while (*p == ' ') p++;
    if (*p) {
      long n = strtol(p, nullptr, 10);
      if (n >= 8 && n <= 72) steps = (uint8_t)n;
    }
    runScan360(steps);
    return;
  }
  if (strncmp(line, "scan", 4) == 0) {
    uint8_t steps = SCAN_STEPS_DEFAULT;
    const char *p = line + 4;
    while (*p == ' ') p++;
    if (*p) {
      long n = strtol(p, nullptr, 10);
      if (n >= 8 && n <= 72) steps = (uint8_t)n;
    }
    runScan360(steps);
    return;
  }

  if (strncmp(line, "tofreset", 8) == 0) {
    gTofCount = 0;
    Serial.println(F("OK tofreset"));
    return;
  }

  if (strncmp(line, "tofinit", 7) == 0) {
    tofInit();
    Serial.print(F("OK tofinit="));
    Serial.println(gTofOk ? 1 : 0);
    return;
  }

  if (strncmp(line, "i2c?", 4) == 0 || strncmp(line, "scan", 4) == 0) {
    printI2CScan();
    return;
  }

  if (strncmp(line, "tofdiag", 7) == 0) {
    printTofDiag();
    return;
  }

  if (strncmp(line, "ilim", 4) == 0 || strncmp(line, "current", 7) == 0) {
    const char *p = line + (line[1] == 'l' ? 4 : 7);
    while (*p == ' ') p++;
    long pct = strtol(p, nullptr, 10);
    if (pct < 20) pct = 20;
    if (pct > 100) pct = 100;
    gCurrentPct = (uint8_t)pct;
    applyDrive();
    Serial.print(F("OK ilim="));
    Serial.println(gCurrentPct);
    return;
  }

  if (strncmp(line, "again", 5) == 0 || strncmp(line, "sgain", 5) == 0) {
    const char *p = line + 5;
    while (*p == ' ') p++;
    long pct = strtol(p, nullptr, 10);
    if (pct < 10) pct = 10;
    if (pct > 100) pct = 100;
    gAudioPct = (uint8_t)pct;
    if (gAudioActive && gWaveHz > 0) {
      setWave(gWaveHz);
    }
    Serial.print(F("OK again="));
    Serial.println(gAudioPct);
    return;
  }

  if (strncmp(line, "beep", 4) == 0) {
    char *tok = strtok(line, " \t");
    tok = strtok(nullptr, " \t");
    if (!tok) {
      Serial.println(F("ERR beep freq ms [A|B]"));
      return;
    }
    uint16_t hz = (uint16_t)strtoul(tok, nullptr, 10);
    tok = strtok(nullptr, " \t");
    if (!tok) {
      Serial.println(F("ERR beep freq ms [A|B]"));
      return;
    }
    uint16_t ms = (uint16_t)strtoul(tok, nullptr, 10);
    tok = strtok(nullptr, " \t");
    uint8_t pin = parseChannelPin(tok);
    startBeep(hz, ms, pin);
    Serial.print(F("OK beep "));
    Serial.print(hz);
    Serial.print(F("Hz "));
    Serial.print(ms);
    Serial.print(F("ms ch="));
    Serial.println(pin == PIN_PWMB ? F("B") : F("A"));
    return;
  }

  if (strncmp(line, "melody", 6) == 0) {
    char *tok = strtok(line, " \t");
    tok = strtok(nullptr, " \t");
    if (!tok) {
      Serial.println(F("ERR melody <1|2|3|stop> [A|B]"));
      return;
    }
    if (strncmp(tok, "stop", 4) == 0) {
      stopAudio();
      Serial.println(F("OK melody stop"));
      return;
    }
    uint8_t id = (uint8_t)strtoul(tok, nullptr, 10);
    tok = strtok(nullptr, " \t");
    uint8_t pin = parseChannelPin(tok);
    if (!(id == 1 || id == 2 || id == 3 || id == 9 || id == 10)) {
      Serial.println(F("ERR melody id 1|2|3|9|10"));
      return;
    }
    startMelody(id, pin);
    Serial.print(F("OK melody "));
    Serial.println(id);
    return;
  }

  if (strncmp(line, "say", 3) == 0) {
    char *tok = strtok(line, " \t");
    tok = strtok(nullptr, " \t");
    if (!tok) {
      Serial.println(F("ERR say <privet> [A|B]"));
      return;
    }
    char *ch = strtok(nullptr, " \t");
    uint8_t pin = parseChannelPin(ch);
    if (strncmp(tok, "privet", 6) == 0) {
      startMelody(9, pin);
      Serial.println(F("OK say privet"));
      return;
    }
    if (strncmp(tok, "privet2", 7) == 0 || strncmp(tok, "privet-v2", 9) == 0) {
      startMelody(10, pin);
      Serial.println(F("OK say privet2"));
      return;
    }
    Serial.println(F("ERR say only privet"));
    return;
  }

  int16_t l = gCmdL;
  int16_t r = gCmdR;
  bool gotL = false;
  bool gotR = false;
  char *tok = strtok(line, " \t");
  while (tok) {
    if (tok[0] == 'L' || tok[0] == 'l') {
      if (tok[1]) {
        l = parseSpeed(tok + 1);
      } else {
        char *n = strtok(nullptr, " \t");
        if (n) l = parseSpeed(n);
      }
      gotL = true;
    } else if (tok[0] == 'R' || tok[0] == 'r') {
      if (tok[1]) {
        r = parseSpeed(tok + 1);
      } else {
        char *n = strtok(nullptr, " \t");
        if (n) r = parseSpeed(n);
      }
      gotR = true;
    }
    tok = strtok(nullptr, " \t");
  }

  if (gotL || gotR) {
    stopAudio();
    if (gotL) gCmdL = l;
    if (gotR) gCmdR = r;
    applyDrive();
    Serial.print(F("OK L="));
    Serial.print(gCmdL);
    Serial.print(F(" R="));
    Serial.println(gCmdR);
    return;
  }

  Serial.println(F("? stop | L <n> R <n> | scan360 [steps] | tof? | tofauto | tofprofile | x"));
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_AIN1, OUTPUT);
  pinMode(PIN_AIN2, OUTPUT);
  pinMode(PIN_BIN1, OUTPUT);
  pinMode(PIN_BIN2, OUTPUT);
  pinMode(PIN_PWMA, OUTPUT);
  pinMode(PIN_PWMB, OUTPUT);
  pinMode(PIN_STBY, OUTPUT);
  enableDriver(true);

  analogRead(PIN_VBAT);
  tofInit();
  oledInit();

  gLastOled = millis();
  Serial.println(F("uno_motor_serial ready. Example: L 120 R 120"));
}

void loop() {
  static char buf[96];
  static uint8_t n = 0;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (n > 0) {
        buf[n] = '\0';
        handleLine(buf);
        n = 0;
      }
    } else if (n < sizeof(buf) - 1) {
      buf[n++] = c;
    }
  }

  if (gTofNeedReinit && !Serial.available()) {
    gTofNeedReinit = false;
    tofInit();
  } else if (!Serial.available()) {
    tofTick();
  }
  audioTick();

  unsigned long now = millis();
  if (now - gLastOled >= OLED_MS) {
    gLastOled = now;
    oledPaint();
  }
}
