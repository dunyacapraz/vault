import os
import json
import uuid
from datetime import datetime
from functools import wraps

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from flask import (
    Flask, render_template, request, session, jsonify,
    abort, Response, stream_with_context
)
from werkzeug.utils import secure_filename

load_dotenv()  # .env dosyasındaki değişkenleri oku

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'vault_secure_key_2026_change_me')

BASE_DIR = os.path.dirname(__file__)
DATA_FILE = os.path.join(BASE_DIR, 'data', 'trips.json')
EVENTS_FILE = os.path.join(BASE_DIR, 'data', 'events.json')
os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp4', 'mov', 'avi', 'webm'}
VIDEO_EXTENSIONS = {'mp4', 'mov', 'avi', 'webm'}

# ---------------------------------------------------------------------------
# Cloudflare R2 yapılandırması (.env dosyasından okunur)
# ---------------------------------------------------------------------------
R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.environ.get('R2_BUCKET_NAME')
R2_ENDPOINT_URL = os.environ.get('R2_ENDPOINT_URL') or (
    f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else None
)

if not all([R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_ENDPOINT_URL]):
    raise RuntimeError(
        "R2 ortam değişkenleri eksik. .env dosyanı kontrol et "
        "(R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME)."
    )

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT_URL,
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version="s3v4", region_name="auto"),
)


def r2_key(trip_id, filename):
    return f"{trip_id}/{filename}"


def r2_upload(fileobj, trip_id, filename, content_type=None):
    extra = {"ContentType": content_type} if content_type else {}
    s3.upload_fileobj(fileobj, R2_BUCKET_NAME, r2_key(trip_id, filename), ExtraArgs=extra)


def r2_delete(trip_id, filename):
    try:
        s3.delete_object(Bucket=R2_BUCKET_NAME, Key=r2_key(trip_id, filename))
    except ClientError:
        pass


def r2_delete_prefix(trip_id):
    """Bir albümün tüm dosyalarını R2'den siler."""
    paginator = s3.get_paginator('list_objects_v2')
    try:
        for page in paginator.paginate(Bucket=R2_BUCKET_NAME, Prefix=f"{trip_id}/"):
            for obj in page.get('Contents', []):
                s3.delete_object(Bucket=R2_BUCKET_NAME, Key=obj['Key'])
    except ClientError:
        pass


# ---------------------------------------------------------------------------
# Sabit arkadaş listesi — yeni bir arkadaş eklemek için buraya bir satır ekle.
# Şifreleri herkesin haberi olacak şekilde kendiniz belirleyip paylaşın.
# ---------------------------------------------------------------------------
USERS = {
    "admin":  {"password": os.environ.get('PW_ADMIN', '1234'),        "name": "Admin",  "avatar_color": "#0f172a"},
    "ayse":   {"password": os.environ.get('PW_AYSE', 'ayse2026'),     "name": "Ayşe",   "avatar_color": "#be185d"},
    "mehmet": {"password": os.environ.get('PW_MEHMET', 'mehmet2026'), "name": "Mehmet", "avatar_color": "#1d4ed8"},
    "zeynep": {"password": os.environ.get('PW_ZEYNEP', 'zeynep2026'), "name": "Zeynep", "avatar_color": "#15803d"},
}


# ---------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ---------------------------------------------------------------------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def media_type(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return 'video' if ext in VIDEO_EXTENSIONS else 'image'


def load_trips():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_trips(trips):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(trips, f, ensure_ascii=False, indent=2)


def find_trip(trips, trip_id):
    return next((t for t in trips if t['id'] == trip_id), None)


