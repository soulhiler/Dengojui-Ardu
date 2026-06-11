/**
 * XIAO ESP32-S3 Sense: камера + MJPEG / веб-снимок через домашний Wi-Fi (STA).
 *
 * Wi-Fi: два SSID по умолчанию (кириллица/латиница) или XIAO_WIFI_SSID_1/2 в secrets.h; пароль — kWifiPass в secrets.h (см. secrets.h.example).
 * После загрузки: Serial — IP; также mDNS http://xiao-cam.local/
 *
 * Встроенный браузер Cursor часто НЕ показывает видео, если страница на 127.0.0.1,
 * а поток с 192.168.x.x — запусти локальный прокси: py -3 tools/xiao_cam_proxy.py <IP>
 * и открой http://127.0.0.1:8898/ — видео /stream и звук /mic_s16 (PCM с платы, TCP :81).
 *
 * Индикация жёлтым LED (GPIO21): быстро — камера; медленнее — Wi‑Fi;
 * ровный «пульс» — HTTP работает; ультрабыстро — ошибка камеры; быстро, но реже — ошибка Wi‑Fi.
 * Красный у зарядки не программируется — смотри только жёлтый у USB.
 *
 * Телеметрия: каждые ~1.5 с в Serial и GET /telemetry — MCU, память, flash/OTA, Wi‑Fi, BLE (LE), PDM‑микрофон Sense, AP (BSS), камера, RTOS.
 * Управление: GET /control?cam=0|1&mic=0|1&ble=0|1&wifi=0|1&drive=0|1 (wifi — эко‑сон радио).
 * Привод: GET /drive?l=-255..255&r=...  или /drive?stop=1  (пины — drive_config.h).
 * ToF VL53L7CX (мультизонный): tof_mm в /telemetry — минимум по центральной полосе; сетка зон — GET /tof.
 * Сбор на ПК (по умолчанию): Wi‑Fi GET /telemetry → USB Serial → BLE (компактный JSON в GATT). Скрипт: tools/xiao_serial_telemetry.py
 * Панель в одном окне: прокси http://127.0.0.1:8898/telemetry
 *
 * Bluetooth LE: в Arduino IDE включи BLE для платы (иначе ble_hw=0 в телеметрии).
 *
 * OTA по Wi‑Fi: в secrets.h задай #define XIAO_OTA_PASSWORD "…" (не пустой). Тогда с ПК в той же LAN:
 *   arduino-cli upload -p IP_ПЛАТЫ --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi xiao_cam_stream
 *   или: .\\tools\\xiao_wifi_ota.ps1  (пароль в $env:XIAO_OTA_PASSWORD)
 *   или: py -3 tools\\xiao_http_ota.py  (POST /update?pwd=… — если TCP 3232 недоступен)
 * (пароль спросит CLI или задай в конфиге). Схема разделов default_8MB уже с ota_0 / ota_1.
 * Версия прошивки (счётчик репозитория): поля fw_build / fw_version в /telemetry — см. kXiaoFwBuild в .ino.
 * Без модуля камеры (разъём не распаян): initCamera() не блокирует Wi‑Fi/BLE/HTTP — cam_ok=0, MJPEG недоступен.
 */

#include <Arduino.h>
#include "driver/gpio.h"
#include "esp_camera.h"
#include "esp_chip_info.h"
#include "esp_heap_caps.h"
#include "esp_system.h"
#include <WiFi.h>
#include <WiFiMulti.h>
#include <WiFiServer.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <stdio.h>
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_ota_ops.h"
#include "soc/soc_caps.h"

#if defined(SOC_BT_SUPPORTED) && SOC_BT_SUPPORTED && __has_include("BLEDevice.h")
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#define XIAO_TELEM_HAVE_BLE 1
#else
#define XIAO_TELEM_HAVE_BLE 0
#endif

// Скетч только для XIAO ESP32S3 Sense (PDM). Условный __has_include давал mic_hw=0 и отказ TCP :81.
#include <ESP_I2S.h>
#define XIAO_TELEM_HAVE_PDM 1

// --- Wi-Fi STA (домашняя сеть, 2.4 ГГц) ---
// Пароль в secrets.h (не в git). Образец: secrets.h.example
// В secrets.h можно задать #define XIAO_WIFI_SSID_1 / XIAO_WIFI_SSID_2 — точное имя сети с роутера (2.4 ГГц).
#include "secrets.h"
#include "xiao_drive.h"
#include "xiao_motor_audio.h"
#include "xiao_tof.h"
#include "xiao_http_ota.h"

#ifndef XIAO_OTA_PASSWORD
#define XIAO_OTA_PASSWORD ""
#endif
#ifndef XIAO_OTA_ENABLE
#define XIAO_OTA_ENABLE 0
#endif

/** Версия прошивки (репозиторий): увеличивай `kXiaoFwBuild` при каждом релизе / OTA; `kXiaoFwVersion` — для людей. */
static constexpr uint32_t kXiaoFwBuild = 16u;
static constexpr char kXiaoFwVersion[] = "1.3.0";

#ifndef XIAO_WIFI_SSID_1
#define XIAO_WIFI_SSID_1 "дуангдихауз 2"
#endif
#ifndef XIAO_WIFI_SSID_2
#define XIAO_WIFI_SSID_2 "duangdihaus 2"
#endif

static constexpr uint32_t kWifiTimeoutMs = 60000;
/** Если STA отвалился во время работы — не чаще одного такого интервала подряд вызывать wifiMulti.run. */
static constexpr uint32_t kWifiStaRetryMs = 1600;

/** Последний код отключения STA (wifi_err_reason_t), из ARDUINO_EVENT_WIFI_STA_DISCONNECTED. */
static int g_wifiLastDiscReason = 0;
/** Период строки телеметрии в Serial (мс). */
static constexpr uint32_t kTelemetrySerialMs = 1500;

WebServer server(80);
WiFiMulti wifiMulti;
static bool gCamOk = false;
static bool gHttpServerStarted = false;
static bool gArduinoOtaReady = false;

/** Управление с GET /control?cam=0|1&mic=0|1&ble=0|1&wifi=0|1 (wifi: 1 — без эко‑сна радио, 0 — WIFI_PS_MAX_MODEM). */
static volatile bool gCtrlCamEnabled = true;
static volatile bool gCtrlMicEnabled = true;
#if XIAO_TELEM_HAVE_BLE
static volatile bool gCtrlBleAdvEnabled = true;
#endif
static volatile bool gCtrlWifiHiPower = true;

static volatile uint32_t g_streamFrameCount = 0;
static volatile uint32_t g_captureCount = 0;

#if XIAO_TELEM_HAVE_PDM
static I2SClass gMicI2s;
static WiFiServer gMicTcpServer{81};
static volatile bool gMicTcpServing = false;
#endif
static bool gMicOk = false;
static volatile float gMicRms = 0.0f;
static volatile float gMicDbfs = -96.0f;
static uint32_t gMicLastTickMs = 0;

