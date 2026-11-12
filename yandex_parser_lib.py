#!/usr/bin/env python3
"""Библиотека парсинга Яндекс.Музыки (console)."""

from __future__ import annotations

import json
import os
from typing import List, Optional
from urllib.parse import quote

from yandex_music import Client

TOKEN_FILE = "yandex_token.txt"
JSON_OUT = "yandex_selected_playlist.json"
API_BASE = "https://api.music.yandex.net"

_client = None


def _read_token() -> str:
    token = ""
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip().strip('"').strip("'")
    if token.lower().startswith("oauth "):
        token = token[6:].strip()
    return token


def _get_client():
    """Ленивая инициализация клиента + Device Flow при отсутствии токена."""
    global _client
    if _client is not None:
        return _client

    token = _read_token()
    if not token:
        print("[..] Токен Яндекс.Музыки не найден, запускаю Device Flow...")

        def on_code(code):
            print(f"\n>>> Открой: {code.verification_url}")
            print(f">>> Введи код: {code.user_code}\n")

        tmp = Client()
        token_obj = tmp.device_auth(on_code=on_code)
        token = token_obj.access_token
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(token)
        print(f"[OK] Токен сохранён в {TOKEN_FILE}")

    _client = Client(token).init()
    return _client


def _api_get(path: str, params: Optional[dict] = None):
    token = _read_token()
    if not token:
        return None
    try:
        import requests
    except ImportError:
        return None
    headers = {
        "Authorization": f"OAuth {token}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "X-Yandex-Music-Client": "YandexMusicDesktopAppWindows/5.95.0",
    }
    try:
        r = requests.get(f"{API_BASE}{path}", params=params or {}, headers=headers, timeout=25)
        if r.status_code >= 400:
            print(f"[yandex] API {path} → HTTP {r.status_code}")
            return None
        data = r.json()
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data
    except Exception as e:
        print(f"[yandex] API {path} error: {e}")
        return None


def get_sections():
    client = _get_client()
    metatags = client.metatags()
    trees = getattr(metatags, "trees", None) or []
    return [getattr(t, "title", "?") for t in trees]


def get_metatags_in_section(section_title):
    client = _get_client()
    metatags = client.metatags()
    trees = getattr(metatags, "trees", None) or []

    for tree in trees:
        if getattr(tree, "title", None) != section_title:
            continue
        result = []

        def walk(leaves):
            for leaf in leaves or []:
                tag = getattr(leaf, "tag", None)
                title = getattr(leaf, "title", None) or tag or "Unknown"
                if tag:
                    result.append({"title": title, "tag": tag})
                sub = getattr(leaf, "leaves", None) or []
                if sub:
                    walk(sub)

        walk(getattr(tree, "leaves", []) or [])
        return result
    return []


def _pl_from_lib_obj(pl) -> Optional[dict]:
    if not pl:
        return None
    return {
        "title": getattr(pl, "title", None) or "Unknown",
        "owner": getattr(getattr(pl, "owner", None), "name", "") or "",
        "kind": getattr(pl, "kind", None),
        "owner_id": getattr(getattr(pl, "owner", None), "uid", None),
        "track_count": getattr(pl, "track_count", 0) or 0,
        "uuid": getattr(pl, "playlist_uuid", None),
    }


def _parse_api_playlists(res) -> List[dict]:
    out: List[dict] = []
    if not res:
        return out
    if isinstance(res, list):
        playlists = res
    elif isinstance(res, dict):
        playlists = res.get("playlists") or res.get("items") or []
    else:
        return out
    for item in playlists:
        if isinstance(item, dict) and isinstance(item.get("playlist"), dict):
            pl = item["playlist"]
        elif isinstance(item, dict):
            pl = item
        else:
            continue
        owner = pl.get("owner") or {}
        if not isinstance(owner, dict):
            owner = {}
        owner_id = pl.get("uid") or owner.get("uid") or owner.get("login")
        uuid = pl.get("playlistUuid") or pl.get("uuid") or pl.get("playlist_uuid")
        kind = pl.get("kind")
        if not uuid and (owner_id is None or kind is None):
            continue
        out.append({
            "title": pl.get("title") or "Unknown",
            "owner": owner.get("name") or "",
            "kind": kind,
            "owner_id": owner_id,
            "track_count": pl.get("trackCount") or pl.get("track_count") or 0,
            "uuid": uuid,
        })
    if not out and isinstance(res, dict):
        for it in res.get("ids") or []:
            if isinstance(it, dict) and it.get("uid") is not None and it.get("kind") is not None:
                out.append({
                    "title": f"Плейлист {it['kind']}",
                    "owner": "",
                    "kind": it["kind"],
                    "owner_id": it["uid"],
                    "track_count": 0,
                    "uuid": None,
                })
    return out


