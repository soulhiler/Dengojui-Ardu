# A/B-OTA с защитой от «кирпича» для ESP32-S3 — набросок

> Цель: обновлять прошивку робота по Wi-Fi так, чтобы **неудачное обновление не окирпичивало** —
> при сбое система сама откатывается на прошлую рабочую версию. Под нашу Arduino-прошивку
> (`xiao_cam_stream`, XIAO ESP32-S3 8 МБ, уже есть ArduinoOTA + поля `fw_version/fw_build`).
> Делать на **отдельной ветке**. Это набросок (дизайн + скелет), не финальный код.

## Принцип A/B
Во flash — **два слота приложения** (`app0`, `app1`) + служебная партиция `otadata` (хранит, какой слот
грузить). Новую прошивку пишем в **неактивный** слот, помечаем его загрузочным, перезагружаемся.
Если новая версия не прошла **самопроверку** — откат на прежний слот. Старая версия всегда цела.

```
flash:  [bootloader][otadata][app0 = текущая][app1 = новая]   ← OTA пишет в неактивный
boot:   bootloader читает otadata → грузит активный слот
fail:   self-test не прошёл / boot-loop → otadata ← прежний слот → грузимся со старой
```

## Шаг 1 — партиции (две app-партиции)
В Arduino выбрать **схему разделов с двумя app-слотами**. Для XIAO ESP32-S3 (8 МБ), например:
`Tools → Partition Scheme → "8M with spiffs (3MB APP/1.5MB SPIFFS)"` (там есть `app0`+`app1`).
FQBN: `...:PartitionScheme=app3M_fat9M_8MB` (или аналог с двумя app). **Без двух app-слотов A/B не работает.**

## Шаг 2 — передача прошивки (уже есть)
**ArduinoOTA** уже умеет: пишет в неактивный слот, ставит его загрузочным, ребутит. Оставляем его для
заливки (push с ПК), либо добавляем HTTP-pull (`esp_https_ota`/`HTTPUpdate`) для «скачать с URL». Механизм
A/B и так задействуется — нам нужно лишь добавить **анти-кирпич сверху**.

## Шаг 3 — анти-кирпич: самопроверка + откат

Логика на каждый загрузке:
1. **Рано в `setup()`** увеличить счётчик «неподтверждённых загрузок» этого слота (в NVS).
2. Если счётчик превысил порог (boot-loop) → переключить загрузку на **другой слот** и ребут (откат).
3. Прогнать **self-test** (Wi-Fi подключился, камера/привод инициализировались).
4. Self-test прошёл → **подтвердить** слот (сбросить счётчик; если бутлоадер с rollback — `esp_ota_mark_app_valid_cancel_rollback()`).

### Скелет (Arduino, работает со стоковым бутлоадером)
```cpp
#include "esp_ota_ops.h"
#include <Preferences.h>

static Preferences otaPrefs;
static const uint8_t kMaxUnconfirmedBoots = 3;

static void abOtaKey(char* k, size_t n) {
  const esp_partition_t* run = esp_ota_get_running_partition();
  snprintf(k, n, "b_%lx", (unsigned long)run->address);   // ключ привязан к слоту
}

// вызвать ПЕРВЫМ делом в setup()
void abOtaBootCheck() {
  char key[16]; abOtaKey(key, sizeof(key));
  otaPrefs.begin("abota", false);
  uint8_t boots = otaPrefs.getUChar(key, 0) + 1;
  otaPrefs.putUChar(key, boots);
  if (boots > kMaxUnconfirmedBoots) {                     // слот зациклился на ребутах
    const esp_partition_t* other = esp_ota_get_next_update_partition(NULL);
    if (other && esp_ota_set_boot_partition(other) == ESP_OK) {
      otaPrefs.putUChar(key, 0);
      otaPrefs.end();
      Serial.println(F("AB-OTA: boot-loop → откат на прошлый слот"));
      delay(100); ESP.restart();
    }
  }
  otaPrefs.end();
}

// вызвать ПОСЛЕ успешного self-test (Wi-Fi OK + камера OK + привод OK)
void abOtaMarkHealthy() {
  char key[16]; abOtaKey(key, sizeof(key));
  otaPrefs.begin("abota", false);
  otaPrefs.putUChar(key, 0);                              // подтвердили — счётчик в ноль
  otaPrefs.end();
  // если бутлоадер собран с CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE — официально подтвердить:
  const esp_partition_t* run = esp_ota_get_running_partition();
  esp_ota_img_states_t st;
  if (esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY) {
    esp_ota_mark_app_valid_cancel_rollback();
  }
}
```