#if XIAO_TELEM_HAVE_BLE
#define XIAO_BLE_SVC_UUID "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define XIAO_BLE_CHR_UUID "beb5483e-36e1-4688-b7f7-eaa05907848d"

static volatile int gBleConn = 0;

class XiaoBleServerCallbacks final : public BLEServerCallbacks {
public:
  void onConnect(BLEServer *) override {
    if (gBleConn < 8) {
      gBleConn++;
    }
  }
  void onDisconnect(BLEServer *) override {
    if (gBleConn > 0) {
      gBleConn--;
    }
  }
};

static XiaoBleServerCallbacks gBleCallbacks;
static BLEServer *gBleServerPtr = nullptr;
static bool gBleInited = false;
static bool gBleStarted = false;
static String gBleAdvName;
static BLECharacteristic *gBleTelemChr = nullptr;
#endif

// Пользовательский LED на XIAO ESP32-S3 (Sense): GPIO21 (см. variants/XIAO_ESP32S3).
static constexpr gpio_num_t kStatusLedGpio = GPIO_NUM_21;

enum class StatusLed : uint8_t { CameraInit, WifiWait, Running, ErrorCamera, ErrorWifi };

static StatusLed g_statusLed = StatusLed::CameraInit;
static bool g_statusLedResync = false;

/** Прямой вывод: чередуются HIGH/LOW — видно и при «катод на GPIO», и при «анод на GPIO». */
static inline void ledDriveLevel(int level) { gpio_set_level(kStatusLedGpio, level); }

static void statusLedInitHw() {
  gpio_reset_pin(kStatusLedGpio);
  gpio_set_direction(kStatusLedGpio, GPIO_MODE_OUTPUT);
  gpio_set_drive_capability(kStatusLedGpio, GPIO_DRIVE_CAP_3);
  ledDriveLevel(0);
}

static void statusLedInit() { statusLedInitHw(); }

/** Короткая заметная серия при старте (после задержки USB CDC). */
static void statusLedBootHello() {
  for (int i = 0; i < 4; i++) {
    ledDriveLevel(1);
    delay(90);
    ledDriveLevel(0);
    delay(90);
  }
}

static void statusLedSet(StatusLed m) {
  g_statusLed = m;
  g_statusLedResync = true;
}

/** Неблокирующий «пульс»: вызывать из loop и при ожидании Wi‑Fi. */
static void statusLedTick() {
  static uint32_t tflip = 0;
  static bool phase = false;
  const uint32_t now = millis();

  if (g_statusLedResync) {
    g_statusLedResync = false;
    tflip = now;
    phase = false;
    ledDriveLevel(0);
  }

  uint32_t halfMs = 200;
  if (g_statusLed == StatusLed::ErrorCamera) {
    halfMs = 90;
  } else if (g_statusLed == StatusLed::ErrorWifi) {
    halfMs = 220;
  } else if (g_statusLed == StatusLed::WifiWait) {
    halfMs = 350;
  } else if (g_statusLed == StatusLed::CameraInit) {
    halfMs = 120;
  } else if (g_statusLed == StatusLed::Running) {
    halfMs = 380;
  }

  if (now - tflip < halfMs) {
    return;
  }
  tflip = now;
  phase = !phase;
  ledDriveLevel(phase ? 1 : 0);
}

/** Пока блокирующий код не отдал управление — хотя бы крутим индикацию. */
static void statusLedSpin(uint32_t ms) {
  const uint32_t end = millis() + ms;
  while ((int32_t)(millis() - end) < 0) {
    statusLedTick();
    delay(4);
  }
}

#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 10
#define SIOD_GPIO_NUM 40
#define SIOC_GPIO_NUM 39
#define Y9_GPIO_NUM 48
#define Y8_GPIO_NUM 11
#define Y7_GPIO_NUM 12
#define Y6_GPIO_NUM 14
#define Y5_GPIO_NUM 16
#define Y4_GPIO_NUM 18
#define Y3_GPIO_NUM 17
#define Y2_GPIO_NUM 15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM 47
#define PCLK_GPIO_NUM 13

static bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_UXGA;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 8;
  config.fb_count = 1;

  if (config.pixel_format == PIXFORMAT_JPEG) {
    if (psramFound()) {
      /* Стабильный непрерывный MJPEG важнее макс. разрешения: QXGA/UXGA @ q2
         на OV3660 переполняет буфер (cam_hal FB-OVF) → esp_camera_fb_get()=NULL,
         кадров нет вообще. SVGA + q12 отдаётся надёжно. q: меньше = качественнее. */
      config.frame_size = FRAMESIZE_SVGA;
      config.jpeg_quality = 12;
      config.fb_count = 2;
      config.grab_mode = CAMERA_GRAB_LATEST;
    } else {
      config.frame_size = FRAMESIZE_SVGA;
      config.fb_location = CAMERA_FB_IN_DRAM;
    }
  }

  statusLedSpin(120);
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed 0x%x\n", err);
    return false;
  }

  // Вернуть GPIO21 в режим выхода (после инициализации периферии).
  statusLedInitHw();

  sensor_t *s = esp_camera_sensor_get();
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  }
  if (config.pixel_format == PIXFORMAT_JPEG) {
    if (psramFound()) {
      /* Для OV3660 и OV2640 одинаково: стабильный поток важнее разрешения (FB-OVF, см. выше). */
      s->set_framesize(s, FRAMESIZE_SVGA);
      s->set_quality(s, 12);
    } else {
      s->set_framesize(s, FRAMESIZE_SVGA);
      s->set_quality(s, 6);
    }
  }
  return true;
}

/** TCP :81 — сырой PCM s16le mono 16 kHz (для прокси /mic_s16); не блокирует HTTP MJPEG. */
#if XIAO_TELEM_HAVE_PDM
static void micTcpTask(void * /*arg*/) {
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      vTaskDelay(pdMS_TO_TICKS(120));
      continue;
    }
    WiFiClient c = gMicTcpServer.available();
    if (!c) {
      vTaskDelay(pdMS_TO_TICKS(2));
      continue;
    }
    if (!gCtrlMicEnabled) {
      c.stop();
      continue;
    }
    gMicTcpServing = true;
    int16_t buf[512];
    while (c.connected()) {
      const size_t got = gMicI2s.readBytes(reinterpret_cast<char *>(buf), sizeof(buf));
      if (got == 0) {
        vTaskDelay(pdMS_TO_TICKS(1));
        continue;
      }
      size_t off = 0;
      while (off < got && c.connected()) {
        const size_t w = c.write(reinterpret_cast<const uint8_t *>(buf) + off, got - off);
        if (w == 0) {
          break;
        }
        off += w;
      }
    }
    gMicTcpServing = false;
  }
}
#endif

