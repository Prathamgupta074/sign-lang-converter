''' 
from flask import Flask, render_template, request, jsonify, url_for
from flask_cors import CORS
import speech_recognition as sr
import io
import os 
import requests
import time
from collections import defaultdict
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────
# 🌍 Language Names
# ─────────────────────────────────────────────
LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "bn": "Bengali", "as": "Assamese",
    "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "mr": "Marathi",
    "ne": "Nepali", "or": "Odia", "pa": "Punjabi", "ta": "Tamil",
    "te": "Telugu", "ur": "Urdu", "ar": "Arabic", "fr": "French",
    "de": "German", "es": "Spanish", "ru": "Russian", "zh-CN": "Chinese",
    "ja": "Japanese", "ko": "Korean", "it": "Italian", "tr": "Turkish",
    "bg": "Bulgarian", "mk": "Macedonian", "id": "Indonesian",
    "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "sv": "Swedish"
}

def get_language_name(code):
    return LANGUAGE_NAMES.get(code, code.upper())


# ─────────────────────────────────────────────
# ⏱️ Rate Limiting (per IP: 20 req/min)
# ─────────────────────────────────────────────
rate_store = defaultdict(lambda: {"count": 0, "reset_at": 0})
RATE_LIMIT = 20
RATE_WINDOW = 60  # seconds

def check_rate_limit(ip):
    now = time.time()
    record = rate_store[ip]
    if now > record["reset_at"]:
        record["count"] = 0
        record["reset_at"] = now + RATE_WINDOW
    if record["count"] >= RATE_LIMIT:
        remaining = int(record["reset_at"] - now)
        return False, remaining
    record["count"] += 1
    return True, RATE_LIMIT - record["count"]


# ─────────────────────────────────────────────
# 📊 Usage Stats (in-memory, per session)
# ─────────────────────────────────────────────
usage_stats = {
    "total_conversions": 0,
    "total_signs": 0,
    "total_words": 0,
    "languages": defaultdict(int),
    "sign_languages": defaultdict(int),
    "conversions_by_hour": defaultdict(int)
}

def record_usage(data, sign_lang):
    usage_stats["total_conversions"] += 1
    usage_stats["total_signs"] += len([s for s in data.get("signs", []) if s.get("image")])
    usage_stats["total_words"] += len((data.get("processed", "") or "").split())
    lang = data.get("language_name", "English")
    usage_stats["languages"][lang] += 1
    usage_stats["sign_languages"][sign_lang.upper()] += 1
    hour = datetime.now().strftime("%H:00")
    usage_stats["conversions_by_hour"][hour] += 1


# ─────────────────────────────────────────────
# 🌐 Translation (Google Translate, no lib)
# ─────────────────────────────────────────────
def translate_text(text):
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx", "sl": "auto", "tl": "en",
            "dt": "t", "q": text
        }
        response = requests.get(url, params=params, timeout=5)
        result = response.json()
        translated = "".join([item[0] for item in result[0]])
        detected_lang = result[2]
        return translated, detected_lang
    except Exception:
        return text, "unknown"


# ─────────────────────────────────────────────
# 🧠 Name Detection
# ─────────────────────────────────────────────
def is_probably_name(text):
    words = text.strip().split()
    if len(words) <= 3:
        if all(word[0].isupper() for word in words if word):
            if all(len(word) <= 8 for word in words):
                return True
    return False


# ─────────────────────────────────────────────
# 🔥 Process Text
# ─────────────────────────────────────────────
def process_text(text):
    translated, detected_lang = translate_text(text)
    if detected_lang == "en":
        return text, detected_lang
    if is_probably_name(text):
        return text, detected_lang
    return translated, detected_lang


# ─────────────────────────────────────────────
# ✋ Text → Signs
# ─────────────────────────────────────────────
def get_signs(text):
    BASE_URL = "https://sign-lang-converter-0gpi.onrender.com"
    signs = []

    for char in text.lower():
        if char == " ":
            signs.append({"char": " ", "image": None})
        else:
            full_path = os.path.join("static/signs", f"{char}.jpg")

            if os.path.exists(full_path):
                img_path = f"{BASE_URL}/static/signs/{char}.jpg"
                signs.append({"char": char, "image": img_path})
            else:
                signs.append({"char": char, "image": None})

    return signs


# ─────────────────────────────────────────────
# 🌐 ROUTES
# ─────────────────────────────────────────────



@app.route('/convert', methods=['POST'])
def convert():
    try:
        ip = request.remote_addr

        allowed, remaining = check_rate_limit(ip)
        if not allowed:
            return jsonify({
                'error': 'Rate limit exceeded',
                'retry_after': remaining
            }), 429

        data = request.get_json() or {}
        original_text = data.get('text', '').strip()
        language = data.get('language', 'asl')

        if not original_text:
            return jsonify({'error': 'No text provided'}), 400

        if len(original_text) > 500:
            return jsonify({'error': 'Text too long'}), 400

        # 🔥 DEBUG PRINTS
        print("INPUT:", original_text)

        processed_text, detected_lang = process_text(original_text)
        print("PROCESSED:", processed_text)

        signs = text_to_signs(processed_text, language)
        print("SIGNS GENERATED")

        response_data = {
            'original': original_text,
            'processed': processed_text,
            'detected_language': detected_lang,
            'language_name': get_language_name(detected_lang),
            'signs': signs,
            'rate_remaining': remaining
        }

        record_usage(response_data, language)

        return jsonify(response_data)

    except Exception as e:
        print("🔥 ERROR:", str(e))
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/speech', methods=['POST'])
def speech_to_text():
    ip = request.remote_addr
    allowed, _ = check_rate_limit(ip)
    if not allowed:
        return jsonify({'text': '', 'success': False, 'error': 'Rate limit exceeded'}), 429

    recognizer = sr.Recognizer()
    try:
        audio_data = request.data
        audio_file = io.BytesIO(audio_data)
        with sr.AudioFile(audio_file) as source:
            audio = recognizer.record(source)
        text = recognizer.recognize_google(audio)
        return jsonify({'text': text, 'success': True})
    except Exception as e:
        return jsonify({'text': '', 'success': False, 'error': str(e)})


@app.route('/stats', methods=['GET'])
def get_stats():
    """Public stats endpoint for the dashboard."""
    return jsonify({
        'total_conversions': usage_stats['total_conversions'],
        'total_signs': usage_stats['total_signs'],
        'total_words': usage_stats['total_words'],
        'top_languages': dict(
            sorted(usage_stats['languages'].items(), key=lambda x: x[1], reverse=True)[:10]
        ),
        'sign_languages': dict(usage_stats['sign_languages']),
        'conversions_by_hour': dict(usage_stats['conversions_by_hour'])
    })


@app.route('/feedback', methods=['POST'])
def submit_feedback():
    """Receives feedback from users."""
    data = request.get_json() or {}
    rating = data.get('rating', 0)
    tags = data.get('tags', [])
    message = data.get('message', '').strip()
    ip = request.remote_addr
    timestamp = datetime.now().isoformat()

    # In production: save to DB or send to email/Slack
    print(f"\n📬 Feedback Received at {timestamp}")
    print(f"   IP: {ip} | Rating: {rating}⭐ | Tags: {tags}")
    print(f"   Message: {message or '(none)'}\n")

    return jsonify({'success': True, 'message': 'Thank you for your feedback!'})


@app.route('/languages', methods=['GET'])
def get_languages():
    """Returns all supported language names."""
    return jsonify(LANGUAGE_NAMES)


# ─────────────────────────────────────────────
# 🚀 RUN
# ─────────────────────────────────────────────
if __name__ == '__main__':
    app.run(debug=True)
    '''
