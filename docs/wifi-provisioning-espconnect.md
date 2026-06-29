# Внедрение Wi-Fi-провижининга (ESPConnect) — инструкция

> **Статус: ПРЕДЛОЖЕНИЕ (не реализовано).** Зафиксировано как вариант; реализовывать — отдельной
> веткой по решению. См. [proposals.md](proposals.md).

> Цель: убрать **хардкод Wi-Fi** в `secrets.h` и настраивать сеть **с телефона** через captive-portal
> (как окно входа в Wi-Fi в кафе). Делать на **отдельной ветке**, прошивку основной ветки не трогаем.
> Файлы: `xiao_cam_stream/xiao_cam_stream.ino` (блок Wi-Fi ~стр. 1158–1198), `xiao_cam_stream/secrets.h`.

## ⚠️ Сначала прочитай — выбор библиотеки

Текущая прошивка использует **синхронный** HTTP-сервер на порту 80. Это влияет на выбор:

| Библиотека | Сервер | Плюс | Минус |
|---|---|---|---|
| **MycilaESPConnect** (то, что просили) | **async** (ESPAsyncWebServer+AsyncTCP) | современный UI, Ethernet | тянет async-зависимости; **конфликт порта 80** с нашим sync-сервером → нужна аккуратная последовательность |
| **tzapu/WiFiManager** (рекомендую как проще) | **sync** (WebServer+DNSServer) | **тот же стек, что у нас**, почти drop-in, зрелая | UI попроще |

**Рекомендация:** если цель — просто «настроить Wi-Fi с телефона без перепрошивки», **tzapu/WiFiManager**
интегрируется чище (совпадает с синхронным сервером). **MycilaESPConnect** бери, если хочешь async-UI и
готов к доп. зависимостям + правильной последовательности (портал → connect → стоп портала → наш сервер).

Ниже — оба варианта. Логика встраивания одинаковая: **провижинимся ДО старта нашего HTTP-сервера**.

---

## 0. Завести соседнюю ветку

```bash
git checkout claude/russian-greeting-aN4wi          # базовая ветка
git checkout -b claude/wifi-espconnect              # соседняя ветка под этот функционал
```

## 1. Принцип интеграции (общий)

Заменяем блок `WiFiMulti` (строки ~1158–1198 в `setup()`) на:
1. Инициализация менеджера Wi-Fi (читает сохранённые креды из NVS).
2. Если кредов нет / не подключились за таймаут → **поднять AP + captive-portal**, ждать ввода сети с телефона.
3. После успешного `WL_CONNECTED` — **закрыть портал** и идти дальше: `hostname`, mDNS, OTA, BLE, наш HTTP-сервер
   (всё как сейчас, строки 1195+ остаются).
4. **Обратная совместимость:** если в `secrets.h` заданы `XIAO_WIFI_SSID_1`/`kWifiPass` — предзаполнить их
   как первичную попытку (чтобы существующая сеть подхватывалась без портала).

> Что НЕ меняем: камеру, привод, телеметрию, OTA, mDNS, весь HTTP-API (`/drive`, `/telemetry`, `/stream`).
> Меняется только способ получить Wi-Fi-креды.

---

## Вариант A — tzapu/WiFiManager (sync, проще) ✅

### Зависимость
Library Manager → **WiFiManager** (tzapu). Или `platformio.ini`: `lib_deps = tzapu/WiFiManager`.

### Код в `xiao_cam_stream.ino`

Вверху рядом с `#include <WiFi.h>`:
```cpp
#include <WiFiManager.h>   // tzapu
```

Заменить блок `WiFiMulti` (стр. ~1158–1198) на:
```cpp
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("xiao-cam");
  applyWifiPsFromFlag();
  WiFi.setAutoReconnect(true);
  WiFi.onEvent(wifiOnArduinoEvent, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);

  WiFiManager wm;
  wm.setConfigPortalTimeout(180);     // 3 мин на настройку, потом продолжить
  wm.setHostname("xiao-cam");

  // (опц.) обратная совместимость: подсунуть сеть из secrets.h при первом запуске
#ifdef XIAO_WIFI_SSID_1
  if (WiFi.SSID() == "") { /* нет сохранённых — попробуем secrets как стартовую */ }
#endif

  statusLedSet(StatusLed::WifiWait);
  // Подключается к сохранённой сети; если нет/не вышло — поднимает AP "Dengojui-Setup"
  // с captive-portal. Блокирует, пока не подключится или не истечёт таймаут.
  bool ok = wm.autoConnect("Dengojui-Setup");   // можно добавить пароль вторым аргументом

  if (!ok || WiFi.status() != WL_CONNECTED) {
    Serial.println(F("WiFi: портал по таймауту, нет подключения."));
    statusLedSet(StatusLed::ErrorWifi);
    gHttpServerStarted = false;
    telemetryBleInit();
    return;
  }
  Serial.print(F("WiFi OK, IP: ")); Serial.println(WiFi.localIP());
  Serial.print(F("SSID: "));        Serial.println(WiFi.SSID());
```
Дальше всё как было (строки 1200+: `telemetryBleInit()`, mDNS, OTA, старт HTTP-сервера).

