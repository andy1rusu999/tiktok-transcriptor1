import json
import os
import re
import requests
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
import whisper
import yt_dlp
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime

dist_dir = Path(__file__).resolve().parent.parent / "dist"
app = Flask(__name__, static_folder=str(dist_dir), static_url_path="/")

cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://165.227.169.91,https://transcribe.propagandahunter.net")
cors_list = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]
CORS(app, resources={r"/api/*": {"origins": cors_list}})

# Load Whisper model globally to avoid reloading on every request
# Using 'base' for a good balance between speed and accuracy
print("Loading Whisper model...")
model = whisper.load_model("base")
print("Whisper model loaded.")

def apply_moldovan_slang(text: str) -> str:
    if not text:
        return text
    replacements = [
        ("și", "și"),
        ("astăzi", "azi"),
        ("foarte", "tare"),
        ("puțin", "oleacă"),
        ("copil", "copchil"),
        ("băiat", "băiet"),
        ("fată", "fată"),
        ("oricum", "oricum"),
        ("deci", "deci"),
        ("bine", "ghini"),
        ("vreau", "vreau"),
        ("face", "face"),
    ]
    for src, dst in replacements:
        text = text.replace(src, dst)
        text = text.replace(src.capitalize(), dst.capitalize())
    return text

def get_cookiefile():
    cookiefile = os.environ.get("TIKTOK_COOKIE_FILE")
    if cookiefile and os.path.exists(cookiefile):
        return cookiefile
    default_path = Path(__file__).resolve().parent / "cookies.txt"
    if default_path.exists():
        return str(default_path)
    return None

def load_cookie_jar():
    cookiefile = get_cookiefile()
    cookies = {}
    if not cookiefile:
        return cookies
    try:
        with open(cookiefile, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                name = parts[5]
                value = parts[6]
                cookies[name] = value
    except Exception as exc:
        print(f"Failed to read cookie file: {exc}")
    return cookies

def build_cookie_header(cookies: dict) -> str:
    if not cookies:
        return ""
    return "; ".join([f"{key}={value}" for key, value in cookies.items()])

def fetch_profile_html(username: str, cookies: dict) -> str | None:
    url = f"https://www.tiktok.com/@{username}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.tiktok.com/',
    }
    cookie_header = build_cookie_header(cookies)
    if cookie_header:
        headers['Cookie'] = cookie_header
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"Failed to fetch profile HTML for {username}: {exc}")
        return None

def resolve_secuid(username: str, cookies: dict) -> str | None:
    if username.startswith("tiktokuser:"):
        return username.split(":", 1)[1]
    api_secuid = fetch_secuid_from_api(username, cookies)
    if api_secuid:
        return api_secuid
    html = fetch_profile_html(username, cookies)
    if not html:
        return None
    match = re.search(r'"secUid":"([^"]+)"', html)
    if match:
        return match.group(1)
    return None

def fetch_secuid_from_api(username: str, cookies: dict) -> str | None:
    ms_token = cookies.get("msToken")
    params = {
        "uniqueId": username,
        "language": "en",
        "aid": "1988",
    }
    if ms_token:
        params["msToken"] = ms_token
    url = "https://www.tiktok.com/api/user/detail/?" + urllib.parse.urlencode(params)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Referer': f"https://www.tiktok.com/@{username}",
    }
    cookie_header = build_cookie_header(cookies)
    if cookie_header:
        headers['Cookie'] = cookie_header
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8", errors="ignore")
        data = json.loads(payload)
        return data.get("userInfo", {}).get("user", {}).get("secUid")
    except Exception as exc:
        print(f"Failed to fetch secUid via API: {exc}")
        return None

