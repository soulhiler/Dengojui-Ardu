# LLM-«сознание» робота: диалог + управление (2026)

> Как сделать робота, с которым можно **разговаривать**, и у которого LLM — не чат-бот, а
> **разум**: он слышит, видит, думает, **управляет телом** (function-calling) и помнит. Два пути:
> облачный мозг (умнее, нужен интернет) и локальный на борту (автономный, слабее). Под проект
> «Деньгожуй» (ESP32-S3 + MAX98357A-динамик уже есть; камера; перспектива Pi/Jetson).
> Метод — параллельное исследование облака и edge. Дата: июнь 2026.

## Концепция: робот как embodied-агент

«Сознание» = **embodied voice agent**, а не болталка. LLM получает голос (+картинку с камеры),
рассуждает и **вызывает функции робота** (`ехать`, `повернуться`, `смотреть`, `читать датчики`),
затем отвечает голосом. Разделение ролей:

```
   ТЕЛО (рефлексы)         ЧУВСТВА/ОРКЕСТРАЦИЯ        РАЗУМ (сознание)
   ESP32-S3               Raspberry Pi (или сам      LLM (облако или
   моторы, энкодеры,  ←→  ESP32-S3 для голоса):  ←→  Jetson локально):
   IMU, ToF, серво        аудио I/O, камера,         слышит/говорит,
   watchdog 450мс         MCP-tools, безопасность     думает, вызывает
                                                      tools, видит, помнит
```

## Два пути

| | **A. Облачный мозг** ⭐ | **B. Локальный мозг** |
|---|---|---|
| Где LLM | OpenAI gpt-realtime / Gemini Live / Claude | Jetson Orin Nano (или RK3588) на борту |
| Ум | **высокий** (frontier) | средний (модели ≤3–8B) |
| Латентность диалога | **~200–300 мс** (speech-to-speech) | ~5–8 с на Pi5, sub-5с на Jetson |
| Tool-calling (управление) | **надёжный** (~76% BFCL) | слабый у 3B (~35% BFCL) |
| Интернет | **нужен** | **не нужен (автономно)** |
| Русский | нативно | нужны Vosk/Piper RU + мультиязычная модель |
| Цена | ~$0.05–0.46/мин диалога | $249 (Jetson) разово, дальше $0 |
| Приватность | данные в облаке | полностью локально |

**Вывод:** для «идеального» диалога и надёжного управления — **облако** (Путь A). Для автономности
без интернета — **локально** (Путь B), но смирись с более слабой моделью и задержкой. Можно **гибрид**.

---

## Путь A — облачный мозг (рекомендуется)

**Ядро:** realtime **speech-to-speech** модель (нет двойной сериализации аудио↔текст → быстро):
- **OpenAI gpt-realtime** (WebRTC): аудио вход $100/1M / выход $200/1M токенов (≈$0.06/$0.24 за мин);
  реальный диалог **$0.18–0.46/мин**, с prompt-caching **$0.05–0.10/мин**; **~200–300 мс**;
  поддерживает **function calling**, **remote MCP**, **image input** («что видишь?»).
- **Google Gemini Live** (WebSocket): аудио-вход **в ~10× дешевле** ($1/1M), нативное **видео-вход**,
  barge-in (перебивание), function calling. Сильная альтернатива при упоре на бюджет/зрение.
- **Claude** — лучший детерминированный function calling + MCP (300+ коннекторов) как «мозг» в каскаде.

**Минимальный голосовой контур может крутиться прямо на ESP32-S3** (без Pi!): прошивка стримит
Opus-аудио по WebRTC/WebSocket в realtime-модель, та вызывает tools робота. Прообразы — **ElatoAI**
(OpenAI Realtime на ESP32-S3) и **xiaozhi-esp32** (MCP-управление телом). То есть «поговорить с
роботом + он едет» реально на том, что у тебя **уже есть** (ESP32-S3 + MAX98357A) + облачный API.
Pi нужен, когда добавляешь локальное зрение/память/оркестрацию.

## Путь B — локальный мозг (автономно)

**Jetson Orin Nano Super (8 ГБ, $249, 67 TOPS)** — практичный edge-LLM:
- Llama 3.2 **3B ≈ 28 tok/s**, 1B ≈ 47, 7–8B ≈ 14–18 tok/s (Ollama/llama.cpp Q4, узкое место — память).
- Локальный **VLM** (зрение): moondream/qwen2.5-vl-3b/gemma3-4b — **~0.2–1 FPS** (медленно, для «опиши кадр» ок).
- Полный оффлайн-голос: **WhisperTRT** (STT, ~3× быстрее) + локальный LLM (Ollama) + **Piper** (TTS).
- Сквозная задержка: Pi5 ~5–8 с; Jetson — быстрее, sub-5с реально с потоковой подачей по предложениям.
- ⚠️ Маленькие модели **слабо вызывают функции** (3B ~35% BFCL против ~76% у топовых) → надёжно только
  для **узкого набора команд**, не для свободного агентизма. Дообучение/жёсткая схема помогает.

