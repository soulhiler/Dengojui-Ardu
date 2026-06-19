# DIY-приводы с обратной связью: руки, ноги, кисти (2026)

> Глубокий обзор самосборных манипуляторов/ног на сервоприводах и BLDC с обратной связью:
> **практический оптимум** (что реально собрать) + **красивые нестандартные решения**
> (capstan, циклоидные/волновые редукторы, QDD-BLDC). Под проект «Деньгожуй» (ESP32 + Pi).
> Метод — глубокое исследование GitHub/Hackaday/arXiv по 6 направлениям, звёзды GitHub
> проверены через API (июнь 2026). Цены ориентировочные (часть вендоров отдаёт 403 на фетч).

## TL;DR — что брать

- **Оптимум «рука с обратной связью, дёшево»:** собрать **SO-ARM100/101** — 3D-печать + **5–6× Feetech STS3215 (~$15)** + плата шины + БП. ~$120–250, нативно в LeRobot.
- **Электроника управления:** **Waveshare General Driver Board (ESP32, ~$25)** — рулит до 253 bus-серв с обратной связью; либо **FE-URT-1 (~$14)** + Pi.
- **Красивое/необычное (вау-инженерия):** **Dummy-Robot** (FOC-контроллер на каждом моторе, 15k★), **capstan-приводы Aaed Musa** (нулевой люфт, backdrivable), **OpenQDD** (3D-печатный Mini-Cheetah-актуатор ~$247).
- **Нога:** bus-серво хватает только для **медленной лёгкой** ходьбы (≤~2 кг); для динамики/прыжков **обязателен BLDC-QDD** (moteus/qdd100, ODRI, AK80-9, OpenTorque).

---

## 1. Сервоприводы с обратной связью — выбор

Обратная связь: P=положение, V=скорость, C=ток, L=нагрузка, T=темп, U=напряжение.

| Серво | Момент (stall) | Питание | Энкодер | Обр. связь | Backdrive | Цена |
|---|---|---|---|---|---|---|
| **Feetech STS3215** | 19.5 кг·см@7.4V / 30@12V | 6–14V | магн. 12-бит (0.088°) | P,V,C,L,T,U | нет (1:345) | **~$15** |
| Feetech SCS0009/SCS15 | 2.3 / 15.6 кг·см | 4–8.4V | **потенциометр** | только P | нет | $9–21 |
| **Feetech SM45BL/85BL** (BLDC) | 4.4 / 8.3 Н·м @24V | 24V | магн. | P,V,C,L,T,U | частично | $98–180 |
| **Dynamixel XL330-M288** | 0.52 Н·м | 3.7–6V | магн. 12-бит | P,V,**C**,T,U | софт (current) | ~$27 |
| Dynamixel XL430 / MX-64 | 1.4 / 6.0 Н·м | 6–14V | магн. 12-бит | (XL430 без C) / MX64 с C | софт | $27 / $330 |
| Dynamixel AX-12A | 1.5 Н·м | 9–12V | потенциометр | P,L,T,U | нет | ~$45 |
| **Hiwonder LX-224/225** | 20 / 25 кг·см | 6–8.4V | потенциометр | P,T,U | нет | $22–33 |
| **Hiwonder HX-35H** (2-вал) | 35 кг·см | 3S | потенциометр (HM=магн.) | P,T,U | нет | $20–25 |
| **CubeMars AK80-9** (BLDC QDD) | ном.9 / пик 22 Н·м | 48V | магн. абс. | P,V,**C/момент**,T | **да** | ~$580 |
| **SimpleFOC + свой BLDC** | зависит | 8–30V+ | внеш. магн. (AS5048/5600) | **истинный FOC момент** | **да** | плата $10–40 |

**Выводы:**
- **Рука — Feetech STS3215** (цена/телеметрия/экосистема LeRobot). Премиум с токовым/комплаенс-контролем — **Dynamixel XL330** (дёшево) или XM430/MX64.
- **Нога — bus-серво НЕ backdrivable** (передаточное 1:345 → удар ломает зубья/PLA, а не амортизируется); под динамику нужен **BLDC+FOC** или low-ratio QDD.
- ⚠️ Грабли: заявленные 0.088° — потолок датчика, реальная точность ограничена **люфтом редуктора ~1°**; хобби bus-серво **перегреваются** под статикой и защитой режут момент до ~20% → закладывай запас; SCS/AX-12A — потенциометр (износ, нет тока).

