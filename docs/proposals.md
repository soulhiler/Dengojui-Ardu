# Предложения и идеи (не реализовано)

> Реестр **вариантов/предложений** по проекту «Деньгожуй»: задокументированы как опции на будущее,
> но **в код не внедрены**. Реализовывать — отдельными ветками по решению. Это не план и не обязательство.

## Прошивка / софт

| Предложение | Что даёт | Статус | Документ |
|---|---|---|---|
| **Wi-Fi-провижининг (ESPConnect / WiFiManager)** | настройка Wi-Fi с телефона (captive-portal) без перепрошивки и хардкода `secrets.h` | предложение | [wifi-provisioning-espconnect.md](wifi-provisioning-espconnect.md) |
| **A/B-OTA с анти-кирпич** | обновление по Wi-Fi с авто-откатом на рабочую версию при сбое | предложение | [ab-ota-esp32s3.md](ab-ota-esp32s3.md) |

## Железо / архитектура (дорожная карта)

Подробные исследования и этапы — в [`docs/hardware/`](hardware/README.md). Ключевые направления как опции:
- **Компаньон-компьютер** (Pi Zero 2 W / Orange Pi Zero 2W) для зрения — [hardware/candidate-1-budget-compute.md](hardware/candidate-1-budget-compute.md).
- **Сенсоры** (ToF/LiDAR/IMU/INA3221) — [hardware/sensors-2026.md](hardware/sensors-2026.md).
- **Драйвер 6WD + питание/BMS** — [hardware/power-and-battery-2026.md](hardware/power-and-battery-2026.md).
- **Голос и LLM-«сознание»** (диалог + управление) — [hardware/llm-brain-2026.md](hardware/llm-brain-2026.md), детальный [Этап 1](hardware/stage1-voice-llm.md).
- **DIY-приводы** (руки/ноги на серво с обратной связью) — [hardware/diy-actuators-2026.md](hardware/diy-actuators-2026.md).

---

*Как работаем: пока пункт здесь — это «вариант». Когда решаем делать — заводим отдельную ветку
(`claude/<feature>`), реализуем, тестируем, и только потом мёржим в основную.*
