#!/usr/bin/env python3
"""Deezer Importer — интерактивная оболочка: Яндекс / ВК / Zvuk / Spotify / Apple Music / Свой список → Deezer."""

import json as _json
import os
import re
import sys
from datetime import datetime

import yandex_parser_lib as ypl
import vk_parser_lib as vpl
import zvuk_parser_lib as zpl
import spotify_parser_lib as spl
import apple_music_parser_lib as apl
import deezer_adder_lib as dal
import vk_cookies_help as vch


CONFIG_FILE = "config.cfg"


def _flush_stdin():
    """Сбросить накопившиеся нажатия Enter, чтобы не улетали в следующий input()."""
    try:
        import select
        if hasattr(select, "select"):
            while True:
                r, _, _ = select.select([sys.stdin], [], [], 0.0)
                if not r:
                    break
                if not sys.stdin.readline():
                    break
    except Exception:
        pass
    try:
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass
    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getch()
    except Exception:
        pass


CUSTOM_LIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_list")


def ask_choice(prompt, options):
    print(f"\n{prompt}")
    for i, opt in enumerate(options, 1):
        print(f"  {i:3d}. {opt}")
    _flush_stdin()
    while True:
        try:
            raw = input("\nНомер (q=выход, b=назад): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            continue
        low = raw.lower()
        if low in ("q", "b", "назад"):
            return None
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return idx
        print("[!] Неверный номер, попробуй снова")


def ask_choice_paged(prompt, options, page_size=15):
    if not options:
        print("[!] Список пуст")
        return None
    page = 0
    total = len(options)
    pages = max(1, (total + page_size - 1) // page_size)
    _flush_stdin()
    while True:
        start = page * page_size
        end = min(start + page_size, total)
        print(f"\n{prompt}")
        print(f"  --- страница {page+1}/{pages}  (всего {total}) ---")
        for i in range(start, end):
            print(f"  {i+1:3d}. {options[i]}")
        print()
        print("  n = следующая страница | p = предыдущая | b = назад | q = выход")
        try:
            raw = input("Номер или команда: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            continue
        low = raw.lower()
        if low in ("q",):
            return None
        if low in ("b", "назад"):
            return None
        if low in ("n", "next", "д"):
            if page + 1 < pages:
                page += 1
            else:
                print("[i] Это последняя страница")
            continue
        if low in ("p", "prev", "п"):
            if page > 0:
                page -= 1
            else:
                print("[i] Это первая страница")
            continue
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < total:
                return idx
            print("[!] Номер вне списка")
            continue
        print("[!] Не понял. Номер, n/p, b или q")


def _safe_playlist_name(name):
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.replace("[", "(").replace("]", ")")
    return name.strip()[:100] or "playlist"


def _confirm_and_send(tracks, playlist_title, source_tag):
    if not tracks:
        print("[FAIL] Треки не найдены")
        return
    print(f"[OK] Плейлист: '{playlist_title}' — {len(tracks)} треков")
    print("\nПервые 5 треков:")
    for i, t in enumerate(tracks[:5], 1):
        print(f"  {i}. {t.get('artist', '?')} - {t.get('title', '?')}")

    print()
    _flush_stdin()
    ans = input("Добавить в Deezer? (y/n): ").strip().lower()
    if ans != "y":
        print("[STOP] Треки не отправлены.")
        return

    json_path = "selected_playlist.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(tracks, f, ensure_ascii=False, indent=2)
    json_path = os.path.abspath(json_path)
    print(f"[OK] JSON сохранён: {json_path}")

    date_str = datetime.now().strftime("%d.%m.%y")
    base = _safe_playlist_name(playlist_title)
    playlist_name = f"{base} [{source_tag} {date_str}]"
    print(f"\n[OK] Плейлист Deezer будет называться: {playlist_name}")

    dal.run_import(json_path, playlist_name)


# ===== Spotify proxy =====

def _read_spotify_proxy():
    if not os.path.exists(CONFIG_FILE):
        return ""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                s = raw.strip()
                if not s or s.startswith("#"):
                    continue
                if s.upper().startswith("SPOTIFY_PROXY="):
                    return s[len("SPOTIFY_PROXY="):].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _write_spotify_proxy(value):
    lines = []
    found = False
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                if raw.strip().upper().startswith("SPOTIFY_PROXY="):
                    lines.append(f"SPOTIFY_PROXY={value}\n")
                    found = True
                else:
                    lines.append(raw)
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append(f"SPOTIFY_PROXY={value}\n")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ===== Яндекс =====

def _yandex_send(selected):
    print(f"\n[..] Парсинг треков из '{selected['title']}'...")
    tracks = ypl.fetch_tracks_for_playlist(selected)
    if not tracks:
        print("[FAIL] Треки не найдены")
        return
    json_path = ypl.save_tracks_json(tracks)
    print(f"[OK] Сохранено: {json_path}  ({len(tracks)} треков)")
    print()
    try:
        _flush_stdin()
        ans = input("Добавить в Deezer? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans != "y":
        print("[STOP] Треки сохранены в JSON, но в Deezer не отправлены.")
        return
    date_str = datetime.now().strftime("%d.%m.%y")
    base = _safe_playlist_name(selected["title"])
    playlist_name = f"{base} [Яндекс {date_str}]"
    print(f"\n[OK] Плейлист Deezer будет называться: {playlist_name}")
    dal.run_import(json_path, playlist_name)


def import_yandex():
    while True:
        modes = [
            "Категории / метатеги",
            "Поиск плейлистов",
        ]
        mi = ask_choice("Яндекс.Музыка — режим:", modes)
        if mi is None:
            print("[STOP]")
            return

        if mi == 1:
            try:
                q = input("\nПоисковый запрос: ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not q:
                continue
            print(f"\n[..] Поиск: {q}...")
            playlists = ypl.search_playlists(q, limit=30)
            if not playlists:
                print("[FAIL] Плейлисты не найдены")
                continue
            labels = [
                f"{pl['title']}  ({pl.get('track_count', '?')} треков)"
                + (f" — {pl['owner']}" if pl.get("owner") else "")
                for pl in playlists
            ]
            pl_idx = ask_choice_paged("Найденные плейлисты:", labels, page_size=15)
            if pl_idx is None:
                continue
            _yandex_send(playlists[pl_idx])
            continue

        print("\n[..] Загрузка категорий Яндекс.Музыки...")
        sections = ypl.get_sections()
        if not sections:
            print("[FAIL] Не удалось получить категории")
            continue

        si = ask_choice("Выбери категорию:", sections)
        if si is None:
            continue
        section_name = sections[si]

        print(f"\n[..] Загрузка метатегов раздела '{section_name}'...")
        metatags = ypl.get_metatags_in_section(section_name)
        if not metatags:
            print("[FAIL] Нет метатегов")
            continue

        mt_idx = ask_choice(
            f"Выбери метатег ({section_name}):",
            [mt["title"] for mt in metatags],
        )
        if mt_idx is None:
            continue
        metatag = metatags[mt_idx]

        print(f"\n[..] Загрузка плейлистов '{metatag['title']}'...")
        playlists = ypl.get_playlists_for_tag(metatag["tag"])
        if not playlists:
            print("[FAIL] Плейлисты не найдены")
            continue

        pl_idx = ask_choice(
            f"Выбери плейлист ({metatag['title']}):",
            [f"{pl['title']}  ({pl.get('track_count', '?')} треков)" for pl in playlists],
        )
        if pl_idx is None:
            continue
        _yandex_send(playlists[pl_idx])


# ===== ВКонтакте =====

def _vk_confirm_and_send(title, tracks):
    if not tracks:
        print("[FAIL] Список треков пуст")
        return
    print(f"[OK] '{title}' — {len(tracks)} треков")
    print("\nПервые 5:")
    for i, t in enumerate(tracks[:5], 1):
        a = t.get("artist", "?") or "?"
        n = t.get("title", "?") or "?"
        print(f"  {i}. {a} - {n}")
    print()
    try:
        _flush_stdin()
        ans = input("Добавить в Deezer? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans != "y":
        print("[STOP] Не отправлено")
        return
    json_path = vpl.save_json(tracks)
    print(f"[OK] JSON: {json_path}")
    date_str = datetime.now().strftime("%d.%m.%y")
    base = _safe_playlist_name(title)
    playlist_name = f"{base} [VK {date_str}]"
    print(f"\n[OK] Deezer playlist: {playlist_name}")
    dal.run_import(json_path, playlist_name)


def import_vk():
    while True:
        print()
        modes = [
            "Explore — подборки по блокам",
            "Кураторы — плейлисты по жанрам",
            "Вставить ссылку на плейлист",
            "Авто-вытащить куки из Firefox",
        ]
        mi = ask_choice("VK Музыка — режим:", modes)
        if mi is None:
            print("[STOP]")
            return

        if mi == 3:
            ok = vch.extract_from_firefox()
            if ok:
                print("\n[i] Куки обновлены. Возвращаюсь в меню.")
            else:
                print("\n[i] Не удалось получить куки.")
            try:
                input("Нажми Enter...")
            except (EOFError, KeyboardInterrupt):
                pass
            continue

        try:
            if mi == 0:
                blocks = vpl.get_explore_blocks()
                if not blocks:
                    print("[FAIL] Блоки не найдены")
                    continue
                labels = [name for _, name in blocks]
                bi = ask_choice_paged("Выбери блок Explore:", labels, page_size=15)
                if bi is None:
                    continue
                block_name, block_title = blocks[bi]

                playlists = vpl.get_block_playlists(block_name, section="explore")
                if not playlists:
                    print("[FAIL] Плейлисты не найдены")
                    continue

                if block_name in vpl.TRACK_BLOCKS:
                    pl = playlists[0]
                    title = pl.get("title") or block_title
                    tracks = pl.get("tracks") or []
                    if not tracks:
                        print("[i] В этом блоке нет треков.")
                        continue
                    _vk_confirm_and_send(title, tracks)
                    continue

                pl_labels = [p["title"] for p in playlists]
                pi = ask_choice_paged(f"{block_title}:", pl_labels, page_size=15)
                if pi is None:
                    continue
                pl = playlists[pi]
                title, tracks = vpl.playlist_to_tracks(pl)
                if tracks:
                    _vk_confirm_and_send(title, tracks)
                else:
                    print("[i] В этом плейлисте нет треков.")

            elif mi == 1:
                blocks = vpl.get_curators_blocks()
                if not blocks:
                    print("[FAIL] Жанры не найдены")
                    continue
                labels = [name for _, name in blocks]
                bi = ask_choice_paged("Выбери жанр кураторов:", labels, page_size=15)
                if bi is None:
                    continue
                block_name, block_title = blocks[bi]

                playlists = vpl.get_block_playlists(block_name, section="curators_genres")
                if not playlists:
                    print("[FAIL] Плейлисты не найдены")
                    continue

                if block_name in vpl.TRACK_BLOCKS:
                    pl = playlists[0]
                    title = pl.get("title") or block_title
                    tracks = pl.get("tracks") or []
                    if not tracks:
                        print("[i] В этом блоке нет треков.")
                        continue
                    _vk_confirm_and_send(title, tracks)
                    continue

                pl_labels = [p["title"] for p in playlists]
                pi = ask_choice_paged(f"Кураторы — {block_title}:", pl_labels, page_size=15)
                if pi is None:
                    continue
                pl = playlists[pi]
                title, tracks = vpl.playlist_to_tracks(pl)
                if tracks:
                    _vk_confirm_and_send(title, tracks)
                else:
                    print("[i] В этом плейлисте нет треков.")

            elif mi == 2:
                print("\n[i] Пример: https://vk.ru/music/playlist/123_456")
                try:
                    url = input("\nСсылка (или 'q'/'b'): ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if url.lower() in ("q", "b", ""):
                    continue
                print(f"\n[..] Парсинг: {url}")
                title, tracks = vpl.get_vk_playlist(url)
                if not tracks:
                    print("[FAIL] Треки не найдены")
                    continue
                _vk_confirm_and_send(title, tracks)

        except Exception as e:
            print(f"[FAIL] Ошибка VK: {e}")
            import traceback
            traceback.print_exc()


def _zvuk_confirm_and_send(title, tracks):
    if not tracks:
        print("[FAIL] Треки не найдены")
        return
    print(f"[OK] '{title}' — {len(tracks)} треков")
    print("\nПервые 5:")
    for i, t in enumerate(tracks[:5], 1):
        print(f"  {i}. {t.get('artist', '?')} - {t.get('title', '?')}")
    print()
    try:
        _flush_stdin()
        ans = input("Добавить в Deezer? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans != "y":
        print("[STOP]")
        return
    json_path = zpl.save_json(tracks)
    print(f"[OK] JSON: {json_path}")
    date_str = datetime.now().strftime("%d.%m.%y")
    base = _safe_playlist_name(title)
    playlist_name = f"{base} [Zvuk {date_str}]"
    print(f"\n[OK] Deezer playlist: {playlist_name}")
    dal.run_import(json_path, playlist_name)


def import_zvuk():
    while True:
        modes = [
            "📌 Подборки",
            "📂 Жанры — вкладки с https://zvuk.com/genres",
            "Поиск плейлистов",
            "Вставить ссылку на плейлист",
        ]
        mi = ask_choice("Zvuk — режим:", modes)
        if mi is None:
            print("[STOP]")
            return

        try:
            if mi == 0:
                sections = zpl.list_curated_sections()
                si = ask_choice("Подборки:", [s["title"] for s in sections])
                if si is None:
                    continue
                sec = sections[si]
                if sec["id"] == "editorial":
                    profiles = zpl.list_editorial_profiles()
                    pi = ask_choice("Редакция — профиль:", [p["title"] for p in profiles])
                    if pi is None:
                        continue
                    sid = profiles[pi]["id"]
                    label = profiles[pi]["title"]
                else:
                    sid = sec["id"]
                    label = sec["title"]
                print(f"\n[..] Плейлисты «{label}»...")
                playlists = zpl.get_curated_playlists(sid, limit=40)
                if not playlists:
                    print("[FAIL] Плейлисты не найдены")
                    continue
                labels = [p.get("title") or p["id"] for p in playlists]
                pi = ask_choice_paged(f"Подборки — {label}:", labels, page_size=15)
                if pi is None:
                    continue
                selected = playlists[pi]
                print(f"\n[..] Загрузка id={selected['id']}...")
                title, tracks = zpl.get_playlist(selected["id"])
                _zvuk_confirm_and_send(title or selected.get("title"), tracks)

            elif mi == 1:
                genres = zpl.get_genres()
                gi = ask_choice("Выбери жанр:", [g["title"] for g in genres])
                if gi is None:
                    continue
                genre = genres[gi]

                tabs = genre.get("tabs") or []
                tab_opts = ["Все (поиск по жанру)"] + list(tabs)
                ti = ask_choice(f"Поджанр / вкладка ({genre['title']}):", tab_opts)
                if ti is None:
                    continue
                tab = None if ti == 0 else tabs[ti - 1]

                print(f"\n[..] Поиск плейлистов: {genre['title']}" + (f" / {tab}" if tab else "") + "...")
                playlists = zpl.get_playlists_for_genre(genre, tab=tab)
                if not playlists:
                    print("[FAIL] Плейлисты не найдены")
                    continue

                labels = []
                for p in playlists:
                    desc = (p.get("description") or "")[:40]
                    extra = f" — {desc}" if desc else ""
                    labels.append(f"{p['title']}{extra}")
                pi = ask_choice_paged(f"Плейлисты ({genre['title']}):", labels, page_size=15)
                if pi is None:
                    continue
                selected = playlists[pi]

                print(f"\n[..] Загрузка плейлиста id={selected['id']}...")
                title, tracks = zpl.get_playlist(selected["id"])
                _zvuk_confirm_and_send(title or selected["title"], tracks)

            elif mi == 2:
                try:
                    q = input("\nПоисковый запрос: ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if not q:
                    continue
                print(f"\n[..] Поиск: {q}...")
                playlists = zpl.search_playlists(q, limit=30)
                if not playlists:
                    print("[FAIL] Ничего не найдено")
                    continue
                labels = [p["title"] for p in playlists]
                pi = ask_choice_paged("Найденные плейлисты:", labels, page_size=15)
                if pi is None:
                    continue
                selected = playlists[pi]
                print(f"\n[..] Загрузка id={selected['id']}...")
                title, tracks = zpl.get_playlist(selected["id"])
                _zvuk_confirm_and_send(title or selected["title"], tracks)

            elif mi == 3:
                print("\n[i] Пример: https://zvuk.com/playlist/1062105")
                try:
                    url = input("\nСсылка (или 'q'/'b'): ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if url.lower() in ("q", "b", ""):
                    continue
                pid = zpl.get_playlist_id_from_url(url)
                if not pid:
                    print("[FAIL] Не смог вытащить id. Пример: https://zvuk.com/playlist/1062105")
                    continue
                print(f"\n[..] Загрузка плейлиста id={pid}...")
                title, tracks = zpl.get_playlist(pid)
                _zvuk_confirm_and_send(title, tracks)

        except Exception as e:
            print(f"[FAIL] Ошибка Zvuk: {e}")
            import traceback
            traceback.print_exc()


# ===== Spotify =====

def _spotify_confirm_and_send(title, tracks):
    if not tracks:
        print("[FAIL] Треки не найдены")
        return
    print(f"[OK] '{title}' — {len(tracks)} треков")
    print("\nПервые 5:")
    for i, t in enumerate(tracks[:5], 1):
        print(f"  {i}. {t.get('artist', '?')} - {t.get('title', '?')}")
    print()
    try:
        _flush_stdin()
        ans = input("Добавить в Deezer? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans != "y":
        print("[STOP]")
        return
    json_path = spl.save_json(tracks)
    print(f"[OK] JSON: {json_path}")
    date_str = datetime.now().strftime("%d.%m.%y")
    base = _safe_playlist_name(title)
    playlist_name = f"{base} [Spotify {date_str}]"
    print(f"\n[OK] Deezer playlist: {playlist_name}")
    dal.run_import(json_path, playlist_name)


def _spotify_configure_proxy():
    cur = _read_spotify_proxy()
    print()
    print("=" * 60)
    print("  ПРОКСИ ДЛЯ SPOTIFY")
    print("=" * 60)
    if cur:
        print(f"  Текущее значение: {cur}")
    else:
        print("  Сейчас прокси не задан.")
    print()
    print("  Spotify блокирует российские IP (451 not available).")
    print("  Если Spotify не работает — укажи зарубежный прокси.")
    print()
    print("  Примеры:")
    print("    socks5://user:pass@host:1080")
    print("    socks5://127.0.0.1:1080")
    print("    socks5h://user:pass@host:1080")
    print("    http://user:pass@host:7890")
    print("    http://127.0.0.1:7890")
    print()
    print("  Оставь пустым и нажми Enter — прокси будет отключён.")
    print("  Введи q — отмена без изменений.")
    print()
    try:
        val = input("Прокси: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n[STOP]")
        return
    if val.lower() == "q":
        print("[i] Отменено.")
        return
    _write_spotify_proxy(val)
    spl.set_proxy(val)
    if val:
        print(f"[OK] Сохранено в {CONFIG_FILE}: SPOTIFY_PROXY={val}")
    else:
        print("[OK] Прокси отключён.")


def import_spotify():
    proxy = _read_spotify_proxy()
    spl.set_proxy(proxy)
    if proxy:
        print(f"\n[i] Spotify прокси: {proxy}")
    else:
        print("\n[i] Spotify прокси не задан.")
        print("[i] Если Spotify отдаёт 451 — зайди в «Настроить прокси для Spotify».")

    while True:
        modes = [
            "Категории / жанры (Explore)",
            "Поиск плейлистов",
            "Вставить ссылку на жанр (open.spotify.com/genre/...)",
            "Вставить ссылку на плейлист",
            "Настроить прокси для Spotify",
        ]
        mi = ask_choice("Spotify — режим:", modes)
        if mi is None:
            print("[STOP]")
            return

        if mi == 4:
            _spotify_configure_proxy()
            continue

        try:
            if mi == 0:
                cats = spl.get_categories()
                if not cats:
                    print("[FAIL] Список категорий пуст")
                    continue
                ci = ask_choice_paged("Выбери категорию:", [c["title"] for c in cats], page_size=15)
                if ci is None:
                    continue
                cat = cats[ci]
                print(f"\n[..] Загрузка плейлистов категории '{cat['title']}'...")
                playlists = spl.get_playlists_for_genre(cat["id"])
                if not playlists:
                    print("[FAIL] Плейлисты не найдены.")
                    print("[i] Если только что был 451 — проверь прокси в пункте 5.")
                    continue
                labels = [p["title"] for p in playlists]
                pi = ask_choice_paged(f"{cat['title']} — плейлисты:", labels, page_size=15)
                if pi is None:
                    continue
                pl = playlists[pi]
                print(f"\n[..] Загрузка треков: {pl['title']}...")
                title, tracks = spl.get_playlist(pl["id"])
                _spotify_confirm_and_send(title or pl["title"], tracks)

            elif mi == 1:
                try:
                    q = input("\nПоисковый запрос (например Pop hits): ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if not q:
                    continue
                print(f"\n[..] Поиск: {q}...")
                playlists = spl.search_playlists(q, limit=30)
                if not playlists:
                    print("[FAIL] Ничего не найдено")
                    continue
                labels = [p["title"] for p in playlists]
                pi = ask_choice_paged("Найденные плейлисты:", labels, page_size=15)
                if pi is None:
                    continue
                pl = playlists[pi]
                print(f"\n[..] Загрузка треков: {pl['title']}...")
                title, tracks = spl.get_playlist(pl["id"])
                _spotify_confirm_and_send(title or pl["title"], tracks)

            elif mi == 2:
                print("\n[i] Пример: https://open.spotify.com/genre/0JQ5DAqbMKFEC4WFtoNRpw")
                try:
                    url = input("\nСсылка на жанр: ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if not url:
                    continue
                gid = spl.get_genre_id_from_url(url)
                if not gid:
                    print("[FAIL] Не распознал genre id")
                    continue
                print(f"\n[..] Загрузка плейлистов genre={gid}...")
                playlists = spl.get_playlists_for_genre(gid)
                if not playlists:
                    print("[FAIL] Плейлисты не найдены")
                    continue
                labels = [p["title"] for p in playlists]
                pi = ask_choice_paged("Плейлисты жанра:", labels, page_size=15)
                if pi is None:
                    continue
                pl = playlists[pi]
                print(f"\n[..] Загрузка треков...")
                title, tracks = spl.get_playlist(pl["id"])
                _spotify_confirm_and_send(title or pl["title"], tracks)

            elif mi == 3:
                print("\n[i] Пример: https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
                try:
                    url = input("\nСсылка на плейлист: ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if not url:
                    continue
                pid = spl.get_playlist_id_from_url(url)
                if not pid:
                    print("[FAIL] Не смог вытащить playlist id")
                    continue
                print(f"\n[..] Загрузка плейлиста {pid}...")
                title, tracks = spl.get_playlist(pid)
                _spotify_confirm_and_send(title, tracks)

        except Exception as e:
            print(f"[FAIL] Ошибка Spotify: {e}")
            import traceback
            traceback.print_exc()


# ===== Свой список =====

CUSTOM_LIST_EXAMPLE = """# Deezer Importer — свой список
#
# В режиме «Треки» — по одному треку в строке.
# Формат: Исполнитель - Название
# Дефис может быть -, –, — или −.
#
# В режиме «Исполнители» — по одному имени в строке.
# Без дефиса, без названий треков.
#
# Строки, начинающиеся с #, и пустые строки игнорируются.
#
# Пример (режим треков):
# Motörhead - Ace of Spades
# Adele - Hello
# Michael Bublé - Home
# Café Tacvba - Eres
#
# Пример (режим исполнителей):
# Motörhead
# Queen
# Nirvana
"""


def _custom_list_path():
    return CUSTOM_LIST_FILE


def _read_custom_list_file():
    """Читает custom_list. Автовыбор кодировки, автосоздание с примером.

    Возвращает список строк или None, если пользователь отменил.
    """
    path = _custom_list_path()

    if not os.path.exists(path):
        print(f"\n[..] Файла не было — создаю: {path}")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(CUSTOM_LIST_EXAMPLE)
        except Exception as e:
            print(f"[FAIL] Не удалось создать файл: {e}")
            return None

    print()
    print("=" * 60)
    print("  ФАЙЛ СО СПИСКОМ")
    print("=" * 60)
    print(f"  Путь: {path}")
    print()
    print("  Открой этот файл в блокноте (или любом редакторе):")
    print("   - Windows: правый клик → Открыть с помощью → Блокнот")
    print("   - Linux/macOS: nano, vim, любой редактор")
    print()
    print("  Впиши свои треки/исполнители (см. комментарии в файле).")
    print("  Сохрани в UTF-8. Строки, начинающиеся с #, игнорируются.")
    print()
    print("  Когда готово — вернись в это окно и нажми Enter.")
    print("  (Ctrl+C — отмена)")
    print()
    try:
        input("Нажми Enter когда заполнишь файл...")
    except (EOFError, KeyboardInterrupt):
        print("\n[STOP]")
        return None

    raw_text = ""
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with open(path, "r", encoding=enc) as f:
                raw_text = f.read()
            if enc != "utf-8-sig":
                print(f"[i] Файл прочитан в кодировке {enc}")
            break
        except UnicodeDecodeError:
            continue
        except Exception as e:
            print(f"[FAIL] Не удалось открыть файл: {e}")
            return None

    lines = []
    for ln in raw_text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)

    if not lines:
        print("[FAIL] Файл пустой или только комментарии")
        return None

    return lines


def import_custom():
    """Свой список: треки или исполнители, читается из custom_list."""
    while True:
        modes = [
            "Список треков (Artist - Title в каждой строке)",
            "Список исполнителей (топ-треки каждого)",
        ]
        mi = ask_choice("Свой список — режим:", modes)
        if mi is None:
            print("[STOP]")
            return

        mode = "tracks" if mi == 0 else "artists"

        try:
            title = input("\nНазвание плейлиста в Deezer: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("[STOP]")
            return
        if not title:
            title = "Мой плейлист"

        per = 10
        if mode == "artists":
            try:
                raw_per = input("Сколько треков брать у каждого исполнителя? [10]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("[STOP]")
                return
            if raw_per:
                try:
                    per = max(1, min(int(raw_per), 50))
                except ValueError:
                    print("[i] Не число, беру 10")
                    per = 10

        lines = _read_custom_list_file()
        if not lines:
            continue

        print(f"\n[OK] Строк из файла (без # и пустых): {len(lines)}")
        print("Первые 5:")
        for x in lines[:5]:
            print(f"  - {x}")
        if len(lines) > 5:
            print(f"  ... и ещё {len(lines) - 5}")

        if mode == "tracks":
            print(f"\n[..] Ищу {len(lines)} треков на Deezer...")
            tracks, not_found = dal.collect_tracks_from_lines(lines, debug=True)
        else:
            print(f"\n[..] Собираю топ-{per} треков для {len(lines)} исполнителей...")
            tracks = dal.collect_tracks_from_artists(lines, tracks_per_artist=per, debug=True)
            not_found = []

        # Ненайденные — в файл, не в консоль
        safe_title = _safe_playlist_name(title)
        if not_found:
            not_found_path = os.path.abspath(f"not_found - {safe_title}.txt")
            try:
                with open(not_found_path, "w", encoding="utf-8") as f:
                    for x in not_found:
                        f.write(x + "\n")
                print(f"\n[i] Не найдено: {len(not_found)} → {not_found_path}")
            except Exception as e:
                print(f"\n[!] Не удалось записать файл ненайденных: {e}")
                print(f"[i] Не найдено: {len(not_found)}")

        if not tracks:
            print("[FAIL] Ничего не найдено")
            continue

        print(f"\n[OK] Найдено треков: {len(tracks)}")
        print("Первые 10:")
        for i, t in enumerate(tracks[:10], 1):
            print(f"  {i}. {t.get('artist', '?')} - {t.get('title', '?')}")
        if len(tracks) > 10:
            print(f"  ... и ещё {len(tracks) - 10}")

        print()
        try:
            _flush_stdin()
            ans = input("Добавить в Deezer? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if ans != "y":
            print("[STOP]")
            return

        json_path = os.path.abspath("custom_selected_playlist.json")
        with open(json_path, "w", encoding="utf-8") as f:
            _json.dump(tracks, f, ensure_ascii=False, indent=2)
        print(f"[OK] JSON: {json_path}")

        print(f"\n[OK] Deezer playlist: {safe_title}")
        dal.run_import(json_path, safe_title)


# ===== Apple Music =====

def _apple_confirm_and_send(title, tracks):
    if not tracks:
        print("[FAIL] Треки не найдены")
        return
    print(f"[OK] '{title}' — {len(tracks)} треков")
    print("\nПервые 5:")
    for i, t in enumerate(tracks[:5], 1):
        print(f"  {i}. {t.get('artist', '?')} - {t.get('title', '?')}")
    print()
    try:
        _flush_stdin()
        ans = input("Добавить в Deezer? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans != "y":
        print("[STOP]")
        return
    json_path = apl.save_json(tracks)
    print(f"[OK] JSON: {json_path}")
    date_str = datetime.now().strftime("%d.%m.%y")
    base = _safe_playlist_name(title)
    playlist_name = f"{base} [Apple Music {date_str}]"
    print(f"\n[OK] Deezer playlist: {playlist_name}")
    dal.run_import(json_path, playlist_name)


def import_apple_music():
    while True:
        modes = [
            "Чарты — топ-плейлисты",
            "Обзор по жанрам",
            "Десятилетия",
            "Занятия и настроение",
            "Со всего мира",
            "Поиск плейлистов",
            "Вставить ссылку на плейлист",
        ]
        mi = ask_choice("Apple Music — режим:", modes)
        if mi is None:
            print("[STOP]")
            return

        try:
            # 0 charts
            if mi == 0:
                print("\n[..] Загрузка чартов Apple Music...")
                playlists = apl.get_chart_playlists(limit=30)
                if not playlists:
                    print("[FAIL] Чарты пусты")
                    continue
                labels = [p.get("label") or p["title"] for p in playlists]
                pi = ask_choice_paged("Топ-плейлисты:", labels, page_size=15)
                if pi is None:
                    continue
                pl = playlists[pi]
                print(f"\n[..] Загрузка треков: {pl['title']}...")
                title, tracks = apl.get_playlist(pl["id"], storefront=pl.get("storefront"))
                _apple_confirm_and_send(title or pl["title"], tracks)

            # 1-4 room sections
            elif mi in (1, 2, 3, 4):
                section = apl.get_browse_sections()[mi - 1]
                print(f"\n[..] Загрузка «{section['title']}»...")
                cats = apl.get_categories_in_section(section["id"])
                if not cats:
                    print("[FAIL] Категории не найдены")
                    continue
                gi = ask_choice_paged(
                    f"{section['title']}:",
                    [c["title"] for c in cats],
                    page_size=15,
                )
                if gi is None:
                    continue
                cat = cats[gi]
                print(f"\n[..] Плейлисты «{cat['title']}»...")
                playlists = apl.get_playlists_for_curator(cat["id"], limit=40)
                if not playlists:
                    print("[FAIL] Плейлисты не найдены")
                    continue
                labels = [p.get("label") or p["title"] for p in playlists]
                pi = ask_choice_paged(
                    f"{cat['title']} — плейлисты:",
                    labels,
                    page_size=15,
                )
                if pi is None:
                    continue
                pl = playlists[pi]
                print(f"\n[..] Загрузка треков: {pl['title']}...")
                title, tracks = apl.get_playlist(pl["id"], storefront=pl.get("storefront"))
                _apple_confirm_and_send(title or pl["title"], tracks)

            elif mi == 5:
                try:
                    q = input("\nПоисковый запрос (например rock hits): ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if not q:
                    continue
                print(f"\n[..] Поиск Apple Music: {q}...")
                playlists = apl.search_playlists(q, limit=30)
                if not playlists:
                    print("[FAIL] Ничего не найдено")
                    continue
                labels = [p.get("label") or p["title"] for p in playlists]
                pi = ask_choice_paged("Найденные плейлисты:", labels, page_size=15)
                if pi is None:
                    continue
                pl = playlists[pi]
                print(f"\n[..] Загрузка треков: {pl['title']}...")
                title, tracks = apl.get_playlist(pl["id"], storefront=pl.get("storefront"))
                _apple_confirm_and_send(title or pl["title"], tracks)

            elif mi == 6:
                print("\n[i] Пример: https://music.apple.com/ru/playlist/todays-hits/pl.f4d106fed2bd41149aaacabb233eb5eb")
                try:
                    url = input("Ссылка на плейлист: ").strip()
                except (EOFError, KeyboardInterrupt):
                    continue
                if not url:
                    continue
                parsed = apl.get_playlist_id_from_url(url)
                if not parsed:
                    print("[FAIL] Не смог вытащить id. Нужна ссылка вида music.apple.com/.../playlist/.../pl.xxx")
                    continue
                print(f"\n[..] Загрузка треков...")
                title, tracks = apl.get_playlist(url)
                _apple_confirm_and_send(title, tracks)

        except Exception as e:
            print(f"[FAIL] Ошибка: {e}")



def settings_deezer():
    """Показать / обновить ARL и ACCESS_TOKEN (в т.ч. автологин по email+паролю)."""
    arl = token = cookies = None
    if os.path.exists(CONFIG_FILE):
        try:
            arl, token, cookies = dal.load_deezer_config(CONFIG_FILE)
        except SystemExit:
            arl = token = cookies = None
    print()
    print("=" * 60)
    print("  НАСТРОЙКИ DEEZER")
    print("=" * 60)
    print(f"  config: {CONFIG_FILE}")
    print(f"  ARL:           {'задан (' + str(len(arl)) + ' симв.)' if arl and not str(arl).startswith('вставь') else 'НЕТ'}")
    print(f"  ACCESS_TOKEN:  {'задан (' + str(len(token)) + ' симв.)' if token and not str(token).startswith('вставь') else 'НЕТ'}")
    print()
    modes = [
        "Автологин по email + пароль (получить ACCESS_TOKEN, иногда ARL)",
        "Вставить ACCESS_TOKEN вручную",
        "Вставить ARL вручную",
        "Показать текущие значения (частично)",
        "Убрать дубликаты в плейлисте (относительно других)",
        "Назад",
    ]
    mi = ask_choice("Действие:", modes)
    if mi is None or mi == 5:
        return

    if mi == 0:
        try:
            email = input("Email Deezer: ").strip()
            password = input("Пароль Deezer: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not email or not password:
            print("[FAIL] Нужны email и пароль")
            return
        print("[..] Логин в Deezer...")
        try:
            creds = dal.generate_deezer_credentials(email, password)
        except Exception as e:
            print(f"[FAIL] {e}")
            return
        path = dal.save_deezer_config(
            arl=creds.get("arl") or (arl or ""),
            access_token=creds["access_token"],
            cookies=cookies or "",
            path=CONFIG_FILE,
        )
        print(f"[OK] ACCESS_TOKEN сохранён в {path}")
        if creds.get("arl"):
            print("[OK] ARL тоже получен и сохранён")
        else:
            print("[i] ARL сервер не вернул — при необходимости вставь вручную из cookies deezer.com")
        return

    if mi == 1:
        try:
            val = input("ACCESS_TOKEN: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not val:
            print("[STOP]")
            return
        path = dal.save_deezer_config(arl=arl or "", access_token=val, cookies=cookies or "", path=CONFIG_FILE)
        print(f"[OK] ACCESS_TOKEN сохранён в {path}")
        return

    if mi == 2:
        try:
            val = input("ARL: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not val:
            print("[STOP]")
            return
        path = dal.save_deezer_config(arl=val, access_token=token or "", cookies=cookies or "", path=CONFIG_FILE)
        print(f"[OK] ARL сохранён в {path}")
        return

    if mi == 3:
        def prev(s):
            s = s or ""
            if len(s) <= 16:
                return s or "(пусто)"
            return s[:10] + "…" + s[-6:]
        print(f"  ARL={prev(arl)}")
        print(f"  ACCESS_TOKEN={prev(token)}")
        return

    if mi == 4:
        if not token or str(token).startswith("вставь"):
            print("[FAIL] Нужен ACCESS_TOKEN (автологин или вставь вручную)")
            return
        print("\n[..] Загрузка списка плейлистов Deezer...")
        playlists = dal.get_all_user_playlists(token)
        if not playlists:
            print("[FAIL] Плейлисты не найдены (проверь ACCESS_TOKEN)")
            return
        labels = [f"{p['title']}  (id={p['id']})" for p in playlists]
        pi = ask_choice_paged("Какой плейлист почистить от дублей:", labels, page_size=15)
        if pi is None:
            return
        pl = playlists[pi]
        print(f"\n[..] Плейлист: {pl['title']!r} (id={pl['id']})")
        print("Будут удалены треки, которые уже есть в ДРУГИХ твоих плейлистах.")
        _flush_stdin()
        try:
            ans = input("Продолжить? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if ans not in ("y", "yes", "д", "да"):
            print("[STOP]")
            return
        try:
            removed, total = dal.dedupe_current_playlist(pl["id"], pl["title"], token)
            print(f"\n[OK] Удалено: {removed} из {total}")
        except Exception as e:
            print(f"[FAIL] {e}")
        return


# ===== Главное меню =====

def main():
    print("=" * 60)
    print("Deezer Importer")
    print("=" * 60)

    platforms = [
        "Яндекс.Музыка",
        "ВКонтакте",
        "Zvuk",
        "Spotify",
        "Apple Music",
        "Свой список (из файла custom_list)",
        "Настройки Deezer (ARL / ACCESS_TOKEN)",
    ]
    p_idx = ask_choice("Выбери площадку:", platforms)
    if p_idx is None:
        print("[STOP]")
        return

    if p_idx == 0:
        import_yandex()
    elif p_idx == 1:
        import_vk()
    elif p_idx == 2:
        import_zvuk()
    elif p_idx == 3:
        import_spotify()
    elif p_idx == 4:
        import_apple_music()
    elif p_idx == 5:
        import_custom()
    elif p_idx == 6:
        settings_deezer()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP]")
        sys.exit(1)