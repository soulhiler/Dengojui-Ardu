#!/usr/bin/env python3
"""
ИИ-«мозг» робота (Фаза 4). Замкнутый контур: perceive → decide → govern → act.

Транспортно-нейтрален: говорит существующий HTTP-контракт платы
(`/telemetry`, `/capture`, `/drive` + опц. токен), прошивку менять не нужно.
Тот же код можно запускать на ПК-сервере или портировать на телефон.

Запуск (ПК в той же Wi-Fi, что и плата):
  py -3 brain/brain.py 192.168.1.50 --token СЕКРЕТ
  py -3 brain/brain.py 192.168.1.50 --recognizer brightness
  py -3 brain/brain.py 192.168.1.50 --dry-run     # решения без отправки
Контракт: docs/brain-api.md
"""
from __future__ import annotations

import argparse
import sys
import time

from perception import BrightnessRecognizer, DummyRecognizer
from planner import NamedSkillPlanner, reactive_policy, sequence_policy
from robot import RobotClient, RobotError
from safety import SafetyGovernor
from skills import SkillContext, build_default_registry


def build_recognizer(name: str):
    if name == "brightness":
        r = BrightnessRecognizer()
        if not r.available():
            print("brightness недоступен (нет Pillow) → откат на dummy", file=sys.stderr)
            return DummyRecognizer()
        return r
    return DummyRecognizer()


def main() -> int:
    ap = argparse.ArgumentParser(description="XIAO робот: ИИ-мозг (perceive/decide/act)")
    ap.add_argument("host", help="IP платы, напр. 192.168.1.50 (или host:port)")
    ap.add_argument("--token", default="", help="токен платы (если включён XIAO_API_TOKEN)")
    ap.add_argument("--recognizer", choices=["dummy", "brightness"], default="dummy")
    ap.add_argument("--planner", choices=["off", "patrol", "reactive"], default="off",
                    help="секвенсор навыков (Фаза 2): off=распознаватель напрямую; "
                         "patrol=скриптовый круг; reactive=explore + отворот по ToF")
    ap.add_argument("--max-speed", type=int, default=180)
    ap.add_argument("--stop-cm", type=int, default=20, help="0 = без УЗ-стопа в мозге")
    ap.add_argument("--interval", type=float, default=0.0, help="0 = безопасный по watchdog")
    ap.add_argument("--no-frame", action="store_true", help="не запрашивать /capture")
    ap.add_argument("--dry-run", action="store_true", help="печатать решения, не слать /drive")
    ap.add_argument("--once", action="store_true", help="один цикл и выход")
    args = ap.parse_args()

    client = RobotClient(args.host, token=args.token)
    rec = build_recognizer(args.recognizer)
    gov = SafetyGovernor(max_speed=args.max_speed, stop_cm=args.stop_cm)
    interval = args.interval if args.interval > 0 else gov.command_interval_s

    # Источник Intent: либо распознаватель напрямую (off), либо планировщик навыков.
    planner = None
    if args.planner != "off":
        reg = build_default_registry(recognizer=rec)
        if args.planner == "patrol":
            policy = sequence_policy([("forward", 3.0), ("turn_right", 1.0)],
                                     clock=time.monotonic)
        else:  # reactive: исследуем (explore), близко по ToF → отворот (avoid)
            policy = reactive_policy("explore", near_mm=350)
        planner = NamedSkillPlanner(reg, policy)

    print(
        "мозг: host=%s rec=%s max=%d stop_cm=%d interval=%.3fs%s"
        % (args.host, rec.name, gov.max_speed, gov.stop_cm, interval,
           " [DRY-RUN]" if args.dry_run else ""),
        file=sys.stderr,
    )

    last_perception = time.monotonic()
    try:
        while True:
            t_cycle = time.monotonic()
            try:
                tele = client.telemetry()
            except RobotError as e:
                if args.dry_run:
                    tele = {}
                else:
                    print("связь потеряна (%s) → аварийный стоп" % e, file=sys.stderr)
                    client.stop_quiet()
                    if args.once:
                        return 1
                    time.sleep(min(2.0, interval * 4))
                    continue

            frame = None
            if not args.no_frame:
                try:
                    frame = client.capture()
                except RobotError:
                    frame = None  # кадр не критичен — перцепция устареет → governor стопнет

            if planner is not None:
                intent = planner.tick(SkillContext(telemetry=tele, frame=frame))
            else:
                intent = rec.infer(frame, tele)
            now = time.monotonic()
            if intent.confident:
                last_perception = now

            left, right = gov.decide(intent, tele, now, last_perception)

            if args.dry_run:
                src = ("навык:%s" % planner.active) if planner is not None else rec.name
                print("L=%4d R=%4d  [%s]  %s  tele.safety=%s us=%s"
                      % (left, right, gov.last_reason, src,
                         tele.get("drive_safety"), tele.get("us_cm")))
            else:
                try:
                    client.drive(left, right)
                except RobotError as e:
                    print("drive не дошёл (%s) → стоп" % e, file=sys.stderr)
                    client.stop_quiet()

            if args.once:
                return 0
            dt = time.monotonic() - t_cycle
            if dt < interval:
                time.sleep(interval - dt)
    except KeyboardInterrupt:
        print("\nостановка по Ctrl+C", file=sys.stderr)
        return 0
    finally:
        client.stop_quiet()


if __name__ == "__main__":
    raise SystemExit(main())