def fetch_videos_via_api(username: str, start_day, end_day):
    cookies = load_cookie_jar()
    secuid = resolve_secuid(username, cookies)
    if not secuid:
        return []

    ms_token = cookies.get("msToken")
    cursor = 0
    has_more = True
    max_pages = 80
    page = 0
    videos = []

    while has_more and page < max_pages:
        params = {
            "aid": "1988",
            "count": "35",
            "cursor": str(cursor),
            "secUid": secuid,
        }
        if ms_token:
            params["msToken"] = ms_token
        url = "https://www.tiktok.com/api/post/item_list/?" + urllib.parse.urlencode(params)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': f"https://www.tiktok.com/@{username}",
        }
        cookie_header = build_cookie_header(cookies)
        if cookie_header:
            headers['Cookie'] = cookie_header

        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            print(f"Failed to fetch TikTok API page {page}: {exc}")
            break

        try:
            data = json.loads(payload)
        except Exception as exc:
            print(f"Failed to parse TikTok API response: {exc}")
            break

        items = data.get("itemList") or data.get("item_list") or []
        if not items:
            break

        for item in items:
            create_time = item.get("createTime")
            if not create_time:
                continue
            video_date = datetime.fromtimestamp(int(create_time)).replace(tzinfo=None)
            if start_day and video_date.date() < start_day:
                has_more = False
                break
            if end_day and video_date.date() > end_day:
                continue

            author = item.get("author", {})
            author_name = author.get("uniqueId") or author.get("nickname") or username
            video_id = item.get("id")
            video_url = f"https://www.tiktok.com/@{author_name}/video/{video_id}" if video_id else None
            video_info = item.get("video", {}) or {}
            duration = video_info.get("duration")
            direct_url = None
            play_addr = video_info.get("playAddr") or video_info.get("play_addr") or {}
            download_addr = video_info.get("downloadAddr") or video_info.get("download_addr") or {}
            for addr in (play_addr, download_addr):
                if isinstance(addr, dict):
                    url_list = addr.get("urlList") or addr.get("url_list") or []
                    if url_list:
                        direct_url = url_list[0]
                        break

            videos.append({
                "id": video_id,
                "url": video_url,
                "directUrl": direct_url,
                "title": item.get("desc") or "Untitled Video",
                "createdAt": video_date.isoformat(),
                "duration": str(duration) if duration is not None else "0",
                "status": "pending"
            })

        cursor = data.get("cursor", 0)
        has_more = bool(data.get("hasMore"))
        page += 1

    return videos

def extract_video_id(video_url: str) -> str | None:
    if not video_url:
        return None
    match = re.search(r'/video/(\d+)', video_url)
    if match:
        return match.group(1)
    return None

def extract_username_from_url(video_url: str) -> str | None:
    match = re.search(r'@([^/?#]+)', video_url)
    if match:
        return match.group(1)
    return None

def fetch_direct_url_from_item_list(video_url: str, log=None) -> str | None:
    video_id = extract_video_id(video_url)
    if not video_id:
        return None
    username = extract_username_from_url(video_url)
    if not username:
        return None

    cookies = load_cookie_jar()
    secuid = resolve_secuid(username, cookies)
    if not secuid:
        return None

    ms_token = cookies.get("msToken")
    cursor = 0
    has_more = True
    max_pages = 40
    page = 0

    def _log(msg: str):
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    while has_more and page < max_pages:
        params = {
            "aid": "1988",
            "count": "35",
            "cursor": str(cursor),
            "secUid": secuid,
        }
        if ms_token:
            params["msToken"] = ms_token
        url = "https://www.tiktok.com/api/post/item_list/?" + urllib.parse.urlencode(params)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Referer': f"https://www.tiktok.com/@{username}",
        }
        cookie_header = build_cookie_header(cookies)
        if cookie_header:
            headers['Cookie'] = cookie_header

        try:
            response = requests.get(url, headers=headers, cookies=cookies, timeout=20)
            _log(f"item_list HTTP {response.status_code} page={page} cursor={cursor}")
            if not response.ok:
                _log(f"item_list body (first 300): {response.text[:300]}")
                return None
            data = response.json()
        except Exception as exc:
            _log(f"item_list exception page={page}: {exc}")
            return None

        items = data.get("itemList") or data.get("item_list") or []
        for item in items:
            if str(item.get("id")) == str(video_id):
                return extract_url_from_item(item)

        cursor = data.get("cursor", 0)
        has_more = bool(data.get("hasMore"))
        page += 1

    return None

