import json
import os
import re
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

cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
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
    html = fetch_profile_html(username, cookies)
    if not html:
        return None
    match = re.search(r'"secUid":"([^"]+)"', html)
    if match:
        return match.group(1)
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

def fetch_direct_url(video_url: str) -> str | None:
    video_id = extract_video_id(video_url)
    if not video_id:
        return None

    cookies = load_cookie_jar()
    ms_token = cookies.get("msToken")
    params = {
        "aid": "1988",
        "itemId": video_id,
    }
    if ms_token:
        params["msToken"] = ms_token
    url = "https://www.tiktok.com/api/item/detail/?" + urllib.parse.urlencode(params)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://www.tiktok.com/',
    }
    cookie_header = build_cookie_header(cookies)
    if cookie_header:
        headers['Cookie'] = cookie_header

    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8", errors="ignore")
        data = json.loads(payload)
    except Exception as exc:
        print(f"Failed to fetch direct URL for {video_id}: {exc}")
        return None

    item = data.get("itemInfo", {}).get("itemStruct")
    if not item:
        return None
    video_info = item.get("video", {}) or {}
    play_addr = video_info.get("playAddr") or video_info.get("play_addr") or {}
    download_addr = video_info.get("downloadAddr") or video_info.get("download_addr") or {}
    for addr in (play_addr, download_addr):
        if isinstance(addr, dict):
            url_list = addr.get("urlList") or addr.get("url_list") or []
            if url_list:
                return url_list[0]
    return None

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
        'extractor_args': {'tiktok': {'impersonate': ['chrome']}},
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(tiktok_url, download=False)
    except Exception as exc:
        print(f"yt-dlp user extraction failed: {exc}")
        videos = fetch_videos_via_api(username, start_day, end_day)
        return jsonify({"videos": videos})

    entries = result.get('entries') if isinstance(result, dict) else None
    if not entries:
        videos = fetch_videos_via_api(username, start_day, end_day)
        return jsonify({"videos": videos})

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

    if not videos:
        videos = fetch_videos_via_api(username, start_day, end_day)

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
            
            # 2. Resolve direct media URL using yt-dlp --get-url
            if not direct_url:
                cookiefile = get_cookiefile()
                cmd = [
                    sys.executable,
                    "-m",
                    "yt_dlp",
                    "--cookies", cookiefile if cookiefile else "/dev/null",
                    "--extractor-args", "tiktok:impersonate=chrome",
                    "--get-url",
                    video_url,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0 and result.stdout.strip():
                    direct_url = result.stdout.strip().split('\n')[0]
                else:
                    return jsonify({"error": f"Failed to resolve media URL: {result.stderr}"}), 500

            # 3. Extract audio using ffmpeg with headers and cookies
            cookie_header = build_cookie_header(load_cookie_jar())
            headers = [
                "Referer: https://www.tiktok.com/",
            ]
            if cookie_header:
                headers.append(f"Cookie: {cookie_header}")
            header_value = "\r\n".join(headers) + "\r\n"

            command = [
                "ffmpeg",
                "-y",
                "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "-headers", header_value,
                "-i", direct_url,
                "-vn",
                "-acodec", "libmp3lame",
                "-q:a", "2",
                full_audio_path,
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                return jsonify({"error": f"Failed to extract audio: {result.stderr}"}), 500

            if not os.path.exists(full_audio_path):
                return jsonify({"error": "Failed to download audio"}), 500

            if os.path.getsize(full_audio_path) == 0:
                return jsonify({"error": "Downloaded audio is empty"}), 500

            # 3. Transcribe using Whisper
            # Map languages if needed (OpenAI Whisper handles 'ro', 'ru' etc.)
            transcribe_opts = {}
            if language and language != 'auto':
                # Handle 'ro-md' as 'ro' for Whisper
                whisper_lang = 'ro' if language == 'ro-md' else language
                transcribe_opts['language'] = whisper_lang

            audio = whisper.load_audio(full_audio_path)
            if audio.size == 0:
                return jsonify({"error": "Downloaded audio has no samples"}), 500

            result = model.transcribe(audio, **transcribe_opts)
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
