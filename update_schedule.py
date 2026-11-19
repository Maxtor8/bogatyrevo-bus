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
import urllib.request
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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


def fetch_times(url: str) -> dict:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

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
        normalized[k] = sorted(list(dict.fromkeys(normalized[k])))
    return normalized


def build_schedule():
    print("Загрузка актуального расписания автобусов №150 и №170с...")
    raw = {}
    for key, url in URLS.items():
        raw[key] = fetch_times(url)
        print(f"  [OK] {key}: будни={len(raw[key]['workdays'])}, сб={len(raw[key]['saturday'])}, вс={len(raw[key]['sunday'])}")

    minsk_tz = timezone(timedelta(hours=3))
    schedule = {
        "updated_at": datetime.now(minsk_tz).strftime("%Y-%m-%d %H:%M"),
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
        arr_150_mal = raw["to_city_150_arr_malinovka"][day_type]
        for i, dep_time in enumerate(dep_150):
            arr_mal = arr_150_mal[i] if i < len(arr_150_mal) else None
            schedule["directions"]["to_school"]["trips"][day_type].append({
                "route": "150",
                "stop_from": "Ост. «Богатырёво»",
                "dep": dep_time,
                "stop_to": "Ст. м. Малиновка",
                "arr": arr_mal,
            })

        # 2. В ГОРОД: Автобус 170с
        dep_170 = raw["to_city_170s_dep"][day_type]
        arr_170_mal = raw["to_city_170s_arr_malinovka"][day_type]
        for i, dep_time in enumerate(dep_170):
            arr_mal = arr_170_mal[i] if i < len(arr_170_mal) else None
            schedule["directions"]["to_school"]["trips"][day_type].append({
                "route": "170с",
                "stop_from": "Ост. «Богатырёво»",
                "dep": dep_time,
                "stop_to": "Ст. м. Малиновка",
                "arr": arr_mal,
            })

        # Сортируем рейсы в город по времени отправления
        schedule["directions"]["to_school"]["trips"][day_type].sort(key=lambda x: x["dep"])

        # 3. ДОМОЙ: Автобус 150 (только от Ст. м. Малиновка)
        dep_home_150_mal = raw["to_home_150_dep_malinovka"][day_type]
        arr_home_150_bog = raw["to_home_150_arr_bogatyrevo"][day_type]
        for i, dep_mal in enumerate(dep_home_150_mal):
            arr_bog = arr_home_150_bog[i] if i < len(arr_home_150_bog) else None
            schedule["directions"]["to_home"]["trips"][day_type].append({
                "route": "150",
                "stop_from": "Ост. «Ст. м. Малиновка»",
                "dep": dep_mal,
                "stop_to": "Богатырёво",
                "arr": arr_bog,
            })

        # 4. ДОМОЙ: Автобус 170с (от Ст. м. Малиновка возле ProStore)
        dep_home_170_mal = raw["to_home_170s_dep_malinovka"][day_type]
        arr_home_170_bog = raw["to_home_170s_arr_bogatyrevo"][day_type]
        for i, dep_mal in enumerate(dep_home_170_mal):
            arr_bog = arr_home_170_bog[i] if i < len(arr_home_170_bog) else None
            schedule["directions"]["to_home"]["trips"][day_type].append({
                "route": "170с",
                "stop_from": "Ост. «Ст. м. Малиновка» (ProStore)",
                "dep": dep_mal,
                "stop_to": "Богатырёво",
                "arr": arr_bog,
            })

        # Сортируем рейсы домой по времени отправления
        schedule["directions"]["to_home"]["trips"][day_type].sort(key=lambda x: x["dep"])

    json_path = os.path.join(BASE_DIR, "schedule_data.json")
    js_path = os.path.join(BASE_DIR, "schedule_data.js")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)

    with open(js_path, "w", encoding="utf-8") as f:
        f.write("window.SCHEDULE_DATA = ")
        json.dump(schedule, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    print("Расписание успешно сохранено в schedule_data.json и schedule_data.js!")


if __name__ == "__main__":
    build_schedule()
