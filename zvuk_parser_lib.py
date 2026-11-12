#!/usr/bin/env python3
"""Парсер Zvuk.com: жанры + подборки (как в dlna-emu) + поиск + прямая ссылка.

Жанры и вкладки — как на https://zvuk.com/genres (список из dlna_emu).
Подборки — curated-разделы (Популярное, Редакция, В машину и т.д.).
Плейлисты подтягиваются через поиск zvuk-music / профиль редакции.
Треки — через публичный API (анонимный токен).
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from zvuk_music import Client, ZvukMusicError

JSON_OUT = "zvuk_selected_playlist.json"

# Жанры как в dlna_emu/zvuk_client.py (zvuk.com/genres)
GENRES: List[Dict] = [
    {"id": "pop", "title": "Поп музыка", "query": "поп",
     "tabs": ["Хиты", "Новинки", "Русский поп", "Зарубежный поп", "Поп 2000-х", "Поп 2010-х"]},
    {"id": "rusrap", "title": "Русский рэп", "query": "русский рэп",
     "tabs": ["Хиты", "Новинки", "Old school", "New school", "Лирика"]},
    {"id": "rock", "title": "Рок", "query": "рок",
     "tabs": ["Хиты", "Новинки", "Русский рок", "Зарубежный рок", "Классика рока"]},
    {"id": "shanson", "title": "Русский шансон", "query": "шансон",
     "tabs": ["Хиты", "Новинки", "Блатной", "Лирический"]},
    {"id": "electronic", "title": "Электронная музыка", "query": "электроника",
     "tabs": ["Хиты", "Новинки", "House", "Techno", "Trance", "Drum & Bass"]},
    {"id": "classical", "title": "Классическая музыка", "query": "классика",
     "tabs": ["Хиты", "Опера", "Симфонии", "Фортепиано"]},
    {"id": "folk", "title": "Русский фолк", "query": "фолк",
     "tabs": ["Хиты", "Новинки", "Народная", "Этно"]},
    {"id": "dance", "title": "Танцевальная музыка", "query": "танцевальная",
     "tabs": ["Хиты", "Новинки", "Клубная", "EDM"]},
    {"id": "jazz", "title": "Джаз", "query": "джаз",
     "tabs": ["Хиты", "Новинки", "Smooth jazz", "Vocal jazz"]},
    {"id": "rnb", "title": "R&B", "query": "r&b",
     "tabs": ["Хиты", "Новинки", "Soul", "Neo soul"]},
    {"id": "metal", "title": "Метал", "query": "метал",
     "tabs": ["Хиты", "Новинки", "Heavy", "Death", "Black"]},
    {"id": "hiphop", "title": "Хип-хоп", "query": "хип-хоп",
     "tabs": ["Хиты", "Новинки", "Зарубежный", "Trap"]},
    {"id": "alt", "title": "Альтернативная музыка", "query": "альтернатива",
     "tabs": ["Хиты", "Новинки", "Indie rock", "Post-punk"]},
    {"id": "blues", "title": "Блюз", "query": "блюз",
     "tabs": ["Хиты", "Новинки", "Classic blues"]},
    {"id": "phonk", "title": "Фонк", "query": "фонк",
     "tabs": ["Хиты", "Новинки", "Drift phonk", "Brazilian phonk"]},
    {"id": "indie", "title": "Инди", "query": "инди",
     "tabs": ["Хиты", "Новинки", "Indie pop", "Indie rock"]},
    {"id": "live", "title": "Лайв концерты", "query": "концерт live",
     "tabs": ["Хиты", "Рок live", "Поп live"]},
    {"id": "world", "title": "Музыка мира", "query": "world music",
     "tabs": ["Хиты", "Новинки", "Latin", "African", "Asian"]},
]

# Подборки как в dlna_emu (discovery / главная)
CURATED: List[Dict] = [
    {"id": "popular_playlists", "title": "Популярное", "source": "search", "query": "популярные плейлисты"},
    {"id": "editorial", "title": "Редакция", "source": "editorial"},
    {"id": "soundtracks", "title": "Саундтреки", "source": "search", "query": "музыка из кино саундтрек"},
    {"id": "best_albums", "title": "Лучшие альбомы XXI века", "source": "search", "query": "лучшие альбомы"},
    {"id": "editors", "title": "Выбор редакции", "source": "search", "query": "выбор редакции"},
    {"id": "new_faces", "title": "Новые лица", "source": "search", "query": "новые лица новинки"},
    {"id": "karaoke", "title": "Караоке", "source": "search", "query": "караоке"},
    {"id": "motivation", "title": "Мотивация", "source": "search", "query": "мотивация"},
    {"id": "epochs", "title": "Эпохи", "source": "search", "query": "хиты по годам эпохи"},
    {"id": "car", "title": "В машину", "source": "search", "query": "музыка в машину за рулем"},
    {"id": "training", "title": "Тренировка", "source": "search", "query": "тренировка"},
    {"id": "active", "title": "Активность", "source": "search", "query": "активность спорт"},
    {"id": "calm", "title": "Спокойствие", "source": "search", "query": "спокойствие релакс"},
    {"id": "romantic", "title": "Романтика", "source": "search", "query": "романтика"},
    {"id": "sleep", "title": "Сон", "source": "search", "query": "сон колыбельная"},
    {"id": "work_study", "title": "Работа и учёба", "source": "search", "query": "работа учёба фокус"},
    {"id": "nature", "title": "Природотерапия", "source": "search", "query": "природа звуки природы"},
    {"id": "party", "title": "Вечеринка", "source": "search", "query": "вечеринка"},
    {"id": "melancholy", "title": "Меланхолия", "source": "search", "query": "грустно меланхолия"},
    {"id": "relax", "title": "Релакс и чилл", "source": "search", "query": "релакс чилл"},
    {"id": "weekend", "title": "Выходные", "source": "search", "query": "выходные weekend"},
    {"id": "food", "title": "Еда и готовка", "source": "search", "query": "готовка кухня"},
    {"id": "wellness", "title": "Wellness", "source": "search", "query": "wellness медитация"},
    {"id": "charts", "title": "Чарты", "source": "search", "query": "топ 100 чарт"},
]

# Редакционные профили (zvuk.com/profile/…)
EDITORIAL_PROFILES: List[Dict] = [
    {"id": "438615003", "title": "ГЛАВНЫЕ НОВИНКИ"},
    {"id": "21322059", "title": "Звук"},
    {"id": "386097314", "title": "HiFi"},
]

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    token = Client.get_anonymous_token()
    _client = Client(token=token)
    return _client


def _tracks_batched(client, ids, batch_size=50):
    out = []
    for i in range(0, len(ids), batch_size):
        batch = ids[i : i + batch_size]
        try:
            chunk = client.get_tracks(batch)
        except ZvukMusicError as e:
            print(f"[!] get_tracks ошибка на батче {i}: {e}")
            continue
        if isinstance(chunk, list):
            out.extend(chunk)
        else:
            out.append(chunk)
    return out


def _track_to_dict(t):
    return {
        "artist": (t.get_artists_str() or "").strip(),
        "title": (t.title or "").strip(),
    }


def get_genres():
    """Список жанров: [{id, title, query, tabs}, ...]."""
    return GENRES


def get_genre_by_id(genre_id: str) -> Optional[Dict]:
    gid = (genre_id or "").strip()
    for g in GENRES:
        if g["id"] == gid or g["title"] == gid:
            return g
    return None


def list_curated_sections():
    """Разделы подборок (как list_curated_sections в dlna)."""
    return CURATED


def list_editorial_profiles():
    return EDITORIAL_PROFILES


def search_playlists(query, limit=30, debug=False):
    """Ищет плейлисты: [{'title', 'id', 'description'}, ...]."""
    client = _get_client()
    try:
        result = client.search(query, limit=limit)
    except ZvukMusicError as e:
        if debug:
            print(f"[debug] search error: {e}")
        return []
    out = []
    playlists = getattr(result, "playlists", None)
    if not playlists:
        return []
    items = getattr(playlists, "items", None) or playlists
    for pl in items:
        if isinstance(pl, dict):
            title = (pl.get("title") or "").strip()
            pid = str(pl.get("id") or "")
            desc = (pl.get("description") or "").strip()
        else:
            title = (getattr(pl, "title", None) or "").strip()
            pid = str(getattr(pl, "id", "") or "")
            desc = (getattr(pl, "description", None) or "").strip()
        if title and pid:
            out.append({"title": title, "id": pid, "description": desc})
    return out


def get_playlists_for_genre(genre, tab=None, limit=30):
    """Плейлисты для жанра (и опционально подвкладки)."""
    if isinstance(genre, str):
        genre = get_genre_by_id(genre) or {"title": genre, "query": genre}
    base = genre.get("query") or genre.get("title") or ""
    if tab:
        query = f"{base} {tab}".strip()
    else:
        query = base
    return search_playlists(query, limit=limit)


def get_curated_playlists(section_id: str, limit: int = 40):
    """Плейлисты раздела подборок: search / editorial profiles."""
    sid = (section_id or "").strip()
    if not sid:
        return []

    # Прямой profile id
    if sid.startswith("profile:"):
        return search_playlists(f"profile {sid.split(':', 1)[1]}", limit=limit)

    for ep in EDITORIAL_PROFILES:
        if ep["id"] == sid:
            # Ищем плейлисты профиля по названию / id
            return search_playlists(ep["title"], limit=limit)

    sec = next((s for s in CURATED if s["id"] == sid), None)
    if not sec:
        return []
    if sec.get("source") == "editorial":
        # Контент — список профилей, не плейлисты
        return []
    q = sec.get("query") or sec.get("title") or sid
    return search_playlists(q, limit=limit)


def get_playlist(playlist_id, debug=False):
    """Возвращает (title, [{'artist', 'title'}, ...])."""
    client = _get_client()
    pid = str(playlist_id)

    try:
        pl = client.get_playlist(pid)
    except ZvukMusicError as e:
        if debug:
            print(f"[debug] get_playlist({pid}) error: {e}")
        return f"Zvuk Playlist {pid}", []

    title = (pl.title or f"Zvuk Playlist {pid}").strip()

    ids = [t.id for t in pl.tracks if getattr(t, "id", None)]
    if not ids:
        try:
            simple = client.get_playlist_tracks(pid, limit=100, offset=0)
            ids = [getattr(t, "id", None) for t in simple if getattr(t, "id", None)]
        except Exception:
            pass
    if not ids:
        return title, []

    full = _tracks_batched(client, ids)
    tracks = [_track_to_dict(t) for t in full if getattr(t, "title", "")]
    return title, tracks


def get_playlist_id_from_url(url):
    """Извлекает числовой ID из URL вида https://zvuk.com/playlist/1062105."""
    m = re.search(r"/playlist/(\d+)", url or "")
    if m:
        return m.group(1)
    if (url or "").strip().isdigit():
        return url.strip()
    return None


def search(query, limit=25, debug=False):
    """Совместимость со старым API: [{'title', 'id'}, ...]."""
    return [{"title": p["title"], "id": p["id"]} for p in search_playlists(query, limit=limit, debug=debug)]


def save_json(tracks, path=JSON_OUT):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tracks, f, ensure_ascii=False, indent=2)
    return os.path.abspath(path)