/** PDM Sense: CLK=42, DATA=41 (Seeed Wiki). Не пересекается с пинами камеры XIAO. */
static void telemetryMicInit() {
#if !XIAO_TELEM_HAVE_PDM
  gMicOk = false;
  Serial.println(F("mic: нет PDM в этой сборке (нужен ESP_I2S / core 3.x)"));
#else
  gMicI2s.setPinsPdmRx(42, 41);
  if (!gMicI2s.begin(I2S_MODE_PDM_RX, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    gMicOk = false;
    Serial.println(F("mic: PDM begin failed"));
    return;
  }
  gMicOk = true;
  Serial.println(F("mic: PDM OK (GPIO42 CLK, GPIO41 DATA)"));
#endif
}

/** Периодически обновляет mic_rms / mic_dbfs для телеметрии. */
static void telemetryMicTick() {
#if XIAO_TELEM_HAVE_PDM
  if (!gCtrlMicEnabled) {
    return;
  }
  if (gMicTcpServing) {
    return;
  }
  if (!gMicOk) {
    return;
  }
  const uint32_t now = millis();
  if (now - gMicLastTickMs < 280u) {
    return;
  }
  gMicLastTickMs = now;
  int16_t buf[320];
  const size_t got = gMicI2s.readBytes(reinterpret_cast<char *>(buf), sizeof(buf));
  const size_t n = got / sizeof(int16_t);
  if (n == 0) {
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
  gMicRms = rms;
  gMicDbfs = db;
#endif
}

/** BLE после Wi‑Fi (совместимость стека). Реклама + простой сервис для nRF Connect. */
static void telemetryBleInit() {
#if !XIAO_TELEM_HAVE_BLE
  return;
#else
  if (gBleInited) {
    return;
  }
  const char *hn = WiFi.getHostname();
  if (hn == nullptr || hn[0] == '\0') {
    hn = "xiao-cam";
  }
  const uint64_t mac = ESP.getEfuseMac();
  char name[28];
  snprintf(name, sizeof name, "%s-%02X", hn, static_cast<unsigned>(mac & 0xFFu));
  gBleAdvName = name;

  BLEDevice::init(gBleAdvName.c_str());
  gBleServerPtr = BLEDevice::createServer();
  gBleServerPtr->setCallbacks(&gBleCallbacks);
  BLEService *svc = gBleServerPtr->createService(XIAO_BLE_SVC_UUID);
  gBleTelemChr = svc->createCharacteristic(
      XIAO_BLE_CHR_UUID, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  gBleTelemChr->addDescriptor(new BLE2902());
  gBleTelemChr->setValue("{}");
  svc->start();

  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(XIAO_BLE_SVC_UUID);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();

  gBleInited = true;
  gBleStarted = true;
  Serial.print(F("ble: LE advertising "));
  Serial.println(gBleAdvName);
#endif
}

static void handleRoot() {
  const String ip = WiFi.localIP().toString();
  String html;
  html.reserve(1400);
  html += F("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">"
           "<title>XIAO CAM</title></head><body>");
  html += F("<h3>XIAO ESP32-S3 Sense</h3>");
  html += F("<p><small>Прошивка ");
  html += kXiaoFwVersion;
  html += F(" · build ");
  html += String(kXiaoFwBuild);
  html += F("</small></p>");
  html += F("<p>Открой с телефона/ПК в <b>этой же Wi-Fi сети</b>:</p>");
  html += F("<p><b>http://");
  html += ip;
  html += F("/</b></p>");
  html += F("<p>Видео (MJPEG):</p>");
  if (gCamOk && gCtrlCamEnabled) {
    html += F("<p><img src=\"/stream\" style=\"max-width:100%;height:auto;border:1px solid #0a0\"></p>");
    html += F("<p>Снимок:</p>");
    html += F("<p><img src=\"/capture\" style=\"max-width:100%;height:auto;border:1px solid #ccc\"></p>");
  } else if (!gCamOk) {
    html += F("<p><em>Камера не подключена (нет модуля / разъём). Остальное: /telemetry, /drive.</em></p>");
  } else {
    html += F("<p><em>Камера выключена (GET /control?cam=1)</em></p>");
  }
  html += F("<p><a href=\"/stream\">/stream</a> · <a href=\"/capture\">/capture</a> · <a href=\"/telemetry\">/telemetry</a></p>");
  html += F("<p><small>Mic PCM (TCP 81, s16le 16 kHz mono) — через прокси: <code>/mic_s16</code></small></p>");
  html += F("<p><small>Привод: <code>/drive?l=0&r=0</code> · ToF <code>/status</code> <code>/tof</code> · карта <code>/scan360?steps=30</code> · звук <code>/beep</code> <code>/melody</code></small></p>");
  html += F("</body></html>");
  server.send(200, "text/html; charset=utf-8", html);
}

static void handleStream() {
  WiFiClient client = server.client();
  if (!client || !client.connected()) {
    return;
  }
  if (!gCamOk || !gCtrlCamEnabled) {
    client.println(F("HTTP/1.1 503 Service Unavailable"));
    client.println(F("Content-Type: text/plain; charset=utf-8"));
    client.println(F("Connection: close"));
    client.println();
    client.println(gCamOk ? F("camera disabled (GET /control?cam=1)") : F("camera hardware not present"));
    return;
  }

  const char *const boundary = "mjpeg";

  client.println(F("HTTP/1.1 200 OK"));
  client.println(F("Access-Control-Allow-Origin: *"));
  // Не ставить Connection: close — часть клиентов рвёт MJPEG после первого кадра.
  client.print(F("Content-Type: multipart/x-mixed-replace; boundary="));
  client.println(boundary);
  client.println();

  while (client.connected()) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
      delay(2);
      continue;
    }
    client.print(F("\r\n--"));
    client.print(boundary);
    client.print(F("\r\nContent-Type: image/jpeg\r\nContent-Length: "));
    client.print(fb->len);
    client.print(F("\r\n\r\n"));

    size_t remain = fb->len;
    uint8_t *p = fb->buf;
    while (remain > 0 && client.connected()) {
      const size_t chunk = remain > 4096 ? 4096 : remain;
      const size_t w = client.write(p, chunk);
      if (w == 0) {
        break;
      }
      p += w;
      remain -= w;
    }
    esp_camera_fb_return(fb);
    if (remain == 0) {
      g_streamFrameCount++;
    }
    yield();
  }
}

static void handleCapture() {
  if (!gCamOk || !gCtrlCamEnabled) {
    server.sendHeader(F("Cache-Control"), F("no-store"));
    server.sendHeader(F("Access-Control-Allow-Origin"), F("*"));
    server.send(503, "text/plain; charset=utf-8",
                  gCamOk ? "camera disabled (GET /control?cam=1)" : "camera hardware not present");
    return;
  }
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    server.send(500, "text/plain", "capture failed");
    return;
  }
  server.sendHeader("Cache-Control", "no-store");
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.setContentLength(fb->len);
  server.send(200, "image/jpeg", "");
  server.sendContent((const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  g_captureCount++;
}

static const char *resetReasonLabel(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_UNKNOWN:
      return "unknown";
    case ESP_RST_POWERON:
      return "poweron";
    case ESP_RST_EXT:
      return "ext_pin";
    case ESP_RST_SW:
      return "sw";
    case ESP_RST_PANIC:
      return "panic";
    case ESP_RST_INT_WDT:
      return "int_wdt";
    case ESP_RST_TASK_WDT:
      return "task_wdt";
    case ESP_RST_WDT:
      return "other_wdt";
    case ESP_RST_DEEPSLEEP:
      return "deepsleep";
    case ESP_RST_BROWNOUT:
      return "brownout";
    case ESP_RST_SDIO:
      return "sdio";
    default:
      return "other";
  }
}

static const char *wifiStatusLabel(wl_status_t s) {
  switch (s) {
    case WL_IDLE_STATUS:
      return "idle";
    case WL_NO_SSID_AVAIL:
      return "no_ssid";
    case WL_SCAN_COMPLETED:
      return "scan_done";
    case WL_CONNECTED:
      return "connected";
    case WL_CONNECT_FAILED:
      return "connect_failed";
    case WL_CONNECTION_LOST:
      return "lost";
    case WL_DISCONNECTED:
      return "disconnected";
    default:
      return "other";
  }
}

static const char *statusLedLabel(StatusLed m) {
  switch (m) {
    case StatusLed::CameraInit:
      return "camera_init";
    case StatusLed::WifiWait:
      return "wifi_wait";
    case StatusLed::Running:
      return "running";
    case StatusLed::ErrorCamera:
      return "error_camera";
    case StatusLed::ErrorWifi:
      return "error_wifi";
    default:
      return "?";
  }
}

static void jsonAppendEscaped(String &j, const char *key, const String &val) {
  j += '"';
  j += key;
  j += F("\":\"");
  for (size_t i = 0; i < val.length(); ++i) {
    const char c = val.charAt(i);
    if (c == '"' || c == '\\') {
      j += '\\';
    }
    if ((uint8_t)c < 0x20u) {
      j += ' ';
    } else {
      j += c;
    }
  }
  j += F("\"");
}

static void telemetryAppendPair(String &j, bool &comma, const char *key, const String &val) {
  if (comma) {
    j += ',';
  }
  comma = true;
  jsonAppendEscaped(j, key, val);
}

static void telemetryAppendUInt(String &j, bool &comma, const char *key, uint32_t v) {
  if (comma) {
    j += ',';
  }
  comma = true;
  j += '"';
  j += key;
  j += F("\":");
  j += String(v);
}

static void telemetryAppendInt(String &j, bool &comma, const char *key, int v) {
  if (comma) {
    j += ',';
  }
  comma = true;
  j += '"';
  j += key;
  j += F("\":");
  j += String(v);
}

static void telemetryAppendFloat(String &j, bool &comma, const char *key, float v) {
  if (comma) {
    j += ',';
  }
  comma = true;
  j += '"';
  j += key;
  j += F("\":");
  j += String(v, 2);
}

static const char *wifiModeStr(wifi_mode_t m) {
  switch (m) {
    case WIFI_MODE_NULL:
      return "null";
    case WIFI_MODE_STA:
      return "sta";
    case WIFI_MODE_AP:
      return "ap";
    case WIFI_MODE_APSTA:
      return "apsta";
    default:
      return "other";
  }
}

static const char *flashModeStr(FlashMode_t m) {
  switch (m) {
    case FM_QIO:
      return "qio";
    case FM_QOUT:
      return "qout";
    case FM_DIO:
      return "dio";
    case FM_DOUT:
      return "dout";
    default:
      return "unknown";
  }
}

/** Максимально полный снимок состояния (одна строка JSON для Serial и /telemetry). */
static String telemetryBuildJson() {
  String j;
  j.reserve(6800);
  j = '{';
  bool comma = false;

  esp_chip_info_t chip{};
  esp_chip_info(&chip);

  telemetryAppendUInt(j, comma, "uptime_ms", millis());
  telemetryAppendUInt(j, comma, "micros_lo", static_cast<uint32_t>(micros()));
  telemetryAppendUInt(j, comma, "fw_build", kXiaoFwBuild);
  telemetryAppendPair(j, comma, "fw_version", String(kXiaoFwVersion));
  telemetryAppendPair(j, comma, "reset_reason", String(resetReasonLabel(esp_reset_reason())));
  telemetryAppendPair(j, comma, "led_mode", String(statusLedLabel(g_statusLed)));
  telemetryAppendUInt(j, comma, "chip_features", chip.features);
  telemetryAppendPair(j, comma, "chip_model", String(ESP.getChipModel()));
  telemetryAppendUInt(j, comma, "chip_revision", ESP.getChipRevision());
  telemetryAppendUInt(j, comma, "chip_cores", chip.cores);
  telemetryAppendUInt(j, comma, "cpu_mhz", ESP.getCpuFreqMHz());
  telemetryAppendUInt(j, comma, "cpu_cycles", ESP.getCycleCount());
  telemetryAppendPair(j, comma, "sdk", String(ESP.getSdkVersion()));
  telemetryAppendPair(j, comma, "core_version", String(ESP.getCoreVersion()));
#ifdef ARDUINO_BOARD
  telemetryAppendPair(j, comma, "arduino_board", String(ARDUINO_BOARD));
#endif
#ifdef ARDUINO_FQBN
  telemetryAppendPair(j, comma, "arduino_fqbn", String(ARDUINO_FQBN));
#endif

  {
    const uint64_t em = ESP.getEfuseMac();
    char machex[20];
    snprintf(machex, sizeof machex, "%04X%08X", static_cast<uint16_t>(em >> 32), static_cast<uint32_t>(em & 0xFFFFFFFFu));
    telemetryAppendPair(j, comma, "efuse_mac", String(machex));
  }

  telemetryAppendUInt(j, comma, "heap_total", ESP.getHeapSize());
  telemetryAppendUInt(j, comma, "heap_free", ESP.getFreeHeap());
  telemetryAppendUInt(j, comma, "heap_min", ESP.getMinFreeHeap());
  telemetryAppendUInt(j, comma, "heap_max_block", ESP.getMaxAllocHeap());
  telemetryAppendUInt(j, comma, "heap_internal_free", heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
  telemetryAppendUInt(j, comma, "heap_dma_free", heap_caps_get_free_size(MALLOC_CAP_DMA));
  telemetryAppendUInt(j, comma, "heap_ever_min", esp_get_minimum_free_heap_size());

  telemetryAppendUInt(j, comma, "psram_size", ESP.getPsramSize());
  telemetryAppendUInt(j, comma, "psram_free_caps", heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
  telemetryAppendUInt(j, comma, "psram_largest_block", heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM));
  telemetryAppendUInt(j, comma, "psram_free_esp", ESP.getFreePsram());
  telemetryAppendUInt(j, comma, "psram_min_free", ESP.getMinFreePsram());
  telemetryAppendUInt(j, comma, "psram_max_alloc", ESP.getMaxAllocPsram());

  telemetryAppendUInt(j, comma, "stack_watermark", static_cast<uint32_t>(uxTaskGetStackHighWaterMark(nullptr)));
  telemetryAppendUInt(j, comma, "rtos_task_count", static_cast<uint32_t>(uxTaskGetNumberOfTasks()));

  telemetryAppendUInt(j, comma, "flash_size", ESP.getFlashChipSize());
  telemetryAppendUInt(j, comma, "flash_chip_hz", ESP.getFlashChipSpeed());
  telemetryAppendUInt(j, comma, "flash_freq_mhz", ESP.getFlashFrequencyMHz());
  telemetryAppendPair(j, comma, "flash_mode", String(flashModeStr(ESP.getFlashChipMode())));
  telemetryAppendUInt(j, comma, "sketch_size", ESP.getSketchSize());
  telemetryAppendUInt(j, comma, "sketch_free", ESP.getFreeSketchSpace());
  telemetryAppendPair(j, comma, "sketch_md5", ESP.getSketchMD5());

  {
    const esp_partition_t *runpart = esp_ota_get_running_partition();
    if (runpart) {
      telemetryAppendPair(j, comma, "part_label", String(runpart->label));
      telemetryAppendUInt(j, comma, "part_type", runpart->type);
      telemetryAppendUInt(j, comma, "part_subtype", runpart->subtype);
      telemetryAppendUInt(j, comma, "part_address", runpart->address);
      telemetryAppendUInt(j, comma, "part_size", runpart->size);
    }
  }

  const wl_status_t wst = WiFi.status();
  telemetryAppendPair(j, comma, "wifi_status", String(wifiStatusLabel(wst)));
  telemetryAppendInt(j, comma, "wifi_status_code", static_cast<int>(wst));
  telemetryAppendInt(j, comma, "wifi_disc_reason", g_wifiLastDiscReason);
  telemetryAppendPair(j, comma, "wifi_mode", String(wifiModeStr(WiFi.getMode())));
  telemetryAppendInt(j, comma, "wifi_ps", static_cast<int>(WiFi.getSleep()));
  telemetryAppendInt(j, comma, "wifi_tx_power", static_cast<int>(WiFi.getTxPower()));
  telemetryAppendPair(j, comma, "wifi_ssid", WiFi.SSID());
  telemetryAppendPair(j, comma, "wifi_ip", WiFi.localIP().toString());
  telemetryAppendPair(j, comma, "wifi_mask", WiFi.subnetMask().toString());
  telemetryAppendPair(j, comma, "wifi_gateway", WiFi.gatewayIP().toString());
  telemetryAppendPair(j, comma, "wifi_dns", WiFi.dnsIP(0).toString());
  telemetryAppendInt(j, comma, "wifi_rssi", WiFi.RSSI());
  telemetryAppendUInt(j, comma, "wifi_channel", WiFi.channel());
  telemetryAppendPair(j, comma, "wifi_mac_sta", WiFi.macAddress());
  {
    const char *hn = WiFi.getHostname();
    telemetryAppendPair(j, comma, "wifi_hostname", hn ? String(hn) : String());
  }
  telemetryAppendPair(j, comma, "wifi_ipv6_ll", WiFi.linkLocalIPv6().toString());
  telemetryAppendPair(j, comma, "wifi_ipv6_global", WiFi.globalIPv6().toString());

  const uint8_t *bssid = WiFi.BSSID();
  char bssidStr[20] = "00:00:00:00:00:00";
  if (bssid != nullptr) {
    snprintf(bssidStr, sizeof bssidStr, "%02X:%02X:%02X:%02X:%02X:%02X", bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5]);
  }
  telemetryAppendPair(j, comma, "wifi_bssid", String(bssidStr));

  {
    wifi_ap_record_t ap{};
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
      char essid[33];
      memcpy(essid, ap.ssid, sizeof(ap.ssid));
      essid[32] = '\0';
      telemetryAppendPair(j, comma, "ap_ssid_from_bss", String(essid));
      telemetryAppendInt(j, comma, "ap_rssi_bss", ap.rssi);
      telemetryAppendUInt(j, comma, "ap_primary_chan", ap.primary);
      telemetryAppendUInt(j, comma, "ap_second_chan", static_cast<unsigned>(ap.second));
      telemetryAppendUInt(j, comma, "ap_authmode", ap.authmode);
      telemetryAppendUInt(j, comma, "ap_pairwise_cipher", ap.pairwise_cipher);
      telemetryAppendUInt(j, comma, "ap_group_cipher", ap.group_cipher);
    }
  }

  {
    sensor_t *sens = esp_camera_sensor_get();
    if (sens) {
      telemetryAppendUInt(j, comma, "cam_pid", sens->id.PID);
      telemetryAppendUInt(j, comma, "cam_ver", sens->id.VER);
      telemetryAppendUInt(j, comma, "cam_midh", sens->id.MIDH);
      telemetryAppendUInt(j, comma, "cam_midl", sens->id.MIDL);
      telemetryAppendUInt(j, comma, "cam_sccb_addr", sens->slv_addr);
      telemetryAppendUInt(j, comma, "cam_pixformat", static_cast<unsigned>(sens->pixformat));
      telemetryAppendUInt(j, comma, "cam_framesize", static_cast<unsigned>(sens->status.framesize));
      telemetryAppendUInt(j, comma, "cam_quality", sens->status.quality);
      telemetryAppendInt(j, comma, "cam_brightness", sens->status.brightness);
      telemetryAppendInt(j, comma, "cam_contrast", sens->status.contrast);
      telemetryAppendInt(j, comma, "cam_saturation", sens->status.saturation);
      telemetryAppendInt(j, comma, "cam_sharpness", sens->status.sharpness);
      telemetryAppendUInt(j, comma, "cam_denoise", sens->status.denoise);
      telemetryAppendUInt(j, comma, "cam_xclk_hz", static_cast<uint32_t>(sens->xclk_freq_hz));
      telemetryAppendUInt(j, comma, "cam_hmirror", sens->status.hmirror);
      telemetryAppendUInt(j, comma, "cam_vflip", sens->status.vflip);
    } else {
      telemetryAppendUInt(j, comma, "cam_pid", 0);
    }
  }
  telemetryAppendUInt(j, comma, "cam_ok", gCamOk ? 1u : 0u);
  telemetryAppendUInt(j, comma, "cam_frames_stream", g_streamFrameCount);
  telemetryAppendUInt(j, comma, "cam_captures", g_captureCount);

#if XIAO_TELEM_HAVE_BLE
  telemetryAppendUInt(j, comma, "ble_hw", 1u);
  telemetryAppendUInt(j, comma, "ble_started", gBleStarted ? 1u : 0u);
  telemetryAppendInt(j, comma, "ble_clients", static_cast<int>(gBleConn));
  telemetryAppendPair(j, comma, "ble_adv_name", gBleAdvName);
#else
  telemetryAppendUInt(j, comma, "ble_hw", 0u);
#endif

#if XIAO_TELEM_HAVE_PDM
  telemetryAppendUInt(j, comma, "mic_hw", 1u);
  telemetryAppendUInt(j, comma, "mic_ok", gMicOk ? 1u : 0u);
  telemetryAppendUInt(j, comma, "mic_tcp_listen", (gMicOk && gMicTcpServer) ? 1u : 0u);
  telemetryAppendUInt(j, comma, "mic_tcp_port", gMicOk ? 81u : 0u);
  if (gMicOk) {
    telemetryAppendFloat(j, comma, "mic_rms", static_cast<float>(gMicRms));
    telemetryAppendFloat(j, comma, "mic_dbfs", static_cast<float>(gMicDbfs));
  }
#else
  telemetryAppendUInt(j, comma, "mic_hw", 0u);
#endif

  telemetryAppendUInt(j, comma, "ctrl_cam", gCtrlCamEnabled ? 1u : 0u);
  telemetryAppendUInt(j, comma, "ctrl_mic", gCtrlMicEnabled ? 1u : 0u);
#if XIAO_TELEM_HAVE_BLE
  telemetryAppendUInt(j, comma, "ctrl_ble", gCtrlBleAdvEnabled ? 1u : 0u);
#else
  telemetryAppendUInt(j, comma, "ctrl_ble", 0u);
#endif
  telemetryAppendUInt(j, comma, "ctrl_wifi", gCtrlWifiHiPower ? 1u : 0u);

  xiaoDriveAppendTelemetry(j, comma);
  xiaoTofAppendTelemetry(j, comma);

  const float tchip = temperatureRead();
  if (!isnan(tchip) && tchip > -40.0f && tchip < 130.0f) {
    telemetryAppendFloat(j, comma, "chip_temp_c", tchip);
  }

  j += '}';
  return j;
}

