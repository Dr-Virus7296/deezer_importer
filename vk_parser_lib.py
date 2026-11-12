#!/usr/bin/env python3
"""Парсер VK Музыки — консольный (requests + cookies)."""

import json
import html as html_module
import os
import re
import time
import urllib.parse
from collections import Counter
from http.cookiejar import MozillaCookieJar

import requests

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(_BASE_DIR, "vk_cookies.txt")
COOKIES_JSON = os.path.join(_BASE_DIR, "vk_cookies.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) "
      "Gecko/20100101 Firefox/156.0")

HEADERS = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

STATIC_VERSION = "249061"

EXPLORE_BLOCKS = [
    ("editors_choice", "Выбор редакции"),
    ("player_today", "Плейлист дня"),
    ("new_songs", "Новинки"),
    ("stay_on_trend", "В тренде"),
    ("new_albums", "Новые альбомы"),
    ("new_artist", "Новые имена"),
    ("seasonal_playlists", "Сезонные плейлисты"),
    ("most_expected", "Самые ожидаемые"),
    ("genre_new_releases", "Новинки по жанрам"),
    ("best_of_last_week", "Лучшее за неделю"),
    ("tracks_charts", "Чарты треков"),
    ("mood_new_releases", "Новинки по настроению"),
    ("collections", "Музыкальные подборки"),
    ("acoustic", "Акустика"),
]

# Блоки, которые отдают сразу треки, а не плейлисты.
TRACK_BLOCKS = {
    "new_songs",
    "new_artist",
    "most_expected",
    "best_of_last_week",
}

CURATORS_BLOCKS = [
    ("curators_pop", "Поп"),
    ("curators_indie", "Инди"),
    ("curators_hip_hop", "Хип-хоп"),
    ("curators_rock", "Рок"),
    ("curators_jazz", "Джаз"),
    ("curators_cassical", "Классика"),
    ("curators_edm", "EDM"),
    ("curators_other", "Другое"),
    ("curators_radio", "Радио"),
]


# ============================================================
#  Cookies
# ============================================================

