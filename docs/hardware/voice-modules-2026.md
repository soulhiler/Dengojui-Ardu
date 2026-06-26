# Голосовые модули для роботов (2026)

> Обзор голосового стека для робота: захват → распознавание → понимание → синтез ответа, и **где
> что работает** (ESP32 / Raspberry Pi / Jetson / облако). Под проект «Деньгожуй» (ESP32-S3 +
> планируемый Pi; уже есть PDM-микрофон, аудио-выход MAX98357A на Pi). Метод — параллельное
> исследование по 4 направлениям. Дата: июнь 2026.

## Как устроен голосовой стек (3 слоя)

```
[МИКРОФОН] → wake word → [STT: речь→текст] → [ПОНИМАНИЕ: команда/intent/LLM] → действие → [TTS: текст→речь] → [ДИНАМИК]
```
Главный принцип: **разные слои живут на разном железе.** Чем «умнее» слой, тем мощнее нужен хост.

## TL;DR — карта по платформам

| Платформа | Что реально может | Чего НЕ может |
|---|---|---|
| **ESP32-S3 (сейчас)** | wake word + **фикс. команды оффлайн** (ESP-SR), ~200 команд EN/CN | свободная речь, качественный TTS, **русские команды** |
| **+ дешёвый модуль команд** (SEN0539) | plug-and-play команды EN/CN + динамик-ответ | то же ограничение (EN/CN, фикс. набор) |
| **Raspberry Pi 5** | полноценный STT (**Vosk/whisper.cpp, есть русский**), **Piper TTS** (рус+англ), ESPHome/HA | тяжёлый LLM локально |
| **Jetson Orin** | STT с GPU + **локальный LLM до ~4B** (диалог на борту) | — |
| **Облако** | максимум качества/языков, **LLM-диалог** (Claude/ChatGPT) | работа без интернета, приватность |

**Вывод:** wake-word + базовые команды — локально на ESP32; свободная речь / **русский** / TTS — на Pi; «поговорить с роботом» (LLM) — облако или Jetson.

---

## 1. Захват звука (микрофоны / массивы)

| Решение | Мик. | Аппаратный DSP | DOA | Интерфейс | Хост | Цена |
|---|---|---|---|---|---|---|
| **PDM-мик** (есть на XIAO Sense) | 1 | нет | нет | PDM | ESP32-S3 | — |
| **reSpeaker Lite** (XMOS XU316) | 2 | **AEC + шумоподавл.** | нет | I2S/USB | ESP32-S3/Pi | ~$25 (Kit $30) |
| **reSpeaker XVF3800** (XMOS) | 4 | **полный** (AEC, beamforming, denoise, dereverb) | **да** | I2S/USB | Pi/Jetson/ESP32 | ~$50–55 |
| ICS-43434 (свой массив) | 1/шт | нет | сам | I2S | ESP32/Pi | ~$6–7 |
| reSpeaker 2-Mic Pi HAT v2 | 2 | нет (кодек) | софт | I2S | **только Pi** | ~$10–13 |

⚠️ Старые reSpeaker **4-mic/6-mic Pi HAT** — Seeed **забросила DSP-софт**, это теперь «сырые» платы записи (DOA/beamforming пишешь сам). Matrix Voice — мёртв. INMP441 снят → замена **ICS-43434**.

**Эхоподавление (AEC):** если робот сам играет звук (динамик) — нужен AEC, у голого PDM его нет. reSpeaker Lite/XVF3800 дают AEC аппаратно.

## 2. Wake word + распознавание речи (STT)

| Движок | Где | Оффлайн | Языки | Что даёт |
|---|---|---|---|---|
| **ESP-SR** (WakeNet+MultiNet7) | **ESP32-S3** | ✅ | EN/CN (нет RU) | wake + до ~200 фикс. команд, $0 |
| microWakeWord | ESP32 (ESPHome) | ✅ | кастом | только wake word (3 одновременно) |
| Picovoice Porcupine+Rhino | MCU/Pi | ✅ | EN+ | wake + intent, но платно ($$$) |
| openWakeWord | Pi | ✅ | EN (тренир.) | wake word (движок HA) |
| **Vosk** | Pi (даже слабый) | ✅ | **20+ вкл. русский** | полный STT, small ~50 МБ/300 МБ RAM |
| **whisper.cpp** | Pi 5 / Jetson | ✅ | многояз. вкл. **рус** | полный STT, tiny ~15× RT на Pi5 |
| WhisperTRT | Jetson Orin | ✅ | многояз. | Whisper с TensorRT (быстро) |
| Whisper/Google/Azure API | облако | ❌ | 100+ | максимум качества, $0.006–0.017/мин |

