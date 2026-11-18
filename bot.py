#!/usr/bin/env python3
"""
Telegram-бот для отслеживания автобусов №150 и №170с:
Дом (д. Богатырёво, ул. Полесская, 7) <-> В Город (Малиновка / Космонавтов).

Работает на стандартной библиотеке Python (не требует установки pip-пакетов!).
Поддерживает Windows, macOS и Linux.

Настройка (выберите любой способ):
  1) Создайте файл bot_config.json рядом с bot.py (по образцу bot_config.example.json)
  2) Либо задайте переменные окружения TELEGRAM_BOT_TOKEN и (опционально) WEB_APP_URL

Тест без токена:
    python bot.py --test
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime

import update_schedule

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule_data.json")
CONFIG_FILE = os.path.join(BASE_DIR, "bot_config.json")


def load_config() -> dict:
    cfg = {
        "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "web_app_url": os.environ.get("WEB_APP_URL", "").strip(),
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
            if not cfg["telegram_bot_token"]:
                cfg["telegram_bot_token"] = str(file_cfg.get("telegram_bot_token", "")).strip()
            if not cfg["web_app_url"]:
                cfg["web_app_url"] = str(file_cfg.get("web_app_url", "")).strip()
        except Exception as e:
            print(f"Ошибка чтения bot_config.json: {e}")
    return cfg


def load_schedule() -> dict:
    if not os.path.exists(SCHEDULE_FILE):
        update_schedule.build_schedule()
    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_day_type(dt: datetime) -> str:
    wd = dt.weekday()  # 0=Пн .. 4=Пт, 5=Сб, 6=Вс
    if wd == 5:
        return "saturday"
    if wd == 6:
        return "sunday"
    return "workdays"


def get_day_label(day_type: str) -> str:
    return {
        "workdays": "Будни (Пн–Пт)",
        "saturday": "Суббота",
        "sunday": "Воскресенье",
    }.get(day_type, day_type)


def time_to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def minutes_to_hhmm(total_min: int) -> str:
    norm = total_min % 1440
    return f"{norm // 60:02d}:{norm % 60:02d}"


def format_direction_message(direction_key: str, now: datetime = None) -> str:
    if now is None:
        now = datetime.now()

    data = load_schedule()
    day_type = get_day_type(now)
    dir_data = data["directions"][direction_key]
    walk_min = dir_data["walk_minutes"]
    trips = dir_data["trips"][day_type]

    now_min = now.hour * 60 + now.minute
    upcoming = []
    for t in trips:
        dep_min = time_to_minutes(t["dep"])
        diff_min = dep_min - now_min
        if diff_min >= -1:
            upcoming.append((diff_min, dep_min, t))

    header_icon = "🏠 ➔ 🏙️" if direction_key == "to_school" else "🏙️ ➔ 🏠"
    lines = [
        f"{header_icon} *{dir_data['title']}*",
        f"📍 _{dir_data['subtitle']}_",
        f"🕒 Сейчас: *{now.strftime('%H:%M')}* ({get_day_label(day_type)})",
        f"🚶 Пешком до остановки: *{walk_min} мин*",
        "",
    ]

    if not upcoming:
        lines.append("🌙 На сегодня все рейсы по этому направлению завершены.")
        return "\n".join(lines)

    shown = 0
    for diff_min, dep_min, t in upcoming:
        if shown >= 4:
            break
        leave_min = diff_min - walk_min
        leave_clock = minutes_to_hhmm(dep_min - walk_min)

        if diff_min < int(walk_min * 0.45):
            status = "⚫ _Только если вы уже у остановки_"
        elif leave_min <= 0:
            if diff_min >= int(walk_min * 0.75):
                status = "🟡 *ПОРА ВЫХОДИТЬ!*"
            else:
                status = "🔴 *ПОТОРОПИТЕСЬ (быстрым шагом)!*"
        elif leave_min <= 5:
            status = f"🟡 *Выходить в {leave_clock}* (через *{leave_min} мин*)"
        else:
            status = f"🟢 Выходить в *{leave_clock}* (через {leave_min} мин)"

        stop_info = t["stop_from"]
        arr_stop = "Ост. «Ст. м. Малиновка»" if direction_key == "to_school" else "Ост. «Богатырёво»"
        arr_str = f"\n   🏁 Прибытие: {arr_stop} в *{t['arr']}*" if t.get("arr") else ""
        lines.append(
            f"🚌 *Автобус {t['route']}* — в *{t['dep']}* (через *{max(0, diff_min)} мин*)\n"
            f"   📍 {stop_info}{arr_str}\n"
            f"   {status}\n"
        )
        shown += 1

    return "\n".join(lines)


def format_target_arrival_message(target_hhmm: str, direction_key: str, now: datetime = None) -> str:
    if now is None:
        now = datetime.now()

    data = load_schedule()
    day_type = get_day_type(now)
    dir_data = data["directions"][direction_key]
    walk_before_min = dir_data["walk_minutes"]
    walk_after_min = (
        data["directions"]["to_home"]["walk_minutes"]
        if direction_key == "to_school"
        else data["directions"]["to_school"]["walk_minutes"]
    )

    target_min = time_to_minutes(target_hhmm)
    trips = dir_data["trips"][day_type]

    enriched = []
    for t in trips:
        dep_min = time_to_minutes(t["dep"])
        arr_min = time_to_minutes(t["arr"]) if t.get("arr") else dep_min + 15
        door_min = arr_min + walk_after_min
        enriched.append({
            **t,
            "dep_min": dep_min,
            "arr_min": arr_min,
            "door_min": door_min,
            "door_clock": minutes_to_hhmm(door_min),
            "leave_clock": minutes_to_hhmm(dep_min - walk_before_min),
        })

    fitting_door = [t for t in enriched if t["door_min"] <= target_min]
    fitting_stop = [t for t in enriched if t["arr_min"] <= target_min]
    base_list = fitting_door if fitting_door else fitting_stop
    picked = list(reversed(base_list[-2:]))

    dest_label = "на Космонавтов" if direction_key == "to_school" else "дома (Богатырёво)"
    header_icon = "🏠 ➔ 🏙️" if direction_key == "to_school" else "🏙️ ➔ 🏠"

    lines = [
        f"🎯 *Быть {dest_label} к {target_hhmm}* ({get_day_label(day_type)})",
        f"{header_icon} Направление: *{dir_data['title']}*",
        "",
    ]

    if not picked:
        lines.append(f"К {target_hhmm} рейсов ещё нет (первый автобус приезжает позже).")
        return "\n".join(lines)

    for idx, t in enumerate(picked):
        tag = "✅ *Вариант 1 (Вовремя)*" if idx == 0 else "🛡️ *Вариант 2 (С запасом)*"
        lines.append(
            f"{tag}\n"
            f"   🚌 Автобус *{t['route']}* — сесть в *{t['dep']}*\n"
            f"   📍 {t['stop_from']}\n"
            f"   🚶 Выход из дома/места в *{t['leave_clock']}*\n"
            f"   🏁 На остановке в *{t['arr']}* ➔ {dest_label} в *{t['door_clock']}*\n"
        )

    return "\n".join(lines)


def format_full_day_schedule(now: datetime = None) -> str:
    if now is None:
        now = datetime.now()
    data = load_schedule()
    day_type = get_day_type(now)

    lines = [f"📅 *Полное расписание на сегодня ({get_day_label(day_type)})*\n"]
    for key, icon in [
        ("to_school", "🏠 ➔ 🏙️ В Город (ост. «Богатырёво»):"),
        ("to_home", "🏙️ ➔ 🏠 Домой (ост. «Ст. м. Малиновка»):"),
    ]:
        trips = data["directions"][key]["trips"][day_type]
        formatted = [f"`{t['dep']}` ({t['route']})" for t in trips]
        lines.append(f"*{icon}*")
        lines.append(", ".join(formatted))
        lines.append("")

    return "\n".join(lines)


def tg_request(token: str, method: str, payload: dict) -> dict:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=ctx, timeout=35) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_time_input(text: str):
    m = re.match(r"^\s*(\d{1,2})[:.\s-](\d{2})\s*$", text)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mn <= 59:
        return f"{h:02d}:{mn:02d}"
    return None


def setup_menu_button(token: str, web_app_url: str):
    if not web_app_url:
        return
    try:
        tg_request(
            token,
            "setChatMenuButton",
            {
                "menu_button": {
                    "type": "web_app",
                    "text": "Автобусы",
                    "web_app": {"url": web_app_url},
                }
            },
        )
        print(f"[OK] Кнопка Mini App привязана: {web_app_url}")
    except Exception as e:
        print(f"[!] Не удалось установить кнопку Mini App: {e}")


def run_bot(token: str, web_app_url: str = ""):
    print("Telegram-бот запущен! Нажмите Ctrl+C для остановки.")
    setup_menu_button(token, web_app_url)

    kb_rows = [
        [{"text": "🏠 ➔ 🏙️ В Город"}, {"text": "🏙️ ➔ 🏠 Домой"}],
        [{"text": "🎯 Быть к времени"}, {"text": "📅 Все рейсы"}],
        [{"text": "🔄 Обновить расписание"}],
    ]
    if web_app_url:
        kb_rows.append([{"text": "📱 Открыть веб-приложение", "web_app": {"url": web_app_url}}])

    keyboard = {
        "keyboard": kb_rows,
        "resize_keyboard": True,
        "is_persistent": True,
    }

    last_direction_by_chat = {}
    offset = 0

    while True:
        try:
            res = tg_request(token, "getUpdates", {"offset": offset, "timeout": 25})
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1

                # Обработка нажатий на inline-кнопки (смена направления в расчете "Быть к ЧЧ:ММ")
                cb = upd.get("callback_query")
                if cb:
                    cb_id = cb["id"]
                    cb_data = cb.get("data", "")
                    cb_msg = cb.get("message") or {}
                    chat_id = cb_msg.get("chat", {}).get("id")
                    msg_id = cb_msg.get("message_id")
                    if cb_data.startswith("plan:") and chat_id and msg_id:
                        _, dir_key, hhmm = cb_data.split(":", 2)
                        other_dir = "to_home" if dir_key == "to_school" else "to_school"
                        other_label = "🏙️ ➔ 🏠 Показать Домой" if other_dir == "to_home" else "🏠 ➔ 🏙️ Показать В Город"
                        inline_kb = {
                            "inline_keyboard": [
                                [{"text": other_label, "callback_data": f"plan:{other_dir}:{hhmm}"}]
                            ]
                        }
                        tg_request(
                            token,
                            "editMessageText",
                            {
                                "chat_id": chat_id,
                                "message_id": msg_id,
                                "text": format_target_arrival_message(hhmm, dir_key),
                                "parse_mode": "Markdown",
                                "reply_markup": inline_kb,
                            },
                        )
                    tg_request(token, "answerCallbackQuery", {"callback_query_id": cb_id})
                    continue

                msg = upd.get("message") or {}
                chat_id = msg.get("chat", {}).get("id")
                text = (msg.get("text") or "").strip()
                if not chat_id or not text:
                    continue

                inline_markup = None
                parsed_hhmm = parse_time_input(text)

                if "город" in text.lower() or text == "/city" or text == "/school":
                    last_direction_by_chat[chat_id] = "to_school"
                    reply = format_direction_message("to_school")
                elif "домой" in text.lower() or text == "/home":
                    last_direction_by_chat[chat_id] = "to_home"
                    reply = format_direction_message("to_home")
                elif parsed_hhmm:
                    dir_key = last_direction_by_chat.get(
                        chat_id,
                        "to_school" if datetime.now().hour < 12 else "to_home",
                    )
                    other_dir = "to_home" if dir_key == "to_school" else "to_school"
                    other_label = "🏙️ ➔ 🏠 Показать Домой" if other_dir == "to_home" else "🏠 ➔ 🏙️ Показать В Город"
                    reply = format_target_arrival_message(parsed_hhmm, dir_key)
                    inline_markup = {
                        "inline_keyboard": [
                            [{"text": other_label, "callback_data": f"plan:{other_dir}:{parsed_hhmm}"}]
                        ]
                    }
                elif "быть к" in text.lower():
                    reply = (
                        "🎯 *К какому времени нужно быть на месте?*\n"
                        "Просто отправьте время сообщением (например, `16:40` или `08:30`), "
                        "и я сразу подберу 2 лучших варианта!"
                    )
                elif "все рейсы" in text.lower() or text == "/all":
                    reply = format_full_day_schedule()
                elif "обновить" in text.lower() or text == "/update":
                    update_schedule.build_schedule()
                    reply = "✅ База расписания автобусов №150 и №170с успешно обновлена!"
                else:
                    default_dir = "to_school" if datetime.now().hour < 12 else "to_home"
                    last_direction_by_chat[chat_id] = default_dir
                    reply = (
                        "👋 *Привет! Автобусы №150 и №170с*\n"
                        "Маршрут: *д. Богатырёво ↔ Малиновка (Космонавтов)*\n"
                        "_Подсказка: напишите время вроде `16:40`, чтобы узнать, на какой автобус сесть._\n\n"
                        + format_direction_message(default_dir)
                    )

                payload = {
                    "chat_id": chat_id,
                    "text": reply,
                    "parse_mode": "Markdown",
                    "reply_markup": inline_markup if inline_markup else keyboard,
                }
                tg_request(token, "sendMessage", payload)

        except KeyboardInterrupt:
            print("\nБот остановлен.")
            break
        except Exception as e:
            print(f"Ошибка сети: {e}, повтор через 3 сек...")
            time.sleep(3)


if __name__ == "__main__":
    if "--test" in sys.argv:
        print("=" * 60)
        print(format_direction_message("to_school"))
        print("=" * 60)
        print(format_direction_message("to_home"))
        print("=" * 60)
        print(format_target_arrival_message("16:40", "to_school"))
        print("=" * 60)
    else:
        cfg = load_config()
        if not cfg["telegram_bot_token"]:
            print("Токен Telegram-бота не найден!")
            print("Создайте файл bot_config.json рядом с bot.py (образец в bot_config.example.json)")
            print("или задайте переменную окружения TELEGRAM_BOT_TOKEN.")
            print("\nТестовый вывод расписания прямо сейчас:\n")
            print(format_direction_message("to_school"))
        else:
            run_bot(cfg["telegram_bot_token"], cfg["web_app_url"])