def fetch_direct_url_from_item_detail(video_url: str, log=None) -> str | None:
    video_id = extract_video_id(video_url)
    if not video_id:
        return None
    cookies = load_cookie_jar()
    ms_token = cookies.get("msToken")
    params = {
        "aid": "1988",
        "itemId": video_id,
        "app_name": "tiktok_web",
        "device_platform": "webapp",
        "os": "web",
    }
    if ms_token:
        params["msToken"] = ms_token
    url = "https://www.tiktok.com/api/item/detail/?" + urllib.parse.urlencode(params)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': video_url,
    }
    cookie_header = build_cookie_header(cookies)
    if cookie_header:
        headers['Cookie'] = cookie_header

    def _log(msg: str):
        if callable(log):
            try:
                log(msg)
            except Exception:
                pass

    try:
        response = requests.get(url, headers=headers, cookies=cookies, timeout=20)
        _log(f"item_detail HTTP {response.status_code} len={len(response.text or '')}")
        if not response.ok:
            _log(f"item_detail body (first 300): {(response.text or '')[:300]}")
            return None
        data = response.json()
    except Exception as exc:
        _log(f"item_detail exception: {exc}")
        return None

    item = data.get("itemInfo", {}).get("itemStruct")
    if not item:
        return None
    return extract_url_from_item(item)

def normalize_direct_url(url: str) -> str | None:
    if not url:
        return None
    cleaned = url.strip().strip('"').strip("'")
    cleaned = cleaned.replace("\\u0026", "&").replace("\\/", "/")
    try:
        parsed = urllib.parse.urlparse(cleaned)
        if parsed.scheme and parsed.netloc:
            host = parsed.netloc.rstrip(".")
            if host.endswith("tiktok") and not host.endswith(".com"):
                host = f"{host}.com"
            elif host.endswith("tiktok") is False and host.endswith("tiktok.com") is False and host.endswith("tiktokcdn.com") is False:
                if host.endswith("tiktok."):
                    host = host.rstrip(".") + ".com"
            cleaned = parsed._replace(netloc=host).geturl()
    except Exception:
        return cleaned
    return cleaned

