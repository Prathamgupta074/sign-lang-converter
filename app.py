from flask import Flask, request, jsonify
from flask_cors import CORS
import speech_recognition as sr
import io
import requests
import time
import os
from collections import defaultdict
from flask import send_from_directory
from datetime import datetime

app = Flask(__name__, static_folder='static')
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
            img = f"{BASE_URL}/static/signs/{language}/{char}.jpg"
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
        signs = get_signs(processed, lang)
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


@app.route('/static/signs/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)
# ─────────────────────────────────────────────
# 🚀 RUN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)