#if XIAO_TELEM_HAVE_BLE
/** Укороченный JSON для BLE NOTIFY (ограничение MTU); полный снимок — по Wi‑Fi / Serial. */
static String telemetryBuildJsonBleCompact() {
  const wl_status_t wst = WiFi.status();
  String j;
  j.reserve(480);
  j = F("{\"transport\":\"ble_compact\",\"uptime_ms\":");
  j += String(millis());
  j += F(",\"heap_free\":");
  j += String(ESP.getFreeHeap());
  j += F(",\"heap_min\":");
  j += String(ESP.getMinFreeHeap());
  j += F(",\"wifi_status\":\"");
  j += wifiStatusLabel(wst);
  j += F("\",\"wifi_status_code\":");
  j += String(static_cast<int>(wst));
  j += F(",\"wifi_disc_reason\":");
  j += String(g_wifiLastDiscReason);
  j += F(",\"wifi_rssi\":");
  j += String(WiFi.RSSI());
  j += F(",\"fw_build\":");
  j += String(kXiaoFwBuild);
  j += F(",\"fw_version\":\"");
  j += kXiaoFwVersion;
  j += F("\",\"ble_clients\":");
  j += String(static_cast<int>(gBleConn));
#if XIAO_TELEM_HAVE_PDM
  j += F(",\"mic_rms\":");
  j += String(gMicRms, 4);
  j += F(",\"mic_dbfs\":");
  j += String(gMicDbfs, 2);
#endif
  j += F(",\"cam_frames_stream\":");
  j += String(g_streamFrameCount);
  j += F(",\"cam_captures\":");
  j += String(g_captureCount);
  const float tchip = temperatureRead();
  if (!isnan(tchip) && tchip > -40.0f && tchip < 130.0f) {
    j += F(",\"chip_temp_c\":");
    j += String(tchip, 2);
  }
  j += '}';
  return j;
}

