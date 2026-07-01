import os
import json
import uuid
import random
from datetime import datetime
from functools import wraps

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

try:
    from pywebpush import webpush, WebPushException
    PUSH_AVAILABLE = True
except ImportError:
    PUSH_AVAILABLE = False

from flask import (
    Flask, render_template, request, session, jsonify,
    abort, Response, stream_with_context, redirect, url_for
)
from werkzeug.utils import secure_filename

load_dotenv()  # .env dosyasındaki değişkenleri oku

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'vault_secure_key_2026_change_me')


@app.after_request
def add_sw_scope_header(response):
    # sw.js /static/ altından servis edildiği için tarayıcı varsayılan olarak
    # scope'unu /static/ ile sınırlar. Tüm siteyi (scope: '/') kontrol
    # edebilmesi için bu header gerekiyor.
    if request.path == '/static/sw.js':
        response.headers['Service-Worker-Allowed'] = '/'
    return response

BASE_DIR = os.path.dirname(__file__)
META_TRIPS_KEY = "_meta/trips.json"
META_EVENTS_KEY = "_meta/events.json"
META_USERS_KEY = "_meta/users.json"
META_PUSH_KEY = "_meta/push_subscriptions.json"
META_FEED_KEY = "_meta/feed.json"

# ---------------------------------------------------------------------------
# Web Push (PWA bildirimleri) yapılandırması — .env dosyasından okunur.
# Anahtarları üretmek için: python3 generate_vapid_keys.py
# ---------------------------------------------------------------------------
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY')
VAPID_CLAIM_EMAIL = os.environ.get('VAPID_CLAIM_EMAIL', 'mailto:admin@example.com')
PUSH_CONFIGURED = bool(PUSH_AVAILABLE and VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)

# Yeni üyelik için gereken referans kodu (sadece bu kodu bilenler kayıt olabilir)
REFERRAL_CODE = os.environ.get('REFERRAL_CODE', 'dunya3461')

AVATAR_PALETTE = [
    "#be185d", "#1d4ed8", "#15803d", "#b45309", "#7c3aed",
    "#0e7490", "#c2410c", "#4d7c0f", "#9d174d", "#1e40af",
]

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