def fetch_direct_url(video_url: str) -> str | None:
    debug_log = Path("/tmp/tiktok_debug.log")
    
    def log(msg):
        with open(debug_log, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    
    log(f"=== fetch_direct_url for {video_url}")
    video_id = extract_video_id(video_url)
    if not video_id:
        log("No video_id extracted")
        return None

    cookies = load_cookie_jar()
    cookiefile = get_cookiefile()
    log(f"Cookies loaded: {len(cookies)} items, msToken={'yes' if cookies.get('msToken') else 'no'}")
    
    def yt_dlp_get_url(use_impersonate: bool) -> str | None:
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "--cookies", cookiefile if cookiefile else "/dev/null",
            "--no-warnings",
            "--add-header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "--add-header", "Referer: https://www.tiktok.com/",
        ]
        if use_impersonate:
            cmd += ["--extractor-args", "tiktok:impersonate=chrome"]
        cmd += ["--print", "url", video_url]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
            if res.returncode == 0 and res.stdout.strip():
                url = res.stdout.strip().split('\n')[0]
                if url.startswith("http"):
                    return url
            log(f"yt-dlp failed (impersonate={use_impersonate}) code {res.returncode}")
            if res.stdout.strip():
                log(f"yt-dlp stdout (first 500): {res.stdout[:500]}")
            if res.stderr.strip():
                log(f"yt-dlp stderr (first 2000): {res.stderr[:2000]}")
        except Exception as e:
            log(f"yt-dlp exception (impersonate={use_impersonate}): {e}")
        return None

    # METODA 1: yt-dlp fără impersonate
    log("Method 1: Trying yt-dlp (no impersonate args)...")
    url_no_imp = yt_dlp_get_url(False)
    if url_no_imp:
        log(f"yt-dlp SUCCESS: {url_no_imp[:80]}")
        return url_no_imp

    # METODA 1b: yt-dlp cu impersonate (chiar dacă targets apar unavailable, uneori funcționează)
    log("Method 1b: Trying yt-dlp (impersonate=chrome)...")
    url_imp = yt_dlp_get_url(True)
    if url_imp:
        log(f"yt-dlp SUCCESS (impersonate): {url_imp[:80]}")
        return url_imp

    # METODA 2: item_list API (Fallback-ul care a mers la listare)
    log("Method 2: Trying item_list fallback...")
    direct_from_list = fetch_direct_url_from_item_list(video_url, log=log)
    if direct_from_list:
        log(f"item_list SUCCESS: {direct_from_list[:80]}")
        return direct_from_list
    log("item_list FAILED")

    # METODA 2.5: item/detail API
    log("Method 2.5: Trying item/detail API...")
    direct_from_detail = fetch_direct_url_from_item_detail(video_url, log=log)
    if direct_from_detail:
        log(f"item_detail SUCCESS: {direct_from_detail[:80]}")
        return direct_from_detail
    log("item_detail FAILED")

    # METODA 3: Parse HTML for direct URL
    log("Method 3: Fetching HTML for direct URL parsing...")
    html = fetch_video_html(video_url, cookies)
    if html:
        log(f"HTML fetched, length: {len(html)}. Parsing...")
        direct_from_html = extract_url_from_html(html)
        if direct_from_html:
            normalized = normalize_direct_url(direct_from_html)
            log(f"Final regex SUCCESS: {normalized[:120]}")
            return normalized

    log("ALL METHODS FAILED")
    return None

def build_video_html_candidates(video_url: str) -> list[str]:
    candidates = [video_url]
    video_id = extract_video_id(video_url)
    if video_id:
        candidates.extend([
            f"https://www.tiktok.com/embed/v2/{video_id}",
            f"https://www.tiktok.com/embed/{video_id}",
            f"https://m.tiktok.com/v/{video_id}.html",
        ])
    # Try webapp variants that often include richer JSON
    if "tiktok.com/" in video_url:
        candidates.extend([
            f"{video_url}?is_copy_url=1&is_from_webapp=v1",
            f"{video_url}?lang=en",
            f"{video_url}?is_copy_url=1&is_from_webapp=v1&lang=en",
        ])
    seen = set()
    ordered = []
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered

def fetch_video_html(video_url: str, cookies: dict) -> str | None:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': video_url,
        'Upgrade-Insecure-Requests': '1',
    }
    last_html = None
    for url in build_video_html_candidates(video_url):
        try:
            response = requests.get(url, headers=headers, cookies=cookies, timeout=20)
            if response.ok and response.text:
                last_html = response.text
                if len(last_html) > 2000:
                    return last_html
            else:
                print(f"HTML fetch {url} status {response.status_code}")
        except Exception as exc:
            print(f"Failed to fetch video HTML from {url}: {exc}")
            continue
    return last_html

def extract_url_from_item(item: dict) -> str | None:
    video_info = item.get("video", {}) if isinstance(item, dict) else {}
    play_addr = video_info.get("playAddr") or video_info.get("play_addr") or {}
    download_addr = video_info.get("downloadAddr") or video_info.get("download_addr") or {}
    for addr in (play_addr, download_addr):
        if isinstance(addr, dict):
            url_list = addr.get("urlList") or addr.get("url_list") or []
            if url_list:
                return url_list[0]
    return None

