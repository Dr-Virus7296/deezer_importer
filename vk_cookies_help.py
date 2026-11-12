#!/usr/bin/env python3
"""Авто-вытаскивание куки VK для парсера."""

import os
import json
import shutil
import sqlite3
import tempfile


def _find_firefox_profiles():
    """Находит все профили Firefox с файлом cookies.sqlite."""
    appdata = os.environ.get("APPDATA", "")
    base = os.path.join(appdata, "Mozilla", "Firefox", "Profiles")
    if not os.path.isdir(base):
        return []

    profiles = []
    for name in os.listdir(base):
        path = os.path.join(base, name)
        cookies_db = os.path.join(path, "cookies.sqlite")
        if os.path.isfile(cookies_db):
            profiles.append((name, cookies_db))
    return profiles


def _read_vk_cookies(cookies_db_path):
    """Читает куки VK из SQLite БД Firefox."""
    tmp = os.path.join(tempfile.gettempdir(), "vk_cookies_tmp.sqlite")
    try:
        shutil.copy2(cookies_db_path, tmp)
    except Exception as e:
        print(f"[!] Не удалось скопировать БД: {e}")
        return {}

    cookies = {}
    try:
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        cur.execute("""
            SELECT name, value
            FROM moz_cookies
            WHERE host LIKE '%vk.ru%' OR host LIKE '%vk.com%'
        """)
        for name, value in cur.fetchall():
            if name and value is not None:
                cookies[name] = str(value)
        conn.close()
    except Exception as e:
        print(f"[!] Ошибка чтения БД: {e}")
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass

    return cookies


def extract_from_firefox():
    """Извлекает куки VK из Firefox и сохраняет в vk_cookies.json.

    Возвращает True при успехе, False иначе.
    """
    print()
    print("=" * 60)
    print("  АВТО-ВЫТАСКИВАНИЕ КУКИ VK ИЗ FIREFOX")
    print("=" * 60)

    print("[..] Ищу профили Firefox...")
    profiles = _find_firefox_profiles()
    if not profiles:
        print("[FAIL] Профили Firefox не найдены.")
        return False

    print(f"[OK] Найдено профилей Firefox: {len(profiles)}")

    best = None
    best_count = 0
    best_name = ""

    for name, db_path in profiles:
        cookies = _read_vk_cookies(db_path)
        count = len(cookies)
        print(f"     профиль {name}: VK-куки = {count}")
        if count > best_count:
            best_count = count
            best = cookies
            best_name = name

    if not best or best_count == 0:
        print()
        print("[FAIL] Ни в одном профиле нет куки VK.")
        return False

    print()
    print(f"[OK] Использую профиль: {best_name}")
    print(f"[OK] Собрано куки VK: {len(best)}")

    if "remixsid" not in best:
        print()
        print("[!] В куках НЕТ remixsid — ты не залогинен в Firefox.")
        return False

    out_path = os.path.abspath("vk_cookies.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False, indent=2)

    print()
    print(f"[OK] Файл сохранён: {out_path}")
    print()
    print("Ключи в файле:")
    for k in sorted(best.keys()):
        print(f"  - {k}")

    print()
    print("Теперь можно запускать парсер: py run.py -> ВКонтакте")
    return True