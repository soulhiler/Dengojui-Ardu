/**
 * Тест моторов на Arduino UNO + TB6612FNG.
 * Временный скетч, пока ждём новые XIAO ESP32-S3 Sense.
 * Схема подключения: docs/hardware/wiring-arduino-motor-test.pdf
 *
 * Цикл теста (8 шагов с паузами):
 *   1) Forward (вперёд оба мотора, PWM=150)  ─ 1.5 с
 *   2) Stop                                    ─ 1.0 с
 *   3) Backward (назад оба)                    ─ 1.5 с
 *   4) Stop                                    ─ 1.0 с
 *   5) Spin L (L назад, R вперёд)              ─ 1.0 с
 *   6) Stop                                    ─ 1.0 с
 *   7) Spin R (L вперёд, R назад)              ─ 1.0 с
 *   8) Stop                                    ─ 2.0 с
 *   повтор.
 *
 * Serial: 115200 baud, печатает текущий шаг и команды.
 *
 * Безопасность:
 *   - PWM начальный 150/255 (≈60%) — умеренный, чтобы не сорвало с подставки.
 *   - STBY на D9, можно программно отключить драйвер (LOW = выкл).
 *   - При получении 'x' в Serial — аварийная остановка + STBY LOW.
 *   - Поднять плату на подставку (колёса в воздухе) для первого теста!
 *
 * Пинаут (см. также верхний комментарий схемы):
 *   D2 → AIN1   D3 → PWMA (~PWM)   D4 → AIN2     (мотор A = левый)
 *   D5 → PWMB (~PWM)   D7 → BIN1   D8 → BIN2     (мотор B = правый)
 *   D9 → STBY (~PWM, используется как digital)
 *   Arduino 5V → TB6612 VCC; GND общий; TB6612 VM ← BAT_prot+ 7.4 В
 */

// ===== Пинаут =====
constexpr uint8_t PIN_AIN1 = 2;
constexpr uint8_t PIN_PWMA = 3;   // ~ HW PWM
constexpr uint8_t PIN_AIN2 = 4;
constexpr uint8_t PIN_PWMB = 5;   // ~ HW PWM
constexpr uint8_t PIN_BIN1 = 7;
constexpr uint8_t PIN_BIN2 = 8;
constexpr uint8_t PIN_STBY = 9;

// ===== Параметры теста =====
constexpr uint8_t TEST_SPEED = 150;   // 0..255, ≈60%
constexpr uint16_t STEP_MS_MOVE = 1500;
constexpr uint16_t STEP_MS_TURN = 1000;
constexpr uint16_t STEP_MS_STOP = 1000;
constexpr uint16_t STEP_MS_CYCLE_STOP = 2000;

// ===== Утилиты управления =====

/** Левый мотор (A): speed -255..255; STBY должен быть HIGH. */
void motorA(int16_t speed) {
  if (speed > 0) {
    digitalWrite(PIN_AIN1, HIGH);
    digitalWrite(PIN_AIN2, LOW);
  } else if (speed < 0) {
    digitalWrite(PIN_AIN1, LOW);
    digitalWrite(PIN_AIN2, HIGH);
    speed = -speed;
  } else {
    // Стоп накатом (coast): оба IN1=IN2=LOW + PWM=0.
    // Для активного торможения было бы IN1=IN2=HIGH — здесь не нужно.
    digitalWrite(PIN_AIN1, LOW);
    digitalWrite(PIN_AIN2, LOW);
  }
  analogWrite(PIN_PWMA, (uint8_t)constrain(speed, 0, 255));
}

/** Правый мотор (B): то же самое. */
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

void driveBoth(int16_t l, int16_t r) {
  motorA(l);
  motorB(r);
}

void stopAll() {
  driveBoth(0, 0);
}

void enableDriver(bool on) {
  digitalWrite(PIN_STBY, on ? HIGH : LOW);
}

// ===== Аварийная остановка через Serial =====
void checkEmergency() {
  while (Serial.available() > 0) {
    int c = Serial.read();
    if (c == 'x' || c == 'X') {
      stopAll();
      enableDriver(false);
      Serial.println(F("!!! EMERGENCY STOP — STBY=LOW. Reset кнопкой R на Arduino."));
      while (true) {
        delay(1000);   // зависаем навсегда, пока пользователь не перезагрузит
      }
    }
  }
}

/** Пауза с обработкой emergency stop. */
void waitMs(uint16_t ms) {
  const uint32_t until = millis() + ms;
  while ((int32_t)(until - millis()) > 0) {
    checkEmergency();
    delay(10);
  }
}

// ===== setup / loop =====

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println(F("==========================================="));
  Serial.println(F("Arduino UNO + TB6612FNG — motor test sketch"));
  Serial.println(F("Поставь робота колёсами вверх перед стартом!"));
  Serial.println(F("Аварийная остановка: отправь 'x' в Serial."));
  Serial.println(F("==========================================="));

  pinMode(PIN_AIN1, OUTPUT);
  pinMode(PIN_AIN2, OUTPUT);
  pinMode(PIN_BIN1, OUTPUT);
  pinMode(PIN_BIN2, OUTPUT);
  pinMode(PIN_PWMA, OUTPUT);
  pinMode(PIN_PWMB, OUTPUT);
  pinMode(PIN_STBY, OUTPUT);

  stopAll();
  enableDriver(false);

  // 3 секунды задержка перед стартом — успеть отойти / поднять платформу
  for (int i = 3; i > 0; --i) {
    Serial.print(F("Старт через ")); Serial.print(i); Serial.println(F("..."));
    delay(1000);
  }

  enableDriver(true);
  Serial.println(F("STBY=HIGH, драйвер включён. Начинаем цикл."));
}

void loop() {
  // 1) Forward
  Serial.println(F("[1/8] FORWARD"));
  driveBoth(TEST_SPEED, TEST_SPEED);
  waitMs(STEP_MS_MOVE);

  // 2) Stop
  Serial.println(F("[2/8] STOP"));
  stopAll();
  waitMs(STEP_MS_STOP);

  // 3) Backward
  Serial.println(F("[3/8] BACKWARD"));
  driveBoth(-TEST_SPEED, -TEST_SPEED);
  waitMs(STEP_MS_MOVE);

  // 4) Stop
  Serial.println(F("[4/8] STOP"));
  stopAll();
  waitMs(STEP_MS_STOP);

  // 5) Spin Left (L назад, R вперёд → робот вращается влево вокруг центра)
  Serial.println(F("[5/8] SPIN LEFT"));
  driveBoth(-TEST_SPEED, TEST_SPEED);
  waitMs(STEP_MS_TURN);

  // 6) Stop
  Serial.println(F("[6/8] STOP"));
  stopAll();
  waitMs(STEP_MS_STOP);

  // 7) Spin Right (L вперёд, R назад)
  Serial.println(F("[7/8] SPIN RIGHT"));
  driveBoth(TEST_SPEED, -TEST_SPEED);
  waitMs(STEP_MS_TURN);

  // 8) Stop (длиннее, чтобы видеть конец цикла)
  Serial.println(F("[8/8] STOP (end of cycle)"));
  stopAll();
  waitMs(STEP_MS_CYCLE_STOP);
}