def find_item_struct(payload: object) -> dict | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("itemStruct"), dict):
            return payload["itemStruct"]
        for value in payload.values():
            found = find_item_struct(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_item_struct(value)
            if found:
                return found
    return None

def extract_url_from_html(html: str) -> str | None:
    def extract_tiktok_json(payload: str):
        match = re.search(
            r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"\s*type="application/json"\s*>(.*?)</script>',
            payload,
            re.S
        )
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass
        match = re.search(
            r'id="SIGI_STATE"\s*type="application/json"\s*>(.*?)</script>',
            payload,
            re.S
        )
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass
        return None

    def deep_find(obj, key):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == key:
                    yield v
                yield from deep_find(v, key)
        elif isinstance(obj, list):
            for it in obj:
                yield from deep_find(it, key)

    data = extract_tiktok_json(html)
    if data:
        candidates = list(deep_find(data, "playAddr")) + list(deep_find(data, "downloadAddr"))
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.startswith("http"):
                return candidate
            if isinstance(candidate, dict):
                url_list = candidate.get("urlList") or candidate.get("url_list") or []
                for u in url_list:
                    if isinstance(u, str) and u.startswith("http"):
                        return u

    sigi_match = re.search(r'id="SIGI_STATE"[^>]*>(.*?)</script>', html, re.DOTALL)
    if sigi_match:
        try:
            data = json.loads(sigi_match.group(1))
            item_module = data.get("ItemModule") if isinstance(data, dict) else None
            if isinstance(item_module, dict):
                for item in item_module.values():
                    direct = extract_url_from_item(item)
                    if direct:
                        return direct
        except Exception:
            pass

    uni_match = re.search(
        r'__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*({.*?})\s*;</script>',
        html,
        re.DOTALL
    )
    if uni_match:
        try:
            data = json.loads(uni_match.group(1))
            item_struct = find_item_struct(data)
            direct = extract_url_from_item(item_struct or {})
            if direct:
                return direct
        except Exception:
            pass

    candidates = [
        r'"playAddr":"(.*?)"',
        r'"downloadAddr":"(.*?)"',
        r'"playAddr"\s*:\s*\{"urlList":\["(.*?)"',
        r'"downloadAddr"\s*:\s*\{"urlList":\["(.*?)"',
    ]
    for pattern in candidates:
        match = re.search(pattern, html)
        if not match:
            continue
        raw = match.group(1)
        try:
            decoded = json.loads(f"\"{raw}\"")
            return decoded
        except Exception:
            return raw
    # Loose fallback: find any URL near playAddr/downloadAddr
    hint_idx = html.find("playAddr")
    if hint_idx == -1:
        hint_idx = html.find("downloadAddr")
    if hint_idx != -1:
        window = html[max(0, hint_idx - 2000): hint_idx + 120000]
        m = re.search(r'https?://[^\s\"\'<>]+', window)
        if m:
            return m.group(0)
    return None

def download_media_url(media_url: str, target_path: str, referer: str | None = None) -> bool:
    cookies = load_cookie_jar()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        # TikTok is strict about Referer; use the actual video URL when available
        'Referer': referer or 'https://www.tiktok.com/',
    }
    debug_log = Path("/tmp/tiktok_debug.log")
    try:
        with requests.get(media_url, headers=headers, cookies=cookies, stream=True, timeout=30) as response:
            try:
                with open(debug_log, "a", encoding="utf-8") as handle:
                    handle.write(
                        f"{datetime.now().isoformat()} download_media_url status={response.status_code} "
                        f"content-type={response.headers.get('Content-Type','')} url={media_url[:80]}\n"
                    )
            except Exception:
                pass
            response.raise_for_status()
            with open(target_path, 'wb') as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        return True
    except Exception as exc:
        print(f"Failed to download media URL: {exc}")
        try:
            with open(debug_log, "a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now().isoformat()} download_media_url exception: {exc}\n")
        except Exception:
            pass
        return False

def extract_subtitle_text(raw_text: str, ext: str) -> str:
    lines = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("WEBVTT"):
            continue
        if stripped.startswith("NOTE"):
            continue
        if re.match(r"^\d+$", stripped):
            continue
        if "-->" in stripped:
            continue
        lines.append(stripped)
    return " ".join(lines).strip()