from flask import Flask, request, jsonify
from flask_cors import CORS
import speech_recognition as sr
import io
import requests
import time
import os
from collections import defaultdict
from datetime import datetime

app = Flask(__name__)
CORS(app)

# 🔗 YOUR RENDER URL (IMPORTANT)
BASE_URL = "https://sign-lang-converter-0gpi.onrender.com"

# ─────────────────────────────────────────────
# 🌍 Language Names
# ─────────────────────────────────────────────
LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "bn": "Bengali", "ta": "Tamil",
    "te": "Telugu", "mr": "Marathi", "gu": "Gujarati", "kn": "Kannada",
    "ml": "Malayalam", "pa": "Punjabi", "ur": "Urdu", "ar": "Arabic",
    "fr": "French", "de": "German", "es": "Spanish", "ru": "Russian"
}

def get_language_name(code):
    return LANGUAGE_NAMES.get(code, code.upper())


# ─────────────────────────────────────────────
# ⏱️ Rate Limiting
# ─────────────────────────────────────────────
rate_store = defaultdict(lambda: {"count": 0, "reset_at": 0})
RATE_LIMIT = 20
RATE_WINDOW = 60

def check_rate_limit(ip):
    now = time.time()
    record = rate_store[ip]

    if now > record["reset_at"]:
        record["count"] = 0
        record["reset_at"] = now + RATE_WINDOW

    if record["count"] >= RATE_LIMIT:
        return False, int(record["reset_at"] - now)

    record["count"] += 1
    return True, RATE_LIMIT - record["count"]