### Минимальная электроника
- **ESP32 + Feetech/Hiwonder:** Waveshare **General Driver Board** (ESP32, до 253 серв + DC-моторы + IMU, 7–13V, ~$25) — её же советует докуменация LeRobot. Полудуплекс 1-wire уже разведён.
- **Pi + Feetech:** **FE-URT-1** (USB↔TTL, ~$14) или Waveshare Bus Servo Driver; библиотеки `FTServo_Python`/`scservo_sdk` или весь **LeRobot** (`pip install lerobot[feetech]`).
- **Dynamixel:** **U2D2** (~$32) + Power Hub + Dynamixel SDK.
- Голый UART без платы → нужен tri-state буфер 74HC241 (полудуплекс) или MAX485 (RS485).

---

## 2. Контроллеры FOC для BLDC (для QDD/ног и «умных» приводов)

| | ODrive | moteus / qdd100 | SimpleFOC |
|---|---|---|---|
| Что это | контроллер-плата | контроллер + **готовый QDD-актуатор** | библиотека + дешёвые платы |
| Момент | от мотора+редуктора | qdd100: 16 Н·м пик / 6 непр. | от gimbal-мотора |
| Backdrive | хороший | **отличный (true QDD)** | хороший, маломощный |
| Цена | Micro $79 / Pro $229 | moteus $79; qdd100 ~$430–540 | Mini €12 / Shield €15 |
| GitHub | 3 665★ | 1 185★ | 2 869★ |
| Фишка | макс. мощность | под квадропеды, CAN-FD | дешевле всех, ESP32/STM32 |

Самосбор QDD: gimbal/pancake BLDC + магн. энкодер **AS5048A** (14-бит) + 3D-печатный **планетарный 8:1** (backdrivable) или **циклоидный 10–11:1** (компактнее) + ODrive/SimpleFOC. Готовые рецепты: **OpenTorque** (~56 Н·м пик), **timxuti Integrated-Joint-Actuator** (циклоид 11:1), **OpenQDD** (~$247).

---

## 3. Готовые open-source РУКИ

| Проект | DOF | Привод | Реальная обр. связь | Цена | ★ | Лиценз. |
|---|---|---|---|---|---|---|
| **SO-ARM100/101** | 5–6 | STS3215 | ✅ магн. в каждом | $120–250 | 6 607 | Apache-2.0 |
| **Koch v1.1** | 5 | Dynamixel | ✅ | $250–350 | (в LeRobot) | MIT |
| **LeKiwi** (моб. манип.) | 6+база | STS3215+XL430 | ✅ | $300–500 | 1 328 | Apache-2.0 |
| **PAROL6** | 6 | шаговики +опц. FOC | опц. closed-loop, ~0.08 мм | $600–900 | 2 963 | GPL |
| **Dummy-Robot** | 6 | шаговики +**FOC на каждом** | ✅ настоящий | $300–600 | 15 105 | GPL-3 |
| **Faze4** | 6 | шаговики + **циклоиды** | open-loop | ~$700 | 864 | open |
| Annin **AR4** | 6 | шаговики+энкодеры | ⚠️ энкодеры есть, но **firmware open-loop** | ~$2000 | актив. | NC |
| **Thor** | 6 | шаговики | open-loop | $350–500 | ~1500 | CC-BY-SA |
| **BCN3D Moveo** | 5 | шаговики | open-loop | $300–500 | 1 884 | GPL-3 |
| ROBOTIS **OpenManipulator-X** | 4+1 | Dynamixel XM430 | ✅ заводская | $1500+ | 640 | Apache-2.0 |
| **XLeRobot** | 2×рука+база | STS3215 | ✅ | ~$660 | 5 248 | open |

**Топ-3 «оптимум»:** SO-101/SO-ARM100 → PAROL6 (точность) → Koch/OpenManipulator-X (Dynamixel).
**Топ-3 «красивое»:** Dummy-Robot (FOC на каждом моторе) → Faze4 (печатные циклоиды) → Thor (икона DIY).
⚠️ Важно: **AR4, Thor, Moveo при всей популярности работают open-loop** (у AR4 энкодеры есть в железе, но не задействованы).

---

## 4. Красивые нестандартные приводы/трансмиссии