def try_fetch_subtitles(video_url: str, language: str | None) -> str | None:
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'no_playlist': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.tiktok.com/',
        },
    }
    cookiefile = get_cookiefile()
    if cookiefile:
        ydl_opts['cookiefile'] = cookiefile

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except Exception as exc:
        print(f"Subtitle extraction failed for {video_url}: {exc}")
        return None

    subtitles = info.get('subtitles') or {}
    auto_captions = info.get('automatic_captions') or {}
    tracks = subtitles or auto_captions
    if not tracks:
        return None

    lang = None
    if language:
        lang = 'ro' if language == 'ro-md' else language
    if lang and lang not in tracks:
        lang = None
    if not lang:
        for preferred in ('ro', 'ru', 'en'):
            if preferred in tracks:
                lang = preferred
                break
    if not lang:
        lang = next(iter(tracks.keys()), None)
    if not lang:
        return None

    track_entries = tracks.get(lang) or []
    if not track_entries:
        return None

    preferred_exts = ('vtt', 'srt')
    chosen = None
    for ext in preferred_exts:
        chosen = next((item for item in track_entries if item.get('ext') == ext and item.get('url')), None)
        if chosen:
            break
    if not chosen:
        chosen = next((item for item in track_entries if item.get('url')), None)
    if not chosen:
        return None

    url = chosen.get('url')
    ext = chosen.get('ext', '')
    if not url or ext not in preferred_exts:
        return None

    with urllib.request.urlopen(url) as response:
        raw = response.read().decode('utf-8', errors='ignore')
    text = extract_subtitle_text(raw, ext)
    return text or None

def extract_video_date(info: dict) -> datetime | None:
    upload_date_str = info.get('upload_date')
    if upload_date_str:
        try:
            return datetime.strptime(upload_date_str, '%Y%m%d').replace(tzinfo=None)
        except Exception as exc:
            print(f"Error parsing date {upload_date_str}: {exc}")
    
    timestamp = info.get('timestamp') or info.get('release_timestamp')
    if timestamp:
        try:
            return datetime.fromtimestamp(int(timestamp)).replace(tzinfo=None)
        except Exception as exc:
            print(f"Error parsing timestamp {timestamp}: {exc}")
            
    # Fallback: Extract from TikTok ID
    video_id = info.get('id')
    if video_id and video_id.isdigit():
        try:
            ts = int(video_id) >> 32
            if ts > 1262304000: # After 2010
                return datetime.fromtimestamp(ts).replace(tzinfo=None)
        except Exception as exc:
            print(f"Error extracting date from ID {video_id}: {exc}")
            
    return None

@app.route('/api/fetch-videos', methods=['POST'])
@app.route('/fetch-videos', methods=['POST'])
def fetch_videos():
    data = request.json
    username = data.get('username')
    start_date_str = data.get('start_date')  # Format: ISO or YYYY-MM-DD
    end_date_str = data.get('end_date')      # Format: ISO or YYYY-MM-DD

    if not username:
        return jsonify({"error": "Username is required"}), 400

    # Clean username/link
    if 'tiktok.com/' in username:
        # Extract @username from URL
        import re
        match = re.search(r'@([^/?#]+)', username)
        if match:
            username = match.group(1)
    
    if username.startswith('@'):
        username = username[1:]

    if username.startswith("tiktokuser:"):
        tiktok_url = username
    else:
        tiktok_url = f"https://www.tiktok.com/@{username}"
    
    ydl_opts = {
        'extract_flat': True,
        'quiet': False,
        'no_warnings': False,
        'playlist_end': 100, # Limit to last 100 videos for performance
        'extractor_args': {'tiktok': {'impersonate': ['chrome']}},
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.tiktok.com/',
        }
    }
    cookiefile = get_cookiefile()
    if cookiefile:
        ydl_opts['cookiefile'] = cookiefile

    start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')) if start_date_str else None
    end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')) if end_date_str else None
    start_day = start_date.date() if start_date else None
    end_day = end_date.date() if end_date else None

    videos = fetch_videos_via_api(username, start_day, end_day)
    if videos:
        return jsonify({"videos": videos})

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(tiktok_url, download=False)
    except Exception as exc:
        print(f"yt-dlp user extraction failed: {exc}")
        return jsonify({"videos": []})

    entries = result.get('entries') if isinstance(result, dict) else None
    if not entries:
        return jsonify({"videos": []})

    videos = []
    for entry in entries:
        if not entry:
            continue

        video_date = extract_video_date(entry)
        if start_day or end_day:
            if not video_date:
                continue
            video_day = video_date.date()
            if start_day and video_day < start_day:
                continue
            if end_day and video_day > end_day:
                continue

        videos.append({
            "id": entry.get('id'),
            "url": entry.get('url') or (f"https://www.tiktok.com/@{username}/video/{entry.get('id')}" if entry.get('id') else None),
            "title": entry.get('title') or entry.get('description') or "Untitled Video",
            "createdAt": video_date.isoformat() if video_date else None,
            "duration": str(entry.get('duration', '0:00')),
            "status": "pending"
        })

    return jsonify({"videos": videos})