static void telemetryBlePushIfDue() {
  if (!gBleTelemChr || !gBleStarted) {
    return;
  }
  if (!gCtrlBleAdvEnabled) {
    return;
  }
  String s = telemetryBuildJsonBleCompact();
  if (s.length() > 500) {
    s.remove(499);
    s += '}';
  }
  gBleTelemChr->setValue(s.c_str());
  gBleTelemChr->notify();
}
#endif

static void telemetryPrintSerialIfDue() {
  static uint32_t s_last = 0;
  const uint32_t now = millis();
  if (now - s_last < kTelemetrySerialMs) {
    return;
  }
  s_last = now;
  const String line = telemetryBuildJson();
  Serial.println(line);
#if XIAO_TELEM_HAVE_BLE
  telemetryBlePushIfDue();
#endif
}

static void applyWifiPsFromFlag() {
  if (gCtrlWifiHiPower) {
    esp_wifi_set_ps(WIFI_PS_NONE);
    WiFi.setSleep(false);
  } else {
    esp_wifi_set_ps(WIFI_PS_MAX_MODEM);
    WiFi.setSleep(true);
  }
}

static void handleControl() {
  if (server.hasArg("cam")) {
    const bool want = server.arg("cam").toInt() != 0;
    gCtrlCamEnabled = gCamOk && want;
  }
  if (server.hasArg("mic")) {
    gCtrlMicEnabled = server.arg("mic").toInt() != 0;
  }
#if XIAO_TELEM_HAVE_BLE
  if (server.hasArg("ble") && gBleInited) {
    gCtrlBleAdvEnabled = server.arg("ble").toInt() != 0;
    BLEAdvertising *adv = BLEDevice::getAdvertising();
    if (adv) {
      if (gCtrlBleAdvEnabled) {
        BLEDevice::startAdvertising();
      } else {
        adv->stop();
      }
    }
  }
#endif
  if (server.hasArg("wifi")) {
    gCtrlWifiHiPower = server.arg("wifi").toInt() != 0;
    applyWifiPsFromFlag();
  }
#if XIAO_DRIVE_ENABLE
  if (server.hasArg("drive")) {
    xiaoDriveSetEnabled(server.arg("drive").toInt() != 0);
  }
#endif

  String j = F("{\"ok\":1,\"ctrl_cam\":");
  j += gCtrlCamEnabled ? F("1") : F("0");
  j += F(",\"ctrl_mic\":");
  j += gCtrlMicEnabled ? F("1") : F("0");
  j += F(",\"ctrl_wifi\":");
  j += gCtrlWifiHiPower ? F("1") : F("0");
#if XIAO_TELEM_HAVE_BLE
  j += F(",\"ctrl_ble\":");
  j += gCtrlBleAdvEnabled ? F("1") : F("0");
#else
  j += F(",\"ctrl_ble\":0");
#endif
#if XIAO_DRIVE_ENABLE
  {
    XiaoDriveState ds{};
    xiaoDriveGetState(&ds);
    j += F(",\"ctrl_drive\":");
    j += ds.enabled ? F("1") : F("0");
    j += F(",\"drive_watchdog\":");
    j += ds.watchdog_stop ? F("1") : F("0");
  }
#else
  j += F(",\"ctrl_drive\":0");
#endif
  j += F("}");
  server.sendHeader(F("Cache-Control"), F("no-store"));
  server.sendHeader(F("Access-Control-Allow-Origin"), F("*"));
  server.send(200, F("application/json; charset=utf-8"), j);
}