**Потолок ESP32-S3:** ESP-SR распознаёт **заданный список команд** (текстом в конфиге, без обучения), но не свободную речь и **не русский**. Свободная речь / русский → Pi (Vosk/whisper.cpp) или облако.

## 3. Синтез речи (TTS — робот говорит)

| Движок | Где | Качество | Языки | Заметка |
|---|---|---|---|---|
| ESP-TTS (Espressif) | ESP32-S3 | средн. | **только китайский** ❌ | для нас не годится |
| espeak-ng | Pi/любой | низкое («робот») | 100+ вкл. рус | дёшево, ретро-голос |
| **Piper** ⭐ | Pi/Jetson | хорошее (нейро) | 30+ вкл. **рус** (`ru_RU/irina,dmitri`) | **лучший оффлайн для робота**, real-time на Pi 5, дефолт в HA |
| Coqui XTTS-v2 | Pi(CPU)/GPU | топ, клон голоса | 17 вкл. рус | тяжёлый, лицензия non-commercial |
| ElevenLabs / Azure / Google | облако | топ | рус ✅ | ~75–300 мс, платно |

⚠️ **Качественный TTS на самом ESP32 невозможен** (ESP-TTS только китайский) → синтез выносится на **Pi (Piper)** или облако. Воспроизведение — через **MAX98357A** (он у тебя уже есть, план — на Pi).

## 4. Дешёвые оффлайн-модули команд (standalone)

| Модуль | Команд | Язык | Интерфейс | Цена | Плюс/минус |
|---|---|---|---|---|---|
| **ESP-SR на ESP32-S3** ⭐ | ~200 | EN/CN | — (сам S3) | **$0** | бесплатно, но нет RU, нужен PSRAM+I2S-мик |
| **DFRobot SEN0539-EN** ⭐ | 121 + 17 своих | EN | UART/I2C | **$16.90** | plug-and-play, **мик+динамик на борту**, без сборки прошивок |
| Grove Offline (US516P6) | ~150 | EN/CN | UART | ~$16.50 | <100 мс; смена команд — пересборка прошивки |
| M5Stack ASR (CI1302) | 53 + до 300 | EN/CN/JP/KR | UART | ~$13–17 | дёшево/много, но команды через веб-тул вендора |
| Ai-Thinker VC-02 | до 150 | EN/CN | UART | **~$3–6** | минимум цены, доки китайские |
| Elechouse VR V3 | 7 активных | **любой звук** | UART | ~$25 | обучаемый под твой голос, но speaker-dependent |

Все эти модули — **EN/CN, фиксированные команды**. Для русских команд оффлайн — только путь через Pi (Vosk).

## 5. Фреймворки и LLM-диалог

- **ESPHome Voice Assistant + Home Assistant Assist** — ESP32-S3 как голосовой «спутник»: microWakeWord (on-device) → Whisper STT на Pi → intent/LLM → **Piper** TTS. Оффлайн через **Wyoming**-протокол. ✅ Идеально для ESP32-робота с мозгом на Pi.
- **Rhasspy / wyoming-satellite** — модульный оффлайн-стек, гибче, без HA.
- **Willow** (ESP32-S3-BOX) — быстро (≤500 мс), но без Wyoming и развитие вялое.
- **LLM-диалог («поговорить с роботом»):**
  - Простой путь: ESP32/Pi → STT → **облачный LLM (Claude/ChatGPT/Groq)** → TTS. Готовые проекты `KALO-ESP32-Voice-ChatGPT`, ESP32 Agent Dev Kit.
  - Локально: **Jetson Orin Nano Super (8 ГБ)** тянет LLM до ~4B (Llama 3.2 3B, Qwen 3B) → Whisper + Ollama + Piper = автономный «мозг».
  - В HA: заменить conversation-agent на LLM (нужен tool-calling для управления).

## 6. ⚠️ Русский язык — важно

- **Оффлайн русские команды на ESP32 — нельзя** (ESP-SR и все дешёвые модули EN/CN).
- **Русский появляется только на Pi/облаке:** STT — **Vosk** (есть русская модель) или whisper.cpp; TTS — **Piper** (`ru_RU/irina,dmitri`).
- Итог: хочешь по-русски говорить с роботом → это **Pi-уровень**, не ESP32.

## 7. Рекомендации под «Деньгожуй» (по этапам)

