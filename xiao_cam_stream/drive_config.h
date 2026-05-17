#pragma once
/**
 * TB6612FNG (BOM #2). На корпусе часто: «TB717A3» + «6612FNG» (или TB67… — та же микросхема).
 * Камера Sense: GPIO 10–18, 38–48; мик 41–42; LED 21 — не использовать.
 *
 * TB6612: STBY → 3.3 V (или DRIVE_STBY_PIN). VM — питание моторов, VCC — 3.3 V, общая GND.
 */

#define XIAO_DRIVE_ENABLE 1
#define XIAO_DRIVE_DRIVER_TB6612 1

/** -1 = STBY закорочен на 3.3 V на модуле */
#define DRIVE_STBY_PIN -1

/* PWMA/AIN1/AIN2 — левый мотор; PWMB/BIN1/BIN2 — правый */
#define DRIVE_L_PWM 6   /* D5  → PWMA */
#define DRIVE_L_IN1 1   /* D0  → AIN1 */
#define DRIVE_L_IN2 2   /* D1  → AIN2 */
#define DRIVE_R_PWM 5   /* D4  → PWMB */
#define DRIVE_R_IN1 3   /* D2  → BIN1 */
#define DRIVE_R_IN2 4   /* D3  → BIN2 */

#define DRIVE_L_DIR 1
#define DRIVE_R_DIR 3

#define DRIVE_ENC_L_A 43  /* D6 */
#define DRIVE_ENC_L_B 44  /* D7 */
#define DRIVE_ENC_R_A 7   /* D8 */
#define DRIVE_ENC_R_B 8   /* D9 */
/** Антидребезг энкодера (мкс); 0 = выкл */
#define DRIVE_ENC_DEBOUNCE_US 150

#define DRIVE_US_TRIG 0
#define DRIVE_US_ECHO 0
#define DRIVE_US_ENABLE 0
/** Рефлекс «стоп» при препятствии ближе X см (0 = только телеметрия) */
#define DRIVE_US_STOP_CM 15

#define DRIVE_BUMPER_PIN_1 0
#define DRIVE_BUMPER_PIN_2 0

#define DRIVE_PWM_FREQ_HZ 20000
#define DRIVE_PWM_BITS 8
#define DRIVE_WATCHDOG_MS 450