### Куда вставить в нашу прошивку
- `abOtaBootCheck();` — **в самом начале `setup()`** (до тяжёлой инициализации).
- `abOtaMarkHealthy();` — **после** успеха: после `WiFi OK` (стр. ~1196) + `Camera OK` + `xiaoDriveInit()`.
  Так «здоровой» считается только версия, которая реально подняла сеть, камеру и привод.
- Что считать self-test'ом — выбираем сами: минимум Wi-Fi+камера; можно строже (мик, телеметрия).

## ⚠️ Честная оговорка (важно)
- **Полный аппаратный авто-откат** бутлоадера срабатывает только если бутлоадер собран с
  `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` (это **ESP-IDF** или Arduino-as-component с кастомным sdkconfig).
  Стоковый Arduino-бутлоадер этого по умолчанию **не включает** → официальный «pending-verify → rollback»
  не работает.
- Поэтому скелет выше делает **софт-откат на уровне приложения** (счётчик загрузок в NVS) — он работает
  со **стоковым** Arduino-бутлоадером и ловит главное: boot-loop новой версии → возврат на старую.
- Дополнительный слой: **RTC-watchdog бутлоадера** (`CONFIG_BOOTLOADER_WDT_ENABLE`, в IDF включён) ловит
  зависание ещё **до** старта приложения. В Arduino — зависит от сборки бутлоадера.
- Хочешь «правильный» аппаратный rollback → перейти на **ESP-IDF** (или Arduino-as-IDF-component) с
  включённым rollback + `esp_https_ota`. Это отдельный, больший шаг.

## Что это даёт нам
- Обновление по Wi-Fi без риска: плохая прошивка → робот сам вернётся на рабочую.
- Версии видны в `/telemetry` (`fw_version/fw_build`) — удобно проверять, что приехало/откатилось.
- Совместимо с текущим ArduinoOTA (заливка) — добавляем только анти-кирпич-логику.

## Порядок работ (отдельная ветка)
```bash
git checkout claude/russian-greeting-aN4wi
git checkout -b claude/ab-ota
```
1. Включить схему разделов с `app0`/`app1` (FQBN PartitionScheme).
2. Добавить `abOtaBootCheck()` / `abOtaMarkHealthy()` и self-test.
3. Тест: залить заведомо «сломанную» версию (например, с неверным Wi-Fi и краш-петлёй) → убедиться,
   что после N ребутов робот **сам откатился** на прошлую и снова в сети.
4. (Опц.) добавить HTTP-pull OTA с URL; (опц.) перейти на IDF-rollback для аппаратной гарантии.

## Тест-чеклист
- [ ] Две app-партиции в схеме (проверить `esp_ota_get_running_partition()` меняется после OTA).
- [ ] Норм. обновление → новая версия в `/telemetry`, `abOtaMarkHealthy` вызвался.
- [ ] «Сломанная» версия (краш до self-test) → авто-откат за ≤N ребутов, старая версия снова работает.
- [ ] Питание выдернули посреди OTA → грузится прежний слот (новый недописан, otadata не переключилась).

## Источники
- ESP-IDF [OTA / rollback](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/system/ota.html), [App rollback](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/bootloader.html)
- Текущий OTA — `ArduinoOTA` в `xiao_cam_stream/xiao_cam_stream.ino` (стр. ~1209+); идея A/B навеяна OpenC6 BIOS (но реализуем штатными средствами ESP-IDF/Arduino, без OpenC6 — он только под ESP32-C6).
