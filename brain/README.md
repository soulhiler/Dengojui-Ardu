# brain/ — ИИ-«мозг» робота (Фаза 4)

Замкнутый контур `perceive → decide → govern → act` поверх существующего
HTTP-контракта платы. Прошивку менять не нужно. Только stdlib
(Pillow — опционально для распознавателя `brightness`).

## Запуск

ПК в той же Wi-Fi, что и плата:

```bash
py -3 brain/brain.py 192.168.1.50                 # dummy-распознаватель
py -3 brain/brain.py 192.168.1.50 --token СЕКРЕТ  # если включён XIAO_API_TOKEN
py -3 brain/brain.py 192.168.1.50 --recognizer brightness
py -3 brain/brain.py 192.168.1.50 --planner reactive  # навыки: explore + отворот по ToF
py -3 brain/brain.py 192.168.1.50 --planner patrol    # скриптовый круг (forward/turn)
py -3 brain/brain.py 127.0.0.1 --dry-run --once   # решения без отправки (без платы)
```

## Архитектура «решать ↔ исполнять» (3T)

Граница A2: планировщик выбирает **навык-примитив** по имени → навык выдаёт
`Intent` → `SafetyGovernor` → `/drive`. Планировщик **моторов не касается**.
Политика выбора навыка подключаемая — `sequence` (секвенсор/BT-lite),
`reactive` (по сенсору) или **LLM** (`make_llm_policy`: модель называет навык;
`ask()` инъектируется, без сетевых зависимостей в `brain/`). Это шов под
Behavior Trees и LLM-планировщик — сами они здесь не реализованы.

## Состав

| Файл | Назначение |
|------|------------|
| `brain.py` | CLI + главный цикл, аварийный стоп при потере связи |
| `robot.py` | клиент платы (`/telemetry`, `/capture`, `/drive`, токен) |
| `perception.py` | `Recognizer` + `Dummy` / `Brightness` (модель — новой реализацией) |
| `safety.py` | `SafetyGovernor` — рефлексы > перцепция, темп ≤ watchdog, плавный регулятор скорости по фронтальному ToF (зеркало прошивки `safeSpeed`) |
| `skills.py` | примитивы-навыки (`Stop/Forward/Turn/Explore/Avoid`) + реестр по имени — «инструменты на границе решать↔исполнять» |
| `planner.py` | `NamedSkillPlanner` + политики (`sequence`/`reactive`/LLM-шов) — выбор навыка; задел под Behavior Trees / LLM-планировщик |
| `test_safety.py`, `test_skills.py` | юнит-тесты (без железа) |

## Тесты

```bash
py -3 -m unittest discover -s brain -p "test_*.py"
```

Контракт и модель безопасности: [`../docs/brain-api.md`](../docs/brain-api.md).