# ─────────────────────────────────────────────
# 📊 Usage Stats
# ─────────────────────────────────────────────
usage_stats = {
    "total_conversions": 0,
    "total_signs": 0,
    "total_words": 0
}

def record_usage(data):
    usage_stats["total_conversions"] += 1
    usage_stats["total_signs"] += len([s for s in data["signs"] if s["image"]])
    usage_stats["total_words"] += len(data["processed"].split())


# ─────────────────────────────────────────────
# 🌐 Translation (Google API)
# ─────────────────────────────────────────────
def translate_text(text):
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx", "sl": "auto", "tl": "en",
            "dt": "t", "q": text
        }
        res = requests.get(url, params=params, timeout=5)
        result = res.json()

        translated = "".join([item[0] for item in result[0]])
        detected_lang = result[2]

        return translated, detected_lang

    except:
        return text, "en"


# ─────────────────────────────────────────────
# 🧠 Process Text
# ─────────────────────────────────────────────
def process_text(text):
    translated, lang = translate_text(text)

    if lang == "en":
        return text, lang

    return translated, lang


# ─────────────────────────────────────────────
# ✋ TEXT → SIGNS (FIXED)
# ─────────────────────────────────────────────
def get_signs(text):
    signs = []

    for char in text.lower():
        if char == " ":
            signs.append({"char": " ", "image": None})
        elif char.isalpha():
            img = f"{BASE_URL}/static/signs/{char.upper()}.jpg"
            signs.append({"char": char, "image": img})
        else:
            signs.append({"char": char, "image": None})

    return signs


# ─────────────────────────────────────────────
# 🔥 MAIN API
# ─────────────────────────────────────────────
@app.route('/convert', methods=['POST'])
def convert():
    try:
        ip = request.remote_addr

        allowed, remaining = check_rate_limit(ip)
        if not allowed:
            return jsonify({'error': 'Rate limit exceeded'}), 429

        data = request.get_json() or {}
        text = data.get("text", "").strip()

        if not text:
            return jsonify({"error": "No text provided"}), 400

        if len(text) > 500:
            return jsonify({"error": "Text too long"}), 400

        processed, lang = process_text(text)
        signs = get_signs(processed)

        response = {
            "original": text,
            "processed": processed,
            "detected_language": lang,
            "language_name": get_language_name(lang),
            "signs": signs,
            "rate_remaining": remaining
        }

        record_usage(response)

        return jsonify(response)

    except Exception as e:
        print("🔥 ERROR:", str(e))
        return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────
# 🎤 SPEECH API
# ─────────────────────────────────────────────
@app.route('/speech', methods=['POST'])
def speech():
    try:
        recognizer = sr.Recognizer()
        audio_file = io.BytesIO(request.data)

        with sr.AudioFile(audio_file) as source:
            audio = recognizer.record(source)

        text = recognizer.recognize_google(audio)

        return jsonify({"text": text, "success": True})

    except Exception as e:
        return jsonify({"text": "", "success": False})


# ─────────────────────────────────────────────
# 📊 STATS
# ─────────────────────────────────────────────
@app.route('/stats')
def stats():
    return jsonify(usage_stats)


# ─────────────────────────────────────────────
# ❤️ HEALTH CHECK
# ─────────────────────────────────────────────
@app.route('/')
def home():
    return "Backend is running 🚀"


# ─────────────────────────────────────────────
# 🚀 RUN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)