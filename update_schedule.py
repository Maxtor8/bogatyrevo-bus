#!/usr/bin/env python3
"""
Скрипт загрузки и обновления актуального расписания автобусов №150 и №170с (Минск)
для маршрута: Дом (д. Богатырёво, ул. Полесская 7) <-> В Город (Малиновка / Космонавтов).
Создает файлы schedule_data.json (для Telegram-бота) и schedule_data.js (для Веб-приложения PWA).
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MINSK_TZ = timezone(timedelta(hours=3))

URLS = {
    # В Город (от ост. Богатырёво в сторону Малиновки)
    "to_city_150_dep": "https://zippybus.com/by/minsk/bus/150/rk-ozertso-brilevichi/stop-4903203",
    "to_city_150_arr_malinovka": "https://zippybus.com/by/minsk/bus/150/rk-ozertso-brilevichi/stop-12073",
    "to_city_170s_dep": "https://zippybus.com/by/minsk/bus/170s/zapadnoe-kladbische-as-yugo-zapadnaya/stop-4903203",
    "to_city_170s_arr_malinovka": "https://zippybus.com/by/minsk/bus/170s/zapadnoe-kladbische-as-yugo-zapadnaya/stop-57928",
    # Домой (от ост. «Ст. м. Малиновка» в сторону Богатырёво)
    "to_home_150_dep_malinovka": "https://zippybus.com/by/minsk/bus/150/brilevichi-rk-ozertso/stop-4913890",
    "to_home_150_arr_bogatyrevo": "https://zippybus.com/by/minsk/bus/150/brilevichi-rk-ozertso/stop-4903261",
    "to_home_170s_dep_malinovka": "https://zippybus.com/by/minsk/bus/170s/as-yugo-zapadnaya-zapadnoe-kladbische/stop-12091",
    "to_home_170s_arr_bogatyrevo": "https://zippybus.com/by/minsk/bus/170s/as-yugo-zapadnaya-zapadnoe-kladbische/stop-4903261",
}

_SSL_UNVERIFIED_FALLBACK = False


def _open_url_with_ssl_fallback(req: urllib.request.Request, timeout: int = 15) -> bytes:
    """Открывает URL со стандартной проверкой SSL; при отсутствии корневых сертификатов в ОС делает fallback."""
    global _SSL_UNVERIFIED_FALLBACK
    if not _SSL_UNVERIFIED_FALLBACK:
        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLError):
                _SSL_UNVERIFIED_FALLBACK = True
                print("  [!] Системные SSL-сертификаты не найдены, переключение на резервный режим TLS.")
            else:
                raise
        except ssl.SSLError:
            _SSL_UNVERIFIED_FALLBACK = True
            print("  [!] Ошибка проверки SSL-сертификата, переключение на резервный режим TLS.")

    ctx_fallback = ssl.create_default_context()
    ctx_fallback.check_hostname = False
    ctx_fallback.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx_fallback, timeout=timeout) as resp:
        return resp.read()


def _hhmm_to_minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    total = h * 60 + m
    # На случай ночных рейсов после полуночи (00:00..03:59)
    if h < 4:
        total += 1440
    return total


def match_arrival_times(
    dep_list: list,
    arr_list: list,
    min_travel: int = 5,
    max_travel: int = 40,
) -> list:
    """
    Сопоставляет каждому времени отправления ближайшее корректное время прибытия (arr >= dep + min_travel),
    чтобы укороченные/парковые рейсы не сдвигали расписание на весь остаток дня.
    """
    dep_mins = [_hhmm_to_minutes(t) for t in dep_list]
    arr_mins = [_hhmm_to_minutes(t) for t in arr_list]

    matched = []
    j = 0
    n_arr = len(arr_list)
    n_dep = len(dep_list)

    for i, d_min in enumerate(dep_mins):
        while j < n_arr and (arr_mins[j] - d_min) < min_travel:
            j += 1

        if j >= n_arr:
            matched.append(None)
            continue

        diff = arr_mins[j] - d_min
        if diff > max_travel:
            matched.append(None)
            continue

        # Проверяем, не относится ли arr[j] на самом деле к следующему отправлению dep[i+1]
        # (случай, когда текущий рейс i был укороченным и не доехал до конечной)
        if i + 1 < n_dep:
            next_diff_same_arr = arr_mins[j] - dep_mins[i + 1]
            next_has_own_arr = (
                (j + 1 < n_arr)
                and (min_travel <= (arr_mins[j + 1] - dep_mins[i + 1]) <= max_travel)
            )
            if min_travel <= next_diff_same_arr <= max_travel and not next_has_own_arr:
                matched.append(None)
                continue

        matched.append(arr_list[j])
        j += 1

    return matched


def fetch_times(url: str, retries: int = 3) -> dict:
    last_err = None
    html = ""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw_bytes = _open_url_with_ssl_fallback(req, timeout=15)
            html = raw_bytes.decode("utf-8", errors="ignore")
            break
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 * attempt)
    else:
        raise RuntimeError(f"Не удалось загрузить {url} после {retries} попыток: {last_err}")

    matches = re.findall(r"/by/minsk/bus/[^/]+/[^/]+/(\d+)/stop-\d+/(\d+)/(\d+)", html)
    raw_by_days = {}
    for days, h, m in matches:
        t = f"{int(h):02d}:{int(m):02d}"
        raw_by_days.setdefault(days, []).append(t)

    # Нормализуем на "workdays" (Пн-Пт), "saturday" (Сб), "sunday" (Вс)
    normalized = {"workdays": [], "saturday": [], "sunday": []}
    for days_mask, times_list in raw_by_days.items():
        if any(d in days_mask for d in ("1", "2", "3", "4", "5")):
            normalized["workdays"].extend(times_list)
        if "6" in days_mask:
            normalized["saturday"].extend(times_list)
        if "7" in days_mask:
            normalized["sunday"].extend(times_list)

    for k in normalized:
        normalized[k] = sorted(list(dict.fromkeys(normalized[k])), key=_hhmm_to_minutes)

    # Защита от пустой страницы / капчи / поломки вёрстки источника
    if (
        len(normalized["workdays"]) < 3
        or len(normalized["saturday"]) < 2
        or len(normalized["sunday"]) < 2
    ):
        raise RuntimeError(
            f"Ошибка валидации данных с {url}: слишком мало рейсов "
            f"(будни={len(normalized['workdays'])}, сб={len(normalized['saturday'])}, вс={len(normalized['sunday'])}). "
            f"Существующая база расписания НЕ перезаписана."
        )

    return normalized


def _atomic_write_text(path: str, content: str):
    tmp_path = f"{path}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)


def build_schedule(skip_if_same_day_unchanged: bool = False) -> bool:
    """
    Скачивает и собирает расписание.
    Если skip_if_same_day_unchanged=True и сегодняшняя дата уже записана в базе,
    а сами рейсы не изменились — не трогает файлы (возвращает False).
    """
    print("Загрузка актуального расписания автобусов №150 и №170с...")
    raw = {}
    for key, url in URLS.items():
        raw[key] = fetch_times(url)
        print(
            f"  [OK] {key}: будни={len(raw[key]['workdays'])}, "
            f"сб={len(raw[key]['saturday'])}, вс={len(raw[key]['sunday'])}"
        )

    now_str = datetime.now(MINSK_TZ).strftime("%Y-%m-%d %H:%M")
    today_prefix = now_str[:10]

    schedule = {
        "updated_at": now_str,
        "config": {
            "home_address": "д. Богатырёво, ул. Полесская, 7",
            "city_address": "Малиновка (Космонавтов)",
            "walk_minutes_to_city_stop": 10,  # Запас пешком от дома до ост. Богатырёво
            "walk_minutes_to_home_stop": 15,  # Запас пешком от Малиновки (Космонавтов) до ост. Ст. м. Малиновка
        },
        "directions": {
            "to_school": {
                "title": "В Город",
                "subtitle": "из д. Богатырёво → ост. «Богатырёво»",
                "walk_minutes": 10,
                "trips": {"workdays": [], "saturday": [], "sunday": []},
            },
            "to_home": {
                "title": "Домой",
                "subtitle": "Малиновка (Космонавтов) → ост. «Ст. м. Малиновка»",
                "walk_minutes": 15,
                "trips": {"workdays": [], "saturday": [], "sunday": []},
            },
        },
    }

    for day_type in ["workdays", "saturday", "sunday"]:
        # 1. В ГОРОД: Автобус 150
        dep_150 = raw["to_city_150_dep"][day_type]
        arr_150_mal = match_arrival_times(dep_150, raw["to_city_150_arr_malinovka"][day_type])
        for dep_time, arr_mal in zip(dep_150, arr_150_mal):
            schedule["directions"]["to_school"]["trips"][day_type].append({
                "route": "150",
                "stop_from": "Ост. «Богатырёво»",
                "dep": dep_time,
                "stop_to": "Ст. м. Малиновка",
                "arr": arr_mal,
            })

        # 2. В ГОРОД: Автобус 170с
        dep_170 = raw["to_city_170s_dep"][day_type]
        arr_170_mal = match_arrival_times(dep_170, raw["to_city_170s_arr_malinovka"][day_type])
        for dep_time, arr_mal in zip(dep_170, arr_170_mal):
            schedule["directions"]["to_school"]["trips"][day_type].append({
                "route": "170с",
                "stop_from": "Ост. «Богатырёво»",
                "dep": dep_time,
                "stop_to": "Ст. м. Малиновка",
                "arr": arr_mal,
            })

        # Сортируем рейсы в город по времени отправления
        schedule["directions"]["to_school"]["trips"][day_type].sort(
            key=lambda x: _hhmm_to_minutes(x["dep"])
        )

        # 3. ДОМОЙ: Автобус 150 (от Ст. м. Малиновка)
        dep_home_150_mal = raw["to_home_150_dep_malinovka"][day_type]
        arr_home_150_bog = match_arrival_times(
            dep_home_150_mal, raw["to_home_150_arr_bogatyrevo"][day_type]
        )
        for dep_mal, arr_bog in zip(dep_home_150_mal, arr_home_150_bog):
            schedule["directions"]["to_home"]["trips"][day_type].append({
                "route": "150",
                "stop_from": "Ост. «Ст. м. Малиновка»",
                "dep": dep_mal,
                "stop_to": "Богатырёво",
                "arr": arr_bog,
            })

        # 4. ДОМОЙ: Автобус 170с (от Ст. м. Малиновка возле ProStore)
        dep_home_170_mal = raw["to_home_170s_dep_malinovka"][day_type]
        arr_home_170_bog = match_arrival_times(
            dep_home_170_mal, raw["to_home_170s_arr_bogatyrevo"][day_type]
        )
        for dep_mal, arr_bog in zip(dep_home_170_mal, arr_home_170_bog):
            schedule["directions"]["to_home"]["trips"][day_type].append({
                "route": "170с",
                "stop_from": "Ост. «Ст. м. Малиновка» (ProStore)",
                "dep": dep_mal,
                "stop_to": "Богатырёво",
                "arr": arr_bog,
            })

        # Сортируем рейсы домой по времени отправления
        schedule["directions"]["to_home"]["trips"][day_type].sort(
            key=lambda x: _hhmm_to_minutes(x["dep"])
        )

    json_path = os.path.join(BASE_DIR, "schedule_data.json")
    js_path = os.path.join(BASE_DIR, "schedule_data.js")

    if skip_if_same_day_unchanged and os.path.exists(json_path) and os.path.exists(js_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            same_day = str(existing.get("updated_at", "")).startswith(today_prefix)
            same_dirs = existing.get("directions") == schedule["directions"]
            if same_day and same_dirs:
                print(f"Расписание за {today_prefix} уже актуально и не изменилось — запись пропущена.")
                return False
        except Exception:
            pass

    json_text = json.dumps(schedule, ensure_ascii=False, indent=2)
    js_text = f"window.SCHEDULE_DATA = {json_text};\n"

    _atomic_write_text(json_path, json_text)
    _atomic_write_text(js_path, js_text)

    print("Расписание успешно сохранено в schedule_data.json и schedule_data.js!")
    return True


if __name__ == "__main__":
    smart_flag = "--smart" in sys.argv
    build_schedule(skip_if_same_day_unchanged=smart_flag)