def get_playlists_for_tag(tag, limit: int = 50):
    """Плейлисты метатега: lib → API fallback → search."""
    tag = (tag or "").strip()
    if not tag:
        return []
    out: List[dict] = []

    try:
        client = _get_client()
        result = None
        for kwargs in ({"playlists_count": limit}, {"playlistsCount": limit}, {}):
            try:
                result = client.metatag(tag, **kwargs)
                if result:
                    break
            except TypeError:
                continue
            except Exception as e:
                print(f"[yandex] metatag({tag!r}) lib: {e}")
        if result:
            for item in getattr(result, "playlists", None) or []:
                row = _pl_from_lib_obj(getattr(item, "playlist", item))
                if row:
                    out.append(row)
    except Exception as e:
        print(f"[yandex] metatag({tag!r}): {e}")

    if out:
        return out[:limit]

    tag_enc = quote(tag, safe="")
    for path, params in (
        (f"/metatags/{tag_enc}", {"playlistsCount": limit}),
        (f"/metatags/{tag_enc}", {"playlists-count": limit}),
        (f"/metatags/{tag_enc}/playlists", {"offset": 0, "limit": limit}),
    ):
        res = _api_get(path, params)
        out = _parse_api_playlists(res)
        if out:
            print(f"[yandex] tag {tag!r}: via {path}")
            return out[:limit]

    try:
        found = search_playlists(tag, limit=limit)
        if found:
            print(f"[yandex] tag {tag!r}: fallback search → {len(found)}")
            return found
    except Exception as e:
        print(f"[yandex] search fallback: {e}")

    print(f"[yandex] tag {tag!r}: плейлистов не найдено")
    return []


def fetch_tracks_for_playlist(playlist_info):
    client = _get_client()
    playlist = None
    try:
        if playlist_info.get("uuid"):
            playlist = client.playlist(playlist_info["uuid"])
        elif playlist_info.get("kind") is not None and playlist_info.get("owner_id"):
            playlist = client.users_playlists(playlist_info["kind"], playlist_info["owner_id"])
        else:
            return []
    except Exception as e:
        print(f"[FAIL] Ошибка загрузки плейлиста: {e}")
        return []

    if not playlist:
        return []

    tracks = getattr(playlist, "tracks", []) or []
    parsed = []
    for t in tracks:
        track = getattr(t, "track", t)
        if not track:
            continue
        artists_list = getattr(track, "artists", None)
        if artists_list:
            artists = ", ".join(a.name for a in artists_list if a and a.name)
        else:
            artists = "Unknown"
        title = getattr(track, "title", None) or "Unknown"
        parsed.append({"artist": artists, "title": title})
    return parsed


def save_tracks_json(tracks, path=JSON_OUT):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tracks, f, ensure_ascii=False, indent=2)
    return os.path.abspath(path)


def search_playlists(query, limit=30):
    client = _get_client()
    try:
        result = client.search(query, type_="playlist", page=0)
    except Exception as e:
        print(f"[FAIL] Поиск Яндекс: {e}")
        return []
    out = []
    playlists_block = getattr(result, "playlists", None)
    if not playlists_block:
        return []
    for pl in (getattr(playlists_block, "results", None) or [])[:limit]:
        if not pl:
            continue
        row = _pl_from_lib_obj(pl)
        if row:
            out.append(row)
    return out