@app.route('/api/health', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

@app.route('/api/transcribe', methods=['POST'])
@app.route('/transcribe', methods=['POST'])
def transcribe():
    data = request.json
    video_url = data.get('video_url')
    direct_url = data.get('direct_url')
    language = data.get('language') # e.g., 'ro', 'ru', 'auto'

    if not video_url:
        return jsonify({"error": "Video URL is required"}), 400

    try:
        # 1. Create a temporary directory to store the audio file
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, 'audio')
            full_audio_path = audio_path + '.mp3'
            
            # 2. Resolve direct URL (from API) and download media
            if not direct_url:
                direct_url = fetch_direct_url(video_url)
            direct_url = normalize_direct_url(direct_url)
            if not direct_url:
                return jsonify({"error": "Nu am putut obține URL-ul direct pentru acest clip."}), 500

            # 3. Extract audio via yt-dlp (most reliable for TikTok)
            yt_dlp_audio = [
                sys.executable, "-m", "yt_dlp",
                "--no-playlist",
                "--cookies", get_cookiefile() if get_cookiefile() else "/dev/null",
                "--no-warnings",
                "--add-header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "--add-header", "Referer: https://www.tiktok.com/",
                "-x",
                "--audio-format", "mp3",
                "--audio-quality", "2",
                "-o", audio_path,
                video_url,
            ]
            ytdlp_audio_result = subprocess.run(yt_dlp_audio, capture_output=True, text=True)
            if ytdlp_audio_result.returncode != 0:
                try:
                    with open("/tmp/tiktok_debug.log", "a", encoding="utf-8") as handle:
                        handle.write(f"{datetime.now().isoformat()} yt-dlp audio stderr: {ytdlp_audio_result.stderr[:2000]}\n")
                except Exception:
                    pass
                # Fallback to direct URL -> ffmpeg
                cookie_header = build_cookie_header(load_cookie_jar())
                headers_lines = [
                    f"Referer: {video_url}",
                    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                ]
                if cookie_header:
                    headers_lines.append(f"Cookie: {cookie_header}")
                headers_arg = "\r\n".join(headers_lines) + "\r\n"
                command = [
                    "ffmpeg",
                    "-y",
                    "-headers", headers_arg,
                    "-i", direct_url,
                    "-vn",
                    "-acodec", "libmp3lame",
                    "-q:a", "2",
                    full_audio_path,
                ]
                result = subprocess.run(command, capture_output=True, text=True)
                if result.returncode != 0:
                    try:
                        with open("/tmp/tiktok_debug.log", "a", encoding="utf-8") as handle:
                            handle.write(f"{datetime.now().isoformat()} ffmpeg direct-url stderr: {result.stderr[:2000]}\n")
                    except Exception:
                        pass
                    # Last fallback: download file first, then extract audio
                    video_path = os.path.join(tmpdir, 'video.mp4')
                    if not download_media_url(direct_url, video_path, referer=video_url):
                        return jsonify({"error": "Nu am putut descărca video-ul."}), 500
                    command = [
                        "ffmpeg",
                        "-y",
                        "-i", video_path,
                        "-vn",
                        "-acodec", "libmp3lame",
                        "-q:a", "2",
                        full_audio_path,
                    ]
                    result = subprocess.run(command, capture_output=True, text=True)
                    if result.returncode != 0:
                        try:
                            with open("/tmp/tiktok_debug.log", "a", encoding="utf-8") as handle:
                                handle.write(f"{datetime.now().isoformat()} ffmpeg local-file stderr: {result.stderr[:2000]}\n")
                        except Exception:
                            pass
                        return jsonify({"error": f"Failed to extract audio: {result.stderr}"}), 500

            if not os.path.exists(full_audio_path):
                return jsonify({"error": "Failed to download audio"}), 500

            if os.path.getsize(full_audio_path) == 0:
                return jsonify({"error": "Downloaded audio is empty"}), 500

            # Validate audio duration before Whisper to avoid zero-length tensor errors
            probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nokey=1:noprint_wrappers=1",
                full_audio_path,
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
            if probe_result.returncode != 0:
                return jsonify({"error": f"Failed to probe audio: {probe_result.stderr}"}), 500
            try:
                duration_sec = float((probe_result.stdout or "").strip() or "0")
            except ValueError:
                duration_sec = 0.0
            if duration_sec <= 0.5:
                return jsonify({"error": "Downloaded audio is too short to transcribe"}), 500

            # 3. Transcribe using Whisper
            # Map languages if needed (OpenAI Whisper handles 'ro', 'ru' etc.)
            transcribe_opts = {}
            if language and language != 'auto':
                # Handle 'ro-md' as 'ro' for Whisper
                whisper_lang = 'ro' if language == 'ro-md' else language
                transcribe_opts['language'] = whisper_lang

            try:
                audio = whisper.load_audio(full_audio_path)
            except Exception as exc:
                return jsonify({"error": f"Failed to load audio for Whisper: {exc}"}), 500
            if audio.size == 0:
                return jsonify({"error": "Downloaded audio has no samples"}), 500

            # Guard against extremely short audio that breaks Whisper's mel shapes
            try:
                audio_seconds = float(audio.shape[0]) / 16000.0
            except Exception:
                audio_seconds = 0.0
            if audio_seconds < 1.0:
                return jsonify({"error": "Downloaded audio is too short to transcribe"}), 500

            try:
                result = model.transcribe(audio, **transcribe_opts)
            except Exception as exc:
                return jsonify({"error": f"Whisper failed to transcribe audio: {exc}"}), 500
            transcription_text = result['text']
            if language == 'ro-md':
                transcription_text = apply_moldovan_slang(transcription_text)
            
            return jsonify({
                "transcription": transcription_text,
                "status": "completed"
            })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/subtitles', methods=['POST'])
@app.route('/subtitles', methods=['POST'])
def subtitles():
    data = request.json
    video_url = data.get('video_url')
    language = data.get('language')

    if not video_url:
        return jsonify({"error": "Video URL is required"}), 400

    try:
        subtitle_text = try_fetch_subtitles(video_url, language if language != 'auto' else None)
        if not subtitle_text:
            return jsonify({"error": "Nu au fost găsite subtitrări pentru acest clip."}), 404
        if language == 'ro-md':
            subtitle_text = apply_moldovan_slang(subtitle_text)
        return jsonify({
            "subtitles": subtitle_text,
            "status": "completed"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    if dist_dir.exists():
        file_path = dist_dir / path
        if path and file_path.is_file():
            return send_from_directory(dist_dir, path)
        index_path = dist_dir / "index.html"
        if index_path.exists():
            return send_from_directory(dist_dir, "index.html")
    return jsonify({"error": "Frontend build not found. Run npm run build."}), 404

if __name__ == '__main__':
    port = int(os.environ.get("PORT", "5001"))
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host='0.0.0.0', port=port, debug=debug)
