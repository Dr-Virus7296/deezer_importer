#!/usr/bin/env python3
"""Библиотека добавления треков в Deezer (для run.py)."""

import csv
import html as _html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse

try:
    from curl_cffi import requests as curl_requests
    USE_CURL_CFFI = True
except ImportError:
    import requests as curl_requests
    USE_CURL_CFFI = False

import requests

CONFIG_FILE = "config.cfg"


def _flush_stdin():
    """Сбросить накопившиеся нажатия (Enter и т.п.), чтобы не улетали в следующий input()."""
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


def _ask_yes_no(prompt, default=None):
    """Спросить y/n. Пустой ввод не принимается (если default=None)."""
    _flush_stdin()
    while True:
        try:
            raw = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if not raw and default in ("y", "n"):
            return default == "y"
        if raw in ("y", "yes", "д", "да"):
            return True
        if raw in ("n", "no", "н", "нет"):
            return False
        print("[!] Введи y или n")



DEEZER_SEARCH = "https://www.deezer.com/ru/search/{}"
DEEZER_ARTIST = "https://www.deezer.com/ru/artist/{}"
API_BASE = "https://www.deezer.com/ajax/gw-light.php"
DEEZER_API = "https://api.deezer.com/2.0"
PUBLIC_API = "https://api.deezer.com"



# ====== Автологин (email+пароль -> ARL + ACCESS_TOKEN) ======
# Константы официального веб-клиента Deezer (pleezer / deezer-py / deemix).
_AUTOLOGIN_APP_ID = "447462"
_AUTOLOGIN_SALT = "a83bf7f38ad2f137e444727cfc3775cf"
_AUTOLOGIN_URL = "https://connect.deezer.com/oauth/user_auth.php"


def generate_deezer_credentials(email: str, password: str, timeout: int = 15) -> dict:
    """
    Логин в Deezer по email+паролю без браузера.
    Возвращает {"arl": ..., "access_token": ...}.
    """
    import hashlib

    if not email or not password:
        raise ValueError("Нужны email и пароль")

    password_md5 = hashlib.md5(password.encode("utf-8")).hexdigest()
    to_hash = f"{_AUTOLOGIN_APP_ID}{email}{password_md5}{_AUTOLOGIN_SALT}"
    h = hashlib.md5(to_hash.encode("utf-8")).hexdigest()

    params = {
        "app_id": _AUTOLOGIN_APP_ID,
        "login": email,
        "password": password_md5,
        "hash": h,
    }

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    r = session.get(_AUTOLOGIN_URL, params=params, timeout=timeout)
    r.raise_for_status()

    try:
        data = r.json()
    except ValueError as e:
        raise RuntimeError(f"Deezer вернул не-JSON ответ: {r.text[:200]!r}") from e

    access_token = data.get("access_token")
    if not access_token:
        err = data.get("error") or data
        raise RuntimeError(f"Логин не удался: {err}")

    arl = None
    for c in session.cookies:
        if c.name.lower() == "arl":
            arl = c.value
            break

    return {"arl": arl or "", "access_token": access_token}


def save_deezer_config(arl: str, access_token: str, cookies: str = "",
                       path: str = CONFIG_FILE, keep_existing: bool = True) -> str:
    """Пишет ARL/ACCESS_TOKEN/COOKIES в config.cfg. Не затирает хвост после TRACKS:."""
    existing = {"ARL": "", "ACCESS_TOKEN": "", "COOKIES": "", "SPOTIFY_PROXY": ""}
    tracks_tail = ""
    if keep_existing and os.path.exists(path):
        raw = open(path, "r", encoding="utf-8").read()
        lines = raw.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.strip().upper() == "TRACKS:" or line.strip().upper().startswith("TRACKS:"):
                tracks_tail = "".join(lines[i:])
                break
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            for key in existing:
                if s.upper().startswith(key + "="):
                    existing[key] = s[len(key) + 1:].strip().strip('"').strip("'")
    if arl is not None and str(arl).strip():
        existing["ARL"] = str(arl).strip()
    if access_token is not None and str(access_token).strip():
        existing["ACCESS_TOKEN"] = str(access_token).strip()
    if cookies is not None and str(cookies).strip():
        existing["COOKIES"] = str(cookies).strip()

    body = (
        "# Deezer config\n"
        f"ARL={existing['ARL']}\n"
        f"ACCESS_TOKEN={existing['ACCESS_TOKEN']}\n"
        f"COOKIES={existing['COOKIES']}\n"
        f"SPOTIFY_PROXY={existing.get('SPOTIFY_PROXY', '')}\n"
        "\n"
    )
    if tracks_tail:
        body += tracks_tail
    else:
        body += "TRACKS:\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return os.path.abspath(path)


# ====== Unicode ======

def _to_nfc(s):
    if not s:
        return s
    return unicodedata.normalize("NFC", s)


def _strip_diacritics(s):
    if not s:
        return s
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ====== Конфиг ======

def load_deezer_config(path=CONFIG_FILE):
    if not os.path.exists(path):
        print(f"[FAIL] Не найден {path}")
        sys.exit(1)

    arl = None
    access_token = None
    cookies_raw = None

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n").rstrip("\r")
            s = line.strip()
            if s.startswith("#") or not s:
                continue
            if s.upper() == "TRACKS:":
                break
            if s.upper().startswith("ARL="):
                arl = s[4:].strip().strip('"').strip("'")
            elif s.upper().startswith("ACCESS_TOKEN="):
                access_token = s[13:].strip().strip('"').strip("'")
            elif s.upper().startswith("COOKIES="):
                cookies_raw = s[8:].strip().strip('"').strip("'")

    return arl, access_token, cookies_raw