**RK3588 (Orange Pi/Radxa, ~$115)** — дешевле, но локальный LLM **хрупкий**: только W8A8, тулчейн
rkllm вне Ollama, sub-3B на 7–17 tok/s, 7B ~3–4 tok/s. Для серьёзного edge-LLM Jetson лучше.

**Реальный пример (Jarvis-home на Orin Nano Super):** Whisper base.en + Gemma 4B Q4 + Piper + YOLO11n →
**~3–4 с** кнопка→ответ, занято **~4.6 ГБ из 7.4**. Грабли из практики (важно):
- ⚠️ **Over-current троттлинг:** в MAXN под нагрузкой Jetson ругается «throttled due to over-current» —
  нужен **сильный баррел-БП (≥4–5 А)**, не USB-C; иначе тормозит/выключается.
- ⚠️ **7–8B не влезают комфортно** в 8 ГБ (OOM при квантовании/инференсе) → держись **1–4B**, ограничивай
  контекст. **Тепло:** при долгой нагрузке нужен активный кулер (иначе скачет латентность).
- ⚠️ Тулчейн: новые модели иногда без CUDA в Ollama → переходят на llama.cpp; ручная возня с шаблонами.

> ⚠️ NVIDIA **GR00T** — это **не** «LLM, который дёргает функции», а VLA-политика (выдаёт моторные
> действия). Для диалога+управления нужен именно **LLM + tool-calling** (Путь A/B), GR00T — отдельная тема.

---

## «Руки»: tool-calling / MCP

Оборачиваешь команды прошивки в **tools** и публикуешь их как **локальный MCP-сервер на Pi**
(или прямо в realtime-сессию). Любая облачная модель будет их вызывать:
```
drive(left, right)      stop()              turn(degrees)
get_telemetry()         look() / describe_scene()   set_led(...) / say(...)
```
MCP — вендор-нейтрально: один сервер подойдёт OpenAI/Gemini/Claude.

## Зрение (VLM) — лениво

Снимай кадр **только по tool-вызову** `look()`/`describe_scene()` (не стримь видео постоянно —
экономия токенов). Простейше — **image input прямо в realtime-сессии** (gpt-realtime) или нативное
видео Gemini Live. Для точной детекции объектов по тексту без дообучения — **Grounding DINO/OWL-ViT**
(на Pi/облаке).

## ⚠️ Безопасность — главное

LLM **может галлюцинировать** → нельзя доверять ему «сырые» команды моторов. Слой защиты — **на
Pi/ESP32, не в облаке**:
- **Клампинг скоростей** (velocity bounds), **allowlist** команд, валидация tool-вызовов (как
  `before_tool_call` в ROSClaw).
- **Watchdog/стоп при потере связи** — у нас уже есть (привод стопается за 450 мс без команд).
- Финальное «обрезание» опасных команд делает прошивка, а не модель.

## Память и персона

- **System prompt = характер робота** (персона) + правила безопасности.
- Долговременная память: **vector store** (Mem0/Zep/Qdrant) — после каждой реплики извлекаешь факты,
  топ-k по relevance×recency инъектишь в system prompt. Это и даёт ощущение «личности/памяти».

## Русский язык

- **Облако (Путь A): русский нативно** (gpt-realtime/Gemini понимают и говорят по-русски).
- **Локально (Путь B):** нужны **Vosk** (STT RU) + **Piper** (`ru_RU/irina,dmitri`, TTS RU) +
  мультиязычная маленькая модель (Qwen2.5 знает русский). Сложнее и слабее, чем облако.

## Железо под это

| Узел | Что | У нас |
|---|---|---|
| Тело | ESP32-S3 (моторы/датчики/watchdog) | ✅ есть |
| Динамик (TTS) | MAX98357A I²S | ✅ есть |
| Микрофон | PDM (есть) → лучше **reSpeaker Lite (AEC)** / XVF3800 (DOA) | частично |
| Оркестратор/зрение | Raspberry Pi (5 для локального; Zero 2W хватит для облачного голоса) | план |
| Локальный мозг (опц.) | **Jetson Orin Nano Super $249** | если автономно |
| Камера | CSI Pi / камера ESP32 | ✅/план |

## Готовые проекты-прообразы

