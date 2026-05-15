/**
 * XIAO ESP32-S3 Sense — проверка BLE (LE) и встроенного PDM-микрофона.
 *
 * Где искать на плате (Sense): микрофон — маленький чип у края платы рядом с разъёмом камеры;
 * антенна Wi‑Fi/BLE — светлая «петля» на краю модуля.
 *
 * Arduino IDE: плата XIAO_ESP32S3, включи USB CDC on boot, PSRAM OPI (как для камеры).
 * Ядро esp32 ≥ 3.x (нужна библиотека ESP_I2S).
 *
 * Проверка BLE: приложение nRF Connect / LightBlue — устройство BLE_NAME, сервис TEST_SVC_UUID.
 * Проверка микрофона: Serial Monitor 115200 — строки mic_rms / mic_db (говори в микрофон, RMS растёт).
 */

#include <Arduino.h>

#if !__has_include("ESP_I2S.h")
#error "Нужен Arduino-ESP32 3.x с библиотекой ESP_I2S.h (обнови платформу в Boards Manager)."
#endif

#include "ESP_I2S.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// Seeed Wiki: XIAO ESP32S3 Sense PDM — CLK 42, DATA 41
static constexpr int8_t kPdmClkPin = 42;
static constexpr int8_t kPdmDataPin = 41;

static constexpr uint32_t kSampleRate = 16000;

#define BLE_NAME "XIAO-S3-Sense-Test"
#define TEST_SVC_UUID "6d2f6b50-1234-4a5b-8c9d-0a1b2c3d4e5f"
#define TEST_CHR_UUID "6d2f6b51-1234-4a5b-8c9d-0a1b2c3d4e5f"

static I2SClass gI2s;
static BLEServer *gServer;
static BLECharacteristic *gChr;
static bool sMicOk = false;

static bool initMic() {
  gI2s.setPinsPdmRx(kPdmClkPin, kPdmDataPin);
  if (!gI2s.begin(I2S_MODE_PDM_RX, kSampleRate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println("mic: I2S PDM begin failed");
    return false;
  }
  Serial.println("mic: PDM RX OK (CLK=42, DATA=41), 16 kHz mono");
  sMicOk = true;
  return true;
}

static void initBle() {
  BLEDevice::init(BLE_NAME);
  gServer = BLEDevice::createServer();
  BLEService *svc = gServer->createService(TEST_SVC_UUID);
  gChr = svc->createCharacteristic(
      TEST_CHR_UUID,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  gChr->addDescriptor(new BLE2902());
  gChr->setValue("ok");
  svc->start();

  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(TEST_SVC_UUID);
  adv->setScanResponse(true);
  adv->setMinPreferred(0x06);
  adv->setMinPreferred(0x12);
  BLEDevice::startAdvertising();

  Serial.print("ble: advertising as \"");
  Serial.print(BLE_NAME);
  Serial.println("\" (LE only, nRF Connect)");
}

/** Один блок сэмплов → RMS (0…~32768) и dBFS (типично отрицательные). */
static void sampleMic(float *rmsOut, float *dbOut) {
  static int16_t buf[512];
  const size_t got = gI2s.readBytes(reinterpret_cast<char *>(buf), sizeof(buf));
  const size_t n = got / sizeof(int16_t);
  if (n == 0) {
    *rmsOut = 0.0f;
    *dbOut = -96.0f;
    return;
  }
  double acc = 0.0;
  for (size_t i = 0; i < n; i++) {
    const double s = static_cast<double>(buf[i]);
    acc += s * s;
  }
  const float rms = static_cast<float>(sqrt(acc / static_cast<double>(n)));
  constexpr float eps = 1e-6f;
  const float db = 20.0f * log10f(rms / 32768.0f + eps);
  *rmsOut = rms;
  *dbOut = db;
}

void setup() {
  Serial.begin(115200);
  delay(800);

  Serial.println();
  Serial.println("=== XIAO ESP32-S3 Sense: BLE + PDM mic test ===");

  initBle();

  if (initMic()) {
    Serial.println("Speak / clap near the mic — watch mic_rms and mic_db.");
  }

  Serial.println("loop: BLE + mic …");
}

void loop() {
  static uint32_t last = 0;
  const uint32_t now = millis();
  if (now - last < 400) {
    return;
  }
  last = now;

  float db = -96.0f;
  float rms = 0.0f;
  if (sMicOk) {
    float a = 0.0f, b = 0.0f;
    for (int i = 0; i < 3; i++) {
      float r, d;
      sampleMic(&r, &d);
      a += r;
      b += d;
    }
    rms = a / 3.0f;
    db = b / 3.0f;
  }

  Serial.printf("mic_rms=%.1f  mic_db=%.1f  ble=advertising \"%s\"\n", static_cast<double>(rms), static_cast<double>(db), BLE_NAME);

  if (gChr != nullptr) {
    const uint32_t n = static_cast<uint32_t>(millis() / 1000);
    gChr->setValue(String(n));
    gChr->notify();
  }
}