# ====== Парсинг строк ======

def parse_track(line):
    line = _to_nfc(line)
    if " - " not in line:
        return [], line.strip()

    artist_part, title = line.split(" - ", 1)
    title = title.strip()

    artist_part = re.sub(
        r'\s+(feat\.?|ft\.?|featuring|при\s+уч\.?)\s+',
        ', ',
        artist_part,
        flags=re.IGNORECASE,
    )

    raw_artists = re.split(r'\s*[,&]\s*|\s+[x×]\s+', artist_part)

    artists = []
    for a in raw_artists:
        a = a.strip()
        if not a:
            continue
        a = a.strip('"').strip("'").strip()
        if a:
            artists.append(a)

    return artists, title


def _norm_words(s, drop_short=True):
    s = _to_nfc(s or "")
    if not s:
        return []
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    all_words = s.split()
    if not drop_short:
        return all_words
    long_words = [w for w in all_words if len(w) > 1]
    if long_words:
        return long_words
    return all_words


def normalize(s):
    s = _strip_diacritics(_to_nfc(s or ""))
    return " ".join(_norm_words(s, drop_short=True))


def artist_match(query_artist, result_artist):
    if not query_artist:
        return True
    q = normalize(query_artist)
    r = normalize(result_artist)
    if not q or not r:
        return False
    if q == r:
        return True
    if q in r or r in q:
        return True
    q_first = q.split()[0] if q.split() else ""
    r_first = r.split()[0] if r.split() else ""
    if q_first and q_first == r_first:
        return True
    q_words = set(q.split())
    r_words = set(r.split())
    if q_words & r_words:
        return True
    return False


def artists_match_any(query_artists, result_artist_str):
    if not query_artists:
        return True
    result_parts = re.split(r'\s*[,&]\s*|\s+[x×]\s+', result_artist_str or "")
    result_parts = [p.strip() for p in result_parts if p.strip()]
    for qa in query_artists:
        for rp in result_parts:
            if artist_match(qa, rp):
                return True
        if artist_match(qa, result_artist_str):
            return True
    return False


def _artist_has_at_least_one_word(query_artists, result_artist_str):
    if not query_artists:
        return True
    r_norm = normalize(result_artist_str or "")
    if not r_norm:
        return False
    r_words = set(r_norm.split())
    for qa in query_artists:
        q_norm = normalize(qa)
        if not q_norm:
            continue
        for w in q_norm.split():
            if len(w) > 1 and w in r_words:
                return True
    return False


def title_match(query_title, result_title):
    if not query_title:
        return True
    q = normalize(query_title)
    r = normalize(result_title)
    if not q or not r:
        q_raw = (query_title or "").strip().lower()
        r_raw = (result_title or "").strip().lower()
        if not q_raw or not r_raw:
            return False
        return q_raw == r_raw
    if q == r:
        return True
    if q in r or r in q:
        return True
    q_words = set(q.split())
    r_words = set(r.split())
    if not q_words or not r_words:
        return False
    overlap = len(q_words & r_words)
    min_len = min(len(q_words), len(r_words))
    return (overlap / min_len) >= 0.5 if min_len else False


# ====== HTML-парсинг ======

