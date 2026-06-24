#pragma once
//
// xiao_net.h — связь робота: (1) токен-авторизация эндпоинтов управления,
// (2) собственная точка доступа SoftAP для ПРЯМОЙ связи робот↔телефон без роутера,
// (3) онбординг домашнего Wi-Fi через страницу /wifi (сохранение в NVS, без правки secrets.h).
//
// Подключать ПОСЛЕ secrets.h (чтобы XIAO_API_TOKEN / XIAO_AP_* из secrets имели приоритет).
//
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>

// ── Настройки (переопределяются в secrets.h) ──
#ifndef XIAO_AP_SSID
#define XIAO_AP_SSID "XIAO-Robot"          // имя собственной точки доступа робота
#endif
#ifndef XIAO_AP_PASSWORD
#define XIAO_AP_PASSWORD "xiaorobot"       // >=8 символов (WPA2). СМЕНИ в secrets.h.
#endif
#ifndef XIAO_API_TOKEN
#define XIAO_API_TOKEN ""                  // пусто = авторизация ВЫКЛ (только LAN). Задай в secrets.h ПЕРЕД выходом в интернет.
#endif

extern WebServer server;                   // определён в xiao_cam_stream.ino

// ───────────────────────── токен-авторизация ─────────────────────────

static inline bool xiaoApiTokenSet() { return XIAO_API_TOKEN[0] != '\0'; }

// Сравнение почти-постоянного времени (не сливает длину/префикс по таймингу).
static inline bool xiaoConstEq(const String &a, const char *b) {
  const size_t lb = strlen(b);
  if (a.length() != lb) return false;
  uint8_t diff = 0;
  for (size_t i = 0; i < lb; ++i) diff |= (uint8_t)a[i] ^ (uint8_t)b[i];
  return diff == 0;
}

// true, если запрос авторизован (или авторизация выключена). Токен: ?token=… или заголовок X-Auth-Token.
static inline bool xiaoAuthOk(WebServer &srv) {
  if (!xiaoApiTokenSet()) return true;     // токен не задан → открыто (LAN/dev)
  if (srv.hasArg("token") && xiaoConstEq(srv.arg("token"), XIAO_API_TOKEN)) return true;
  if (srv.hasHeader("X-Auth-Token") && xiaoConstEq(srv.header("X-Auth-Token"), XIAO_API_TOKEN)) return true;
  return false;
}

// В начале защищаемого handler'а: `if (!xiaoRequireAuth(server)) return;`
static inline bool xiaoRequireAuth(WebServer &srv) {
  if (xiaoAuthOk(srv)) return true;
  srv.send(401, F("application/json; charset=utf-8"), F("{\"ok\":0,\"err\":\"unauthorized\"}"));
  return false;
}

// Зарегистрировать заголовок для чтения (вызвать до server.begin()).
static inline void xiaoAuthCollectHeaders() {
  static const char *keys[] = {"X-Auth-Token"};
  server.collectHeaders(keys, 1);
}

// ───────────────────────── SoftAP + онбординг ─────────────────────────

// Поднять собственную точку доступа. Вызывать ПОСЛЕ WiFi.mode(WIFI_AP_STA).
// HTTP-сервер слушает все интерфейсы → доступен на 192.168.4.1 (AP) и на STA-IP.
static inline IPAddress xiaoApStart() {
  WiFi.softAP(XIAO_AP_SSID, XIAO_AP_PASSWORD);
  return WiFi.softAPIP();                  // обычно 192.168.4.1
}

// Сохранённые онбордингом креды домашнего Wi-Fi (NVS, пространство "xiaonet").
static inline String xiaoWifiSavedSsid() {
  Preferences p;
  p.begin("xiaonet", true);
  String s = p.getString("ssid", "");
  p.end();
  return s;
}
static inline String xiaoWifiSavedPass() {
  Preferences p;
  p.begin("xiaonet", true);
  String s = p.getString("pass", "");
  p.end();
  return s;
}
static inline void xiaoWifiSave(const String &ssid, const String &pass) {
  Preferences p;
  p.begin("xiaonet", false);
  p.putString("ssid", ssid);
  p.putString("pass", pass);
  p.end();
}

// GET /wifi — страница онбординга (открыть на http://192.168.4.1/wifi после подключения к AP робота).
static void xiaoHandleWifiPage() {
  String h = F("<!doctype html><meta charset=utf-8>"
               "<meta name=viewport content='width=device-width,initial-scale=1'>"
               "<style>body{font-family:sans-serif;max-width:380px;margin:24px auto;padding:0 12px}"
               "input{width:100%;padding:8px;margin:6px 0;box-sizing:border-box}"
               "button{padding:10px 16px;font-size:15px}</style>"
               "<h2>XIAO-Robot — Wi-Fi</h2>"
               "<form action='/savewifi' method='get'>"
               "<label>Домашняя сеть (SSID, 2.4 ГГц)</label><input name='ssid' value='");
  h += xiaoWifiSavedSsid();
  h += F("'><label>Пароль</label><input name='pass' type='password'>"
         "<button type=submit>Сохранить и подключиться</button></form>"
         "<p>Робот перезагрузится и зайдёт в указанную сеть. Своя точка доступа "
         "останется доступной для прямой связи.</p>");
  server.send(200, F("text/html; charset=utf-8"), h);
}

// GET /savewifi?ssid=&pass= — сохранить в NVS и перезагрузиться.
static void xiaoHandleSaveWifi() {
  if (!server.hasArg("ssid")) {
    server.send(400, F("text/plain; charset=utf-8"), F("need ssid"));
    return;
  }
  xiaoWifiSave(server.arg("ssid"), server.arg("pass"));
  server.send(200, F("text/html; charset=utf-8"),
              F("<!doctype html><meta charset=utf-8><h3>Сохранено. Перезагрузка…</h3>"));
  delay(400);
  ESP.restart();
}

// Зарегистрировать маршруты онбординга (вызвать рядом с другими server.on в setup).
static inline void xiaoNetRegisterRoutes() {
  server.on("/wifi", HTTP_GET, xiaoHandleWifiPage);
  server.on("/savewifi", HTTP_GET, xiaoHandleSaveWifi);
}