def _load_cookies(session: requests.Session) -> bool:
    loaded = False
    if os.path.exists(COOKIES_FILE):
        try:
            jar = MozillaCookieJar(COOKIES_FILE)
            jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies.update(jar)
            loaded = True
            print(f"[i] Cookies: {COOKIES_FILE}")
        except Exception as e:
            print(f"[!] {COOKIES_FILE}: {e}")
    if os.path.exists(COOKIES_JSON):
        try:
            with open(COOKIES_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    session.cookies.set(k, str(v), domain=".vk.ru")
                    session.cookies.set(k, str(v), domain=".vk.com")
                loaded = True
                print(f"[i] Cookies: {COOKIES_JSON}")
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        domain = item.get("domain") or ".vk.ru"
                        session.cookies.set(item["name"], item["value"], domain=domain)
                loaded = True
                print(f"[i] Cookies (list): {COOKIES_JSON}")
        except Exception as e:
            print(f"[!] {COOKIES_JSON}: {e}")
    return loaded


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    _load_cookies(s)
    return s


def _has_cookies_file() -> bool:
    return os.path.exists(COOKIES_FILE) or os.path.exists(COOKIES_JSON)


# ============================================================
#  Утилиты
# ============================================================

def _is_bad_title(t: str) -> bool:
    if not t:
        return True
    t = t.strip()
    if t.startswith("%") and t.endswith("%"):
        return True
    if t.lower() in ("vk playlist", "playlist", "null", "undefined"):
        return True
    return False


def _clean_title(t) -> str:
    if not t:
        return ""
    t = str(t)
    if "%" in t:
        try:
            t = urllib.parse.unquote(t)
        except Exception:
            pass
    t = html_module.unescape(t).strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _parse_vk_json(text: str):
    """VK отдаёт JSON, обёрнутый в HTML-комментарий: <!-- {...} -->"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("<!--"):
        s = s[4:]
    if s.endswith("-->"):
        s = s[:-3]
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    for start_ch, end_ch in (("{", "}"), ("[", "]")):
        i = s.find(start_ch)
        j = s.rfind(end_ch)
        if i >= 0 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                continue
    return None


def _extract_artist_from_playlist(p: dict) -> str:
    """Вытаскивает имя артиста/куратора из всех известных полей VK."""
    # 1) main_artists — [{name}, ...] — основной источник для блоков типа acoustic
    for key in ("main_artists", "performers", "artists"):
        arr = p.get(key)
        if isinstance(arr, list) and arr:
            names = []
            for a in arr:
                if isinstance(a, dict):
                    n = a.get("name") or a.get("title") or ""
                    if n:
                        names.append(str(n))
                elif isinstance(a, str) and a.strip():
                    names.append(a.strip())
            if names:
                return _clean_title(", ".join(names))

    # 2) Прямое поле subtitle (строка или список dict'ов)
    sub = p.get("subtitle")
    if isinstance(sub, str) and sub.strip():
        return _clean_title(sub)
    if isinstance(sub, list):
        parts = []
        for item in sub:
            if isinstance(item, dict):
                n = item.get("name") or item.get("title") or ""
                if n:
                    parts.append(str(n))
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return _clean_title(", ".join(parts))

    # 3) performer / artist
    single = p.get("performer") or p.get("artist")
    if isinstance(single, str) and single.strip():
        return _clean_title(single)
    if isinstance(single, dict):
        n = single.get("name") or single.get("title") or ""
        if n:
            return _clean_title(n)

    return ""


# ============================================================
#  UID
# ============================================================

def _extract_uid_from_value(value: str) -> str:
    if not value:
        return ""
    s = urllib.parse.unquote(str(value))
    for pat in (
        r'"unique_adblock_users"\s*[:,\s]+(\d+)',
        r'unique_adblock_users["\']?\s*[:,\s]+(\d+)',
        r'"user_id"\s*:\s*(\d+)',
        r'"uid"\s*:\s*(\d+)',
    ):
        m = re.search(pat, s, re.I)
        if m:
            return m.group(1)
    m = re.search(r'(?<!\d)(\d{9,11})(?!\d)', s)
    return m.group(1) if m else ""


def _get_uid_from_cookies(session: requests.Session) -> str:
    for c in session.cookies:
        if c.name in ("remix_userid", "uid", "user_id", "vk_user_id"):
            v = str(c.value or "")
            if v.isdigit():
                return v
    for c in session.cookies:
        if c.name == "remixmsts":
            uid = _extract_uid_from_value(c.value)
            if uid:
                return uid
    for c in session.cookies:
        if c.name == "remixsid":
            v = urllib.parse.unquote(str(c.value or ""))
            m = re.search(r'(?<!\d)(\d{6,11})(?!\d)', v)
            if m:
                return m.group(1)
    return ""


def _fetch_uid_live(session: requests.Session) -> str:
    for url in ("https://m.vk.ru/audio", "https://vk.ru/audio"):
        try:
            r = session.get(url, allow_redirects=True, timeout=15)
        except Exception:
            continue
        combined = (r.url or "") + "\n" + (r.text or "")
        for pat in (
            r'audios(\d{5,12})',
            r'/audio\?uid=(\d+)',
            r'"user_id"\s*:\s*(\d+)',
            r'"uid"\s*:\s*(\d+)',
        ):
            m = re.search(pat, combined)
            if m:
                print(f"[i] UID получен из live-запроса: {m.group(1)}")
                return m.group(1)
    return ""


# ============================================================
#  Плейлисты в блоке Explore
# ============================================================

def _normalize_playlist(p: dict) -> dict:
    if not isinstance(p, dict):
        return None

    title = _clean_title(p.get("title") or p.get("name") or "")
    if _is_bad_title(title):
        return None

    artist = _extract_artist_from_playlist(p)

    # Отображаемое имя = title + artist (если есть и отличается)
    if artist and artist.lower() != title.lower():
        display_title = f"{title} — {artist}"
    else:
        display_title = title

    owner_id = p.get("owner_id") or p.get("ownerId")
    playlist_id = p.get("id") or p.get("playlist_id")
    access = (p.get("access_hash") or p.get("accessHash")
              or p.get("access_key") or p.get("accessKey") or "")

    try:
        owner_id = int(owner_id) if owner_id is not None else None
    except Exception:
        owner_id = None

    try:
        if playlist_id is None:
            playlist_id = None
        else:
            pid_digits = re.sub(r"\D", "", str(playlist_id))
            playlist_id = int(pid_digits) if pid_digits else None
    except Exception:
        playlist_id = None

    if owner_id is None or playlist_id is None:
        return None

    return {
        "title": display_title,
        "raw_title": title,
        "artist": artist,
        "owner_id": owner_id,
        "playlist_id": playlist_id,
        "access_hash": access or None,
        "url": None,
        "trackCount": p.get("count") or p.get("size") or p.get("total") or "?",
        "tracks": [],
    }


def _extract_playlists_from_data(payload, debug_keys=False) -> list:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list) or len(data) < 2:
        return []
    info = data[1]
    if not isinstance(info, dict):
        return []
    out, seen = [], set()

    raw_list = info.get("playlists") or []
    if not isinstance(raw_list, list):
        raw_list = []

    # Отладка: показываем ключи первого сырого плейлиста
    if debug_keys and raw_list and isinstance(raw_list[0], dict):
        print(f"[DBG] Поля плейлиста от VK: {sorted(raw_list[0].keys())}")
        sample = raw_list[0]
        for k in ("title", "subtitle", "main_artists", "performers", "description"):
            if k in sample:
                v = sample[k]
                v_short = str(v)[:120]
                print(f"[DBG]   {k} = {v_short}")

    for p in raw_list:
        pl = _normalize_playlist(p)
        if not pl:
            continue
        key = (pl["owner_id"], pl["playlist_id"])
        if key in seen:
            continue
        seen.add(key)
        out.append(pl)

    single = info.get("playlist")
    if isinstance(single, dict):
        pl = _normalize_playlist(single)
        if pl:
            key = (pl["owner_id"], pl["playlist_id"])
            if key not in seen:
                seen.add(key)
                out.append(pl)

    if not out:
        for key_name in ("playlist", "playlists"):
            candidate = payload.get(key_name)
            if isinstance(candidate, dict):
                pl = _normalize_playlist(candidate)
                if pl:
                    out.append(pl)
                    break
            elif isinstance(candidate, list):
                for p in candidate:
                    pl = _normalize_playlist(p)
                    if pl:
                        out.append(pl)
    return out


def _dedupe_display_titles(playlists: list) -> None:
    """Если после сборки остались одинаковые title — добавляем #1, #2, ..."""
    if not playlists:
        return
    counts = Counter(pl["title"] for pl in playlists)
    idx = Counter()
    for pl in playlists:
        if counts[pl["title"]] > 1:
            idx[pl["title"]] += 1
            pl["title"] = f"{pl['title']} #{idx[pl['title']]}"


def _extract_next_from(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data")
    if isinstance(data, list) and len(data) >= 2:
        info = data[1]
        if isinstance(info, dict):
            v = info.get("next_from") or info.get("nextFrom") or info.get("next")
            if isinstance(v, str) and v:
                return v
    for k in ("next_from", "nextFrom", "next"):
        v = payload.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def get_block_playlists(block_name: str, section: str = "explore") -> list:
    session = _session()
    uid = _get_uid_from_cookies(session)
    if not uid:
        print("[i] UID не найден в куках, пробую получить через VK...")
        uid = _fetch_uid_live(session)
    if not uid:
        print("[FAIL] Не удалось получить UID.")
        return []
    print(f"[i] UID: {uid}")

    is_track_block = block_name in TRACK_BLOCKS
    if is_track_block:
        print(f"[i] Блок '{block_name}' отдаёт сразу треки — собираю их.")

    sections_to_try = [section]
    if block_name == "player_today":
        sections_to_try.insert(0, "player_today")

    out, seen = [], set()
    for sec in sections_to_try:
        if out:
            break
        base_url = f"https://m.vk.ru/audio?section={sec}&block={block_name}"
        start_from = ""
        page = 0
        max_pages = 50

        track_buffer = []
        track_seen = set()
        first_page = True

        while page < max_pages:
            page += 1
            url = base_url
            if start_from:
                url += f"&start_from={start_from}"
            print(f"[..] POST стр.{page} (section={sec}): {url}")
            hdrs = {
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"https://m.vk.ru/audio?section={sec}&block={block_name}",
                "Origin": "https://m.vk.ru",
            }
            data = f"_ajax=1&staticVersion={STATIC_VERSION}"
            try:
                r = session.post(url, headers=hdrs, data=data, timeout=25)
            except Exception as e:
                print(f"[!] Ошибка запроса: {e}")
                break
            if r.status_code != 200:
                print(f"[!] HTTP {r.status_code}")
                break
            payload = _parse_vk_json(r.text)
            if not isinstance(payload, dict):
                print("[!] Ответ не JSON")
                break

            new_items = 0

            if is_track_block:
                batch = _extract_tracks_from_payload(payload)
                for t in batch:
                    if t["vk_id"] not in track_seen:
                        track_seen.add(t["vk_id"])
                        track_buffer.append(t)
                        new_items += 1
                print(f"[i] Стр. {page}: +{new_items} треков (всего {len(track_buffer)})")
            else:
                plist = _extract_playlists_from_data(
                    payload, debug_keys=first_page,
                )
                first_page = False
                for pl in plist:
                    key = (pl["owner_id"], pl["playlist_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(pl)
                    new_items += 1
                print(f"[i] Стр. {page}: +{new_items} плейлистов (всего {len(out)})")

            if new_items == 0:
                break
            next_from = _extract_next_from(payload)
            if not next_from or next_from == start_from:
                print("[i] Больше нет next_from — конец")
                break
            start_from = next_from
            time.sleep(0.3)

        if is_track_block and track_buffer:
            block_title = dict(EXPLORE_BLOCKS).get(block_name, block_name)
            out.append({
                "title": block_title,
                "raw_title": block_title,
                "artist": "",
                "owner_id": None,
                "playlist_id": None,
                "access_hash": None,
                "url": None,
                "trackCount": len(track_buffer),
                "tracks": track_buffer,
            })

        if out:
            break

    # Дедуп заголовков (для плейлистов с одинаковым title)
    if not is_track_block and out:
        _dedupe_display_titles(out)

    print(f"[OK] Блок '{block_name}': {len(out)} плейлистов/треков")
    return out


def get_explore_blocks() -> list:
    return EXPLORE_BLOCKS


def get_curators_blocks() -> list:
    return CURATORS_BLOCKS


def _parse_playlist_url(url: str):
    url = url.strip()
    patterns = [
        r"(?:music/playlist/|audio_playlist|playlist/)(-?\d+)_(\d+)(?:[/_%2F]?([a-zA-Z0-9]+))?",
        r"act=audio_playlist(-?\d+)_(\d+)",
        r"z=audio_playlist(-?\d+)_(\d+)(?:%2F|/)?([a-zA-Z0-9]+)?",
    ]
    for pat in patterns:
        m = re.search(pat, url, re.I)
        if m:
            owner = int(m.group(1))
            pl_id = int(m.group(2))
            access = m.group(3) if m.lastindex >= 3 and m.group(3) else None
            return owner, pl_id, access
    return None, None, None


# ============================================================
#  Треки: извлечение
# ============================================================

def _looks_like_hash(s: str) -> bool:
    """VK-хеши: длинные base64-строки без пробелов."""
    if not isinstance(s, str):
        return False
    if len(s) < 12:
        return False
    if " " in s:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_\-+/=]+", s))


def _track_from_list_item(item) -> dict:
    """VK list[] формат: [audio_id, owner_id, hash, title, artist, duration, ...]"""
    if not isinstance(item, list) or len(item) < 4:
        return None

    vk_id = item[0]

    strings = [x.strip() for x in item if isinstance(x, str) and x.strip()]
    candidates = [s for s in strings if not _looks_like_hash(s)]

    title = ""
    artist = ""

    if len(item) > 3 and isinstance(item[3], str) and item[3].strip():
        title = item[3].strip()
        if len(item) > 4 and isinstance(item[4], str) and item[4].strip():
            artist = item[4].strip()

    if not title and candidates:
        title = candidates[0]
        if len(candidates) > 1:
            artist = candidates[1]
    elif title and not artist and len(candidates) >= 2:
        for c in candidates:
            if c != title:
                artist = c
                break

    if not title:
        return None

    duration = 0
    if len(item) > 5 and isinstance(item[5], (int, float)):
        duration = int(item[5])

    return {"artist": artist, "title": title,
            "vk_id": str(vk_id), "duration": duration}


def _is_container(item: dict) -> bool:
    """True, если dict — это плейлист/альбом (контейнер), а не трек."""
    if not isinstance(item, dict):
        return False
    if item.get("type") in ("playlist", "album", "audio_playlist"):
        return True
    has_list = isinstance(item.get("list"), list)
    has_count = any(k in item for k in ("count", "size", "total", "totalCount"))
    if has_list and has_count:
        return True
    has_oid = "ownerId" in item or "owner_id" in item
    has_id = "id" in item or "playlist_id" in item
    has_artist = bool(item.get("artist") or item.get("performer"))
    if has_oid and has_id and not has_artist:
        return True
    return False


def _track_from_dict(item: dict) -> dict:
    if not isinstance(item, dict):
        return None
    if _is_container(item):
        return None

    title = (item.get("title") or item.get("name") or "").strip()
    if not title:
        return None
    artist = (item.get("artist") or item.get("performer")
              or item.get("subtitle") or "").strip()
    vk_id = (item.get("id") or item.get("audio_id")
             or item.get("aid") or item.get("vk_id") or "")
    return {"artist": artist, "title": title,
            "vk_id": str(vk_id) if vk_id else title,
            "duration": int(item.get("duration") or 0)}


def _list_contains_tracks(lst) -> bool:
    """Эвристика: похож ли список на список треков."""
    if not isinstance(lst, list) or not lst:
        return False
    first = lst[0]
    if isinstance(first, list):
        return len(first) >= 4 and isinstance(first[3], str) and len(first[3]) > 0
    if isinstance(first, dict):
        if not first.get("title") and not first.get("name"):
            return False
        return not _is_container(first)
    return False


def _walk_json_for_tracks(obj, depth=0, seen_ids=None):
    if seen_ids is None:
        seen_ids = set()
    if depth > 25:
        return

    def emit(item):
        t = None
        if isinstance(item, list):
            t = _track_from_list_item(item)
        elif isinstance(item, dict):
            t = _track_from_dict(item)
        if t and t["vk_id"] not in seen_ids:
            seen_ids.add(t["vk_id"])
            return t
        return None

    if isinstance(obj, dict):
        for key in ("list", "audios", "tracks", "items", "audio"):
            v = obj.get(key)
            if _list_contains_tracks(v):
                for item in v:
                    t = emit(item)
                    if t:
                        yield t
        for k, v in obj.items():
            if k in ("list", "audios", "tracks", "items", "audio") and _list_contains_tracks(v):
                continue
            yield from _walk_json_for_tracks(v, depth + 1, seen_ids)
    elif isinstance(obj, list):
        if _list_contains_tracks(obj):
            for item in obj:
                t = emit(item)
                if t:
                    yield t
        else:
            for v in obj:
                yield from _walk_json_for_tracks(v, depth + 1, seen_ids)


def _extract_tracks_from_payload(payload) -> list:
    return list(_walk_json_for_tracks(payload))


def _extract_title_from_payload(payload) -> str:
    candidates = []

    def walk(obj, depth=0):
        if depth > 10:
            return
        if isinstance(obj, dict):
            if _is_container(obj):
                t = _clean_title(obj.get("title") or obj.get("name") or "")
                if t and not _is_bad_title(t):
                    candidates.append(t)
            for v in obj.values():
                walk(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, depth + 1)

    walk(payload)
    return candidates[0] if candidates else ""


def _extract_total_count(payload) -> int:
    found = []

    def walk(obj, depth=0):
        if depth > 15:
            return
        if isinstance(obj, dict):
            if _is_container(obj):
                for key in ("count", "size", "total", "totalCount", "audiosCount"):
                    v = obj.get(key)
                    if isinstance(v, int) and v > 0:
                        found.append(v)
            for v in obj.values():
                walk(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, depth + 1)

    walk(payload)
    return max(found) if found else 0


def _extract_tracks_from_html(html_text: str) -> list:
    if not html_text:
        return []
    tracks, seen = [], set()

    def add(t):
        if t and t["vk_id"] not in seen:
            seen.add(t["vk_id"])
            tracks.append(t)

    for m in re.finditer(r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
                         html_text, re.DOTALL | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        for t in _walk_json_for_tracks(data):
            add(t)

    for m in re.finditer(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*(?:</script>|window\.)',
                         html_text, re.DOTALL):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        for t in _walk_json_for_tracks(data):
            add(t)

    for pat in (r'data-audio="(\[.*?\])"', r"data-audio='(\[.*?\])'"):
        for raw in re.findall(pat, html_text, re.DOTALL):
            try:
                data = json.loads(html_module.unescape(html_module.unescape(raw)))
                add(_track_from_list_item(data))
            except Exception:
                pass

    return tracks


def _extract_title_from_page(html: str) -> str:
    patterns = [
        r'<div class="audioPlaylist__title"[^>]*>([^<]+)</div>',
        r'property="og:title"\s+content="([^"]+)"',
        r'<title>([^<]+)</title>',
        r'data-title="([^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.I)
        if m:
            t = _clean_title(m.group(1))
            t = re.sub(r"\s*[\|\-–—]\s*VK.*$", "", t, flags=re.I).strip()
            t = re.sub(r"\s*[\|\-–—]\s*ВКонтакте.*$", "", t, flags=re.I).strip()
            if not _is_bad_title(t):
                return t
    return ""


# ============================================================
#  Скачивание плейлиста — с пагинацией
# ============================================================

def _al_audio_load_section(session, owner_id, playlist_id, access_pair,
                            offset=0, count=100):
    data = {
        "al": 1, "_ajax": 1, "staticVersion": STATIC_VERSION,
        "owner_id": owner_id, "playlist_id": playlist_id,
        "type": "playlist", "offset": offset, "count": count,
    }
    data.update(access_pair)
    r = session.post(
        "https://vk.ru/al_audio.php",
        params={"act": "load_section"},
        data=data,
        timeout=25,
    )
    return _parse_vk_json(r.text)


def _debug_payload_shape(payload, label: str):
    try:
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list) and data:
            top = data[1] if len(data) > 1 else None
            if isinstance(top, list) and top:
                first = top[0]
                if isinstance(first, dict):
                    print(f"[DBG {label}] container keys: "
                          f"{sorted(first.keys())[:15]}")
                    lst = first.get("list")
                    if isinstance(lst, list) and lst:
                        print(f"[DBG {label}] list[0] type={type(lst[0]).__name__}, "
                              f"len={len(lst[0]) if isinstance(lst[0], list) else '-'}, "
                              f"sample={lst[0] if not isinstance(lst[0], dict) else list(lst[0].keys())}")
    except Exception:
        pass


def download_vk_playlist(owner_id: int, playlist_id: int, access_hash: str = None) -> tuple:
    session = _session()

    access_pair = {}
    if access_hash:
        access_pair = {"access_hash": access_hash, "access_key": access_hash}

    tracks = []
    title = ""
    seen_ids = set()
    first_dbg_done = False

    print("[..] al_audio.php?act=load_section (с пагинацией)...")
    offset = 0
    total = 0
    iterations = 0
    max_iterations = 100

    while iterations < max_iterations:
        iterations += 1
        try:
            payload = _al_audio_load_section(
                session, owner_id, playlist_id, access_pair, offset=offset,
            )
        except Exception as e:
            print(f"[!] al_audio offset={offset}: {e}")
            break

        if payload is None:
            print(f"[!] al_audio offset={offset}: не JSON")
            break

        if not first_dbg_done:
            _debug_payload_shape(payload, "al_audio")
            first_dbg_done = True

        if not title:
            t = _extract_title_from_payload(payload)
            if t:
                title = t
        if not total:
            total = _extract_total_count(payload)

        batch = _extract_tracks_from_payload(payload)
        new = 0
        for t in batch:
            if t["vk_id"] not in seen_ids:
                seen_ids.add(t["vk_id"])
                tracks.append(t)
                new += 1

        print(f"[i] offset={offset}: +{new} треков (всего {len(tracks)}"
              + (f"/{total}" if total else "") + ")")

        if new == 0:
            break
        if total and len(tracks) >= total:
            break

        offset += len(batch)
        time.sleep(0.25)

    if not tracks:
        for label, url, params in (
            ("m.vk.ru", f"https://m.vk.ru/audio?act=audio_playlist{owner_id}_{playlist_id}",
             access_pair or None),
            ("vk.ru", f"https://vk.ru/music/playlist/{owner_id}_{playlist_id}"
             + (f"/{access_hash}" if access_hash else ""), None),
        ):
            print(f"[..] fallback {label}...")
            try:
                rr = session.get(url, params=params, timeout=20)
            except Exception as e:
                print(f"[!] {label}: {e}")
                continue
            p = _parse_vk_json(rr.text)
            if isinstance(p, (dict, list)):
                found = _extract_tracks_from_payload(p)
                if found:
                    tracks = found
                    print(f"[i] {label} json: {len(found)} треков")
                    if not title:
                        title = _extract_title_from_payload(p) or _extract_title_from_page(rr.text)
                    break
            found = _extract_tracks_from_html(rr.text)
            if found:
                tracks = found
                print(f"[i] {label} html: {len(found)} треков")
                if not title:
                    title = _extract_title_from_page(rr.text)
                break

    if _is_bad_title(title):
        title = f"VK Playlist {owner_id}_{playlist_id}"
    return title, tracks






def get_vk_playlist(url: str):
    if not _has_cookies_file():
        print("[i] Cookies нет — для чужих/закрытых плейлистов могут не открыться.")
    owner_id, playlist_id, access_hash = _parse_playlist_url(url)
    if owner_id is None:
        print("[FAIL] Не разобрал ссылку")
        print("[i] Пример: https://vk.ru/music/playlist/123_456")
        return "VK Playlist", []
    print(f"[i] owner_id={owner_id}, playlist_id={playlist_id}"
          + (f", access_hash={access_hash}" if access_hash else ""))
    return download_vk_playlist(owner_id, playlist_id, access_hash)


def playlist_to_tracks(pl: dict) -> tuple:
    title = pl.get("title") or "VK Playlist"
    tracks = list(pl.get("tracks") or [])
    if tracks:
        return title, tracks
    oid, pid = pl.get("owner_id"), pl.get("playlist_id")
    if oid is not None and pid is not None:
        t2, tracks = download_vk_playlist(oid, pid, pl.get("access_hash"))
        if not _is_bad_title(t2):
            title = t2
        return title, tracks
    print("[!] Нет owner/playlist id и нет встроенных треков")
    return title, []


def save_json(tracks, path="vk_selected_playlist.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tracks, f, ensure_ascii=False, indent=2)
    return os.path.abspath(path)