`loop()` править **не нужно** (autoConnect блокирующий, портал живёт только во время настройки).
Сброс сохранённой сети при желании: `wm.resetSettings();` (например, по долгому нажатию кнопки).

---

## Вариант B — MycilaESPConnect (async, как просили)

### Зависимости
Library Manager: **MycilaESPConnect**, **ESPAsyncWebServer**, **AsyncTCP**.
`platformio.ini`:
```ini
lib_deps =
  mathieucarbou/MycilaESPConnect
  mathieucarbou/ESPAsyncWebServer
  mathieucarbou/AsyncTCP
```

### Код
Вверху:
```cpp
#include <ESPAsyncWebServer.h>
#include <MycilaESPConnect.h>
static AsyncWebServer espConnectServer(80);     // ⚠️ порт 80 — см. грабли ниже
static Mycila::ESPConnect espConnect(espConnectServer);
```

Заменить блок `WiFiMulti` на:
```cpp
  WiFi.setHostname("xiao-cam");
  WiFi.onEvent(wifiOnArduinoEvent, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);

  espConnect.setAutoRestart(false);
  espConnect.setBlocking(true);                 // ждать здесь, как сейчас ждём WiFiMulti
  // AP-имя портала и (опц.) пароль; читает сохранённую сеть из NVS сам
  espConnect.begin("xiao-cam", "Dengojui-Setup");

  if (espConnect.getState() != Mycila::ESPConnect::State::NETWORK_CONNECTED) {
    Serial.println(F("WiFi: портал/таймаут без подключения."));
    statusLedSet(StatusLed::ErrorWifi);
    gHttpServerStarted = false;
    telemetryBleInit();
    return;
  }
  espConnect.end();                             // ⚠️ ОБЯЗАТЕЛЬНО освободить порт 80
  Serial.print(F("WiFi OK, IP: ")); Serial.println(WiFi.localIP());
```
> Точный API сверь с README библиотеки (имена методов меняются между версиями):
> https://mathieu.carbou.me/MycilaESPConnect/

### ⚠️ Грабли MycilaESPConnect (главное)
- **Конфликт порта 80.** Портал поднимает свой async-сервер на 80. Наш основной HTTP-сервер — тоже 80.
  Решение: портал работает **только во время провижининга** (Wi-Fi ещё не подключён), затем `espConnect.end()`
  освобождает порт, и **дальше** стартует наш сервер. Не запускай оба одновременно.
- Если решишь оставить async-портал постоянно (для смены сети на ходу) — придётся переводить весь HTTP-API
  на ESPAsyncWebServer (большой рефактор). Для начала — не надо.
- +Память: async-стек тянет RAM; на XIAO с PSRAM ок, но проверь сборку.

---

## 2. Чистка secrets.h (опционально)

- Можно **оставить** `secrets.h` как «стартовую сеть» (предзаполнение) — удобно дома.
- Или убрать Wi-Fi из `secrets.h`, оставив там только `XIAO_OTA_PASSWORD`. Тогда сеть всегда задаётся порталом.
- `#define XIAO_WIFI_SSID_1/2` в `.ino` (стр. 76–80, дефолтные сети) можно удалить — их заменяет провижининг.

## 3. UX (как пользоваться)

1. Прошил/первый запуск → робот поднимает Wi-Fi **«Dengojui-Setup»**.
2. Телефоном подключаешься к ней → **сама открывается страница** настройки.
3. Выбираешь свою сеть, вводишь пароль → робот сохраняет и подключается.
4. Дальше — как обычно (IP в Serial, `http://xiao-cam.local/`). Смена сети — повтор портала (или кнопка-сброс).

## 4. Тест-чеклист

- [ ] Чистый старт (стёртые креды): поднимается AP-портал, телефон видит сеть, страница открывается.
- [ ] Ввод сети → подключается, в Serial «WiFi OK, IP …».
- [ ] Камера/`/stream`, `/drive`, `/telemetry`, mDNS, OTA — работают как раньше.
- [ ] (Вар. B) после connect **нет** конфликта порта 80 (вызван `espConnect.end()`).
- [ ] Перезагрузка с сохранённой сетью → подключается **без портала**.
- [ ] Потеря сети → переподключение (autoReconnect) или повторный портал по таймауту.

## 5. Коммит и PR

```bash
git add xiao_cam_stream/ docs/
git commit -m "feat(wifi): провижининг Wi-Fi через captive-portal (ESPConnect/WiFiManager)"
git push -u origin claude/wifi-espconnect
```
Затем — черновой PR на соседнюю ветку.

## Источники
- [MycilaESPConnect](https://mathieu.carbou.me/MycilaESPConnect/) (async, как спрашивали)
- [tzapu/WiFiManager](https://github.com/tzapu/WiFiManager) (sync, проще под нашу прошивку)
- Текущий код Wi-Fi — `xiao_cam_stream/xiao_cam_stream.ino` (блок `WiFiMulti`, ~1158–1198), `secrets.h.example`
