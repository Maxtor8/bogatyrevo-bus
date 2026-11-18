#!/usr/bin/env python3
"""
Telegram-бот для отслеживания автобусов №150 и №170с:
Дом (д. Богатырёво, ул. Полесская, 7) <-> В Город (Малиновка).

Работает на стандартной библиотеке Python (не требует установки pip-пакетов!).
Поддерживает Windows, macOS и Linux.

Возможности:
  - Ближайшие автобусы (В Город / Домой) с кнопками постановки напоминаний
  - Напоминания из веб-приложения по deep-link (/start r_c_150_1639_10)
  - Двойное уведомление: за 5 минут до выхода и ровно в момент выхода
  - Калькулятор «Быть на месте к ЧЧ:ММ»
  - Ежедневное автообновление расписания в 06:00

Настройка:
  1) Создайте файл bot_config.json рядом с bot.py (по образцу bot_config.example.json)
  2) Либо задайте переменные окружения TELEGRAM_BOT_TOKEN и (опционально) WEB_APP_URL
"""

import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta

import update_schedule

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule_data.json")
CONFIG_FILE = os.path.join(BASE_DIR, "bot_config.json")
REMINDERS_FILE = os.path.join(BASE_DIR, "reminders.json")
REMINDERS_LOCK = threading.Lock()


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


def route_to_slug(route: str) -> str:
    return "170s" if "170" in route else "150"


def slug_to_route(slug: str) -> str:
    return "170с" if slug == "170s" else slug


def get_stop_name(direction_key: str, route: str) -> str:
    if direction_key == "to_school":
        return "Ост. «Богатырёво»"
    if "170" in route:
        return "Ост. «Ст. м. Малиновка» (ProStore)"
    return "Ост. «Ст. м. Малиновка»"


def get_upcoming_trips(direction_key: str, now: datetime = None) -> list:
    if now is None:
        now = datetime.now()
    data = load_schedule()
    day_type = get_day_type(now)
    dir_data = data["directions"][direction_key]
    walk_min = dir_data["walk_minutes"]
    trips = dir_data["trips"][day_type]
    now_min = now.hour * 60 + now.minute

    result = []
    for t in trips:
        dep_min = time_to_minutes(t["dep"])
        diff_min = dep_min - now_min
        if diff_min >= -1:
            result.append({
                **t,
                "dep_min": dep_min,
                "diff_min": diff_min,
                "leave_min": diff_min - walk_min,
                "leave_clock": minutes_to_hhmm(dep_min - walk_min),
                "walk_min": walk_min,
            })
    return result


def format_direction_message(direction_key: str, now: datetime = None) -> tuple:
    """Возвращает (text, inline_keyboard_rows) для выбранного направления."""
    if now is None:
        now = datetime.now()

    data = load_schedule()
    day_type = get_day_type(now)
    dir_data = data["directions"][direction_key]
    walk_min = dir_data["walk_minutes"]
    upcoming = get_upcoming_trips(direction_key, now)

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
        return "\n".join(lines), []

    dir_code = "c" if direction_key == "to_school" else "h"
    inline_rows = []
    for t in upcoming[:4]:
        diff_min = t["diff_min"]
        leave_min = t["leave_min"]
        leave_clock = t["leave_clock"]

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

        # Кнопка напоминания, если время выхода ещё не наступило
        if leave_min >= 1 and len(inline_rows) < 3:
            hhmm = t["dep"].replace(":", "")
            r_slug = route_to_slug(t["route"])
            inline_rows.append([{
                "text": f"🔔 Напомнить: {t['route']} в {t['dep']} (выход {leave_clock})",
                "callback_data": f"rem:{dir_code}:{r_slug}:{hhmm}:{walk_min}",
            }])

    return "\n".join(lines), inline_rows


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

    dest_label = "в Городе" if direction_key == "to_school" else "дома"
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


# ======================== СИСТЕМА НАПОМИНАНИЙ ========================