def load_events():
    if not os.path.exists(EVENTS_FILE):
        return []
    try:
        with open(EVENTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_events(events):
    with open(EVENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


def event_datetime(event):
    time_str = event.get('time') or '00:00'
    try:
        return datetime.fromisoformat(f"{event['date']}T{time_str}")
    except (ValueError, KeyError):
        return None


def upcoming_events():
    now = datetime.now()
    events = load_events()
    with_dt = [(e, event_datetime(e)) for e in events]
    future = [e for e, dt in with_dt if dt and dt >= now]
    future.sort(key=lambda e: event_datetime(e))
    return future


def public_users():
    return {u: {"name": info["name"], "avatar_color": info["avatar_color"]} for u, info in USERS.items()}


def current_user():
    username = session.get('username')
    if not username or username not in USERS:
        return None
    info = USERS[username]
    return {"username": username, "name": info["name"], "avatar_color": info["avatar_color"]}


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            if request.path.startswith('/api/') or request.method != 'GET':
                return jsonify({"status": "error", "message": "Yetkisiz erişim"}), 403
            abort(403)
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Sayfa rotaları
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    user = current_user()
    events = upcoming_events()
    if not user:
        return render_template('login.html', events=events)

    trips = load_trips()
    trips_sorted = sorted(trips, key=lambda t: t.get('created_at', ''), reverse=True)
    return render_template('dashboard.html', user=user, trips=trips_sorted, users=public_users(),
                            events=events, all_events=load_events())


@app.route('/trip/<trip_id>')
@login_required
def trip_detail(trip_id):
    user = current_user()
    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        abort(404)
    media_sorted = sorted(trip.get('media', []), key=lambda m: m.get('uploaded_at', ''), reverse=True)
    return render_template('trip.html', user=user, trip=trip, media=media_sorted, users=public_users())


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip().lower()
    password = data.get('password') or ''

    user = USERS.get(username)
    if user and user['password'] == password:
        session['username'] = username
        return jsonify({"status": "success", "name": user['name']})
    return jsonify({"status": "error", "message": "Hatalı kullanıcı adı veya şifre"}), 401


@app.route('/logout', methods=['POST'])
def logout():
    session.pop('username', None)
    return jsonify({"status": "success"})


# ---------------------------------------------------------------------------
# Tatil / Anı (trip) API
# ---------------------------------------------------------------------------
@app.route('/api/trips', methods=['POST'])
@login_required
def create_trip():
    user = current_user()
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    date = (data.get('date') or '').strip()
    description = (data.get('description') or '').strip()

    if not title:
        return jsonify({"status": "error", "message": "Başlık gerekli"}), 400

    trip_id = uuid.uuid4().hex[:12]
    trip = {
        "id": trip_id,
        "title": title,
        "date": date,
        "description": description,
        "created_by": user['username'],
        "created_at": datetime.utcnow().isoformat(),
        "cover": None,
        "media": [],
    }

    trips = load_trips()
    trips.append(trip)
    save_trips(trips)

    return jsonify({"status": "success", "trip": trip})


@app.route('/api/trips/<trip_id>/delete', methods=['POST'])
@login_required
def delete_trip(trip_id):
    user = current_user()
    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        return jsonify({"status": "error", "message": "Albüm bulunamadı"}), 404

    if trip['created_by'] != user['username']:
        return jsonify({"status": "error", "message": "Sadece albümü oluşturan kişi silebilir"}), 403

    trips.remove(trip)
    save_trips(trips)

    r2_delete_prefix(trip_id)

    return jsonify({"status": "success"})


@app.route('/api/trips/<trip_id>/upload', methods=['POST'])
@login_required
def upload_media(trip_id):
    user = current_user()
    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        return jsonify({"status": "error", "message": "Albüm bulunamadı"}), 404

    files = request.files.getlist('files') or request.files.getlist('file')
    if not files:
        return jsonify({"status": "error", "message": "Dosya seçilmedi"}), 400

    saved = []
    errors = []
    for file in files:
        if not file or file.filename == '':
            continue
        if not allowed_file(file.filename):
            errors.append(file.filename)
            continue

        ext = file.filename.rsplit('.', 1)[1].lower()
        unique_name = f"{uuid.uuid4().hex[:10]}.{ext}"

        try:
            r2_upload(file.stream, trip_id, unique_name, content_type=file.mimetype)
        except ClientError:
            errors.append(file.filename)
            continue

        entry = {
            "filename": unique_name,
            "original_name": secure_filename(file.filename),
            "type": media_type(unique_name),
            "uploaded_by": user['username'],
            "uploaded_at": datetime.utcnow().isoformat(),
        }
        trip.setdefault('media', []).append(entry)
        if not trip.get('cover') and entry['type'] == 'image':
            trip['cover'] = unique_name
        saved.append(entry)

    save_trips(trips)

    if not saved:
        return jsonify({"status": "error", "message": "Geçersiz dosya formatı"}), 400

    return jsonify({"status": "success", "saved": saved, "errors": errors})


@app.route('/api/trips/<trip_id>/media/<filename>/delete', methods=['POST'])
@login_required
def delete_media(trip_id, filename):
    user = current_user()
    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        return jsonify({"status": "error", "message": "Albüm bulunamadı"}), 404

    entry = next((m for m in trip.get('media', []) if m['filename'] == filename), None)
    if not entry:
        return jsonify({"status": "error", "message": "Dosya bulunamadı"}), 404

    if entry['uploaded_by'] != user['username'] and trip['created_by'] != user['username']:
        return jsonify({"status": "error", "message": "Bu anıyı sadece ekleyen kişi veya albümü oluşturan silebilir"}), 403

    trip['media'] = [m for m in trip['media'] if m['filename'] != filename]
    if trip.get('cover') == filename:
        next_cover = next((m['filename'] for m in trip['media'] if m['type'] == 'image'), None)
        trip['cover'] = next_cover

    save_trips(trips)

    r2_delete(trip_id, filename)

    return jsonify({"status": "success"})


# ---------------------------------------------------------------------------
# Yaklaşan Etkinlikler API
# ---------------------------------------------------------------------------
@app.route('/api/events', methods=['POST'])
@login_required
def create_event():
    user = current_user()
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    date = (data.get('date') or '').strip()
    time = (data.get('time') or '').strip()
    location = (data.get('location') or '').strip()

    if not title or not date:
        return jsonify({"status": "error", "message": "Başlık ve tarih gerekli"}), 400

    event = {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "date": date,
        "time": time,
        "location": location,
        "created_by": user['username'],
        "created_at": datetime.utcnow().isoformat(),
    }

    if event_datetime(event) is None:
        return jsonify({"status": "error", "message": "Geçersiz tarih/saat"}), 400

    events = load_events()
    events.append(event)
    save_events(events)
    return jsonify({"status": "success", "event": event})


@app.route('/api/events/<event_id>/delete', methods=['POST'])
@login_required
def delete_event(event_id):
    user = current_user()
    events = load_events()
    event = next((e for e in events if e['id'] == event_id), None)
    if not event:
        return jsonify({"status": "error", "message": "Etkinlik bulunamadı"}), 404

    if event['created_by'] != user['username']:
        return jsonify({"status": "error", "message": "Sadece etkinliği ekleyen kişi silebilir"}), 403

    events.remove(event)
    save_events(events)
    return jsonify({"status": "success"})


# ---------------------------------------------------------------------------
# Korumalı medya servis — R2'den okuyup Flask üzerinden akıtır (proxy).
# Böylece dosyalar herkese açık değildir, sadece giriş yapanlar görebilir.
# ---------------------------------------------------------------------------
@app.route('/uploads/<trip_id>/<filename>')
@login_required
def serve_file(trip_id, filename):
    try:
        obj = s3.get_object(Bucket=R2_BUCKET_NAME, Key=r2_key(trip_id, filename))
    except ClientError:
        abort(404)

    content_type = obj.get('ContentType', 'application/octet-stream')
    content_length = obj.get('ContentLength')

    headers = {}
    if content_length is not None:
        headers['Content-Length'] = str(content_length)

    return Response(
        stream_with_context(obj['Body'].iter_chunks(chunk_size=65536)),
        mimetype=content_type,
        headers=headers,
    )


if __name__ == '__main__':
    app.run(debug=True, port=5000)