#if XIAO_DRIVE_ENABLE
static void handleDrive() {
  if (!server.hasArg("stop")) {
    if (!server.hasArg("l") && !server.hasArg("r")) {
      server.send(400, F("text/plain; charset=utf-8"),
                    F("use /drive?l=&r= (-255..255) or /drive?stop=1"));
      return;
    }
    const int l = server.hasArg("l") ? server.arg("l").toInt() : 0;
    const int r = server.hasArg("r") ? server.arg("r").toInt() : l;
    xiaoDriveSetLr(static_cast<int16_t>(l), static_cast<int16_t>(r));
  } else {
    xiaoDriveStop();
  }
  XiaoDriveState ds{};
  xiaoDriveGetState(&ds);
  String j = F("{\"ok\":1,\"cmd_l\":");
  j += String(ds.cmd_l);
  j += F(",\"cmd_r\":");
  j += String(ds.cmd_r);
  j += F(",\"enc_l\":");
  j += String(ds.enc_l);
  j += F(",\"enc_r\":");
  j += String(ds.enc_r);
  j += F(",\"us_cm\":");
  j += String(ds.us_cm);
  j += F(",\"bumper\":");
  j += String(ds.bumper);
  j += F(",\"watchdog\":");
  j += ds.watchdog_stop ? F("1") : F("0");
  j += F("}");
  server.sendHeader(F("Cache-Control"), F("no-store"));
  server.sendHeader(F("Access-Control-Allow-Origin"), F("*"));
  server.send(200, F("application/json; charset=utf-8"), j);
}

