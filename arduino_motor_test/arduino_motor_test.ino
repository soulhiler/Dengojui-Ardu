/**
 * Arduino UNO + TB6612FNG — управление с ПК по USB Serial (115200).
 *
 * Пины (как в arduino_motor_test.ino):
 *   D2 AIN1, D3 PWMA, D4 AIN2  — мотор A (левый)
 *   D5 PWMB, D7 BIN1, D8 BIN2  — мотор B (правый)
 *   D9 STBY (HIGH = драйвер включён)
 *
 * Команды (строка + перевод строки):
 *   L <speed> R <speed>   например: L 150 R 150  или  L -80 R 80
 *   stop                  стоп моторов, STBY остаётся HIGH
 *   stby 0 | stby 1       выключить / включить драйвер
 *   x                     аварийный стоп (STBY LOW), нужен Reset
 *
 * VM — питание моторов отдельно (6–12 V). VCC драйвера — 5V с Uno.
 */

constexpr uint8_t PIN_AIN1 = 2;
constexpr uint8_t PIN_PWMA = 3;
constexpr uint8_t PIN_AIN2 = 4;
constexpr uint8_t PIN_PWMB = 5;
constexpr uint8_t PIN_BIN1 = 7;
constexpr uint8_t PIN_BIN2 = 8;
constexpr uint8_t PIN_STBY = 9;

static int16_t gCmdL = 0;
static int16_t gCmdR = 0;
static bool gDriverOn = true;

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

void applyDrive() {
  if (!gDriverOn) {
    motorA(0);
    motorB(0);
    return;
  }
  motorA(gCmdL);
  motorB(gCmdR);
}

void enableDriver(bool on) {
  gDriverOn = on;
  digitalWrite(PIN_STBY, on ? HIGH : LOW);
  if (!on) {
    gCmdL = 0;
    gCmdR = 0;
    motorA(0);
    motorB(0);
  }
}

void emergencyStop() {
  gCmdL = 0;
  gCmdR = 0;
  enableDriver(false);
  Serial.println(F("EMERGENCY STOP — нажмите Reset на Arduino"));
  while (true) {
    delay(500);
  }
}

static int16_t parseSpeed(const char *s) {
  long v = strtol(s, nullptr, 10);
  if (v < -255) {
    v = -255;
  }
  if (v > 255) {
    v = 255;
  }
  return (int16_t)v;
}

void handleLine(char *line) {
  while (*line == ' ' || *line == '\t') {
    line++;
  }
  if (line[0] == '\0') {
    return;
  }
  if (line[0] == 'x' || line[0] == 'X') {
    emergencyStop();
  }
  if (strncmp(line, "stop", 4) == 0) {
    gCmdL = 0;
    gCmdR = 0;
    applyDrive();
    Serial.println(F("OK stop"));
    return;
  }
  if (strncmp(line, "stby", 4) == 0) {
    const char *p = line + 4;
    while (*p == ' ') {
      p++;
    }
    enableDriver(*p != '0');
    Serial.print(F("OK stby="));
    Serial.println(gDriverOn ? 1 : 0);
    return;
  }

  int16_t l = 0;
  int16_t r = 0;
  bool gotL = false;
  bool gotR = false;
  char *tok = strtok(line, " \t");
  while (tok) {
    if (tok[0] == 'L' || tok[0] == 'l') {
      if (tok[1] != '\0') {
        l = parseSpeed(tok + 1);
      } else {
        char *n = strtok(nullptr, " \t");
        if (n) {
          l = parseSpeed(n);
        }
      }
      gotL = true;
    } else if (tok[0] == 'R' || tok[0] == 'r') {
      if (tok[1] != '\0') {
        r = parseSpeed(tok + 1);
      } else {
        char *n = strtok(nullptr, " \t");
        if (n) {
          r = parseSpeed(n);
        }
      }
      gotR = true;
    }
    tok = strtok(nullptr, " \t");
  }
  if (gotL || gotR) {
    if (gotL) {
      gCmdL = l;
    }
    if (gotR) {
      gCmdR = r;
    }
    applyDrive();
    Serial.print(F("OK L="));
    Serial.print(gCmdL);
    Serial.print(F(" R="));
    Serial.println(gCmdR);
    return;
  }
  Serial.println(F("? stop | L <n> R <n> | stby 0|1 | x"));
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
  Serial.println(F("uno_motor_serial ready. Example: L 120 R 120"));
}

void loop() {
  static char buf[64];
  static uint8_t n = 0;
  while (Serial.available()) {
    const char c = (char)Serial.read();
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
}