**Этап 0 — голос-команды сейчас, дёшево (ESP32-S3):**
- **ESP-SR на ESP32-S3 ($0)** + I2S/PDM-мик — английские команды оффлайн, событие прямо в прошивке. Самый дешёвый вход.
- Или **DFRobot SEN0539-EN ($16.90)** — plug-and-play, мик+динамик на борту, команда по UART. Если не хочется возиться.
- ⚠️ Только EN/CN, фикс. набор. Русского нет.

**Этап 1 — нормальный голос + русский + TTS (с Pi):**
- Микрофон **reSpeaker Lite (~$25)** (AEC, к ESP32 по I2S) или **XVF3800 (~$50)** если нужно дальнее поле/DOA.
- На Pi: **Vosk/whisper.cpp** STT (русский) + **Piper** TTS (русский) + **ESPHome/Home Assistant**.
- Ответ голосом — через **MAX98357A** (уже есть).

**Этап 2 — «поговорить с роботом» (диалог):**
- Облачный **LLM (Claude/ChatGPT)** как conversation-agent (быстрый старт, нужен интернет), либо локальный LLM на **Jetson** (автономно).

**DOA (повернуться к говорящему):** только **reSpeaker XVF3800** (аппаратный DOA); старые 4/6-mic HAT не брать.

**Что НЕ делать:** не ждать качественного TTS/свободной речи/русского от голого ESP32; не брать reSpeaker 4/6-mic Pi HAT ради DOA (DSP заброшен); USB-конференц-мики (DOA наружу не отдают).

---

## Источники

**Микрофоны/DSP:** [reSpeaker XVF3800 (Seeed)](https://www.seeedstudio.com/ReSpeaker-XVF3800-USB-Mic-Array-p-6488.html), [XVF3800 (CNX)](https://www.cnx-software.com/2025/07/29/respeaker-xmos-xvf3800-4-mic-array-board-features-esp32-s3-module-works-over-usb/), [reSpeaker Lite](https://www.seeedstudio.com/ReSpeaker-Lite-p-5928.html), [XMOS XVF3800](https://www.xmos.com/xvf3800), [ICS-43434 (Adafruit)](https://www.adafruit.com/product/6049), [2-Mic Pi HAT v2 wiki](https://wiki.seeedstudio.com/ReSpeaker_2_Mics_Pi_HAT/)

**Wake/STT:** [ESP-SR (Espressif)](https://github.com/espressif/esp-sr), [ESP-SR MultiNet docs](https://docs.espressif.com/projects/esp-sr/en/latest/esp32s3/speech_command_recognition/README.html), [Vosk](https://alphacephei.com/vosk/), [whisper.cpp сравнение](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026), [openWakeWord](https://github.com/dscripka/openWakeWord), [Picovoice pricing](https://picovoice.ai/pricing/), [WhisperTRT (Jetson)](https://github.com/NVIDIA-AI-IOT/whisper_trt)

**Дешёвые модули:** [DFRobot SEN0539-EN](https://wiki.dfrobot.com/sen0539-en/), [Grove Offline (Seeed)](https://wiki.seeedstudio.com/Grove-Offline-Voice-Recognition/), [M5Stack ASR CI1302](https://docs.m5stack.com/en/module/Module_ASR), [Ai-Thinker VC-02](https://docs.ai-thinker.com/en/voice_module/), [Elechouse VR V3](https://github.com/elechouse/VoiceRecognitionV3)

**TTS/фреймворки/LLM:** [Piper](https://github.com/rhasspy/piper) + [голоса (рус)](https://huggingface.co/rhasspy/piper-voices), [ESPHome Voice Assistant](https://esphome.io/components/voice_assistant/) + [microWakeWord](https://esphome.io/components/micro_wake_word/), [HA local assistant](https://www.home-assistant.io/voice_control/voice_remote_local_assistant/), [Wyoming satellite](https://github.com/rhasspy/wyoming-satellite), [Willow](https://heywillow.io/), [KALO-ESP32-Voice-ChatGPT](https://github.com/kaloprojects/KALO-ESP32-Voice-ChatGPT), [Jetson робот-мозг](https://thomasthelliez.com/blog/building-a-local-robot-brain-on-jetson-orin-nano-super/)

---

*Достоверность: ключевые факты (ESP-SR EN/CN-only + нет русского; Piper/Vosk — русский на Pi;
reSpeaker старые 4/6-mic с заброшенным DSP; XVF3800 DOA) кросс-сверены по нескольким источникам.
Цены 2026 — ориентир (часть страниц Seeed/Adafruit отдавали 403); проверяй на сайтах вендоров.*
