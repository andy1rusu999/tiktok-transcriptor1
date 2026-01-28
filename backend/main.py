import os
import tempfile
from pathlib import Path
import whisper
import yt_dlp
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime

dist_dir = Path(__file__).resolve().parent.parent / "dist"
app = Flask(__name__, static_folder=str(dist_dir), static_url_path="/")
CORS(app, resources={r"/api/*": {"origins": "*"}})

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

    tiktok_url = f"https://www.tiktok.com/@{username}"
    
    ydl_opts = {
        'extract_flat': True,
        'quiet': False,  # Changed to False for better debugging
        'no_warnings': False,
        'playlist_items': '1-20',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.tiktok.com/',
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(tiktok_url, download=False)
            
            if 'entries' not in result:
                return jsonify({"videos": []})

            videos = []
            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')) if start_date_str else None
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')) if end_date_str else None

            for entry in result.get('entries', []):
                if not entry:
                    continue
                    
                upload_date_str = entry.get('upload_date')
                video_date = None
                
                if upload_date_str:
                    try:
                        video_date = datetime.strptime(upload_date_str, '%Y%m%d').replace(tzinfo=None)
                        
                        if start_date and video_date < start_date.replace(tzinfo=None):
                            continue
                        if end_date and video_date > end_date.replace(tzinfo=None):
                            continue
                    except Exception as e:
                        print(f"Error parsing date {upload_date_str}: {e}")

                videos.append({
                    "id": entry.get('id'),
                    "url": entry.get('url') or (f"https://www.tiktok.com/@{username}/video/{entry.get('id')}" if entry.get('id') else None),
                    "title": entry.get('title') or entry.get('description') or "Untitled Video",
                    "createdAt": video_date.isoformat() if video_date else datetime.now().isoformat(),
                    "duration": str(entry.get('duration', '0:00')),
                    "status": "pending"
                })

            return jsonify({"videos": videos})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

@app.route('/api/transcribe', methods=['POST'])
@app.route('/transcribe', methods=['POST'])
def transcribe():
    data = request.json
    video_url = data.get('video_url')
    language = data.get('language') # e.g., 'ro', 'ru', 'auto'

    if not video_url:
        return jsonify({"error": "Video URL is required"}), 400

    try:
        # 1. Create a temporary directory to store the audio file
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, 'audio')
            
            # 2. Download audio using yt-dlp
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': audio_path,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            full_audio_path = audio_path + '.mp3'
            
            if not os.path.exists(full_audio_path):
                return jsonify({"error": "Failed to download audio"}), 500

            # 3. Transcribe using Whisper
            # Map languages if needed (OpenAI Whisper handles 'ro', 'ru' etc.)
            transcribe_opts = {}
            if language and language != 'auto':
                # Handle 'ro-md' as 'ro' for Whisper
                whisper_lang = 'ro' if language == 'ro-md' else language
                transcribe_opts['language'] = whisper_lang

            result = model.transcribe(full_audio_path, **transcribe_opts)
            transcription_text = result['text']
            if language == 'ro-md':
                transcription_text = apply_moldovan_slang(transcription_text)
            
            return jsonify({
                "transcription": transcription_text,
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