| Решение | Что даёт | Проект | Backdrive |
|---|---|---|---|
| **Capstan / тросовый** | **нулевой люфт**, тихо, дёшево, с 3D-печатью лучше шестерён | [Aaed Musa Capstan-Drive](https://github.com/aaedmusa/Capstan-Drive), квадропед **CARA** | да, отлично |
| **3D-печатный циклоид** | момент компактно, ~1–7 угл.мин люфта | [Ironless-QDD](https://github.com/CKraft11/Ironless-QDD-Actuator) (~30 Н·м, ~$40–70), Faze4 | да (QDD) |
| **Strain wave (harmonic)** | высокая редукция компактно (TPU-флекссплайн) | [Bribro12](https://hackaday.io/project/187309), MakerWorld | слабо |
| **Дифференциальное запястье / тросовая кисть** | антропоморфность, безопасность | **Pollen Amazing Hand** (8-DOF, кит $89), Yale OpenHand, RUKA-v2 | да |
| **3D-печатный Mini-Cheetah QDD** | силовой контроль, backdrivable | **OpenQDD** (~$247, 16 Н·м), OpenTorque (~56 Н·м) | да |

**Топ «красиво и реально дома»:** capstan-привод Aaed Musa (эталон no-backlash+backdrivable, под BLDC+ODrive) и Pollen Amazing Hand (дешёвая дифф-кисть). Циклоид/strain-wave — для тех, кому нужен момент/редукция без покупных редукторов.
Сочетание с обратной связью: capstan/циклоид + **выходной энкодер AS5048A** + BLDC FOC = чистый torque control.

---

## 5. НОГИ (quadruped / biped)

| Проект | Тип | Привод | Когда |
|---|---|---|---|
| **Petoi Bittle/Nybble (OpenCat)** | quad | хобби-серво (PWM/bus) | новичку, медленно |
| **Mini Pupper** (MangDang) | quad 12-DOF | smart/feedback серво, ROS | учебный quad с ROS |
| **Stanford Pupper** | quad 12-DOF | хобби-серво | дёшево, RL-демо |
| **SpotMicro** | quad 12 | мощные PWM-серво | популярный, но open-loop |
| **Stanford Doggo / ODRI Solo** | quad | **BLDC QDD** | динамика, прыжки |
| **mjbots quad** | quad | **qdd100** | динамичный, CAN-FD |
| **CARA** (Aaed Musa) | quad 12 | **BLDC + capstan** | красивое, безредукторное |
| **Berkeley Humanoid Lite** | biped 6/ногу | **BLDC + печатные циклоиды** (~$188/$136) | оптимум «дешёвый BLDC», ~$4.3k, 1.4k★ |
| **ROBOTIS OP3** | biped 20-DOF | Dynamixel XM430 | надёжный quasi-static, ~$12k |
| **NimbRo-OP2X** | biped 18-DOF, 18 кг | Dynamixel | потолок bus-серв для ходьбы |
| **AsterisCrack BipedRobot** | biped 12-DOF | smart bus-серво + RL (Isaac) | DIY-биппед на bus-сервах |

**Ключевое правило (граница):**
- **Статика + медленная ходьба** → хватает smart bus-серв (STS3215 для ≤~2 кг; Dynamixel для 3.5–18 кг как OP3/NimbRo). Всё quasi-static.
- **Динамика / бег / прыжки / амортизация ударов** → **BLDC-QDD обязателен**: нужны пиковый момент, **backdrivability**, полоса токового контроля и плотность мощности, которых геар-серво не дают. Поэтому Berkeley Humanoid Lite и все динамичные квадропеды — на BLDC, а не на STS3215.

---

## 6. Рекомендации под «Деньгожуй»

**Старт (то, что просил — просто и с обратной связью):**
1. Купи **2–3× Feetech STS3215** (~$15) + **Waveshare bus-servo плата** (ESP32/USB) + БП 5–6 В.
2. Собери на бенче 2–3-DOF мини-руку (печать/кронштейны), освой шину, чтение угла/нагрузки/тока, обратную кинематику.
3. Масштабируй в полный **SO-ARM100** (допечатал каркас + до 6 серв) → готов под LeRobot/зрение позже.

> По пинам: вся шина серв = 1 UART через драйвер-плату; на боевой XIAO пинов нет → экспериментируй на отдельной плате/на Pi (см. `pinout-wiring-2026.md`).

**«Красивый» путь на потом (когда захочется вау):**
- Рука: повторить **Dummy-Robot** (FOC на каждом суставе) или **capstan-руку** (нулевой люфт).
- Кисть: **Pollen Amazing Hand** (дёшево, антропоморфно).
- Нога/динамика: **OpenQDD/OpenTorque** актуатор на **SimpleFOC/ODrive** → один сустав-стенд, затем квадропед типа **mjbots quad / ODRI Solo**.

**Чего НЕ делать:** не брать AR4/Thor/Moveo ради «обратной связи» (они open-loop); не пытаться сделать динамичную ногу на STS3215 (перегрев, не backdrivable).

---

## Источники (ключевые)

**Руки:** [SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100), [LeRobot](https://github.com/huggingface/lerobot), [Koch](https://huggingface.co/docs/lerobot/koch), [LeKiwi](https://github.com/SIGRobotics-UIUC/LeKiwi), [PAROL6](https://github.com/Source-Robotics/PAROL6-Desktop-robot-arm), [Dummy-Robot](https://github.com/peng-zhihui/Dummy-Robot), [AR4](https://github.com/Annin-Robotics/ar4-hmi), [Thor](https://github.com/AngelLM/Thor), [Moveo](https://github.com/BCN3D/BCN3D-Moveo), [Faze4](https://github.com/PCrnjak/Faze4-Robotic-arm), [OpenManipulator-X](https://github.com/ROBOTIS-GIT/open_manipulator), [XLeRobot](https://github.com/Vector-Wangel/XLeRobot)

**Трансмиссии:** [Aaed Musa Capstan-Drive](https://github.com/aaedmusa/Capstan-Drive), [CARA](https://www.aaedmusa.com/projects/cara), [Ironless-QDD](https://github.com/CKraft11/Ironless-QDD-Actuator), [strain wave (Hackaday)](https://hackaday.io/project/187309-3d-printable-strain-wave-gearbox-harmonic-drive), [Amazing Hand](https://huggingface.co/blog/pollen-robotics/amazing-hand), [Yale OpenHand](https://www.eng.yale.edu/grablab/pubs/ma_icra2013.pdf), [капстаны (Hackaday)](https://hackaday.com/2024/06/03/gears-are-old-and-busted-capstans-are-cool/)

**QDD/контроллеры:** [ODRI actuator](https://github.com/open-dynamic-robot-initiative/open_robot_actuator_hardware), [Ben Katz motorcontrol](https://github.com/bgkatz/motorcontrol), [OpenQDD](https://github.com/aaedmusa/OpenQDD-V1), [OpenTorque](https://hackaday.io/project/159404-opentorque-actuator), [moteus/mjbots](https://github.com/mjbots/moteus), [ODrive](https://github.com/odriverobotics/ODrive), [SimpleFOC](https://github.com/simplefoc/Arduino-FOC), [Tinymovr](https://github.com/tinymovr/Tinymovr), [CubeMars AK80-9](https://www.cubemars.com/product/ak80-9-v3-0-robotic-actuator.html)

**Ноги:** [Berkeley Humanoid Lite](https://github.com/HybridRobotics/Berkeley-Humanoid-Lite), [ROBOTIS OP3](https://emanual.robotis.com/docs/en/platform/op3/introduction/), [NimbRo OP](https://github.com/AIS-Bonn/humanoid_op_ros), [AsterisCrack BipedRobot](https://github.com/AsterisCrack/BipedRobot), [mjbots quad](https://hackaday.io/project/167845-mjbots-quad), [Stanford Doggo](https://arxiv.org/pdf/1905.04254)

**Сервы/электроника:** [STS3215 (RobotShop)](https://www.robotshop.com/products/feetech-12v-30kgcm-magnetic-encoding-servo-sts3215), [STS3215 независимый тест (Robonine)](https://robonine.com/testing-of-feetech-sts3215-servomotor-backlash-repeatability-and-torque/), [Dynamixel XL330](https://emanual.robotis.com/docs/en/dxl/x/xl330-m288/), [Hiwonder HX-35H](https://www.hiwonder.com/products/hx-35h), [Waveshare General Driver Board](https://www.waveshare.com/wiki/General_Driver_for_Robots), [FE-URT-1 / FTServo_Python](https://github.com/ftservo/FTServo_Python)

---

*Достоверность: звёзды GitHub проверены через API (июнь 2026). Цены — ориентир (многие вендоры
отдают 403 на автофетч; STS3215 «30 кг·см» — маркетинг, реально меньше + люфт ~1°). Спецификации
кросс-сверены по нескольким источникам; перед покупкой сверяйся с первоисточником.*