def r2_read_json(key, default):
    """R2'den JSON oku. Yoksa varsayılan değeri döner (Render gibi disksiz ortamlarda kalıcılık için)."""
    try:
        obj = s3.get_object(Bucket=R2_BUCKET_NAME, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') in ('NoSuchKey', '404'):
            return default
        raise
    except (json.JSONDecodeError, UnicodeDecodeError):
        return default


def r2_write_json(key, data):
    body = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    s3.put_object(Bucket=R2_BUCKET_NAME, Key=key, Body=body, ContentType='application/json')


# ---------------------------------------------------------------------------
# Üyeler — başlangıçta birkaç sabit hesapla başlar, sonrasında /register
# üzerinden referans kodu ile katılan herkes data/_meta/users.json içine
# (R2 üzerinde) kalıcı olarak eklenir.
# ---------------------------------------------------------------------------
DEFAULT_USERS = {
    "admin":  {"password": os.environ.get('PW_ADMIN', 'dunya3461'),   "name": "Admin",  "avatar_color": "#0f172a", "is_admin": True},
    "ayse":   {"password": os.environ.get('PW_AYSE', 'ayse2026'),     "name": "Ayşe",   "avatar_color": "#be185d", "is_admin": False},
    "mehmet": {"password": os.environ.get('PW_MEHMET', 'mehmet2026'), "name": "Mehmet", "avatar_color": "#1d4ed8", "is_admin": False},
    "zeynep": {"password": os.environ.get('PW_ZEYNEP', 'zeynep2026'), "name": "Zeynep", "avatar_color": "#15803d", "is_admin": False},
}


def load_users():
    """Kayıtlı üyeleri R2'den okur. İlk çalıştırmada DEFAULT_USERS ile başlatır."""
    users = r2_read_json(META_USERS_KEY, None)
    if users is None:
        users = dict(DEFAULT_USERS)
        r2_write_json(META_USERS_KEY, users)
        return users

    # Eski kayıtlarda is_admin alanı olmayabilir — geriye dönük uyumluluk
    changed = False
    for uname, info in users.items():
        if 'is_admin' not in info:
            info['is_admin'] = (uname == 'admin')
            changed = True
    if changed:
        save_users(users)
    return users


def save_users(users):
    r2_write_json(META_USERS_KEY, users)


# ---------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# ---------------------------------------------------------------------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def media_type(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    return 'video' if ext in VIDEO_EXTENSIONS else 'image'


def load_trips():
    return r2_read_json(META_TRIPS_KEY, [])


def save_trips(trips):
    r2_write_json(META_TRIPS_KEY, trips)


def find_trip(trips, trip_id):
    return next((t for t in trips if t['id'] == trip_id), None)


def media_sort_key(m):
    """Sıralama için tarih anahtarı: kullanıcı 'anı tarihi' girdiyse onu, girmediyse
    yükleme zamanını kullanır. Böylece yeni tarihliler üstte, eski tarihliler altta kalır."""
    return m.get('memory_date') or m.get('uploaded_at', '')


def find_media(trip, filename):
    entry = next((m for m in trip.get('media', []) if m['filename'] == filename), None)
    if entry is not None:
        entry.setdefault('likes', [])
        entry.setdefault('comments', [])
        entry.setdefault('caption', '')
        entry.setdefault('memory_date', '')
    return entry


def load_push_subscriptions():
    return r2_read_json(META_PUSH_KEY, {})


def save_push_subscriptions(subs):
    r2_write_json(META_PUSH_KEY, subs)


def load_feed():
    return r2_read_json(META_FEED_KEY, [])


def save_feed(posts):
    r2_write_json(META_FEED_KEY, posts)


def find_feed_post(posts, post_id):
    return next((p for p in posts if p['id'] == post_id), None)


def send_push_notification(payload, exclude_username=None, only_username=None):
    """Tüm üyelere (isteğe bağlı biri hariç) ya da tek bir kullanıcıya (only_username)
    push bildirimi gönderir. Push ayarlanmamışsa veya hata olursa sessizce geçer —
    bildirim asla ana işlemi bozmaz."""
    if not PUSH_CONFIGURED:
        return
    if only_username and only_username == exclude_username:
        return

    subs = load_push_subscriptions()
    if not subs:
        return

    if only_username:
        if only_username not in subs:
            return
        subs_to_send = {only_username: subs[only_username]}
    else:
        subs_to_send = subs

    changed = False
    body = json.dumps(payload, ensure_ascii=False)

    for username, sub_list in list(subs_to_send.items()):
        if username == exclude_username:
            continue
        still_valid = []
        for sub in sub_list:
            try:
                webpush(
                    subscription_info=sub,
                    data=body,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": VAPID_CLAIM_EMAIL},
                )
                still_valid.append(sub)
            except WebPushException as e:
                status = getattr(e.response, 'status_code', None)
                if status in (404, 410):
                    changed = True  # abonelik geçersiz/iptal — listeden düşür
                else:
                    still_valid.append(sub)
                    print(f"[PUSH HATASI] {username}: {e}")
            except Exception as e:
                still_valid.append(sub)
                print(f"[PUSH BEKLENMEYEN HATA] {username}: {e}")
        if len(still_valid) != len(sub_list):
            subs[username] = still_valid
            changed = True

    if changed:
        save_push_subscriptions(subs)


def load_events():
    return r2_read_json(META_EVENTS_KEY, [])


def save_events(events):
    r2_write_json(META_EVENTS_KEY, events)


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
    users = load_users()
    return {u: {"name": info["name"], "avatar_color": info["avatar_color"]} for u, info in users.items()}


def current_user():
    username = session.get('username')
    if not username:
        return None
    users = load_users()
    info = users.get(username)
    if not info:
        return None
    return {
        "username": username,
        "name": info["name"],
        "avatar_color": info["avatar_color"],
        "is_admin": bool(info.get("is_admin")),
    }


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            if request.path.startswith('/api/') or request.method != 'GET':
                return jsonify({"status": "error", "message": "Yetkisiz erişim"}), 403
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.get('is_admin'):
            if request.path.startswith('/api/') or request.method != 'GET':
                return jsonify({"status": "error", "message": "Sadece admin erişebilir"}), 403
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


@app.route('/feed')
@login_required
def feed_page():
    user = current_user()
    posts = load_feed()
    posts_sorted = sorted(posts, key=lambda p: p.get('created_at', ''), reverse=True)
    return render_template('feed.html', user=user, posts=posts_sorted, users=public_users())


@app.route('/trip/<trip_id>')
@login_required
def trip_detail(trip_id):
    user = current_user()
    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        abort(404)
    media_sorted = sorted(trip.get('media', []), key=media_sort_key, reverse=True)
    return render_template('trip.html', user=user, trip=trip, media=media_sorted, users=public_users())


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip().lower()
    password = data.get('password') or ''

    users = load_users()
    user = users.get(username)
    if user and user['password'] == password:
        session['username'] = username
        return jsonify({"status": "success", "name": user['name']})
    return jsonify({"status": "error", "message": "Hatalı kullanıcı adı veya şifre"}), 401


@app.route('/register', methods=['GET'])
def register_page():
    if current_user():
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip().lower()
    password = data.get('password') or ''
    name = (data.get('name') or '').strip()
    referral_code = (data.get('referral_code') or '').strip()

    if not username or not password or not name:
        return jsonify({"status": "error", "message": "Kullanıcı adı, şifre ve isim gerekli"}), 400

    if not username.replace('_', '').isalnum():
        return jsonify({"status": "error", "message": "Kullanıcı adı sadece harf, rakam ve alt çizgi içerebilir"}), 400

    if len(password) < 4:
        return jsonify({"status": "error", "message": "Şifre en az 4 karakter olmalı"}), 400

    if referral_code != REFERRAL_CODE:
        return jsonify({"status": "error", "message": "Referans kodu hatalı"}), 403

    users = load_users()
    if username in users:
        return jsonify({"status": "error", "message": "Bu kullanıcı adı zaten alınmış"}), 409

    users[username] = {
        "password": password,
        "name": name,
        "avatar_color": random.choice(AVATAR_PALETTE),
    }
    save_users(users)

    session['username'] = username
    return jsonify({"status": "success", "name": name})


@app.route('/logout', methods=['POST'])
def logout():
    session.pop('username', None)
    return jsonify({"status": "success"})


def rename_username_everywhere(old, new):
    """Kullanıcı adı değiştiğinde tüm albüm/etkinlik verilerindeki referansları günceller."""
    trips = load_trips()
    for trip in trips:
        if trip.get('created_by') == old:
            trip['created_by'] = new
        for m in trip.get('media', []):
            if m.get('uploaded_by') == old:
                m['uploaded_by'] = new
            m['likes'] = [new if u == old else u for u in m.get('likes', [])]
            for c in m.get('comments', []):
                if c.get('username') == old:
                    c['username'] = new
    save_trips(trips)

    events = load_events()
    changed = False
    for ev in events:
        if ev.get('created_by') == old:
            ev['created_by'] = new
            changed = True
    if changed:
        save_events(events)


# ---------------------------------------------------------------------------
# Hesap Ayarları
# ---------------------------------------------------------------------------
@app.route('/account')
@login_required
def account_page():
    return render_template('account.html', user=current_user())


@app.route('/api/account', methods=['POST'])
@login_required
def update_account():
    user = current_user()
    data = request.get_json() or {}
    new_username = (data.get('username') or '').strip().lower()
    new_name = (data.get('name') or '').strip()
    current_password = data.get('current_password') or ''
    new_password = data.get('new_password') or ''

    users = load_users()
    info = users[user['username']]

    if info['password'] != current_password:
        return jsonify({"status": "error", "message": "Mevcut şifre hatalı"}), 403

    if new_username and not new_username.replace('_', '').isalnum():
        return jsonify({"status": "error", "message": "Kullanıcı adı sadece harf, rakam ve alt çizgi içerebilir"}), 400

    if new_password and len(new_password) < 4:
        return jsonify({"status": "error", "message": "Yeni şifre en az 4 karakter olmalı"}), 400

    old_username = user['username']
    final_username = old_username

    if new_username and new_username != old_username:
        if new_username in users:
            return jsonify({"status": "error", "message": "Bu kullanıcı adı zaten alınmış"}), 409
        users[new_username] = info
        del users[old_username]
        final_username = new_username
        rename_username_everywhere(old_username, new_username)

    if new_name:
        users[final_username]['name'] = new_name
    if new_password:
        users[final_username]['password'] = new_password

    save_users(users)
    session['username'] = final_username

    return jsonify({"status": "success", "username": final_username, "name": users[final_username]['name']})


# ---------------------------------------------------------------------------
# Admin Paneli
# ---------------------------------------------------------------------------
@app.route('/admin')
@admin_required
def admin_page():
    users = load_users()
    trips = load_trips()
    events = load_events()
    return render_template('admin.html', user=current_user(), users=users, trips=trips,
                            events=events, referral_code=REFERRAL_CODE)


@app.route('/api/admin/users/<username>/delete', methods=['POST'])
@admin_required
def admin_delete_user(username):
    admin = current_user()
    if username == admin['username']:
        return jsonify({"status": "error", "message": "Kendi hesabını silemezsin"}), 400

    users = load_users()
    if username not in users:
        return jsonify({"status": "error", "message": "Kullanıcı bulunamadı"}), 404

    del users[username]
    save_users(users)
    return jsonify({"status": "success"})


@app.route('/api/admin/users/<username>/toggle-admin', methods=['POST'])
@admin_required
def admin_toggle_admin(username):
    admin = current_user()
    if username == admin['username']:
        return jsonify({"status": "error", "message": "Kendi admin yetkini değiştiremezsin"}), 400

    users = load_users()
    if username not in users:
        return jsonify({"status": "error", "message": "Kullanıcı bulunamadı"}), 404

    users[username]['is_admin'] = not users[username].get('is_admin', False)
    save_users(users)
    return jsonify({"status": "success", "is_admin": users[username]['is_admin']})


@app.route('/api/admin/users/<username>/reset-password', methods=['POST'])
@admin_required
def admin_reset_password(username):
    data = request.get_json() or {}
    new_password = data.get('new_password') or ''
    if len(new_password) < 4:
        return jsonify({"status": "error", "message": "Şifre en az 4 karakter olmalı"}), 400

    users = load_users()
    if username not in users:
        return jsonify({"status": "error", "message": "Kullanıcı bulunamadı"}), 404

    users[username]['password'] = new_password
    save_users(users)
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

    try:
        send_push_notification(
            {"title": "🗂️ Yeni albüm oluşturuldu", "body": f"{user['name']} \"{title}\" adında yeni bir albüm oluşturdu", "url": f"/trip/{trip_id}", "tag": f"trip-new-{trip_id}"},
            exclude_username=user['username'],
        )
    except Exception as e:
        print(f"[PUSH GÖNDERİM HATASI] {e}")

    return jsonify({"status": "success", "trip": trip})


@app.route('/api/trips/<trip_id>/edit', methods=['POST'])
@login_required
def edit_trip(trip_id):
    user = current_user()
    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        return jsonify({"status": "error", "message": "Albüm bulunamadı"}), 404

    if trip['created_by'] != user['username'] and not user.get('is_admin'):
        return jsonify({"status": "error", "message": "Bu albümü sadece oluşturan kişi veya admin düzenleyebilir"}), 403

    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    date = (data.get('date') or '').strip()
    description = (data.get('description') or '').strip()

    if not title:
        return jsonify({"status": "error", "message": "Başlık gerekli"}), 400

    trip['title'] = title
    trip['date'] = date
    trip['description'] = description
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
        except ClientError as e:
            print(f"[R2 UPLOAD HATASI] {file.filename}: {e}")
            errors.append(file.filename)
            continue
        except Exception as e:
            print(f"[BEKLENMEYEN YÜKLEME HATASI] {file.filename}: {e}")
            errors.append(file.filename)
            continue

        entry = {
            "filename": unique_name,
            "original_name": secure_filename(file.filename),
            "type": media_type(unique_name),
            "uploaded_by": user['username'],
            "uploaded_at": datetime.utcnow().isoformat(),
            "caption": "",
            "memory_date": "",
            "likes": [],
            "comments": [],
        }
        trip.setdefault('media', []).append(entry)
        if not trip.get('cover') and entry['type'] == 'image':
            trip['cover'] = unique_name
        saved.append(entry)

    save_trips(trips)

    if not saved:
        if errors:
            return jsonify({"status": "error", "message": f"Yüklenemedi: {', '.join(errors)} (terminaldeki hata mesajına bak)"}), 400
        return jsonify({"status": "error", "message": "Geçersiz dosya formatı"}), 400

    try:
        count = len(saved)
        body = f"{user['name']} \"{trip['title']}\" albümüne {count} yeni anı ekledi" if count > 1 \
            else f"{user['name']} \"{trip['title']}\" albümüne yeni bir anı ekledi"
        send_push_notification(
            {"title": "📸 Yeni anı eklendi", "body": body, "url": f"/trip/{trip_id}", "tag": f"trip-{trip_id}"},
            exclude_username=user['username'],
        )
    except Exception as e:
        print(f"[PUSH GÖNDERİM HATASI] {e}")

    return jsonify({"status": "success", "saved": saved, "errors": errors})


@app.route('/api/trips/<trip_id>/media/<filename>/edit', methods=['POST'])
@login_required
def edit_media(trip_id, filename):
    user = current_user()
    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        return jsonify({"status": "error", "message": "Albüm bulunamadı"}), 404

    entry = find_media(trip, filename)
    if not entry:
        return jsonify({"status": "error", "message": "Dosya bulunamadı"}), 404

    if entry['uploaded_by'] != user['username'] and trip['created_by'] != user['username'] and not user.get('is_admin'):
        return jsonify({"status": "error", "message": "Bu anıyı sadece ekleyen kişi, albümü oluşturan veya admin düzenleyebilir"}), 403

    data = request.get_json() or {}
    caption = (data.get('caption') or '').strip()
    memory_date = (data.get('memory_date') or '').strip()

    if len(caption) > 200:
        return jsonify({"status": "error", "message": "Başlık çok uzun"}), 400

    entry['caption'] = caption
    entry['memory_date'] = memory_date
    save_trips(trips)

    if entry.get('uploaded_by') and entry['uploaded_by'] != user['username']:
        try:
            send_push_notification(
                {"title": "✏️ Anı düzenlendi", "body": f"{user['name']} eklediğin bir anıyı düzenledi", "url": f"/trip/{trip_id}", "tag": f"edit-{trip_id}-{filename}"},
                exclude_username=user['username'],
                only_username=entry.get('uploaded_by'),
            )
        except Exception as e:
            print(f"[PUSH GÖNDERİM HATASI] {e}")

    return jsonify({"status": "success", "caption": caption, "memory_date": memory_date})


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

    try:
        send_push_notification(
            {"title": "🗑️ Bir anı silindi", "body": f"{user['name']} \"{trip['title']}\" albümünden bir anı sildi", "url": f"/trip/{trip_id}", "tag": f"delete-{trip_id}-{filename}"},
            exclude_username=user['username'],
        )
    except Exception as e:
        print(f"[PUSH GÖNDERİM HATASI] {e}")

    return jsonify({"status": "success"})


@app.route('/api/trips/<trip_id>/media/<filename>/set-cover', methods=['POST'])
@login_required
def set_cover(trip_id, filename):
    user = current_user()
    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        return jsonify({"status": "error", "message": "Albüm bulunamadı"}), 404

    if trip['created_by'] != user['username'] and not user.get('is_admin'):
        return jsonify({"status": "error", "message": "Kapak fotoğrafını sadece albümü oluşturan veya admin değiştirebilir"}), 403

    entry = find_media(trip, filename)
    if not entry:
        return jsonify({"status": "error", "message": "Dosya bulunamadı"}), 404

    if entry['type'] != 'image':
        return jsonify({"status": "error", "message": "Sadece fotoğraf kapak olarak seçilebilir"}), 400

    trip['cover'] = filename
    save_trips(trips)

    return jsonify({"status": "success", "cover": filename})


# ---------------------------------------------------------------------------
# Beğeni & Yorum API (fotoğraf/video anılarına)
# ---------------------------------------------------------------------------
@app.route('/api/trips/<trip_id>/media/<filename>/like', methods=['POST'])
@login_required
def toggle_like(trip_id, filename):
    user = current_user()
    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        return jsonify({"status": "error", "message": "Albüm bulunamadı"}), 404

    entry = find_media(trip, filename)
    if not entry:
        return jsonify({"status": "error", "message": "Dosya bulunamadı"}), 404

    likes = entry['likes']
    if user['username'] in likes:
        likes.remove(user['username'])
        liked = False
    else:
        likes.append(user['username'])
        liked = True

    save_trips(trips)

    if liked:
        try:
            send_push_notification(
                {"title": "❤️ Yeni beğeni", "body": f"{user['name']} bir anını beğendi", "url": f"/trip/{trip_id}", "tag": f"like-{trip_id}-{filename}"},
                exclude_username=user['username'],
                only_username=entry.get('uploaded_by'),
            )
        except Exception as e:
            print(f"[PUSH GÖNDERİM HATASI] {e}")

    return jsonify({"status": "success", "liked": liked, "like_count": len(likes)})


@app.route('/api/trips/<trip_id>/media/<filename>/comments', methods=['POST'])
@login_required
def add_comment(trip_id, filename):
    user = current_user()
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()

    if not text:
        return jsonify({"status": "error", "message": "Yorum boş olamaz"}), 400
    if len(text) > 500:
        return jsonify({"status": "error", "message": "Yorum çok uzun"}), 400

    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        return jsonify({"status": "error", "message": "Albüm bulunamadı"}), 404

    entry = find_media(trip, filename)
    if not entry:
        return jsonify({"status": "error", "message": "Dosya bulunamadı"}), 404

    comment = {
        "id": uuid.uuid4().hex[:10],
        "username": user['username'],
        "text": text,
        "created_at": datetime.utcnow().isoformat(),
    }
    entry['comments'].append(comment)
    save_trips(trips)

    try:
        preview = text if len(text) <= 80 else text[:77] + '...'
        send_push_notification(
            {"title": "💬 Yeni yorum", "body": f"{user['name']}: {preview}", "url": f"/trip/{trip_id}", "tag": f"comment-{trip_id}-{filename}"},
            exclude_username=user['username'],
            only_username=entry.get('uploaded_by'),
        )
    except Exception as e:
        print(f"[PUSH GÖNDERİM HATASI] {e}")

    return jsonify({"status": "success", "comment": comment})


@app.route('/api/trips/<trip_id>/media/<filename>/comments/<comment_id>/delete', methods=['POST'])
@login_required
def delete_comment(trip_id, filename, comment_id):
    user = current_user()
    trips = load_trips()
    trip = find_trip(trips, trip_id)
    if not trip:
        return jsonify({"status": "error", "message": "Albüm bulunamadı"}), 404

    entry = find_media(trip, filename)
    if not entry:
        return jsonify({"status": "error", "message": "Dosya bulunamadı"}), 404

    comment = next((c for c in entry['comments'] if c['id'] == comment_id), None)
    if not comment:
        return jsonify({"status": "error", "message": "Yorum bulunamadı"}), 404

    if comment['username'] != user['username'] and trip['created_by'] != user['username']:
        return jsonify({"status": "error", "message": "Bu yorumu sadece yazan kişi veya albümü oluşturan silebilir"}), 403

    entry['comments'] = [c for c in entry['comments'] if c['id'] != comment_id]
    save_trips(trips)

    return jsonify({"status": "success"})


# ---------------------------------------------------------------------------
# Feed / Keşfet — profil sistemi olmadan, herkesin anlık görsel paylaşabildiği akış
# ---------------------------------------------------------------------------
@app.route('/api/feed/upload', methods=['POST'])
@login_required
def feed_upload():
    user = current_user()
    file = request.files.get('file')
    caption = (request.form.get('caption') or '').strip()

    if not file or file.filename == '':
        return jsonify({"status": "error", "message": "Dosya seçilmedi"}), 400
    if not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Geçersiz dosya formatı"}), 400
    if len(caption) > 300:
        return jsonify({"status": "error", "message": "Açıklama çok uzun"}), 400

    post_id = uuid.uuid4().hex[:12]
    ext = file.filename.rsplit('.', 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex[:10]}.{ext}"

    try:
        r2_upload(file.stream, post_id, unique_name, content_type=file.mimetype)
    except ClientError as e:
        print(f"[R2 UPLOAD HATASI] {file.filename}: {e}")
        return jsonify({"status": "error", "message": "Yükleme başarısız oldu"}), 500
    except Exception as e:
        print(f"[BEKLENMEYEN YÜKLEME HATASI] {file.filename}: {e}")
        return jsonify({"status": "error", "message": "Yükleme başarısız oldu"}), 500

    post = {
        "id": post_id,
        "filename": unique_name,
        "type": media_type(unique_name),
        "caption": caption,
        "posted_by": user['username'],
        "created_at": datetime.utcnow().isoformat(),
        "likes": [],
    }

    posts = load_feed()
    posts.append(post)
    save_feed(posts)

    try:
        body = f"{user['name']} bir şey paylaştı" + (f": \"{caption}\"" if caption else "")
        send_push_notification(
            {"title": "✨ Yeni paylaşım", "body": body, "url": "/feed", "tag": f"feed-{post_id}"},
            exclude_username=user['username'],
        )
    except Exception as e:
        print(f"[PUSH GÖNDERİM HATASI] {e}")

    return jsonify({"status": "success", "post": post})


@app.route('/api/feed/<post_id>/like', methods=['POST'])
@login_required
def feed_toggle_like(post_id):
    user = current_user()
    posts = load_feed()
    post = find_feed_post(posts, post_id)
    if not post:
        return jsonify({"status": "error", "message": "Paylaşım bulunamadı"}), 404

    likes = post.setdefault('likes', [])
    if user['username'] in likes:
        likes.remove(user['username'])
        liked = False
    else:
        likes.append(user['username'])
        liked = True

    save_feed(posts)

    if liked and post.get('posted_by'):
        try:
            send_push_notification(
                {"title": "❤️ Yeni beğeni", "body": f"{user['name']} paylaşımını beğendi", "url": "/feed", "tag": f"feed-like-{post_id}"},
                exclude_username=user['username'],
                only_username=post.get('posted_by'),
            )
        except Exception as e:
            print(f"[PUSH GÖNDERİM HATASI] {e}")

    return jsonify({"status": "success", "liked": liked, "like_count": len(likes)})


@app.route('/api/feed/<post_id>/delete', methods=['POST'])
@login_required
def feed_delete(post_id):
    user = current_user()
    posts = load_feed()
    post = find_feed_post(posts, post_id)
    if not post:
        return jsonify({"status": "error", "message": "Paylaşım bulunamadı"}), 404

    if post.get('posted_by') != user['username'] and not user.get('is_admin'):
        return jsonify({"status": "error", "message": "Bu paylaşımı sadece paylaşan kişi veya admin silebilir"}), 403

    posts.remove(post)
    save_feed(posts)

    r2_delete_prefix(post_id)

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

    try:
        send_push_notification(
            {"title": "📅 Yeni etkinlik eklendi", "body": f"{user['name']} \"{title}\" etkinliğini ekledi ({date})", "url": "/", "tag": f"event-new-{event['id']}"},
            exclude_username=user['username'],
        )
    except Exception as e:
        print(f"[PUSH GÖNDERİM HATASI] {e}")

    return jsonify({"status": "success", "event": event})


@app.route('/api/events/<event_id>/delete', methods=['POST'])
@login_required
def delete_event(event_id):
    user = current_user()
    events = load_events()
    event = next((e for e in events if e['id'] == event_id), None)
    if not event:
        return jsonify({"status": "error", "message": "Etkinlik bulunamadı"}), 404

    if event['created_by'] != user['username'] and not user.get('is_admin'):
        return jsonify({"status": "error", "message": "Sadece etkinliği ekleyen kişi silebilir"}), 403

    events.remove(event)
    save_events(events)
    return jsonify({"status": "success"})


# ---------------------------------------------------------------------------
# Push Bildirimi Abonelik API
# ---------------------------------------------------------------------------
@app.route('/api/push/public-key')
@login_required
def push_public_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY if PUSH_CONFIGURED else None})


@app.route('/api/push/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    user = current_user()
    sub = request.get_json(silent=True)
    if not sub or not sub.get('endpoint'):
        return jsonify({"status": "error", "message": "Geçersiz abonelik"}), 400

    subs = load_push_subscriptions()
    user_subs = subs.setdefault(user['username'], [])

    # Aynı endpoint zaten kayıtlıysa tekrar ekleme
    if not any(s.get('endpoint') == sub['endpoint'] for s in user_subs):
        user_subs.append(sub)
        save_push_subscriptions(subs)

    return jsonify({"status": "success"})


@app.route('/api/push/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    user = current_user()
    data = request.get_json(silent=True) or {}
    endpoint = data.get('endpoint')

    subs = load_push_subscriptions()
    user_subs = subs.get(user['username'], [])
    new_subs = [s for s in user_subs if s.get('endpoint') != endpoint]
    if len(new_subs) != len(user_subs):
        subs[user['username']] = new_subs
        save_push_subscriptions(subs)

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
