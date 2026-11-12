#!/usr/bin/env python3
"""Парсер Apple Music: чарты, жанры, поиск и треки через amp-api (без аккаунта).

- Токен (developer JWT) вытаскивается из публичного JS music.apple.com.
- Чарты (топ-плейлисты), жанры → подборки, поиск, ссылка на плейлист.
- Публичные/редакторские плейлисты. Приватные библиотечные без media-user-token не откроются.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import requests

JSON_OUT = "apple_music_selected_playlist.json"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

_token_cache: Optional[Tuple[str, float]] = None
_PROXY_URL: Optional[str] = None

# Storefront по умолчанию (RU, т.к. ссылка в запросе /ru/)
DEFAULT_STOREFRONT = "ru"

# Разделы «Обзор» на music.apple.com/ru/browse → room IDs.
# Внутри room — плитки appleCurator, у каждого куратора список плейлистов.
ROOM_SECTIONS: List[Dict[str, str]] = [
    {"id": "6456176470", "title": "Обзор по жанрам"},
    {"id": "6456176471", "title": "Десятилетия"},
    {"id": "6456176472", "title": "Занятия и настроение"},
    {"id": "6456176473", "title": "Со всего мира"},
]

_room_cache: Dict[str, List[Dict[str, str]]] = {}


def set_proxy(proxy_url: str) -> None:
    """Установить прокси (SOCKS5/HTTP). Пустая строка — отключить."""
    global _PROXY_URL
    _PROXY_URL = (proxy_url or "").strip() or None


def get_proxy() -> Optional[str]:
    return _PROXY_URL


def _proxies() -> Optional[dict]:
    if not _PROXY_URL:
        return None
    return {"http": _PROXY_URL, "https": _PROXY_URL}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    px = _proxies()
    if px:
        s.proxies = px
    return s


def _get_token(debug: bool = False) -> Optional[str]:
    """JWT из index*.js на music.apple.com (кэш ~50 мин)."""
    global _token_cache
    if _token_cache and time.time() < _token_cache[1]:
        return _token_cache[0]

    sess = _session()
    try:
        r = sess.get("https://music.apple.com/ru/browse", timeout=25)
    except Exception as e:
        if debug:
            print(f"[debug] homepage: {e}")
        return None
    if r.status_code != 200:
        if debug:
            print(f"[debug] homepage status {r.status_code}")
        return None

    m = re.search(r'src="(/assets/index[~-][a-zA-Z0-9]+\.js)"', r.text)
    if not m:
        # fallback: любой index*.js
        m = re.search(r'src="(/assets/index[^"]+\.js)"', r.text)
    if not m:
        if debug:
            print("[debug] index.js not found in HTML")
        return None

    js_url = "https://music.apple.com" + m.group(1)
    try:
        jr = sess.get(js_url, timeout=30)
    except Exception as e:
        if debug:
            print(f"[debug] index.js: {e}")
        return None
    if jr.status_code != 200:
        if debug:
            print(f"[debug] index.js status {jr.status_code}")
        return None

    tokens = re.findall(
        r'"(eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"',
        jr.text,
    )
    if not tokens:
        if debug:
            print("[debug] no JWT in index.js")
        return None

    # Берём первый (обычно WebPlayer)
    token = tokens[0]
    _token_cache = (token, time.time() + 50 * 60)
    if debug:
        print(f"[debug] token ok, len={len(token)}")
    return token


def _api_headers(token: str) -> dict:
    return {
        **_HEADERS,
        "Authorization": f"Bearer {token}",
        "Origin": "https://music.apple.com",
        "Referer": "https://music.apple.com/",
        "Accept": "application/json",
    }


def get_playlist_id_from_url(url: str) -> Optional[Tuple[str, str]]:
    """Из URL → (storefront, playlist_id).

    Примеры:
      https://music.apple.com/ru/playlist/todays-hits/pl.f4d106fed2bd41149aaacabb233eb5eb
      https://music.apple.com/us/playlist/pl.u-xxx
      pl.f4d106fed2bd41149aaacabb233eb5eb
    """
    if not url:
        return None
    url = url.strip()

    # Чистый id
    if re.fullmatch(r"pl\.[A-Za-z0-9._-]+", url):
        return DEFAULT_STOREFRONT, url

    m = re.search(
        r"music\.apple\.com/([a-z]{2})/playlist/(?:[^/]+/)?(pl\.[A-Za-z0-9._-]+)",
        url,
        re.I,
    )
    if m:
        return m.group(1).lower(), m.group(2)

    # Без storefront в пути, но id есть
    m = re.search(r"(pl\.[A-Za-z0-9._-]+)", url)
    if m:
        return DEFAULT_STOREFRONT, m.group(1)

    return None


def search_playlists(
    query: str,
    limit: int = 30,
    storefront: str = DEFAULT_STOREFRONT,
    debug: bool = False,
) -> List[Dict[str, str]]:
    """Поиск плейлистов по запросу."""
    token = _get_token(debug=debug)
    if not token:
        return []

    sess = _session()
    headers = _api_headers(token)
    out: List[Dict[str, str]] = []
    seen = set()
    offset = 0
    page_size = min(25, limit)

    while len(out) < limit:
        try:
            r = sess.get(
                f"https://amp-api.music.apple.com/v1/catalog/{storefront}/search",
                headers=headers,
                params={
                    "term": query,
                    "types": "playlists",
                    "limit": page_size,
                    "offset": offset,
                    "l": f"{storefront}-{storefront.upper()}" if storefront != "us" else "en-US",
                },
                timeout=25,
            )
        except Exception as e:
            if debug:
                print(f"[debug] search: {e}")
            break
        if r.status_code != 200:
            if debug:
                print(f"[debug] search status {r.status_code}: {r.text[:200]}")
            break
        try:
            data = r.json()
        except Exception:
            break

        items = (
            data.get("results", {})
            .get("playlists", {})
            .get("data")
            or []
        )
        if not items:
            break

        for it in items:
            pid = it.get("id") or ""
            if not pid or pid in seen:
                continue
            seen.add(pid)
            attrs = it.get("attributes") or {}
            name = (attrs.get("name") or "").strip()
            if not name:
                continue
            curator = (attrs.get("curatorName") or "").strip()
            track_count = attrs.get("trackCount")
            label = name
            if curator:
                label = f"{name} — {curator}"
            if track_count:
                label = f"{label} ({track_count})"
            out.append({
                "id": pid,
                "title": name,
                "label": label,
                "curator": curator,
                "url": f"https://music.apple.com/{storefront}/playlist/{pid}",
                "storefront": storefront,
            })
            if len(out) >= limit:
                break

        next_href = data.get("results", {}).get("playlists", {}).get("next")
        if not next_href or len(items) < page_size:
            break
        offset += page_size
        time.sleep(0.15)

    return out[:limit]


def get_browse_sections() -> List[Dict[str, str]]:
    """Разделы как на сайте: Жанры / Десятилетия / Настроения / Со всего мира."""
    return list(ROOM_SECTIONS)


def _parse_room_categories(room_id: str, debug: bool = False) -> List[Dict[str, str]]:
    """Плитки appleCurator внутри room (как на скриншотах Apple Music)."""
    if room_id in _room_cache:
        return list(_room_cache[room_id])

    sess = _session()
    try:
        r = sess.get(f"https://music.apple.com/ru/room/{room_id}", timeout=30)
    except Exception as e:
        if debug:
            print(f"[debug] room {room_id}: {e}")
        return []
    if r.status_code != 200:
        if debug:
            print(f"[debug] room status {r.status_code}")
        return []

    text = r.content.decode("utf-8", errors="replace")
    m = re.search(
        r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>',
        text, re.DOTALL,
    )
    if not m:
        if debug:
            print("[debug] serialized-server-data not found")
        return []
    try:
        root = json.loads(m.group(1))
    except Exception as e:
        if debug:
            print(f"[debug] room json: {e}")
        return []

    cats: List[Dict[str, str]] = []
    seen = set()
    try:
        sections = root["data"][0]["data"].get("sections") or []
    except (KeyError, IndexError, TypeError):
        return []

    for s in sections:
        for it in s.get("items") or []:
            if not isinstance(it, dict):
                continue
            title = None
            for tl in it.get("titleLinks") or []:
                if isinstance(tl, dict) and tl.get("title"):
                    title = tl["title"]
                    break
            cd = it.get("contentDescriptor") or {}
            if cd.get("kind") != "appleCurator":
                continue
            cid = str((cd.get("identifiers") or {}).get("storeAdamID") or "")
            url = cd.get("url") or ""
            if not title and url:
                murl = re.search(r"/curator/apple-music-([^/]+)/", url)
                if murl:
                    from urllib.parse import unquote as _unquote
                    title = _unquote(murl.group(1)).replace("-", " ").strip()
                    if title:
                        title = title[0].upper() + title[1:]
            if title and cid and cid not in seen:
                seen.add(cid)
                cats.append({
                    "id": cid,
                    "title": title,
                    "url": url,
                    "room_id": room_id,
                })

    _room_cache[room_id] = cats
    if debug:
        print(f"[debug] room {room_id}: {len(cats)} categories")
    return list(cats)


def get_categories_in_section(section_id_or_title: str, debug: bool = False) -> List[Dict[str, str]]:
    """Категории (плитки) внутри раздела."""
    room_id = section_id_or_title
    for s in ROOM_SECTIONS:
        if s["id"] == section_id_or_title or s["title"] == section_id_or_title:
            room_id = s["id"]
            break
    return _parse_room_categories(room_id, debug=debug)


def get_playlists_for_curator(
    curator_id: str,
    limit: int = 40,
    storefront: str = DEFAULT_STOREFRONT,
    debug: bool = False,
) -> List[Dict[str, str]]:
    """Плейлисты куратора (плитка жанра/настроения/десятилетия)."""
    token = _get_token(debug=debug)
    if not token:
        return []

    sess = _session()
    headers = _api_headers(token)
    lang = f"{storefront}-{storefront.upper()}" if storefront != "us" else "en-US"
    out: List[Dict[str, str]] = []
    seen = set()
    url = f"https://amp-api.music.apple.com/v1/catalog/{storefront}/apple-curators/{curator_id}/playlists"
    params: Optional[dict] = {"l": lang, "limit": min(25, limit)}

    while url and len(out) < limit:
        try:
            r = sess.get(url, headers=headers, params=params, timeout=25)
        except Exception as e:
            if debug:
                print(f"[debug] curator playlists: {e}")
            break
        params = None
        if r.status_code != 200:
            if debug:
                print(f"[debug] curator status {r.status_code}: {r.text[:200]}")
            break
        try:
            data = r.json()
        except Exception:
            break

        for it in data.get("data") or []:
            pid = it.get("id") or ""
            if not pid or pid in seen:
                continue
            seen.add(pid)
            attrs = it.get("attributes") or {}
            name = (attrs.get("name") or "").strip()
            if not name:
                continue
            curator = (attrs.get("curatorName") or "").strip()
            track_count = attrs.get("trackCount")
            label = name
            if curator:
                label = f"{name} — {curator}"
            if track_count:
                label = f"{label} ({track_count})"
            out.append({
                "id": pid,
                "title": name,
                "label": label,
                "curator": curator,
                "url": f"https://music.apple.com/{storefront}/playlist/{pid}",
                "storefront": storefront,
            })
            if len(out) >= limit:
                break

        next_path = data.get("next")
        if next_path and len(out) < limit:
            url = next_path if next_path.startswith("http") else "https://amp-api.music.apple.com" + next_path
            time.sleep(0.12)
        else:
            url = None

    return out[:limit]


# Обратная совместимость
def get_genres() -> List[Dict[str, str]]:
    """Все категории из «Обзор по жанрам»."""
    return get_categories_in_section("6456176470")


def get_playlists_for_genre(
    genre_id_or_title: str,
    limit: int = 30,
    storefront: str = DEFAULT_STOREFRONT,
    debug: bool = False,
) -> List[Dict[str, str]]:
    """Плейлисты категории: если id куратора — через API, иначе search."""
    # Числовой id куратора
    if re.fullmatch(r"\d+", str(genre_id_or_title or "")):
        return get_playlists_for_curator(str(genre_id_or_title), limit=limit, storefront=storefront, debug=debug)
    # Поиск по названию среди категорий жанров
    for cat in get_categories_in_section("6456176470"):
        if cat["title"].lower() == (genre_id_or_title or "").lower() or cat["id"] == genre_id_or_title:
            return get_playlists_for_curator(cat["id"], limit=limit, storefront=storefront, debug=debug)
    # fallback search
    return search_playlists(str(genre_id_or_title), limit=limit, storefront=storefront, debug=debug)


def get_chart_playlists(
    limit: int = 30,
    storefront: str = DEFAULT_STOREFRONT,
    debug: bool = False,
) -> List[Dict[str, str]]:
    """Топ-плейлисты из чартов Apple Music (most-played)."""
    token = _get_token(debug=debug)
    if not token:
        return []

    sess = _session()
    headers = _api_headers(token)
    lang = f"{storefront}-{storefront.upper()}" if storefront != "us" else "en-US"
    out: List[Dict[str, str]] = []
    seen = set()
    offset = 0
    page_size = min(20, limit)

    while len(out) < limit:
        try:
            r = sess.get(
                f"https://amp-api.music.apple.com/v1/catalog/{storefront}/charts",
                headers=headers,
                params={
                    "types": "playlists",
                    "chart": "most-played",
                    "limit": page_size,
                    "offset": offset,
                    "l": lang,
                },
                timeout=25,
            )
        except Exception as e:
            if debug:
                print(f"[debug] charts: {e}")
            break
        if r.status_code != 200:
            if debug:
                print(f"[debug] charts status {r.status_code}: {r.text[:200]}")
            break
        try:
            data = r.json()
        except Exception:
            break

        charts = data.get("results", {}).get("playlists") or []
        items = []
        for chart in charts:
            items.extend(chart.get("data") or [])

        if not items:
            break

        for it in items:
            pid = it.get("id") or ""
            if not pid or pid in seen:
                continue
            seen.add(pid)
            attrs = it.get("attributes") or {}
            name = (attrs.get("name") or "").strip()
            if not name:
                continue
            curator = (attrs.get("curatorName") or "").strip()
            track_count = attrs.get("trackCount")
            label = name
            if curator:
                label = f"{name} — {curator}"
            if track_count:
                label = f"{label} ({track_count})"
            out.append({
                "id": pid,
                "title": name,
                "label": label,
                "curator": curator,
                "url": f"https://music.apple.com/{storefront}/playlist/{pid}",
                "storefront": storefront,
            })
            if len(out) >= limit:
                break

        # Пагинация через next у первого chart-блока
        next_path = None
        for chart in charts:
            next_path = chart.get("next")
            if next_path:
                break
        if not next_path or len(items) < page_size:
            break
        offset += page_size
        time.sleep(0.15)

    return out[:limit]


def _track_from_item(item: dict) -> Optional[Dict[str, str]]:
    if not isinstance(item, dict):
        return None
    attrs = item.get("attributes") or {}
    name = (attrs.get("name") or "").strip()
    if not name:
        return None
    artist = (attrs.get("artistName") or "").strip()
    return {"artist": artist, "title": name}


def get_playlist(
    playlist_id_or_url: str,
    storefront: Optional[str] = None,
    debug: bool = False,
) -> Tuple[str, List[Dict[str, str]]]:
    """Загрузить все треки плейлиста. Возвращает (title, tracks)."""
    parsed = get_playlist_id_from_url(playlist_id_or_url)
    if parsed:
        sf, pid = parsed
        if storefront:
            sf = storefront
    else:
        pid = (playlist_id_or_url or "").strip()
        sf = storefront or DEFAULT_STOREFRONT
        if not pid:
            return "Apple Music Playlist", []

    token = _get_token(debug=debug)
    if not token:
        return f"Apple Music {pid}", []

    sess = _session()
    headers = _api_headers(token)
    lang = f"{sf}-{sf.upper()}" if sf != "us" else "en-US"

    # Мета плейлиста
    title = f"Apple Music {pid}"
    try:
        r = sess.get(
            f"https://amp-api.music.apple.com/v1/catalog/{sf}/playlists/{pid}",
            headers=headers,
            params={"l": lang},
            timeout=25,
        )
        if r.status_code == 200:
            data = r.json()
            items = data.get("data") or []
            if items:
                attrs = items[0].get("attributes") or {}
                title = (attrs.get("name") or title).strip()
    except Exception as e:
        if debug:
            print(f"[debug] playlist meta: {e}")

    # Треки с пагинацией
    tracks: List[Dict[str, str]] = []
    url = f"https://amp-api.music.apple.com/v1/catalog/{sf}/playlists/{pid}/tracks"
    params = {"l": lang, "limit": 100}
    page = 0

    while url:
        page += 1
        try:
            r = sess.get(url, headers=headers, params=params if page == 1 else None, timeout=30)
        except Exception as e:
            if debug:
                print(f"[debug] tracks page {page}: {e}")
            break
        if r.status_code != 200:
            if debug:
                print(f"[debug] tracks status {r.status_code}: {r.text[:200]}")
            break
        try:
            data = r.json()
        except Exception:
            break

        for item in data.get("data") or []:
            t = _track_from_item(item)
            if t:
                tracks.append(t)

        next_path = data.get("next")
        if next_path:
            if next_path.startswith("http"):
                url = next_path
            else:
                url = "https://amp-api.music.apple.com" + next_path
            params = None
            time.sleep(0.12)
        else:
            url = None

        if debug:
            print(f"[debug] page {page}: всего {len(tracks)}")

    if debug:
        print(f"[debug] playlist {pid}: {title!r} — {len(tracks)} треков")
    return title, tracks


def save_json(tracks: List[Dict], path: str = JSON_OUT) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tracks, f, ensure_ascii=False, indent=2)
    return os.path.abspath(path)