static void handleRobotStatus() {
  String j = F("{\"ok\":1");
  bool comma = true;
  xiaoTofAppendTelemetry(j, comma);
  XiaoDriveState ds{};
  xiaoDriveGetState(&ds);
  j += F(",\"cmd_l\":");
  j += String(ds.cmd_l);
  j += F(",\"cmd_r\":");
  j += String(ds.cmd_r);
  j += F(",\"enc_l\":");
  j += String(ds.enc_l);
  j += F(",\"enc_r\":");
  j += String(ds.enc_r);
  j += F("}");
  server.sendHeader(F("Cache-Control"), F("no-store"));
  server.sendHeader(F("Access-Control-Allow-Origin"), F("*"));
  server.send(200, F("application/json; charset=utf-8"), j);
}

static void handleScan360() {
  if (!xiaoTofIsOk()) {
    server.send(503, F("application/json; charset=utf-8"), F("{\"ok\":0,\"error\":\"tof\"}"));
    return;
  }
  int steps = server.hasArg("steps") ? server.arg("steps").toInt() : 30;
  if (steps < 8) {
    steps = 8;
  }
  if (steps > 72) {
    steps = 72;
  }
  XiaoScanPoint pts[72];
  const uint8_t n = xiaoTofRunScan360(static_cast<uint8_t>(steps), pts, 72);
  String j = F("{\"ok\":1,\"steps\":");
  j += String(n);
  j += F(",\"points\":[");
  for (uint8_t i = 0; i < n; ++i) {
    if (i) {
      j += ',';
    }
    j += F("{\"ang\":");
    j += String(pts[i].ang);
    j += F(",\"mm\":");
    j += String(pts[i].mm);
    j += F(",\"valid\":");
    j += pts[i].valid ? F("1") : F("0");
    j += F("}");
  }
  j += F("]}");
  server.sendHeader(F("Cache-Control"), F("no-store"));
  server.sendHeader(F("Access-Control-Allow-Origin"), F("*"));
  server.send(200, F("application/json; charset=utf-8"), j);
}

static void handleBeep() {
  const uint16_t hz = server.hasArg("hz") ? static_cast<uint16_t>(server.arg("hz").toInt()) : 880;
  const uint16_t ms = server.hasArg("ms") ? static_cast<uint16_t>(server.arg("ms").toInt()) : 250;
  const String ch = server.hasArg("ch") ? server.arg("ch") : F("A");
  if (server.hasArg("gain")) {
    /* Скважность «голоса» 10..100 % (по умолчанию 10, как на UNO-стенде). */
    xiaoAudioSetGain(static_cast<uint8_t>(server.arg("gain").toInt()));
  }
  xiaoAudioBeepHttp(hz, ms, ch.c_str());
  server.send(200, F("application/json; charset=utf-8"), F("{\"ok\":1}"));
}

static void handleMelody() {
  const uint8_t id = server.hasArg("id") ? static_cast<uint8_t>(server.arg("id").toInt()) : 0;
  const String ch = server.hasArg("ch") ? server.arg("ch") : F("A");
  if (server.hasArg("gain")) {
    xiaoAudioSetGain(static_cast<uint8_t>(server.arg("gain").toInt()));
  }
  xiaoAudioMelodyHttp(id, ch.c_str());
  server.send(200, F("application/json; charset=utf-8"), F("{\"ok\":1}"));
}
#endif

static void handleTelemetry() {
  const String body = telemetryBuildJson();
  server.sendHeader(F("Cache-Control"), F("no-store"));
  server.sendHeader(F("Access-Control-Allow-Origin"), F("*"));
  server.send(200, F("application/json; charset=utf-8"), body);
}

/** Сетка зон VL53L7CX (мм, -1 = нет цели); при XIAO_TOF_ENABLE 0 — {"ok":0}. */
static void handleTofGrid() {
  String j;
  xiaoTofGridJson(j);
  server.sendHeader(F("Cache-Control"), F("no-store"));
  server.sendHeader(F("Access-Control-Allow-Origin"), F("*"));
  server.send(200, F("application/json; charset=utf-8"), j);
}

/** esp32-arduino 3.3: нет WiFi.disconnectReason() — берём reason из системного события. */
static void wifiOnArduinoEvent(arduino_event_id_t event, arduino_event_info_t info) {
  if (event != ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    return;
  }
  uint8_t r = info.wifi_sta_disconnected.reason;
  if (r == 0) {
    r = static_cast<uint8_t>(WIFI_REASON_UNSPECIFIED);
  }
  g_wifiLastDiscReason = static_cast<int>(r);
  Serial.print(F("WiFi: отвал, reason="));
  Serial.println(g_wifiLastDiscReason);
}

