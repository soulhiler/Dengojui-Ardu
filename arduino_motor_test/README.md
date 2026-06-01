# Тест моторов: Arduino UNO + TB6612FNG (USB)



Управление **с ПК по USB** (Serial 115200), без Wi‑Fi.



Полное описание раздела репозитория: **`docs/arduino-motor-uno.md`**.



## Провода



| TB6612 | Arduino UNO |

|--------|-------------|

| VCC | 5V |

| GND | GND |

| STBY | D9 |

| PWMA, AIN1, AIN2 | D3, D2, D4 |

| PWMB, BIN1, BIN2 | D5, D7, D8 |

| VM | аккумулятор моторов 6–12 V |

| AO1/AO2, BO1/BO2 | моторы |



Минус аккумулятора VM — на **GND** Arduino.

## OLED + напряжение

| OLED | UNO |
|------|-----|
| GND, VCC | GND, 5V |
| D0, D1 | D13, D11 |
| CS, DC, **RES** | D10, **D12**, **D6** |

**RES → D6 обязательно** (сброс программой). Если RES оставить только на 5V, дисплей часто не инициализируется.

Текст на экране + строка **drv SSD1306** или после смены константы — **SH1106** (см. `OLED_USE_SH1106` в `.ino`).

VM+ через делитель **20k / 10k** на **A0** (на экране «VM x.x V»).


## Прошивка



**Arduino IDE:** плата *Arduino Uno*, файл `arduino_motor_test.ino` → Загрузить.



**arduino-cli** (из корня репозитория):



```powershell

tools\arduino-cli\arduino-cli.exe compile -b arduino:avr:uno -u -p COM3 arduino_motor_test

```



## Веб‑панель (джойстик)



```powershell

.\tools\start_uno_motor_panel.ps1 COM3

```



Браузер: **http://127.0.0.1:8765/**



- Круговой джойстик: **Вперёд / Назад / Влево / Вправо** подписаны на круге.

- Плавное изменение скорости в реальном времени (~20 команд/с).

- Ползунок и пресеты максимальной скорости (40–255).

- Отпускание ручки — `stop`.



Перед запуском закройте Serial Monitor в IDE.



## Терминал



```bash

py -3 tools/uno_motor_serial.py COM3 --interactive

```



```bash

py -3 tools/uno_motor_serial.py COM3 --l 120 --r 120

py -3 tools/uno_motor_serial.py COM3 --stop

```



## Команды прошивки



`L <скорость> R <скорость>` — от −255 до 255 (с пробелами: `L 180 R 180`).



`stop` · `stby 0|1` · `x` (авария, затем Reset на Uno).



Колёса при первом тесте лучше поднять.