def _load_reminders_unlocked() -> list:
    if not os.path.exists(REMINDERS_FILE):
        return []
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_reminders_unlocked(items: list):
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def parse_reminder_code(code: str):
    """Парсит код формата r_<dir>_<route>_<HHMM>_<walk> или rem:<dir>:<route>:<HHMM>:<walk>."""
    m = re.match(r"^(?:r|rem)[:_]([ch])[:_](150|170s)[:_](\d{4})[:_](\d{1,2})$", code.strip())
    if not m:
        return None
    dir_code, route_slug, hhmm, walk_str = m.groups()
    h, mn = int(hhmm[:2]), int(hhmm[2:])
    if not (0 <= h <= 23 and 0 <= mn <= 59):
        return None
    walk_min = max(1, min(45, int(walk_str)))
    return {
        "direction": "to_school" if dir_code == "c" else "to_home",
        "route": slug_to_route(route_slug),
        "dep": f"{h:02d}:{mn:02d}",
        "walk_min": walk_min,
    }


def create_reminder(chat_id: int, direction_key: str, route: str, dep_hhmm: str, walk_min: int, now: datetime = None) -> tuple:
    """Создаёт напоминание (за 5 мин до выхода и в момент выхода). Возвращает (text, inline_keyboard)."""
    if now is None:
        now = datetime.now()

    h, m = map(int, dep_hhmm.split(":"))
    dep_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    # Если это время сегодня уже прошло больше чем на 2 минуты, ставим на завтра
    if (dep_dt - now).total_seconds() < -120:
        dep_dt += timedelta(days=1)

    leave_dt = dep_dt - timedelta(minutes=walk_min)
    warn_dt = leave_dt - timedelta(minutes=5)
    stop_name = get_stop_name(direction_key, route)
    dir_title = "В Город" if direction_key == "to_school" else "Домой"

    if now >= leave_dt:
        mins_left = max(0, int((dep_dt - now).total_seconds() // 60))
        return (
            f"⚠️ *Выходить нужно уже прямо сейчас!*\n"
            f"🚌 Автобус *{route}* ({dir_title}) отправляется в *{dep_hhmm}* (осталось ~*{mins_left} мин*).\n"
            f"📍 {stop_name}",
            [],
        )

    rem_id = f"{chat_id}_{direction_key}_{route_to_slug(route)}_{dep_hhmm.replace(':', '')}"
    warn_already_passed = now >= warn_dt

    new_item = {
        "id": rem_id,
        "chat_id": chat_id,
        "direction": direction_key,
        "dir_title": dir_title,
        "route": route,
        "dep": dep_hhmm,
        "dep_date": dep_dt.strftime("%Y-%m-%d"),
        "stop_from": stop_name,
        "walk_min": walk_min,
        "warn_ts": int(warn_dt.timestamp()),
        "leave_ts": int(leave_dt.timestamp()),
        "dep_ts": int(dep_dt.timestamp()),
        "warn_sent": warn_already_passed,
        "leave_sent": False,
        "warn_clock": warn_dt.strftime("%H:%M"),
        "leave_clock": leave_dt.strftime("%H:%M"),
    }

    with REMINDERS_LOCK:
        items = _load_reminders_unlocked()
        items = [x for x in items if x.get("id") != rem_id]
        items.append(new_item)
        _save_reminders_unlocked(items)

    day_hint = " (завтра)" if dep_dt.date() > now.date() else ""
    warn_line = (
        f"⏳ Предупреждение за 5 мин пропущено (до выхода меньше 5 мин)\n"
        if warn_already_passed
        else f"⏳ Предупрежу в *{new_item['warn_clock']}* (за 5 мин до выхода)\n"
    )
    msg = (
        f"🔔 *Напоминание поставлено!*\n"
        f"🚌 Автобус *{route}* ({dir_title}) — отпр. в *{dep_hhmm}*{day_hint}\n"
        f"📍 {stop_name}\n\n"
        f"{warn_line}"
        f"🚶 Напомню выйти в *{new_item['leave_clock']}* (запас {walk_min} мин)"
    )
    inline_kb = [[{"text": "❌ Отменить это напоминание", "callback_data": f"delrem:{rem_id}"}]]
    return msg, inline_kb


def format_user_reminders(chat_id: int) -> tuple:
    now_ts = int(time.time())
    with REMINDERS_LOCK:
        items = _load_reminders_unlocked()
        active = [x for x in items if x.get("chat_id") == chat_id and not x.get("leave_sent") and x.get("dep_ts", 0) > now_ts]

    if not active:
        return (
            "⏰ *У вас сейчас нет активных напоминаний.*\n"
            "Нажмите *«🏠 ➔ 🏙️ В Город»* или *«🏙️ ➔ 🏠 Домой»* и выберите нужный рейс кнопкой 🔔 под сообщением (или на сайте).",
            [],
        )

    lines = ["⏰ *Ваши активные напоминания:*\n"]
    inline_kb = []
    for item in sorted(active, key=lambda x: x["leave_ts"]):
        lines.append(
            f"• 🚌 *{item['route']}* ({item['dir_title']}) в *{item['dep']}* — "
            f"выход в *{item['leave_clock']}*"
        )
        inline_kb.append([{
            "text": f"❌ Отменить {item['route']} в {item['dep']}",
            "callback_data": f"delrem:{item['id']}",
        }])
    return "\n".join(lines), inline_kb


def delete_reminder(chat_id: int, rem_id: str) -> bool:
    with REMINDERS_LOCK:
        items = _load_reminders_unlocked()
        filtered = [x for x in items if not (x.get("chat_id") == chat_id and x.get("id") == rem_id)]
        changed = len(filtered) != len(items)
        if changed:
            _save_reminders_unlocked(filtered)
        return changed


def reminder_worker(token: str):
    """Фоновый поток, который проверяет напоминания каждые 5 секунд."""
    while True:
        try:
            now_ts = int(time.time())
            to_send = []
            with REMINDERS_LOCK:
                items = _load_reminders_unlocked()
                kept = []
                dirty = False
                for item in items:
                    if item.get("leave_sent") or now_ts > item.get("dep_ts", 0) + 300:
                        dirty = True
                        continue

                    if not item.get("warn_sent") and now_ts >= item.get("warn_ts", 0) and now_ts < item.get("leave_ts", 0):
                        item["warn_sent"] = True
                        dirty = True
                        to_send.append((
                            item["chat_id"],
                            f"⏳ *Через 5 минут пора выходить!*\n"
                            f"🚌 Автобус *{item['route']}* ({item['dir_title']}) в *{item['dep']}*\n"
                            f"📍 {item['stop_from']}\n"
                            f"🚶 Время выхода: *{item['leave_clock']}* (запас {item['walk_min']} мин)",
                        ))

                    if not item.get("leave_sent") and now_ts >= item.get("leave_ts", 0):
                        item["leave_sent"] = True
                        dirty = True
                        to_send.append((
                            item["chat_id"],
                            f"🚶 *ПОРА ВЫХОДИТЬ!*\n"
                            f"🚌 Автобус *{item['route']}* ({item['dir_title']}) отправляется в *{item['dep']}*\n"
                            f"📍 {item['stop_from']} (пешком {item['walk_min']} мин)",
                        ))
                        # После отправки финального сигнала удаляем напоминание
                        continue

                    kept.append(item)

                if dirty:
                    _save_reminders_unlocked(kept)

            for chat_id, msg_text in to_send:
                try:
                    tg_request(token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": msg_text,
                        "parse_mode": "Markdown",
                    })
                except Exception as send_err:
                    print(f"[!] Ошибка отправки напоминания в чат {chat_id}: {send_err}")
        except Exception as e:
            print(f"[!] Ошибка reminder_worker: {e}")

        time.sleep(5)


# ======================== TELEGRAM API & LOOP ========================

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


def setup_bot_info(token: str, web_app_url: str):
    try:
        me = tg_request(token, "getMe", {})
        username = me.get("result", {}).get("username", "")
        if username:
            print(f"[OK] Бот авторизован: @{username}")
    except Exception as e:
        print(f"[!] Не удалось получить getMe: {e}")

    if web_app_url:
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
    setup_bot_info(token, web_app_url)

    worker = threading.Thread(target=reminder_worker, args=(token,), daemon=True)
    worker.start()

    kb_rows = [
        [{"text": "🏠 ➔ 🏙️ В Город"}, {"text": "🏙️ ➔ 🏠 Домой"}],
        [{"text": "🎯 Быть к времени"}, {"text": "⏰ Мои напоминания"}],
        [{"text": "📅 Все рейсы"}, {"text": "🔄 Обновить базу"}],
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

    def get_saved_update_date() -> str:
        try:
            return load_schedule().get("updated_at", "")[:10]
        except Exception:
            return ""

    last_auto_update_date = get_saved_update_date()
    last_retry_ts = 0.0

    while True:
        try:
            now_dt = datetime.now()
            today_str = now_dt.strftime("%Y-%m-%d")
            if now_dt.hour >= 6 and last_auto_update_date != today_str and (time.time() - last_retry_ts) >= 300:
                try:
                    print(f"[{now_dt.strftime('%Y-%m-%d %H:%M')}] Плановое обновление расписания в 06:00...")
                    update_schedule.build_schedule()
                    last_auto_update_date = today_str
                except Exception as upd_err:
                    last_retry_ts = time.time()
                    print(f"[!] Ошибка автообновления расписания: {upd_err}")

            res = tg_request(token, "getUpdates", {"offset": offset, "timeout": 25})
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1

                # Обработка inline-кнопок
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
                        tg_request(token, "editMessageText", {
                            "chat_id": chat_id,
                            "message_id": msg_id,
                            "text": format_target_arrival_message(hhmm, dir_key),
                            "parse_mode": "Markdown",
                            "reply_markup": {"inline_keyboard": [[{"text": other_label, "callback_data": f"plan:{other_dir}:{hhmm}"}]]},
                        })
                        tg_request(token, "answerCallbackQuery", {"callback_query_id": cb_id})
                    elif cb_data.startswith("rem:") and chat_id:
                        parsed_rem = parse_reminder_code(cb_data)
                        if parsed_rem:
                            rem_text, rem_kb = create_reminder(
                                chat_id,
                                parsed_rem["direction"],
                                parsed_rem["route"],
                                parsed_rem["dep"],
                                parsed_rem["walk_min"],
                            )
                            tg_request(token, "sendMessage", {
                                "chat_id": chat_id,
                                "text": rem_text,
                                "parse_mode": "Markdown",
                                "reply_markup": {"inline_keyboard": rem_kb} if rem_kb else keyboard,
                            })
                        tg_request(token, "answerCallbackQuery", {"callback_query_id": cb_id, "text": "Готово!"})
                    elif cb_data.startswith("delrem:") and chat_id:
                        rem_id = cb_data.split(":", 1)[1]
                        deleted = delete_reminder(chat_id, rem_id)
                        if msg_id:
                            rem_list_text, rem_list_kb = format_user_reminders(chat_id)
                            tg_request(token, "editMessageText", {
                                "chat_id": chat_id,
                                "message_id": msg_id,
                                "text": "✅ Напоминание отменено.\n\n" + rem_list_text if deleted else rem_list_text,
                                "parse_mode": "Markdown",
                                "reply_markup": {"inline_keyboard": rem_list_kb},
                            })
                        tg_request(token, "answerCallbackQuery", {"callback_query_id": cb_id, "text": "Отменено"})
                    else:
                        tg_request(token, "answerCallbackQuery", {"callback_query_id": cb_id})
                    continue

                msg = upd.get("message") or {}
                chat_id = msg.get("chat", {}).get("id")
                text = (msg.get("text") or "").strip()
                if not chat_id or not text:
                    continue

                inline_markup = None
                parsed_hhmm = parse_time_input(text)

                # Обработка deep-link с сайта (/start r_c_150_1639_10)
                if text.startswith("/start "):
                    payload_arg = text.split(" ", 1)[1].strip()
                    parsed_rem = parse_reminder_code(payload_arg)
                    if parsed_rem:
                        reply, rem_kb = create_reminder(
                            chat_id,
                            parsed_rem["direction"],
                            parsed_rem["route"],
                            parsed_rem["dep"],
                            parsed_rem["walk_min"],
                        )
                        if rem_kb:
                            inline_markup = {"inline_keyboard": rem_kb}
                        tg_request(token, "sendMessage", {
                            "chat_id": chat_id,
                            "text": reply,
                            "parse_mode": "Markdown",
                            "reply_markup": inline_markup if inline_markup else keyboard,
                        })
                        continue

                if "город" in text.lower() or text == "/city" or text == "/school":
                    last_direction_by_chat[chat_id] = "to_school"
                    reply, inline_rows = format_direction_message("to_school")
                    if inline_rows:
                        inline_markup = {"inline_keyboard": inline_rows}
                elif "домой" in text.lower() or text == "/home":
                    last_direction_by_chat[chat_id] = "to_home"
                    reply, inline_rows = format_direction_message("to_home")
                    if inline_rows:
                        inline_markup = {"inline_keyboard": inline_rows}
                elif "напоминания" in text.lower() or text == "/reminders":
                    reply, inline_rows = format_user_reminders(chat_id)
                    if inline_rows:
                        inline_markup = {"inline_keyboard": inline_rows}
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
                    welcome_text = (
                        "👋 *Привет! Я семейный помощник по автобусам №150 и №170с*\n"
                        "Маршрут: *д. Богатырёво ↔ Малиновка*\n\n"
                        "• Нажмите *«🏠 ➔ 🏙️ В Город»* или *«🏙️ ➔ 🏠 Домой»* — покажу ближайшие рейсы и время выхода.\n"
                        "• Нажмите *🔔 под рейсом* — напомню за 5 минут до выхода и ровно в момент выхода.\n"
                        "• Напишите нужное время (например, `16:40`) — подберу 2 лучших автобуса.\n"
                        "• Кнопка *«📱 Открыть веб-приложение»* откроет интерактивный таймер прямо внутри Telegram."
                    )
                    tg_request(token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": welcome_text,
                        "parse_mode": "Markdown",
                        "reply_markup": keyboard,
                    })

                    dir_text, inline_rows = format_direction_message(default_dir)
                    tg_request(token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": dir_text,
                        "parse_mode": "Markdown",
                        "reply_markup": {"inline_keyboard": inline_rows} if inline_rows else keyboard,
                    })
                    continue

                tg_request(token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": reply,
                    "parse_mode": "Markdown",
                    "reply_markup": inline_markup if inline_markup else keyboard,
                })

        except KeyboardInterrupt:
            print("\nБот остановлен.")
            break
        except Exception as e:
            print(f"Ошибка сети: {e}, повтор через 3 сек...")
            time.sleep(3)


if __name__ == "__main__":
    if "--test" in sys.argv:
        print("=" * 60)
        msg_c, kb_c = format_direction_message("to_school")
        print(msg_c)
        print("Inline buttons:", kb_c)
        print("=" * 60)
        msg_h, kb_h = format_direction_message("to_home")
        print(msg_h)
        print("Inline buttons:", kb_h)
        print("=" * 60)
        print("Parsed deep link:", parse_reminder_code("r_c_150_1823_10"))
        print(format_target_arrival_message("16:40", "to_school"))
        print("=" * 60)
    else:
        cfg = load_config()
        if not cfg["telegram_bot_token"]:
            print("Токен Telegram-бота не найден!")
            print("Создайте файл bot_config.json рядом с bot.py (образец в bot_config.example.json)")
            print("или задайте переменную окружения TELEGRAM_BOT_TOKEN.")
            print("\nТестовый вывод расписания прямо сейчас:\n")
            msg_c, _ = format_direction_message("to_school")
            print(msg_c)
        else:
            run_bot(cfg["telegram_bot_token"], cfg["web_app_url"])