/** Поддержка STA: при обрыве снова пробуем сети из WiFiMulti (короткие run, чтобы loop и HTTP не подвисали). */
static void wifiMaintainSta() {
  if (WiFi.getMode() == WIFI_MODE_NULL) {
    return;
  }
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }
  static uint32_t s_last = 0;
  const uint32_t now = millis();
  if (now - s_last < kWifiStaRetryMs) {
    return;
  }
  s_last = now;
  Serial.println(F("WiFi: нет связи — повторяю подключение (WiFiMulti)..."));
  statusLedSet(StatusLed::WifiWait);
  uint8_t st = wifiMulti.run(400);
  statusLedTick();
  if (st != WL_CONNECTED && WiFi.status() != WL_CONNECTED) {
    st = wifiMulti.run(400);
    statusLedTick();
  }
  if (st == WL_CONNECTED || WiFi.status() == WL_CONNECTED) {
    statusLedSet(StatusLed::Running);
    Serial.print(F("WiFi восстановлен, IP: "));
    Serial.println(WiFi.localIP());
  }
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);
  delay(800);
  statusLedInit();
  statusLedBootHello();

  statusLedSet(StatusLed::CameraInit);
  gCamOk = initCamera();
  if (gCamOk) {
    Serial.println(F("Camera OK"));
  } else {
    gCtrlCamEnabled = false;
    statusLedInitHw();
    Serial.println(F("Camera: нет модуля — продолжаем без MJPEG (Wi-Fi, BLE, /telemetry, /drive)."));
  }

  xiaoDriveInit();
  xiaoTofInit();

  telemetryMicInit();
#if XIAO_TELEM_HAVE_PDM
  if (!gMicOk) {
    Serial.println(F("mic: повтор PDM после паузы (иногда I2S заводится только после камеры)…"));
    delay(180);
    gMicI2s.end();
    telemetryMicInit();
    if (!gMicOk) {
      Serial.println(F("mic: PDM так и не поднялся — TCP :81 не будет; проверь XIAO Sense, core 3.x, меню PSRAM=OPI."));
    }
  }
#endif

  WiFi.mode(WIFI_STA);
  WiFi.setHostname("xiao-cam");
  applyWifiPsFromFlag();
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  // Быстрее находим известные AP; с внешней антенной обычно стабильнее по RSSI.
  WiFi.setScanMethod(WIFI_FAST_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
  WiFi.onEvent(wifiOnArduinoEvent, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);

  wifiMulti.addAP(XIAO_WIFI_SSID_1, kWifiPass);
  wifiMulti.addAP(XIAO_WIFI_SSID_2, kWifiPass);

  Serial.println(F("WiFi: пробую сети (UTF-8 и латиница)..."));
  statusLedSet(StatusLed::WifiWait);
  const uint32_t t0 = millis();
  while (millis() - t0 < kWifiTimeoutMs) {
    const uint8_t st = wifiMulti.run(500);
    statusLedTick();
    // Иначе telemetryPrintSerialIfDue() не вызывается, пока setup не завершится
    // (до kWifiTimeoutMs), и USB /api/telemetry остаётся на waiting_for_first_json_line.
    telemetryPrintSerialIfDue();
    telemetryMicTick();
    if (st == WL_CONNECTED) {
      break;
    }
    Serial.print('.');
    delay(120);
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println(F("WiFi: не удалось. Проверь имя сети на роутере и пароль."));
    statusLedSet(StatusLed::ErrorWifi);
    gHttpServerStarted = false;
    telemetryBleInit();
    return;
  }
  Serial.print(F("WiFi OK, IP: "));
  Serial.println(WiFi.localIP());
  Serial.print(F("SSID: "));
  Serial.println(WiFi.SSID());

  telemetryBleInit();

  if (MDNS.begin("xiao-cam")) {
    MDNS.addService("http", "tcp", 80);
    Serial.println(F("mDNS: http://xiao-cam.local/"));
  } else {
    Serial.println(F("mDNS: ошибка инициализации"));
  }

  if (XIAO_OTA_ENABLE) {
    ArduinoOTA.setHostname("xiao-cam");
    ArduinoOTA.setPassword(XIAO_OTA_PASSWORD);
    ArduinoOTA.onStart([]() { Serial.println(F("OTA: начало")); });
    ArduinoOTA.onEnd([]() { Serial.println(F("OTA: готово, перезагрузка")); });
    ArduinoOTA.onError([](ota_error_t e) {
      Serial.print(F("OTA: ошибка "));
      Serial.println(static_cast<unsigned>(e));
    });
    ArduinoOTA.begin();
    gArduinoOtaReady = true;
    Serial.print(F("OTA Wi-Fi: TCP 3232, пароль из secrets, IP "));
    Serial.println(WiFi.localIP());
  }

  server.on("/", HTTP_GET, handleRoot);
  server.on("/stream", HTTP_GET, handleStream);
  server.on("/capture", HTTP_GET, handleCapture);
  server.on("/telemetry", HTTP_GET, handleTelemetry);
  server.on("/control", HTTP_GET, handleControl);
  server.on("/tof", HTTP_GET, handleTofGrid);
#if XIAO_DRIVE_ENABLE
  server.on("/drive", HTTP_GET, handleDrive);
  server.on("/status", HTTP_GET, handleRobotStatus);
  server.on("/scan360", HTTP_GET, handleScan360);
  server.on("/beep", HTTP_GET, handleBeep);
  server.on("/melody", HTTP_GET, handleMelody);
#endif
  xiaoHttpOtaRegister();
  server.begin();
  gHttpServerStarted = true;
#if XIAO_TELEM_HAVE_PDM
  if (gMicOk) {
    gMicTcpServer.begin(81);
    // Ядро 1: на 0 часто Wi‑Fi/stack; отдельный TCP‑сервер микрофона стабильнее рядом с I2S.
    if (xTaskCreatePinnedToCore(micTcpTask, "micTcp", 8192, nullptr, 1, nullptr, 1) != pdPASS) {
      Serial.println(F("mic: TCP task start failed"));
    } else {
      Serial.print(F("mic: TCP PCM on :81 listening="));
      Serial.println(static_cast<int>(gMicTcpServer ? 1 : 0));
    }
  } else {
    Serial.println(F("mic: TCP :81 disabled (PDM init failed)"));
  }
#endif
  statusLedSet(StatusLed::Running);
  Serial.println(F("HTTP: /telemetry /drive /status /tof /scan360 /beep /melody"));
  Serial.println(F("Telemetry: JSON lines in Serial every 1.5s; snapshot GET /telemetry"));
}

void loop() {
  wifiMaintainSta();
  if (gArduinoOtaReady && WiFi.status() == WL_CONNECTED) {
    ArduinoOTA.handle();
  }
  telemetryMicTick();
  xiaoTofTick();
  xiaoAudioTick();
  xiaoDriveTick();
  statusLedTick();
  telemetryPrintSerialIfDue();
  if (gHttpServerStarted) {
    server.handleClient();
  }
}
