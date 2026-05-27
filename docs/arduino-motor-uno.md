# Arduino UNO + TB6612FNG (USB)

Отдельный контур для проверки моторов **без Wi‑Fi**: Arduino Uno, драйвер TB6612, управление с ПК по USB Serial.

Подробности по прошивке и проводам: **`arduino_motor_test/README.md`**.

## Состав

| Файл | Назначение |
|------|------------|
| `arduino_motor_test/arduino_motor_test.ino` | Прошивка: PWM, STBY, команды `L` / `R` / `stop` |
| `tools/uno_motor_web.py` | Веб‑панель с **круговым джойстиком** (~20 Гц) |
| `tools/start_uno_motor_panel.ps1` | Запуск панели (освобождает старые процессы на COM) |
| `tools/uno_motor_serial.py` | Терминальные команды и `--interactive` |
| `tools/uno_motor_canvas_bridge.py` | Мост Canvas → Serial (опционально) |
| `tools/start_uno_motor_canvas.ps1` | Запуск моста для Canvas |

## Быстрый старт

1. Собрать и прошить (из корня репозитория, порт свой):

   ```powershell
   tools\arduino-cli\arduino-cli.exe compile -b arduino:avr:uno -u -p COM3 arduino_motor_test
   ```

   Нужно ядро `arduino:avr` (`arduino-cli core install arduino:avr`).

2. Подключить **VM** (6–12 V) к TB6612, общий GND с Uno.

3. Запустить панель:

   ```powershell
   .\tools\start_uno_motor_panel.ps1 COM3
   ```

4. Открыть **http://127.0.0.1:8765/** — джойстик: вверх/вниз езда, влево/вправо поворот, отпускание = стоп.

Закройте **Serial Monitor** в Arduino IDE, иначе COM занят («Отказано в доступе»).

## Команды Serial (115200)

| Команда | Действие |
|---------|----------|
| `L 180 R 180` | Левый и правый мотор (пробелы обязательны) |
| `stop` | Стоп, STBY остаётся включённым |
| `stby 0` / `stby 1` | Выключить / включить драйвер |
| `x` | Аварийный стоп (нужен Reset на плате) |

## Пины

| TB6612 | Arduino UNO |
|--------|-------------|
| VCC | 5V |
| GND | GND |
| STBY | D9 |
| PWMA, AIN1, AIN2 | D3, D2, D4 (мотор A) |
| PWMB, BIN1, BIN2 | D5, D7, D8 (мотор B) |
| VM+/VM− | АКБ моторов; минус АКБ на GND Uno |

## Canvas (опционально)

В Cursor можно открыть canvas `uno-motor-control` (в проекте IDE, каталог `canvases/`) и мост:

```powershell
.\tools\start_uno_motor_canvas.ps1 COM3
```

Если Canvas подвисает IDE — используйте только веб‑панель.

## Зависимости

```bash
pip install pyserial
```
