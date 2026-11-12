#!/usr/bin/env python3
"""Парсер Spotify: категории, поиск, треки через публичный Web API.

- Анонимный access token из публичного embed (без OAuth).
- Поиск и категории — через pathfinder searchDesktop.
- Треки — через api.spotify.com/v1/playlists/{id}/tracks с пагинацией.
- Прокси задаётся снаружи через set_proxy().
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

JSON_OUT = "spotify_selected_playlist.json"

EXPLORE_GENRE_ID = "0JQ5DArNBzkmxXHCqFLx3c"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}

STATIC_CATEGORIES: List[Dict[str, str]] = [
    {"id": "0JQ5DAqbMKFNQ0fGp4byGU", "title": "Afro"},
    {"id": "0JQ5DAqbMKFFtlLYUHv8bT", "title": "Alternative"},
    {"id": "0JQ5DAqbMKFLjmiZRss79w", "title": "Ambient"},
    {"id": "0JQ5DAqbMKFziKOShCi009", "title": "Anime"},
    {"id": "0JQ5DAqbMKFQ1UFISXj59F", "title": "Arab"},
    {"id": "0JQ5DAqbMKFx0uLQR2okcc", "title": "At Home"},
    {"id": "0JQ5DAqbMKFQiK2EHwyjcU", "title": "Blues"},
    {"id": "0JQ5DAqbMKFRwqWSTaphoL", "title": "Broadway"},
    {"id": "0JQ5DAqbMKFObNLOHydSW8", "title": "Caribbean"},
    {"id": "0JQ5DAudkNjCgYMM0TZXDw", "title": "Charts"},
    {"id": "0JQ5DAqbMKFFzDl7qN9Apr", "title": "Chill"},
    {"id": "0JQ5DAqbMKFy0OenPG51Av", "title": "Christian & Gospel"},
    {"id": "0JQ5DAqbMKFPrEiAOxgac3", "title": "Classical"},
    {"id": "0JQ5DAqbMKFRY5ok2pxXJ0", "title": "Cooking & Dining"},
    {"id": "0JQ5DAqbMKFKLfwjuJMoNC", "title": "Country"},
    {"id": "0JQ5DAqbMKFHOzuVTgTizF", "title": "Dance/Electronic"},
    {"id": "0JQ5DAqbMKFIVNxQgRNSg0", "title": "Decades"},
    {"id": "0JQ5DAtOnAEpjOgUKwXyxj", "title": "Discover"},
    {"id": "0JQ5DAqbMKFIxnofjQmnmn", "title": "Disney"},
    {"id": "0JQ5DAqbMKFPw634sFwguI", "title": "EQUAL"},
    {"id": "0JQ5DAqbMKFKsM0jjZhW3J", "title": "Fall"},
    {"id": "0JQ5DAqbMKFCbimwdOYlsl", "title": "Focus"},
    {"id": "0JQ5DAqbMKFy78wprEpAjl", "title": "Folk & Acoustic"},
    {"id": "0JQ5DAqbMKFImHYGo3eTSg", "title": "Fresh Finds"},
    {"id": "0JQ5DAqbMKFFsW9N8maB6z", "title": "Funk & Disco"},
    {"id": "0JQ5DAqbMKFCfObibaOZbv", "title": "Gaming"},
    {"id": "0JQ5DAqbMKFOzQeOmemkQc", "title": "Hip-Hop"},
    {"id": "0JQ5DAqbMKFAXlCG6QvYQ4", "title": "Indie"},
    {"id": "0JQ5DAqbMKFOTGtSOysBYE", "title": "K-pop"},
    {"id": "0JQ5DAqbMKFQIL0AXnG5L5", "title": "Latin"},
    {"id": "0JQ5DAqbMKFSi39LMRT0Cy", "title": "Music"},
    {"id": "0JQ5DAqbMKFEC4WFtoNRpw", "title": "Pop"},
    {"id": "0JQ5DAqbMKFRiRJrIQpX7m", "title": "Punk"},
    {"id": "0JQ5DAqbMKFDBgbRd7gVn0", "title": "Rock"},
    {"id": "0JQ5DAqbMKFRKbHIboYMDb", "title": "R&B"},
]

_SEARCH_HASH = "60efc08b8017f382e73ba2e02ac03d3c3b209610de99da618f36252e457665dd"

_token_cache: Optional[Tuple[str, float]] = None

# Прокси для Spotify (SOCKS5 или HTTP). Задаётся через set_proxy().
_PROXY_URL: Optional[str] = None


def set_proxy(proxy_url: str) -> None:
    """Установить прокси для всех запросов Spotify.

    Пример: 'socks5://user:pass@host:port' или 'http://host:port'.
    Пустая строка — прокси отключён.
    """
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


def _get_anon_token(debug: bool = False) -> Optional[str]:
    global _token_cache
    if _token_cache and time.time() < _token_cache[1]:
        return _token_cache[0]
    url = "https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M"
    try:
        r = _session().get(url, timeout=25)
    except Exception as e:
        if debug:
            print(f"[debug] embed token HTTP: {e}")
        return None
    if r.status_code != 200:
        if debug:
            print(f"[debug] embed token status {r.status_code}")
        return None
    m = re.search(r'"accessToken":"([^"]+)"', r.text)
    if not m:
        if debug:
            print("[debug] accessToken not found")
        return None
    token = m.group(1)
    _token_cache = (token, time.time() + 50 * 60)
    return token


def get_categories() -> List[Dict[str, str]]:
    return list(STATIC_CATEGORIES)


def get_playlist_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    m = re.search(r"(?:playlist[/:]|spotify:playlist:)([a-zA-Z0-9]{22})", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9]{22}", url):
        return url
    return None


def get_genre_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"/genre/(0JQ5D[A-Za-z0-9]+)", url or "")
    return m.group(1) if m else None


def _genre_title_by_id(genre_id: str) -> Optional[str]:
    for c in STATIC_CATEGORIES:
        if c["id"] == genre_id:
            return c["title"]
    return None


def _pathfinder_search_playlists(query: str, limit: int = 30, debug: bool = False) -> List[Dict[str, str]]:
    token = _get_anon_token(debug=debug)
    if not token:
        return []
    headers = {
        **_HEADERS,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "App-Platform": "WebPlayer",
        "Origin": "https://open.spotify.com",
        "Referer": "https://open.spotify.com/",
    }
    body = {
        "variables": {
            "searchTerm": query,
            "offset": 0,
            "limit": min(limit, 50),
            "numberOfTopResults": 10,
            "includeAudiobooks": False,
        },
        "operationName": "searchDesktop",
        "extensions": {
            "persistedQuery": {"version": 1, "sha256Hash": _SEARCH_HASH}
        },
    }
    try:
        r = _session().post(
            "https://api-partner.spotify.com/pathfinder/v1/query",
            headers=headers, json=body, timeout=25,
        )
    except Exception as e:
        if debug:
            print(f"[debug] pathfinder: {e}")
        return []
    if r.status_code != 200:
        if debug:
            print(f"[debug] pathfinder status {r.status_code}: {r.text[:200]}")
        return []
    try:
        data = r.json()
    except Exception:
        return []
    items = (
        data.get("data", {})
        .get("searchV2", {})
        .get("playlists", {})
        .get("items")
        or []
    )
    out: List[Dict[str, str]] = []
    seen = set()
    for it in items:
        d = it.get("data") or it
        if not isinstance(d, dict):
            continue
        uri = d.get("uri") or ""
        m = re.search(r"playlist:([a-zA-Z0-9]{22})", uri)
        if not m:
            continue
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        name = (d.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "id": pid,
            "title": name,
            "url": f"https://open.spotify.com/playlist/{pid}",
        })
    return out


def search_playlists(query: str, limit: int = 30, debug: bool = False) -> List[Dict[str, str]]:
    return _pathfinder_search_playlists(query, limit=limit, debug=debug)


def get_playlists_for_genre(genre_id_or_title: str, debug: bool = False) -> List[Dict[str, str]]:
    title = genre_id_or_title
    if re.match(r"^0JQ5D", genre_id_or_title or ""):
        mapped = _genre_title_by_id(genre_id_or_title)
        if mapped:
            title = mapped
        else:
            title = genre_id_or_title

    queries = [title, f"{title} hits", f"best {title}", f"{title} playlist"]
    seen = set()
    out: List[Dict[str, str]] = []
    for q in queries:
        chunk = _pathfinder_search_playlists(q, limit=20, debug=debug)
        for p in chunk:
            if p["id"] not in seen:
                seen.add(p["id"])
                out.append(p)
        if len(out) >= 40:
            break
    return out[:40]


def _api_get(url: str, token: str, params: Optional[dict] = None,
             debug: bool = False) -> Optional[dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        r = requests.get(
            url, headers=headers, params=params or {},
            timeout=25, proxies=_proxies(),
        )
    except Exception as e:
        if debug:
            print(f"[debug] api_get HTTP: {e}")
        return None
    if r.status_code != 200:
        if debug:
            print(f"[debug] api_get {r.status_code}: {r.text[:200]}")
        return None
    try:
        return r.json()
    except Exception:
        return None


def _track_from_api_item(item) -> Optional[Dict[str, str]]:
    if not isinstance(item, dict):
        return None
    track = item.get("track") or item.get("item")
    if not isinstance(track, dict):
        return None
    if track.get("is_local"):
        return None
    name = (track.get("name") or "").strip()
    if not name:
        return None
    artists = track.get("artists") or []
    artist = ", ".join(
        (a.get("name") or "").strip()
        for a in artists
        if isinstance(a, dict) and a.get("name")
    )
    return {"artist": artist, "title": name}


def _fetch_playlist_via_api(pid: str, token: str, debug: bool = False) -> Tuple[str, List[Dict[str, str]]]:
    data = _api_get(
        f"https://api.spotify.com/v1/playlists/{pid}",
        token,
        params={"market": "US", "additional_types": "track"},
        debug=debug,
    )
    if not data:
        return "", []

    title = (data.get("name") or f"Spotify {pid}").strip()
    tracks: List[Dict[str, str]] = []

    tracks_block = data.get("tracks") or {}
    for item in tracks_block.get("items") or []:
        t = _track_from_api_item(item)
        if t:
            tracks.append(t)

    next_url = tracks_block.get("next")
    page = 1
    while next_url:
        page += 1
        if debug:
            print(f"[debug] tracks page {page}: уже {len(tracks)}")
        page_data = _api_get(next_url, token, debug=debug)
        if not page_data:
            break
        for item in page_data.get("items") or []:
            t = _track_from_api_item(item)
            if t:
                tracks.append(t)
        next_url = page_data.get("next")
        time.sleep(0.1)

    if debug:
        print(f"[debug] api playlist {pid}: {title!r} — {len(tracks)} треков")
    return title, tracks


def _fetch_embed_entity(playlist_id: str, debug: bool = False) -> Optional[Dict[str, Any]]:
    url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    try:
        r = _session().get(url, timeout=25)
    except Exception as e:
        if debug:
            print(f"[debug] embed HTTP: {e}")
        return None
    if r.status_code != 200:
        return None
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        r.text, re.DOTALL,
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    entity = (
        data.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("data", {})
        .get("entity")
    )
    return entity if isinstance(entity, dict) else None


def get_playlist(playlist_id_or_url: str, debug: bool = False) -> Tuple[str, List[Dict[str, str]]]:
    pid = get_playlist_id_from_url(playlist_id_or_url) or (playlist_id_or_url or "").strip()
    if not pid:
        return "Spotify Playlist", []

    token = _get_anon_token(debug=debug)
    if token:
        title, tracks = _fetch_playlist_via_api(pid, token, debug=debug)
        if tracks:
            return title, tracks
        if debug:
            print("[debug] API не отдал треки, пробую embed")

    entity = _fetch_embed_entity(pid, debug=debug)
    if not entity:
        return f"Spotify {pid}", []

    title = (entity.get("name") or entity.get("title") or f"Spotify {pid}").strip()
    tracks: List[Dict[str, str]] = []
    for t in entity.get("trackList") or []:
        if not isinstance(t, dict):
            continue
        name = (t.get("title") or "").strip()
        artist = (t.get("subtitle") or "").strip()
        if name:
            tracks.append({"artist": artist, "title": name})
    return title, tracks


def save_json(tracks: List[Dict], path: str = JSON_OUT) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tracks, f, ensure_ascii=False, indent=2)
    return os.path.abspath(path)