def _json_balanced(html, start):
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(html)):
        c = html[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
    return None


def extract_app_state(html):
    if not html:
        return None
    m = re.search(r'window\.__DZR_APP_STATE__\s*=\s*', html)
    if not m:
        return None
    json_start = html.find('{', m.end())
    if json_start == -1:
        return None
    try:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(html, json_start)
        return obj
    except Exception:
        pass
    raw = _json_balanced(html, json_start)
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return None


def _unescape_js_string(s):
    if not s:
        return s
    try:
        s = s.encode("utf-8").decode("unicode_escape")
    except Exception:
        pass
    s = s.replace('\\/', '/').replace('\\"', '"').replace("\\\\", "\\")
    s = _html.unescape(s)
    return s.strip()


def _tracks_regex_from_html(html):
    out = []
    seen = set()
    if not html:
        return out
    for m in re.finditer(r'"SNG_ID"\s*:\s*"?(\d+)"?', html):
        sn_id = m.group(1)
        if sn_id in seen:
            continue
        start = max(0, m.start() - 3000)
        end = min(len(html), m.end() + 3000)
        window = html[start:end]

        title = None
        for pat in (
            r'"SNG_TITLE"\s*:\s*"((?:[^"\\]|\\.)*)"',
            r'"TITLE"\s*:\s*"((?:[^"\\]|\\.)*)"',
        ):
            tm = re.search(pat, window)
            if tm:
                title = tm.group(1)
                break
        if not title:
            continue

        artist = ""
        for pat in (
            r'"ART_NAME"\s*:\s*"((?:[^"\\]|\\.)*)"',
            r'"ARTIST"\s*:\s*"((?:[^"\\]|\\.)*)"',
        ):
            am = re.search(pat, window)
            if am:
                artist = am.group(1)
                break

        title = _unescape_js_string(title)
        artist = _unescape_js_string(artist)
        if not title:
            continue
        seen.add(sn_id)
        out.append({
            "SNG_ID": sn_id,
            "SNG_TITLE": title,
            "ART_NAME": artist,
            "__TYPE__": "song",
        })
    return out


def _collect_tracks_from_state(state):
    out = []
    seen = set()

    def push(item):
        if not isinstance(item, dict):
            return
        title = (item.get("SNG_TITLE") or item.get("TITLE") or item.get("title") or "").strip()
        tid = item.get("SNG_ID") or item.get("id")
        if not title or tid is None:
            return
        if str(tid) in seen:
            return
        seen.add(str(tid))
        art = item.get("ART_NAME") or item.get("ARTIST") or ""
        if not art:
            artists = item.get("ARTISTS") or item.get("artists") or []
            if isinstance(artists, list) and artists:
                names = []
                for a in artists:
                    if isinstance(a, dict):
                        n = a.get("ART_NAME") or a.get("name") or ""
                        if n:
                            names.append(n)
                art = ", ".join(names)
        if isinstance(art, dict):
            art = art.get("ART_NAME") or art.get("name") or ""
        out.append({
            "SNG_ID": tid,
            "SNG_TITLE": title,
            "ART_NAME": str(art).strip(),
            "__TYPE__": "song",
        })

    def walk(obj, depth=0):
        if depth > 30:
            return
        if isinstance(obj, dict):
            push(obj)
            for v in obj.values():
                walk(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, depth + 1)

    walk(state)
    return out


def fetch_html(url, cookies_dict=None, debug=False):
    try:
        if USE_CURL_CFFI:
            r = curl_requests.get(url, impersonate="chrome120", cookies=cookies_dict or {}, timeout=20)
        else:
            r = curl_requests.get(url, cookies=cookies_dict or {}, timeout=20)
    except Exception as e:
        if debug:
            print(f"         [debug] HTTP ошибка: {e}")
        return None
    if r.status_code != 200:
        if debug:
            print(f"         [debug] HTTP {r.status_code} на {url}")
        return None
    return r.text


def search_html(query, cookies_dict=None, debug=False):
    tracks, albums, artists = [], [], []

    html = fetch_html(DEEZER_SEARCH.format(urllib.parse.quote(query)), cookies_dict, debug)
    if not html:
        if debug:
            print(f"         [debug] HTML пустой")
        return tracks, albums, artists

    state = extract_app_state(html)
    if state:
        for key in ("TRACK", "TOP_RESULT", "TRACKS"):
            tb = state.get(key, {})
            if isinstance(tb, dict):
                tracks.extend(tb.get("data", []) or [])
            elif isinstance(tb, list):
                tracks.extend(tb)
        deep = _collect_tracks_from_state(state)
        existing_ids = {str(t.get("SNG_ID")) for t in tracks if isinstance(t, dict) and t.get("SNG_ID")}
        for t in deep:
            if str(t.get("SNG_ID")) not in existing_ids:
                tracks.append(t)
        ab = state.get("ALBUM", {})
        if isinstance(ab, dict):
            albums.extend(ab.get("data", []))
        ar = state.get("ARTIST", {})
        if isinstance(ar, dict):
            artists.extend(ar.get("data", []))

    if not tracks:
        regex_tracks = _tracks_regex_from_html(html)
        if regex_tracks:
            tracks.extend(regex_tracks)
            if debug:
                print(f"         [debug] regex fallback: +{len(regex_tracks)} треков")

    if debug:
        print(f"         [debug] HTML state: T:{len(tracks)} A:{len(albums)} Ar:{len(artists)}")
    return tracks, albums, artists


def get_artist_tracks_html(artist_id, cookies_dict=None, debug=False):
    urls = [
        DEEZER_ARTIST.format(artist_id),
        f"https://www.deezer.com/ru/artist/{artist_id}/top_track",
    ]
    best = []
    for url in urls:
        html = fetch_html(url, cookies_dict, debug=debug)
        if not html:
            continue
        tracks = []
        state = extract_app_state(html)
        if state:
            tracks.extend(_collect_tracks_from_state(state))
        if not tracks:
            tracks = _tracks_regex_from_html(html)
        if debug:
            print(f"         [debug] artist HTML {url}: {len(tracks)} треков")
        if len(tracks) > len(best):
            best = tracks
        if len(best) > 200:
            break
        time.sleep(0.2)
    return best


# ====== Публичный API ======

def public_get(url, params=None, debug=False):
    """Обёртка над api.deezer.com с curl_cffi."""
    try:
        if USE_CURL_CFFI:
            r = curl_requests.get(url, params=params or {}, impersonate="chrome120", timeout=20)
        else:
            r = curl_requests.get(url, params=params or {}, timeout=20)
    except Exception as e:
        if debug:
            print(f"         [debug] public_get error: {e}")
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def public_search_artist(name, debug=False):
    d = public_get(f"{PUBLIC_API}/search/artist", params={"q": name, "limit": 10}, debug=debug)
    if not d:
        return []
    return d.get("data", []) or []


def public_artist_albums(artist_id, limit=300, debug=False):
    """Список альбомов артиста через публичный API (с пагинацией)."""
    out = []
    url = f"{PUBLIC_API}/artist/{artist_id}/albums"
    idx = 0
    while url and len(out) < limit:
        d = public_get(url, params={"limit": 100, "index": idx}, debug=debug)
        if not d:
            break
        batch = d.get("data", []) or []
        if not batch:
            break
        out.extend(batch)
        idx += len(batch)
        url = d.get("next")
        if not url or len(batch) < 100:
            break
        time.sleep(0.1)
    return out[:limit]


def public_album_tracks(album_id, debug=False):
    d = public_get(f"{PUBLIC_API}/album/{album_id}", debug=debug)
    if not d:
        return None, []
    title = d.get("title") or ""
    tracks_raw = (d.get("tracks") or {}).get("data") or []
    out = []
    for t in tracks_raw:
        tid = t.get("id")
        t_title = (t.get("title") or "").strip()
        if not tid or not t_title:
            continue
        t_artist = ""
        artist = t.get("artist") or {}
        if isinstance(artist, dict):
            t_artist = (artist.get("name") or "").strip()
        out.append({
            "SNG_ID": tid,
            "SNG_TITLE": t_title,
            "ART_NAME": t_artist,
            "__TYPE__": "song",
        })
    return title, out


# ====== API: топ, альбомы ======

def get_artist_tracks(artist_id, dz, debug=False):
    try:
        data = dz.call("deezer.pageArtist", {
            "art_id": artist_id, "nb": 500, "start": 0, "top": True, "lang": "ru",
        })
    except Exception:
        return []
    results = data.get("results", {})
    if not isinstance(results, dict):
        return []
    top = results.get("TOP", {})
    if isinstance(top, dict):
        tracks = top.get("data", [])
        if tracks:
            return tracks
    try:
        data2 = dz.call("artist.getTopTrack", {"art_id": artist_id, "nb": 500})
        res2 = data2.get("results", {})
        if isinstance(res2, dict):
            tracks2 = res2.get("data", [])
            if tracks2:
                return tracks2
    except Exception:
        pass
    return []


def get_artist_albums(artist_id, dz, limit=50, debug=False):
    try:
        data = dz.call("deezer.pageArtist", {
            "art_id": artist_id, "nb": 500, "start": 0, "top": True, "lang": "ru",
        })
    except Exception:
        return []
    results = data.get("results", {})
    if not isinstance(results, dict):
        return []
    ab = results.get("ALBUMS", {})
    raw = []
    if isinstance(ab, dict):
        raw = ab.get("data", [])
    elif isinstance(ab, list):
        raw = ab

    def _dk(x):
        return x.get("DIGITAL_RELEASE_DATE") or x.get("ORIGINAL_RELEASE_DATE") or ""

    raw_sorted = sorted(raw, key=_dk, reverse=True)
    out = []
    for al in raw_sorted[:limit]:
        if not isinstance(al, dict):
            continue
        aid = al.get("ALB_ID")
        if not aid:
            continue
        out.append({
            "ALB_ID": aid,
            "ALB_TITLE": al.get("ALB_TITLE", ""),
            "ART_NAME": al.get("ART_NAME", ""),
            "DATE": _dk(al),
        })
    return out


def get_album_tracks(dz, album_id, debug=False):
    try:
        data = dz.call("deezer.pageAlbum", {
            "alb_id": album_id, "lang": "ru", "tab": "tracklist",
        })
        res = data.get("results", {})
        songs = res.get("SONGS", {})
        if isinstance(songs, dict):
            return songs.get("data", [])
    except Exception:
        pass
    return []


# ====== Плейлисты Deezer ======

def find_playlist_by_name(name, access_token, debug=False):
    try:
        r = requests.get(f"{DEEZER_API}/user/me/playlists",
                         params={"access_token": access_token, "limit": 1000}, timeout=20)
        if r.status_code != 200:
            return None
        data = r.json()
        nl = name.strip().lower()
        for pl in data.get("data", []):
            if (pl.get("title") or "").strip().lower() == nl:
                return pl.get("id")
    except Exception:
        pass
    return None


def create_playlist(name, access_token, debug=False):
    try:
        r = requests.post(f"{DEEZER_API}/user/me/playlists",
                          params={"access_token": access_token, "title": name}, timeout=20)
        if r.status_code == 200:
            return r.json().get("id")
    except Exception:
        pass
    return None


def get_playlist_track_ids(playlist_id, access_token, debug=False):
    ids = set()
    try:
        r = requests.get(f"{DEEZER_API}/playlist/{playlist_id}/tracks",
                         params={"access_token": access_token, "limit": 1000}, timeout=20)
        if r.status_code != 200:
            return ids
        data = r.json()
        for item in data.get("data", []):
            tid = item.get("id")
            if tid is not None:
                ids.add(str(tid))
    except Exception:
        pass
    return ids


def add_to_playlist(playlist_id, track_id, access_token, debug=False):
    url = f"{DEEZER_API}/playlist/{playlist_id}/tracks"
    params = {
        "access_token": access_token,
        "songs": str(track_id),
        "request_method": "POST",
        "order": -1,
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return False
        try:
            data = r.json()
        except Exception:
            return True
        if isinstance(data, bool):
            return data
        if isinstance(data, dict):
            err = data.get("error")
            if not err:
                return True
            if isinstance(err, dict) and "already exists" in str(err.get("message", "")).lower():
                return True
            return False
        return True
    except Exception:
        return False


# ====== Управление плейлистами ======

def get_all_user_playlists(access_token, debug=False):
    out = []
    try:
        r = requests.get(f"{DEEZER_API}/user/me/playlists",
                         params={"access_token": access_token, "limit": 1000}, timeout=20)
        if r.status_code != 200:
            return out
        data = r.json()
        for pl in data.get("data", []):
            pid = pl.get("id")
            title = (pl.get("title") or "").strip()
            if pid is not None and title:
                out.append({"id": str(pid), "title": title})
    except Exception:
        pass
    return out


def remove_track_from_playlist(playlist_id, track_id, access_token, debug=False):
    try:
        r = requests.delete(f"{DEEZER_API}/playlist/{playlist_id}/tracks",
                            params={"access_token": access_token, "songs": str(track_id),
                                    "request_method": "DELETE"}, timeout=20)
        if r.status_code != 200:
            return False
        try:
            data = r.json()
        except Exception:
            return True
        if isinstance(data, bool):
            return data
        if isinstance(data, dict):
            return not data.get("error")
        return True
    except Exception:
        return False


def dedupe_current_playlist(pl_id, pl_title, access_token, debug=False):
    print(f"\n[..] Ищу дубликаты в '{pl_title}'...")
    all_pls = get_all_user_playlists(access_token, debug=debug)
    others = [p for p in all_pls if str(p["id"]) != str(pl_id)]
    if not others:
        print("[i] Других плейлистов нет.")
        return 0, 0
    foreign_ids = set()
    for i, p in enumerate(others, 1):
        ids = get_playlist_track_ids(p["id"], access_token, debug=False)
        foreign_ids |= ids
        print(f"    [{i}/{len(others)}] {p['title']!r}: {len(ids)}")
        time.sleep(0.3)
    current_ids = get_playlist_track_ids(pl_id, access_token, debug=False)
    to_remove = current_ids & foreign_ids
    if not to_remove:
        print("[OK] Дубликатов нет.")
        return 0, 0
    removed = 0
    for idx, tid in enumerate(sorted(to_remove), 1):
        if remove_track_from_playlist(pl_id, tid, access_token, debug=debug):
            removed += 1
        time.sleep(1.0)
    return removed, len(to_remove)


# ====== Кэш ======

def _cache_path(json_path):
    base, _ = os.path.splitext(json_path)
    return base + ".cache.json"


def load_cache(json_path):
    path = _cache_path(json_path)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_cache(json_path, cache):
    path = _cache_path(json_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ====== DeezerAPI (gw-light) ======

class DeezerAPI:
    def __init__(self, arl):
        self.arl = arl
        self.s = requests.Session()
        self.s.cookies.set("arl", arl, domain=".deezer.com")
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
        })
        self.api_token = None
        self.user_id = None
        self._artist_id_cache = {}
        self._artist_albums_cache = {}
        self._album_tracks_cache = {}

    def call(self, method, payload=None, retry_token=True):
        params = {
            "api_version": "1.0",
            "api_token": self.api_token or "null",
            "input": "3",
            "method": method,
        }
        url = API_BASE + "?" + urllib.parse.urlencode(params)
        r = self.s.post(url, json=payload or {}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        data = r.json()
        err = data.get("error")
        if err and isinstance(err, list) and err and "Invalid api token" in str(err):
            if retry_token:
                self._refresh_token()
                return self.call(method, payload, retry_token=False)
        return data

    def _refresh_token(self):
        params = {"api_version": "1.0", "api_token": "null", "input": "3", "method": "deezer.getUserData"}
        url = API_BASE + "?" + urllib.parse.urlencode(params)
        r = self.s.post(url, json={}, timeout=30)
        data = r.json()
        res = data.get("results", {})
        self.api_token = res.get("checkForm")
        self.user_id = res.get("USER", {}).get("USER_ID")

    def login(self):
        self._refresh_token()
        if not self.api_token or self.user_id is None:
            raise RuntimeError("ARL отклонён")
        data = self.call("deezer.getUserData")
        user = data.get("results", {}).get("USER", {})
        if not user.get("USER_ID"):
            raise RuntimeError("ARL отклонён: USER_ID не получен")
        return user

    def search_artist(self, artist_name):
        try:
            data = self.call("deezer.pageSearch", {
                "query": artist_name, "start": 0, "nb": 10, "types": ["artist"],
            })
            ar_block = data.get("results", {}).get("ARTIST", {})
            if isinstance(ar_block, dict):
                return ar_block.get("data", [])
            if isinstance(ar_block, list):
                return ar_block
        except Exception:
            pass
        return []

    def search_all(self, query, limit=100):
        for payload in (
            {"query": query, "start": 0, "nb": limit, "types": ["track", "album", "artist"]},
            {"query": query, "start": 0, "nb": limit},
        ):
            try:
                data = self.call("deezer.pageSearch", payload)
            except Exception:
                continue
            res = data.get("results", {})
            if not isinstance(res, dict):
                continue
            tracks, albums, artists = [], [], []
            tr = res.get("TRACK", {})
            if isinstance(tr, dict):
                tracks = tr.get("data", []) or []
            elif isinstance(tr, list):
                tracks = tr
            ab = res.get("ALBUM", {})
            if isinstance(ab, dict):
                albums = ab.get("data", []) or []
            ar = res.get("ARTIST", {})
            if isinstance(ar, dict):
                artists = ar.get("data", []) or []
            if tracks or albums or artists:
                return tracks, albums, artists
        return [], [], []


# ====== Матчинг ======

def _pick_from_list(candidates, title, artists, require_artist=True, strict_artist_for_soft=True):
    for r in candidates:
        r_title = r.get("SNG_TITLE", "")
        r_artist = r.get("ART_NAME", "")
        if not title_match(title, r_title):
            continue
        if require_artist:
            if not artists_match_any(artists, r_artist):
                continue
        else:
            if strict_artist_for_soft and artists:
                if not _artist_has_at_least_one_word(artists, r_artist):
                    continue
        return {"SNG_ID": r.get("SNG_ID"), "SNG_TITLE": r_title, "ART_NAME": r_artist}
    return None


def _artist_id_for(artist_name, dz, cookies_dict=None, debug=False):
    key = artist_name.lower().strip()
    if key in dz._artist_id_cache:
        return dz._artist_id_cache[key]
    result = (None, artist_name)
    nq = normalize(artist_name)

    # 1) публичный API (самый надёжный по свежим артистам)
    try:
        candidates = public_search_artist(artist_name, debug=debug)
    except Exception:
        candidates = []
    best = None
    for a in candidates:
        an = normalize(a.get("name") or "")
        if an == nq:
            best = a
            break
        if nq in an or an in nq:
            best = best or a
    if not best and candidates:
        best = candidates[0]
    if best and best.get("id"):
        result = (best.get("id"), best.get("name") or artist_name)
        dz._artist_id_cache[key] = result
        return result

    # 2) gw-light
    try:
        candidates = dz.search_artist(artist_name)
    except Exception:
        candidates = []
    best = None
    for a in candidates:
        an = normalize(a.get("ART_NAME") or "")
        if an == nq:
            best = a
            break
        if nq in an or an in nq:
            best = best or a
    if not best and candidates:
        best = candidates[0]
    if best and best.get("ART_ID"):
        result = (best.get("ART_ID"), best.get("ART_NAME") or artist_name)
        dz._artist_id_cache[key] = result
        return result

    # 3) из HTML-поиска
    try:
        _, _, html_artists = search_html(artist_name, cookies_dict, debug=False)
        for a in html_artists:
            an = normalize(a.get("ART_NAME") or "")
            if an == nq or nq in an or an in nq:
                aid = a.get("ART_ID") or a.get("id")
                if aid:
                    result = (aid, a.get("ART_NAME") or artist_name)
                    dz._artist_id_cache[key] = result
                    return result
    except Exception:
        pass

    dz._artist_id_cache[key] = result
    return result


# ====== Универсальный поиск трека ======

def _find_track(line, dz, cookies_dict=None, debug=False):
    if dz is None:
        return _find_track_public(line, debug=debug)

    artists, title = parse_track(line)
    if not title:
        return None

    found_node = None
    artist_candidates = artists if artists else [""]

    if debug:
        print(f"         [debug] parse: artists={artists}, title={title!r}")

    queries = []
    for artist in artist_candidates:
        queries.append(f"{artist} {title}" if artist else title)
    queries.append(line)
    if artists:
        queries.append(f"{artists[0]} {title}")
    queries.append(title)

    seen_q = set()
    uniq_queries = []
    for q in queries:
        if q and q not in seen_q:
            seen_q.add(q)
            uniq_queries.append(q)

    # --- 1. HTML-поиск ---
    for q in uniq_queries:
        try:
            html_tracks, _, _ = search_html(q, cookies_dict, debug=False)
        except Exception:
            html_tracks = []
        if debug:
            print(f"         [debug] HTML «{q}»: T:{len(html_tracks)}")
        node = _pick_from_list(html_tracks, title, artists, require_artist=True)
        if node:
            found_node = node
            if debug:
                print(f"         [debug]   ✓ HTML strict")
            break
        node_soft = _pick_from_list(html_tracks, title, artists, require_artist=False)
        if node_soft:
            found_node = node_soft
            if debug:
                print(f"         [debug]   ✓ HTML soft")
            break
        time.sleep(0.2)

    # --- 2. gw-light search ---
    if not found_node:
        for q in uniq_queries:
            try:
                tr, _, _ = dz.search_all(q, limit=100)
            except Exception:
                tr = []
            if debug:
                print(f"         [debug] gw «{q}»: T:{len(tr)}")
            node = _pick_from_list(tr, title, artists, require_artist=True)
            if node:
                found_node = node
                break
            node_soft = _pick_from_list(tr, title, artists, require_artist=False)
            if node_soft:
                found_node = node_soft
                break
            time.sleep(0.2)

    # --- 3. Публичный API треки артиста (top) ---
    if not found_node and artists:
        for artist in artists:
            if not artist:
                continue
            aid, alabel = _artist_id_for(artist, dz, cookies_dict=cookies_dict, debug=debug)
            if debug:
                print(f"         [debug] artist «{artist}» → id {aid} ({alabel})")
            if not aid:
                continue

            # публичный top
            d = public_get(f"{PUBLIC_API}/artist/{aid}/top", params={"limit": 100}, debug=debug)
            top_tracks = (d or {}).get("data", []) or []
            tr = [{
                "SNG_ID": t.get("id"),
                "SNG_TITLE": (t.get("title") or t.get("title_short") or "").strip(),
                "ART_NAME": ((t.get("artist") or {}).get("name") or alabel).strip(),
            } for t in top_tracks]
            if debug:
                print(f"         [debug]   top public: {len(tr)}")
            node = _pick_from_list(tr, title, artists, require_artist=True)
            if node:
                found_node = node
                break
            node_soft = _pick_from_list(tr, title, artists, require_artist=False)
            if node_soft:
                found_node = node_soft
                break

            # HTML страницы артиста
            html_artist_tracks = get_artist_tracks_html(aid, cookies_dict=cookies_dict, debug=False)
            if debug:
                print(f"         [debug]   HTML artist page: {len(html_artist_tracks)}")
            node = _pick_from_list(html_artist_tracks, title, artists, require_artist=True)
            if node:
                found_node = node
                break
            node_soft = _pick_from_list(html_artist_tracks, title, artists, require_artist=False)
            if node_soft:
                found_node = node_soft
                break

    # --- 4. АЛЬБОМЫ АРТИСТА (главный фикс) ---
    if not found_node and artists:
        for artist in artists:
            if not artist:
                continue
            aid, alabel = _artist_id_for(artist, dz, cookies_dict=cookies_dict, debug=False)
            if not aid:
                continue

            # Кэшируем список альбомов
            if aid in dz._artist_albums_cache:
                albums = dz._artist_albums_cache[aid]
            else:
                albums = public_artist_albums(aid, debug=debug)
                dz._artist_albums_cache[aid] = albums
            if debug:
                print(f"         [debug]   альбомов у {alabel}: {len(albums)}")

            if not albums:
                continue

            # 4.1 — сначала альбомы, чьё название совпадает с названием трека
            target_norm = normalize(title)
            priority = []
            rest = []
            for al in albums:
                al_title = (al.get("title") or "").strip()
                if not al_title:
                    continue
                n_al = normalize(al_title)
                if n_al == target_norm or target_norm in n_al or n_al in target_norm:
                    priority.append(al)
                else:
                    rest.append(al)

            if debug and priority:
                print(f"         [debug]   приоритетных альбомов: {len(priority)}")

            def _scan_albums(album_list, dbg_label):
                for al in album_list:
                    album_id = al.get("id")
                    if not album_id:
                        continue
                    if album_id in dz._album_tracks_cache:
                        at = dz._album_tracks_cache[album_id]
                    else:
                        _, at = public_album_tracks(album_id, debug=False)
                        dz._album_tracks_cache[album_id] = at
                    node_inner = _pick_from_list(at, title, artists, require_artist=True)
                    if node_inner:
                        if debug:
                            print(f"         [debug]   ✓ {dbg_label}: '{al.get('title')}'")
                        return node_inner
                    node_inner_soft = _pick_from_list(at, title, artists, require_artist=False)
                    if node_inner_soft:
                        if debug:
                            print(f"         [debug]   ✓ {dbg_label} (soft): '{al.get('title')}'")
                        return node_inner_soft
                    time.sleep(0.05)
                return None

            # priority — быстро
            found_node = _scan_albums(priority, "album (priority)")
            if found_node:
                break

            # остальные — с ограничением, чтобы 800 треков не превратились в час работы
            # но для одного трека пройдёмся по всем
            found_node = _scan_albums(rest, "album (full scan)")
            if found_node:
                break

    return found_node


def _find_track_public(line, debug=False):
    artists, title = parse_track(line)
    if not title:
        return None
    qs = []
    if artists:
        for a in artists:
            qs.append(f"{a} {title}")
    qs.append(title)
    qs.append(line)
    for q in qs:
        d = public_get(f"{PUBLIC_API}/search", params={"q": q, "limit": 25}, debug=debug)
        if not d:
            continue
        for t in d.get("data", []) or []:
            t_title = (t.get("title") or t.get("title_short") or "").strip()
            t_artist = ((t.get("artist") or {}).get("name") or "").strip()
            if not t_title:
                continue
            if not title_match(title, t_title):
                continue
            if artists and not artists_match_any(artists, t_artist):
                continue
            return {"SNG_ID": t.get("id"), "SNG_TITLE": t_title, "ART_NAME": t_artist}
    return None


# ====== Импорт ======

def _safe_filename(name):
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip()[:80] or "playlist"


def run_import(json_path, playlist_name, debug=False):
    print(f"\n[..] Импорт в Deezer: '{playlist_name}'")
    safe_name = _safe_filename(playlist_name)
    found_csv = f"found_tracks - {safe_name}.csv"
    not_found_file = f"not_found - {safe_name}.txt"

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not data:
        print("[FAIL] JSON пустой")
        return

    tracks = []
    for item in data:
        artist = (item.get("artist") or "").strip()
        title = (item.get("title") or "").strip()
        if not title:
            continue
        tracks.append(f"{artist} - {title}" if artist else title)
    print(f"[OK] Треков: {len(tracks)}")

    arl, access_token, cookies_raw = load_deezer_config(CONFIG_FILE)
    if not arl or arl.startswith("вставь") or arl == "!!!!":
        print("[FAIL] Не заполнен ARL")
        return
    if not access_token or access_token.startswith("вставь") or access_token == "!!!!":
        print("[FAIL] Не заполнен ACCESS_TOKEN")
        return

    cookies_dict = {"arl": arl}
    if cookies_raw:
        for pair in cookies_raw.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                cookies_dict[k.strip()] = v.strip()

    dz = DeezerAPI(arl)
    try:
        user = dz.login()
        print(f"[OK] Вошли как: {user.get('BLOG_NAME', '?')}")
    except Exception as e:
        print(f"[!] Ошибка авторизации: {e}")

    pl_id = find_playlist_by_name(playlist_name, access_token, debug=debug)
    if not pl_id:
        pl_id = create_playlist(playlist_name, access_token, debug=debug)
        if not pl_id:
            print("[FAIL] Не удалось создать плейлист")
            return
        print(f"[OK] Создан, id={pl_id}")
    else:
        print(f"[OK] Найден, id={pl_id}")

    cache = load_cache(json_path)
    existing_ids = get_playlist_track_ids(pl_id, access_token, debug=debug)

    found = []
    not_found = []
    skipped = 0

    for i, line in enumerate(tracks, 1):
        cached_id = cache.get(line)
        if cached_id and str(cached_id) in existing_ids:
            print(f"[{i:3d}/{len(tracks)}] {line}  [skip cached]")
            skipped += 1
            found.append({
                "query": line, "track_id": str(cached_id),
                "track_title": "(cached)", "track_artist": "(cached)",
                "url": f"https://www.deezer.com/ru/track/{cached_id}",
                "playlist_added": True,
            })
            continue

        _, title_check = parse_track(line)
        print(f"[{i:3d}/{len(tracks)}] {line}")
        if not title_check:
            not_found.append(line)
            continue

        found_node = _find_track(line, dz, cookies_dict=cookies_dict, debug=False)

        if found_node:
            tid = str(found_node.get("SNG_ID"))
            t_title = found_node.get("SNG_TITLE", "?")
            t_artist = found_node.get("ART_NAME", "?")
            if tid in existing_ids:
                print(f"         -> {t_title} — {t_artist} (уже в плейлисте)")
                cache[line] = tid
                skipped += 1
                found.append({
                    "query": line, "track_id": tid, "track_title": t_title,
                    "track_artist": t_artist, "url": f"https://www.deezer.com/ru/track/{tid}",
                    "playlist_added": True,
                })
                continue
            print(f"         -> {t_title} — {t_artist} (id={tid})")
            pl_ok = add_to_playlist(pl_id, tid, access_token, debug=debug)
            print("         ✓ добавлено" if pl_ok else "         ✗ не удалось")
            cache[line] = tid
            existing_ids.add(tid)
            save_cache(json_path, cache)
            found.append({
                "query": line, "track_id": tid, "track_title": t_title,
                "track_artist": t_artist, "url": f"https://www.deezer.com/ru/track/{tid}",
                "playlist_added": pl_ok,
            })
        else:
            print("         -> НЕ НАЙДЕНО")
            not_found.append(line)
        time.sleep(0.4)

    if found:
        with open(found_csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Исходный запрос", "ID трека", "Название", "Артист", "Ссылка Deezer", "В плейлисте"])
            for item in found:
                w.writerow([item["query"], item["track_id"], item["track_title"], item["track_artist"], item["url"], "да" if item["playlist_added"] else "нет"])
        print(f"\n[OK] Найдено: {len(found)} -> {found_csv}")

    if not_found:
        with open(not_found_file, "w", encoding="utf-8") as f:
            for l in not_found:
                f.write(l + "\n")
        print(f"[i] Ненайденных: {len(not_found)} -> {not_found_file}")

    print(f"\nИТОГО: найдено {len(found)}, добавлено {sum(1 for f in found if f['playlist_added'])}, не найдено {len(not_found)}")
    print(f"Плейлист: https://www.deezer.com/ru/playlist/{pl_id}")

    print()
    if _ask_yes_no("Убрать дубликаты относительно других плейлистов? (y/n): "):
        try:
            removed, total = dedupe_current_playlist(pl_id, playlist_name, access_token, debug=debug)
            print(f"\n[OK] Удалено: {removed} из {total}")
        except Exception as e:
            print(f"[!] {e}")


# ====== Свой список ======

def _get_authed_dz(config_path=None):
    try:
        arl, _, _ = load_deezer_config(config_path or CONFIG_FILE)
    except Exception:
        return None
    if not arl or arl.startswith("вставь") or arl == "!!!!":
        return None
    try:
        dz = DeezerAPI(arl)
        dz.login()
        return dz
    except Exception:
        return None


def collect_tracks_from_lines(lines, debug=False, config_path=None):
    found = []
    not_found = []
    seen = set()

    def _norm(s):
        s = _strip_diacritics(_to_nfc(s or ""))
        return " ".join(s.lower().split())

    dz = _get_authed_dz(config_path)
    cookies_dict = {"arl": dz.arl} if dz else None

    total = len(lines)
    for idx, raw in enumerate(lines, 1):
        line = _to_nfc((raw or "").strip())
        if not line or line.startswith("#"):
            continue

        node = _find_track(line, dz, cookies_dict=cookies_dict, debug=False)

        if not node:
            if debug:
                print(f"[{idx:4d}/{total}] [–] {line}")
            not_found.append(line)
            time.sleep(0.1)
            continue

        art = (node.get("ART_NAME") or "").strip()
        title = (node.get("SNG_TITLE") or "").strip()
        if not title:
            not_found.append(line)
            continue
        key = (_norm(art), _norm(title))
        if key in seen:
            if debug:
                print(f"[{idx:4d}/{total}] [=] {art} — {title}")
            time.sleep(0.1)
            continue
        seen.add(key)
        found.append({"artist": art, "title": title})
        if debug:
            print(f"[{idx:4d}/{total}] [+] {art} — {title}  ←  {line}")
        time.sleep(0.1)

    return found, not_found


def collect_tracks_from_artists(artists, tracks_per_artist=15, debug=False, config_path=None):
    out = []
    seen = set()

    def _norm(s):
        s = _strip_diacritics(_to_nfc(s or ""))
        return " ".join(s.lower().split())

    dz = _get_authed_dz(config_path)
    cookies_dict = {"arl": dz.arl} if dz else None

    for raw_name in artists:
        name = _to_nfc((raw_name or "").strip())
        if not name:
            continue
        if debug:
            print(f"[..] Исполнитель: {name}")

        aid = None
        label = name
        if dz:
            aid, label = _artist_id_for(name, dz, cookies_dict=cookies_dict, debug=debug)

        limit = max(1, min(int(tracks_per_artist), 50))
        raw_tracks = []

        # публичный top
        if aid:
            d = public_get(f"{PUBLIC_API}/artist/{aid}/top", params={"limit": limit}, debug=False)
            for t in (d or {}).get("data", []) or []:
                raw_tracks.append({
                    "SNG_TITLE": t.get("title") or t.get("title_short") or "",
                    "ART_NAME": ((t.get("artist") or {}).get("name") or label),
                })

        # HTML страница артиста
        if aid and len(raw_tracks) < limit:
            for t in get_artist_tracks_html(aid, cookies_dict=cookies_dict, debug=False):
                raw_tracks.append({"SNG_TITLE": t["SNG_TITLE"], "ART_NAME": t["ART_NAME"]})

        added = 0
        for t in raw_tracks:
            if added >= limit:
                break
            title = (t.get("SNG_TITLE") or "").strip()
            art = (t.get("ART_NAME") or label).strip()
            if not title:
                continue
            key = (_norm(art), _norm(title))
            if key in seen:
                continue
            seen.add(key)
            out.append({"artist": art, "title": title})
            added += 1

        if debug:
            print(f"  [OK] {label}: +{added}")
        time.sleep(0.15)

    return out