- **xiaozhi-esp32** — MCP-голосовой чатбот на ESP32-S3, мульти-управление через MCP (прообраз «тела на MCP»).
- **ElatoAI** — OpenAI Realtime API на ESP32-S3 (Opus по WebSocket) — готовый realtime-голос на железе.
- **ROSClaw** (arXiv 2603.26997) — 8 ROS2-tools + hook валидации (velocity bounds/allowlist) — образец безопасного слоя.
- **bob_llm / ROS-LLM (Auromix)** — динамические Python-функции как LLM-tools, OpenAI-совместимый API, Qdrant-память (если перейдёшь на ROS2).
- **KALO-ESP32-Voice-ChatGPT** — каскад STT→LLM→TTS на ESP32 с облаком.

## Рекомендация под «Деньгожуй» (по этапам)

**Этап 1 — заговорить (дёшево, на том, что есть):**
ESP32-S3 + **MAX98357A** (динамик) + микрофон + **облачный gpt-realtime/Gemini Live** по WebRTC +
MCP-tools (`drive/stop/turn/get_telemetry`). По образцу **ElatoAI/xiaozhi-esp32**. Pi не обязателен.
Робот говорит, понимает, едет по команде. Русский — из коробки.

**Этап 2 — дать «глаза» и память:** добавить **Pi** (оркестратор): камера → `describe_scene()` через
VLM, локальный MCP-сервер, память (Mem0/Qdrant), персона в system prompt. Микрофон — **reSpeaker Lite** (AEC).

**Этап 3 — автономность (опц.):** **Jetson Orin Nano Super** с локальным стеком (WhisperTRT + Qwen/Llama 3B
+ Piper) → работает без интернета. Слабее облака, но приватно и оффлайн. Или **гибрид**: локально —
быстрые команды, в облако — сложные рассуждения.

**Всегда:** слой безопасности (клампинг/allowlist/watchdog) на Pi/ESP32 — LLM не управляет моторами напрямую.

---

## Источники

**Облако/realtime:** [OpenAI gpt-realtime](https://openai.com/index/introducing-gpt-realtime/), [Realtime+MCP гайд](https://bibigpt.co/en/blog/posts/openai-gpt-realtime-api-mcp-audio-video-guide-2026), [стоимость/мин](https://callsphere.ai/blog/vw2c-openai-realtime-cost-per-minute-math-2026), [Gemini Live API](https://ai.google.dev/gemini-api/docs/live-api), [Claude advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use), [Realtime vs каскад (латентность)](https://www.retellai.com/blog/how-real-time-voice-ai-works-stt-llm-tts)
**Локально:** [Jetson Orin Nano Super](https://developer.nvidia.com/blog/nvidia-jetson-orin-nano-developer-kit-gets-a-super-boost/), [tiny-LLM бенчмарк Jetson](https://www.smolhub.com/posts/jetson-nano-super-benchmark-non-reasoning/), [WhisperTRT](https://github.com/NVIDIA-AI-IOT/whisper_trt), [Piper](https://github.com/rhasspy/piper), [RK3588 rkllm](https://github.com/airockchip/rknn-llm), [RK3588 vs Jetson](https://ieeker.com/rk3588-vs-jetson-orin-nano/)
**Tool-calling/безопасность:** [BFCL leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html), [ROSClaw (arXiv)](https://arxiv.org/html/2603.26997), [NASA-JPL ROSA](https://github.com/nasa-jpl/rosa), [ROS-LLM](https://github.com/Auromix/ROS-LLM)
**Прообразы:** [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32), [ElatoAI (OpenAI cookbook)](https://cookbook.openai.com/examples/voice_solutions/running_realtime_api_speech_on_esp32_arduino_edge_runtime_elatoai), [bob_llm](https://github.com/bob-ros2/bob_llm)
**Память/зрение:** [Mem0 long-term memory](https://mem0.ai/blog/long-term-memory-ai-agents), [VLM в робототехнике](https://robocloud-dashboard.vercel.app/learn/blog/vision-language-models)

См. также: [voice-modules-2026.md](voice-modules-2026.md) (микрофоны/STT/TTS), [research-boards-2026.md](research-boards-2026.md) (Pi/Jetson).

---

*Достоверность: ключевые факты (S2S ~200–300 мс vs каскад 600–800 мс; gpt-realtime цены; Jetson 3B
~28 tok/s; малые модели слабый tool-calling ~35% BFCL; GR00T ≠ LLM-агент) кросс-сверены по нескольким
источникам. Часть страниц (OpenAI/Google/NVIDIA/HF) отдавали 403 — цифры из согласующихся вторичных
источников; цены проверяй на офиц. pricing-страницах перед внедрением.*
