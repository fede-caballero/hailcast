import logging
# Debe ir ANTES de importar core.* : init_db()/story_manager loguean al importarse
# y el primer logging.info() sin handlers configura el root en WARNING de forma
# implícita, dejando un basicConfig posterior sin efecto (la API no logueaba nada).
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', force=True)

import re
import os
import ipaddress
import json
import sqlite3
import time
from flask import Flask, jsonify, send_from_directory, request
from werkzeug.utils import secure_filename
import requests
import concurrent.futures
from flask_cors import CORS, cross_origin
from flask_limiter import Limiter
from core.config import STATUS_FILE_PATH, IMAGE_OUTPUT_DIR, DB_PATH, FRONTEND_URL, INGEST_SECRET_KEY, DATA_DIR
from datetime import datetime, timedelta, timezone
from core import auth
from core.story_manager import story_state
from core.radar_context import get_radar_contexts, radar_info

# Contextos de radar (rutas por radar). El primero es el primario ('sur').
RADAR_CONTEXTS = {c.radar_id: c for c in get_radar_contexts()}


def _radar_from_request():
    """Contexto del radar pedido con ?radar=<id> (default: el primario). None si no existe."""
    rid = (request.args.get('radar') or 'sur').strip().lower()
    return RADAR_CONTEXTS.get(rid)

app = Flask(__name__)
# Tope global de request. Los MDV pesan ~60 KB y una foto de celular <= 10 MB.
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
REPORTS_UPLOAD_DIR = os.path.join(os.path.dirname(DB_PATH), 'uploads')
os.makedirs(REPORTS_UPLOAD_DIR, exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}


@app.errorhandler(413)
def request_too_large(_e):
    return jsonify({"error": "File too large (max 16 MB)"}), 413


def _save_report_image(file):
    """
    Guarda la imagen de un reporte validando extensión y contenido real.
    Devuelve (image_url, error). Sin validación se podía subir cualquier
    archivo (p.ej. .html) y /api/uploads lo servía desde el dominio de la API.
    """
    from PIL import Image, UnidentifiedImageError

    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return None, "Only jpg, jpeg, png or webp images are allowed"

    try:
        Image.open(file.stream).verify()
    except (UnidentifiedImageError, OSError):
        return None, "File is not a valid image"
    file.stream.seek(0)

    unique_filename = f"{int(datetime.now().timestamp())}_{filename}"
    file.save(os.path.join(REPORTS_UPLOAD_DIR, unique_filename))
    return f"/api/uploads/{unique_filename}", None

# Security: Restrict CORS to frontend domain
# If FRONTEND_URL is "*", allow all. Otherwise, allow specified origins.
origins_list = [
    "http://localhost:3000",
    "http://localhost:8080",
    "https://app-convlstm.vercel.app",
    "https://hail-cast.vercel.app",
    "https://hail-cast-mendoza.com",
    "https://www.hail-cast-mendoza.com",
    "https://vps-api.hail-cast-mendoza.com",
    "https://www.hail-cast-mendoza.com",
    "https://vps-api.hail-cast-mendoza.com",
    FRONTEND_URL
]

# When supports_credentials=True, origins CANNOT be "*"
CORS(app, origins=origins_list, supports_credentials=True)


def _client_ip():
    """
    IP real del cliente para el rate limiter.
    cloudflared corre en el host del VPS y entra por el port mapping de
    Docker, así que dentro del contenedor remote_addr es la IP del bridge
    (172.x) o loopback, nunca la del usuario: la real viene en
    CF-Connecting-IP. Como los puertos están bindeados a 127.0.0.1 del host
    (BIND_ADDRESS), todo lo que llega al contenedor pasó por el host, y es
    seguro confiar en el header cuando el origen es privado/loopback.
    Si alguna vez el puerto se expone a internet, un origen público no
    puede falsificar el header para esquivar el límite.
    """
    remote = request.remote_addr or "unknown"
    try:
        trusted = ipaddress.ip_address(remote).is_private  # incluye loopback y 172.16/12
    except ValueError:
        trusted = False
    if trusted:
        return request.headers.get("CF-Connecting-IP") or remote
    return remote


# Rate limiting solo en endpoints de auth (fuerza bruta / spam de emails).
# Storage en memoria: válido porque gunicorn corre con 1 worker.
limiter = Limiter(key_func=_client_ip, app=app, default_limits=[], storage_uri="memory://")


@app.errorhandler(429)
def rate_limited(e):
    return jsonify({"error": "Too many requests. Please try again later.", "detail": str(e.description)}), 429

# Initialize DB on module load (ensures migrations run in production/gunicorn)
from core.database import init_db
init_db()


# --- Auth Endpoints ---
from core import auth
from services import email_service, notifier, telegram_service, solar_parks, lightning
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# --- Auth Endpoints ---
@app.route('/auth/register', methods=['POST'])
@limiter.limit("5 per minute; 20 per hour")
def register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')
    first_name = data.get('first_name', '')
    last_name = data.get('last_name', '')
    
    # Seguridad: el rol NUNCA viene del cliente. Los admins se promueven
    # con scripts/promote_admin.py.
    role = 'visitor'

    if not username or not password or not email:
        return jsonify({"error": "Username, password and email required"}), 400
        
    # Validation
    if len(password) < 8 or not re.search(r"\d", password) or not re.search(r"[a-zA-Z]", password):
        return jsonify({"error": "Password must be at least 8 characters long and contain both letters and numbers."}), 400

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        hashed_pw = auth.get_password_hash(password)
        cursor.execute("""
            INSERT INTO users (username, password_hash, email, first_name, last_name, role) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (username, hashed_pw, email, first_name, last_name, role))
        conn.commit()
        
        # Send Welcome Email
        email_service.send_welcome_email(email, first_name or username)
        
        return jsonify({"message": "User created successfully"}), 201
    except sqlite3.IntegrityError as e:
        logging.warning(f"Intento de registro duplicado: {e}")
        if "email" in str(e):
            return jsonify({"error": "Email already exists"}), 400
        return jsonify({"error": "Username already exists"}), 400
    except Exception as e:
        logging.error(f"Error CRÍTICO en registro: {e}", exc_info=True)
        return jsonify({"error": f"Internal error: {str(e)}"}), 500
    finally:
        conn.close()
    


@app.route('/auth/google', methods=['POST'])
@limiter.limit("20 per minute")
def google_login():
    data = request.get_json()
    token = data.get('credential')
    client_id = data.get('client_id') # Provided by frontend

    if not token:
        return jsonify({"error": "No token provided"}), 400

    try:
        # Verify token
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), client_id, clock_skew_in_seconds=60)
        
        # Get user info
        google_id = idinfo['sub']
        email = idinfo['email']
        name = idinfo.get('name', '')
        picture = idinfo.get('picture', '')
        given_name = idinfo.get('given_name', '')
        family_name = idinfo.get('family_name', '')
        
        # Check if user exists
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, role, username FROM users WHERE google_id = ? OR email = ?", (google_id, email))
        user_row = cursor.fetchone()
        
        if user_row:
            # Login existing user
            user_id, role, username = user_row
            
            # Link google_id if matched by email but no google_id yet
            cursor.execute("UPDATE users SET google_id = ?, picture = ? WHERE id = ?", (google_id, picture, user_id))
            conn.commit()
            
        else:
            # Register new user
            username = email.split('@')[0] # Default username from email
            # Check if username exists, append if so
            cursor.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            if cursor.fetchone():
                username = f"{username}_{google_id[:4]}"
            
            cursor.execute("""
                INSERT INTO users (username, email, google_id, first_name, last_name, picture, role, password_hash)
                VALUES (?, ?, ?, ?, ?, ?, 'visitor', 'GOOGLE_OAUTH')
            """, (username, email, google_id, given_name, family_name, picture))
            conn.commit()
            
            user_id = cursor.lastrowid
            role = 'visitor'
            
            # Send welcome email for new google users too
            email_service.send_welcome_email(email, given_name or name)

        conn.close()
        
        # Generate our JWT
        access_token = auth.create_access_token(data={"sub": username, "role": role, "id": user_id}, expires_delta=timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES))
        return jsonify({
            "access_token": access_token, 
            "token_type": "bearer", 
            "role": role, 
            "username": username,
            "picture": picture
        })

    except ValueError as e:
        # Invalid token
        logging.error(f"Google Token Verification Error: {e}")
        return jsonify({"error": f"Invalid token: {str(e)}"}), 401
    except Exception as e:
        logging.error(f"Google Login Error: {e}")
        return jsonify({"error": f"Internal error: {str(e)}"}), 500

# --- User Location Endpoint ---
@app.route('/api/user/location', methods=['POST'])
def update_user_location():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401
    
    token = auth_header.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401
        
    user_id = payload.get('id')
    data = request.get_json()
    lat = data.get('latitude')
    lon = data.get('longitude')
    
    if lat is None or lon is None:
        return jsonify({"error": "Latitude and longitude required"}), 400
        
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute("""
            UPDATE users 
            SET latitude = ?, longitude = ?, last_location_update = ?
            WHERE id = ?
        """, (lat, lon, now, user_id))
        
        conn.commit()
        return jsonify({"message": "Location updated"}), 200
    except Exception as e:
        logging.error(f"Error updating user location: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/auth/login', methods=['POST'])
@limiter.limit("10 per minute; 60 per hour")
def login():
    data = request.get_json()
    username_or_email = data.get('username')
    password = data.get('password')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash, role, id, username FROM users WHERE username = ? OR email = ?", (username_or_email, username_or_email))
    user = cursor.fetchone()
    conn.close()

    if not user or not auth.verify_password(password, user[0]):
        return jsonify({"error": "Invalid credentials"}), 401

    real_username = user[3]
    real_username = user[3]
    access_token = auth.create_access_token(data={"sub": real_username, "role": user[1], "id": user[2]}, expires_delta=timedelta(minutes=auth.ACCESS_TOKEN_EXPIRE_MINUTES))
    return jsonify({"access_token": access_token, "token_type": "bearer", "role": user[1], "username": real_username})

@app.route('/auth/me', methods=['GET'])
def me():
    token = request.headers.get('Authorization')
    if not token or not token.startswith("Bearer "):
        return jsonify({"error": "Missing token"}), 401
    
    token = token.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401
        
    return jsonify(payload)


import uuid

@app.route('/auth/forgot-password', methods=['POST'])
@limiter.limit("3 per minute; 10 per hour")
def forgot_password():
    data = request.get_json()
    email = data.get('email')
    if not email:
         return jsonify({"error": "Email required"}), 400
         
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    
    if user:
        # Generate token and expiry
        reset_token = str(uuid.uuid4())
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        
        cursor.execute("UPDATE users SET reset_token = ?, reset_token_expiry = ? WHERE email = ?", (reset_token, expiry, email))
        conn.commit()
        
        # Build recovery link
        reset_link = f"{FRONTEND_URL}/reset-password?token={reset_token}"
        email_service.send_password_reset_email(email, reset_link)
        
    conn.close()
    
    # Always return success to prevent email sweeping (security best practice)
    return jsonify({"message": "If the email is registered, a recovery link has been sent."}), 200

@app.route('/auth/reset-password', methods=['POST'])
@limiter.limit("5 per minute")
def reset_password():
    data = request.get_json()
    token = data.get('token')
    new_password = data.get('new_password')
    
    if not token or not new_password:
        return jsonify({"error": "Token and new password required"}), 400
        
    # Validation
    if len(new_password) < 8 or not re.search(r"\d", new_password) or not re.search(r"[a-zA-Z]", new_password):
        return jsonify({"error": "Password must be at least 8 characters long and contain both letters and numbers."}), 400
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, reset_token_expiry FROM users WHERE reset_token = ?", (token,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        return jsonify({"error": "Invalid token"}), 400
        
    user_id, expiry_str = user
    expiry_date = datetime.fromisoformat(expiry_str)
    
    if datetime.now(timezone.utc) > expiry_date:
        conn.close()
        return jsonify({"error": "Token expired"}), 400
        
    # Valid token, update password
    hashed_pw = auth.get_password_hash(new_password)
    cursor.execute("UPDATE users SET password_hash = ?, reset_token = NULL, reset_token_expiry = NULL WHERE id = ?", (hashed_pw, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({"message": "Password updated successfully"}), 200



# Endpoint para servir las imágenes generadas
@app.route('/images/<path:filename>')
def serve_image(filename):
    return send_from_directory(IMAGE_OUTPUT_DIR, filename)

@app.route('/api/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(REPORTS_UPLOAD_DIR, filename)

# --- Push Notifications Endpoints ---
from core.config import VAPID_PRIVATE_KEY, VAPID_CLAIM_EMAIL
from pywebpush import webpush, WebPushException
# We need Vapid to derive the public key. Try importing from py_vapid or pywebpush.
try:
    from py_vapid import Vapid
except ImportError:
    # Fallback or error
    logging.error("Could not import Vapid from py_vapid")
    Vapid = None

@app.route('/api/notifications/vapid-public-key', methods=['GET'])
def get_vapid_public_key():
    try:
        if Vapid is None:
             return jsonify({"error": "Server missing 'py-vapid' library. Please rebuild backend image."}), 500
        
        if not VAPID_PRIVATE_KEY:
             return jsonify({"error": "VAPID_PRIVATE_KEY not configured on server."}), 500

        # Load Vapid from PEM string
        # Vapid.from_pem expects bytes
        vapid = Vapid.from_pem(VAPID_PRIVATE_KEY.encode('utf-8'))
        
        # Get the Application Server Key (Public Key)
        # py-vapid provides this as a property, usually returning bytes.
        # We need to ensure it's base64url encoded for the frontend.
        
        # Check if we can get the raw public key bytes or the pre-encoded key
        if hasattr(vapid, "application_server_key"):
             # This property often returns the raw bytes (uncompressed point)
             raw_pub = vapid.application_server_key
        else:
             # Fallback: Serialize manually if needed (should not happen with standard py-vapid)
             # But wait, debug showed public_key is an object.
             # let's try to get the raw bytes from the object if needed.
             pass

        # Actually, the most reliable way with py-vapid is:
        # It calculates the public key on initialization.
        # Inspecting py-vapid source:
        # The public key is an EC point.
        # We need the UNCOMPRESSED format (0x04 + x + y).
        
        from cryptography.hazmat.primitives import serialization
        
        public_key_bytes = vapid.public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        )
        
        import base64
        # Encode to URL-Safe Base64
        vapid_public_key = base64.urlsafe_b64encode(public_key_bytes).decode('utf-8').rstrip('=')
        
        return jsonify({"publicKey": vapid_public_key}), 200
    except Exception as e:
        logging.error(f"Error deriving public key: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/subscribe', methods=['POST'])
def subscribe_push():
    data = request.get_json()
    subscription = data.get('subscription') # Expecting standard PushSubscription object
    lat = data.get('latitude')
    lon = data.get('longitude')
    
    # Optional preferences
    alert_admin = 1 if data.get('alert_admin', True) else 0
    alert_proximity = 1 if data.get('alert_proximity', True) else 0
    alert_aircraft = 1 if data.get('alert_aircraft', False) else 0
    alert_forecast = 1 if data.get('alert_forecast', True) else 0
    
    if not subscription:
        return jsonify({"error": "No subscription data"}), 400

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if user is logged in (optional, but good for linking)
    user_id = None
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith("Bearer "):
        try:
            token = auth_header.split(" ")[1]
            payload = auth.decode_access_token(token)
            if payload:
                user_id = payload.get('id')
        except:
            pass

    try:
        endpoint = subscription.get('endpoint')
        keys = subscription.get('keys', {})
        p256dh = keys.get('p256dh')
        auth_key = keys.get('auth')

        if not endpoint or not p256dh or not auth_key:
             return jsonify({"error": "Invalid subscription object"}), 400

        # Upsert or Insert (SQLite UPSERT syntax or basic check)
        # Using INSERT OR REPLACE to update keys if endpoint exists
        cursor.execute("""
            INSERT OR REPLACE INTO push_subscriptions 
            (user_id, endpoint, p256dh, auth, latitude, longitude, alert_admin, alert_proximity, alert_aircraft, alert_forecast)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, endpoint, p256dh, auth_key, lat, lon, alert_admin, alert_proximity, alert_aircraft, alert_forecast))
        
        conn.commit()
        return jsonify({"message": "Subscribed successfully"}), 201
    except Exception as e:
        logging.error(f"Error subscribing to push: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/notifications/subscribe', methods=['DELETE'])
def unsubscribe_push():
    data = request.get_json()
    endpoint = data.get('endpoint')

    if not endpoint:
        return jsonify({"error": "Missing endpoint"}), 400

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        conn.commit()
        
        if cursor.rowcount == 0:
             return jsonify({"message": "Subscription not found"}), 404
             
        return jsonify({"message": "Unsubscribed successfully"}), 200
    except Exception as e:
        logging.error(f"Error unsubscribing: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

def _send_push_to_all(title, message, url):
    """Aviso de administrador a todos: historial + push + Telegram (ver services/notifier.py)."""
    logging.info(f"Initiating Push to All: {title} - {message}")
    return notifier.broadcast("admin", title, message, url)

@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    try:
        limit_str = request.args.get('limit', '5')
        try:
            limit = int(limit_str)
        except ValueError:
            limit = 5
            
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        
        notifications = [dict(r) for r in rows]
        return jsonify({"notifications": notifications}), 200
    except Exception as e:
        logging.error(f"Error fetching notifications: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/notifications/send', methods=['POST'])
def send_push_notification():
    # Admin only check
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401
    
    token = auth_header.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload or payload.get('role') not in ['admin', 'superadmin']:
        return jsonify({"error": "Admins only"}), 403

    data = request.get_json()
    message = data.get('message', 'Alerta Meteorológica')
    title = data.get('title', 'Alerta')
    url = data.get('url', '/')

    results = _send_push_to_all(title, message, url)
    return jsonify({"results": results}), 200

def _auth_payload():
    """Payload del JWT del header Authorization, o None si falta o es inválido."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    try:
        return auth.decode_access_token(auth_header.split(" ")[1])
    except Exception:
        return None


# --- Telegram: vinculación de cuenta (ver services/telegram_service.py) ---

@app.route('/api/telegram/status', methods=['GET'])
def telegram_status():
    payload = _auth_payload()
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(telegram_service.link_status(DB_PATH, payload.get('id'))), 200


@app.route('/api/telegram/link', methods=['POST'])
@limiter.limit("5 per minute")
def telegram_create_link():
    payload = _auth_payload()
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    if not telegram_service.is_configured():
        return jsonify({"error": "Telegram no está configurado en el servidor"}), 503
    result = telegram_service.create_link_token(DB_PATH, payload.get('id'))
    if not result:
        return jsonify({"error": "No se pudo obtener el bot de Telegram"}), 503
    _token, url, expires_at = result
    return jsonify({"url": url, "expires_at": expires_at}), 200


@app.route('/api/telegram/preferences', methods=['PUT'])
def telegram_preferences():
    payload = _auth_payload()
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    telegram_service.set_enabled(DB_PATH, payload.get('id'), bool(data.get('enabled', True)))
    return jsonify(telegram_service.link_status(DB_PATH, payload.get('id'))), 200


@app.route('/api/telegram/link', methods=['DELETE'])
def telegram_unlink():
    payload = _auth_payload()
    if not payload:
        return jsonify({"error": "Unauthorized"}), 401
    telegram_service.unlink(DB_PATH, payload.get('id'))
    return jsonify(telegram_service.link_status(DB_PATH, payload.get('id'))), 200


# --- Rayos (GLM GOES-19): últimos N minutos como GeoJSON ---

@app.route('/api/lightning', methods=['GET'])
def get_lightning():
    try:
        minutes = max(1, min(int(request.args.get('minutes', 30)), lightning.KEEP_MINUTES))
    except ValueError:
        minutes = 30
    state = lightning.load_recent_from_file(minutes)
    now = time.time()
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]},
        "properties": {"age_s": int(now - f["t"]), "age_min": round((now - f["t"]) / 60.0, 1),
                       "time": datetime.fromtimestamp(f["t"], timezone.utc).isoformat(), "energy": f.get("e")},
    } for f in state.get("flashes", [])]
    return jsonify({
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "minutes": minutes,
        "updated": state.get("updated"),
        "last_file_end": state.get("last_file_end"),
        "stale": state.get("stale", True),
        "source": "GOES-19 GLM (NOAA)",
    }), 200


# --- Parques solares: estado de las alertas privadas (solo superadmin) ---

@app.route('/api/solar-parks', methods=['GET'])
def get_solar_parks():
    payload = _auth_payload()
    if not payload or payload.get('role') != 'superadmin':
        return jsonify({"error": "Forbidden"}), 403
    return jsonify(solar_parks.get_status()), 200


# Endpoints de la API
stations_cache = {"data": None, "updated_at": 0}

@app.route("/api/stations", methods=['GET'])
def get_all_stations():
    now = time.time()
    # Cache for 5 minutes (300 seconds)
    if stations_cache["data"] and now - stations_cache["updated_at"] < 300:
        return jsonify(stations_cache["data"]), 200
        
    def fetch_station(sid):
        try:
            url = f"https://agrometeo.mendoza.gov.ar/api/getInstantaneas.php?estacion={sid}"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) > 0 and 'error' not in data[0]:
                    return data[0]
        except Exception as e:
            logging.debug(f"Error fetching station {sid}: {e}")
        return None

    features = []
    # Fetch from IDs 1 to 65 concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(fetch_station, range(1, 65))
        for res in results:
            if res and 'lat' in res and 'lng' in res:
                try:
                    lat = float(res['lat'])
                    lng = float(res['lng'])
                    features.append({
                        "type": "Feature",
                        "properties": res,
                        "geometry": {
                            "type": "Point",
                            "coordinates": [lng, lat]
                        }
                    })
                except (ValueError, TypeError):
                    pass
                
    cache_data = {
        "type": "FeatureCollection",
        "features": features
    }
    
    stations_cache["data"] = cache_data
    stations_cache["updated_at"] = now
    
    return jsonify(cache_data), 200
@app.route("/api/hail-swath/today", methods=['GET'])
def get_hail_swath_today():
    # El día pluviométrico/granicero empieza a las 8 AM local (11 AM UTC)
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour < 11:
        from datetime import timedelta # just in case
        business_day = now_utc - timedelta(days=1)
    else:
        business_day = now_utc
        
    today_str = business_day.strftime("%Y%m%d")
    ctx = _radar_from_request()
    if ctx is None:
        return jsonify({"error": "Unknown radar"}), 404
    swath_file = ctx.hail_swath_file(today_str)   # el sur conserva hail_swath_<día>.json
    
    if not os.path.exists(swath_file):
        return jsonify({"type": "FeatureCollection", "features": []}), 200
        
    try:
        with open(swath_file, 'r') as f:
            data = json.load(f)
            points = data.get("points", [])
            
            # Devolver formato GeoJSON
            geojson = {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"date": today_str},
                    "geometry": {
                        "type": "MultiPoint",
                        "coordinates": points
                    }
                }]
            }
            return jsonify(geojson), 200
    except Exception as e:
        logging.error(f"Error reading hail swath: {e}")
        return jsonify({"error": str(e)}), 500

def _read_status_file(path):
    """Estado del worker para un radar: (dict, http_code)."""
    if not os.path.exists(path):
        return {"status": "INITIALIZING", "message": "Status file not found. The worker might be starting."}, 404
    try:
        with open(path, 'r') as f:
            return json.load(f), 200
    except json.JSONDecodeError as e:
        logging.warning(f"Status file is temporarily empty or corrupted: {e}")
        return {"status": "UPDATING", "message": "Status file is currently being updated or is empty."}, 200


@app.route("/api/radars")
def get_radars():
    """Radares activos, con su estado actual. El frontend arma el selector con esto."""
    radars = []
    for ctx in RADAR_CONTEXTS.values():
        info = radar_info(ctx)
        status, _ = _read_status_file(ctx.status_file)
        info["status"] = status.get("status")
        info["last_update"] = status.get("last_update")
        radars.append(info)
    return jsonify({"radars": radars}), 200


@app.route("/api/status")
def get_status():
    logging.debug("Request recibido en /api/status")
    try:
        ctx = _radar_from_request()
        if ctx is None:
            return jsonify({"status": "ERROR", "message": "Unknown radar"}), 404
        status_data, code = _read_status_file(ctx.status_file)
        status_data["radar"] = ctx.radar_id
        return jsonify(status_data), code
    except json.JSONDecodeError as e:
        logging.warning(f"Status file is temporarily empty or corrupted: {e}")
        return jsonify({
            "status": "UPDATING",
            "message": "Status file is currently being updated or is empty."
        }), 200
    except Exception as e:
        logging.error(f"Error al leer el archivo de estado: {e}")
        return jsonify({
            "status": "ERROR",
            "message": f"Failed to read status file: {str(e)}"
        }), 500

@app.route("/api/images")
def get_images():
    logging.debug("Request recibido en /api/images")
    try:
        ctx = _radar_from_request()
        if ctx is None:
            return jsonify({"input_images": [], "prediction_images": [], "error": "Unknown radar"}), 404
        image_dir = ctx.image_dir
        if not os.path.exists(image_dir):
            return jsonify({"input_images": [], "prediction_images": [], "message": "Image output directory not found."}), 404

        # Only list base images. Smoothed images are requested dynamically by the frontend.
        all_files = [f for f in os.listdir(image_dir) if f.endswith('.png') and not f.endswith('_smoothed.png')]
        
        # --- Story Mode Filter (Role-aware) ---
        # When story mode is active:
        #   - Superadmin: sees only story images
        #   - Other users: sees live images (story date excluded)
        story_status = story_state.get_status()
        story_date_filter = None
        is_superadmin = False
        
        # Check if requester is superadmin (optional auth — endpoint remains public)
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith("Bearer "):
            try:
                token = auth_header.split(" ")[1]
                payload = auth.decode_access_token(token)
                if payload and payload.get('role') == 'superadmin':
                    is_superadmin = True
            except Exception:
                pass
        
        # El modo historia es del radar primario: para los demás no hay nada que filtrar
        known_story_ids = story_state.get_known_story_ids() if ctx.is_primary else []
        active_story_id = story_status.get("story_id") if (story_status.get("is_active") and ctx.is_primary) else None
        inactive_story_id = story_status.get("story_id") if not story_status.get("is_active") else None
        story_review_mode = False

        if is_superadmin and active_story_id:
            # Superadmin with active story: show only the active story's PNGs
            all_files = [f for f in all_files if active_story_id in f]
            logging.info(f"Story mode filter (superadmin): showing {len(all_files)} files matching {active_story_id}")
        elif is_superadmin and inactive_story_id and inactive_story_id in (known_story_ids or []):
            # Superadmin reviewing a recently-stopped story: keep story PNGs visible
            all_files = [f for f in all_files if inactive_story_id in f]
            story_review_mode = True
            logging.info(f"Story review filter (superadmin): showing {len(all_files)} files matching {inactive_story_id}")
        elif known_story_ids:
            # Normal users (and superadmin with no recent story): hide all story files
            # so old story frames don't leak into the live feed.
            before = len(all_files)
            all_files = [f for f in all_files if not any(sid in f for sid in known_story_ids)]
            role = "superadmin" if is_superadmin else "user"
            logging.info(f"Story mode filter ({role}): excluded {before - len(all_files)} story files, {len(all_files)} live remaining")

        # --- INPUT IMAGES (Last 3) ---
        input_images_names = sorted([f for f in all_files if f.startswith('INPUT_')], reverse=True)[:3]
        input_images_names.sort() # Chronological order

        # --- PREDICTION IMAGES (Last 2 runs, 5th frame only) ---
        # Filename format: PRED_<RUN_ID>_<FORECAST_TIME>.png
        # Example: PRED_20231027-160000_20231027_161500.png
        
        pred_files = [f for f in all_files if f.startswith('PRED_')]
        runs = {}

        for f in pred_files:
            try:
                # Remove prefix and extension
                parts = f.replace('PRED_', '').replace('.png', '').split('_')
                # parts[0] is RUN_ID (e.g. 20231027-160000)
                # parts[1] is FORECAST_DATE (e.g. 20231027)
                # parts[2] is FORECAST_TIME (e.g. 161500)
                
                # Handle potential legacy filenames (without RUN_ID) gracefully
                if len(parts) >= 3:
                    run_id = parts[0]
                    forecast_ts = f"{parts[1]}_{parts[2]}"
                else:
                    # Legacy file, ignore or treat as separate run
                    continue

                if run_id not in runs:
                    runs[run_id] = []
                runs[run_id].append(f)
            except Exception as e:
                logging.warning(f"Error parsing filename {f}: {e}")

        # Sort runs by ID (timestamp)
        sorted_run_ids = sorted(runs.keys())

        selected_predictions = []
        if sorted_run_ids:
            if story_review_mode:
                # Review mode: show the FIRST prediction run (window 1, t+0 = frame 8)
                # so superadmin can evaluate predictions from the initial input window.
                target_run_id = sorted_run_ids[0]
            else:
                # Live/normal mode: show the LATEST run (most recent forecast)
                target_run_id = sorted_run_ids[-1]
            selected_predictions = sorted(runs[target_run_id])

        base_url = "/images/" + ctx.image_url_prefix   # "" para el sur, "norte/" para los demás
        
        def create_image_data(filenames, is_prediction=False):
            image_data_list = []
            for filename in filenames:
                json_path = os.path.join(image_dir, f"{filename}.json")
                bounds = None
                target_time = None
                
                if os.path.exists(json_path):
                    try:
                        with open(json_path, 'r') as f:
                            data = json.load(f)
                            bounds = data.get('bounds')
                            cells = data.get('cells')
                    except Exception as e:
                        logging.warning(f"No se pudo leer o parsear el JSON '{json_path}': {e}")
                
                # Calculate Target Time (UTC-3)
                try:
                    ts_str = None
                    if is_prediction:
                        # PRED_<RUN_ID>_<FORECAST_TIME>.png
                        parts = filename.replace('PRED_', '').replace('.png', '').split('_')
                        if len(parts) >= 3:
                            ts_str = f"{parts[1]}{parts[2]}" # YYYYMMDDHHMMSS
                    else:
                        # INPUT_<TIMESTAMP>.png
                        # Example: INPUT_20080305010845.png
                        parts = filename.replace('INPUT_', '').replace('.png', '').split('_')
                        if len(parts) >= 1:
                            ts_str = parts[0] # 20080305010845

                    if ts_str and len(ts_str) >= 12:
                        # Parse UTC timestamp
                        dt_utc = datetime.strptime(ts_str[:14], "%Y%m%d%H%M%S")
                        # Adjust to UTC-3
                        dt_local = dt_utc - timedelta(hours=3)
                        # Format as DD/MM HH:MM for context
                        target_time = dt_local.strftime("%d/%m %H:%M")
                        
                except Exception as e:
                    logging.warning(f"Error parsing time for {filename}: {e}")

                if bounds:
                    item = {
                        "url": base_url + filename,
                        "bounds": bounds,
                        "cells": cells
                    }
                    if target_time:
                        item["target_time"] = target_time
                        if is_prediction:
                            # For predictions, we can use the forecast time as ISO
                            # We already calculated dt_local. Let's use dt_utc for ISO standard
                            item["timestamp_iso"] = dt_utc.isoformat()
                        else:
                             # For inputs
                             if ts_str and len(ts_str) >= 14:
                                 try:
                                     dt_utc_input = datetime.strptime(ts_str[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                                     item["timestamp_iso"] = dt_utc_input.isoformat()
                                 except:
                                     pass
                    image_data_list.append(item)
            return image_data_list

        return jsonify({
            "input_images": create_image_data(input_images_names, is_prediction=False),
            "prediction_images": create_image_data(selected_predictions, is_prediction=True)
        })

    except Exception as e:
        logging.error(f"Error al listar las imágenes: {e}")
        return jsonify({"error": "An internal error occurred."}), 500

@app.route("/api/upload_mdv", methods=['POST'])
def upload_mdv():
    """
    Endpoint para subir archivos MDV.
    Uso: curl -X POST -H "X-Ingest-Key: $INGEST_SECRET_KEY" -F "file=@/ruta/al/archivo.mdv" http://host:port/api/upload_mdv

    Protegido con la misma clave compartida que /api/aircraft/ingest: lo que
    entra acá pasa por LROSE y por el modelo, y alimenta las alertas.
    """
    if request.headers.get("X-Ingest-Key", "") != INGEST_SECRET_KEY:
        logging.warning("[upload_mdv] Intento no autorizado.")
        return jsonify({"error": "Unauthorized"}), 401

    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    if file and file.filename.endswith('.mdv'):
        try:
            # Asegurar que el directorio existe
            from core.config import MDV_INBOX_DIR, MDV_ARCHIVE_DIR
            os.makedirs(MDV_INBOX_DIR, exist_ok=True)
            
            filename = os.path.basename(file.filename)
            if not re.fullmatch(r"[\w.-]{1,80}\.mdv", filename):
                return jsonify({"error": "Invalid filename"}), 400
            radar = (request.form.get("radar") or "sur").strip().lower()
            if not re.fullmatch(r"[a-z0-9_-]{1,20}", radar):
                return jsonify({"error": "Invalid radar"}), 400

            # Cada radar tiene su bandeja: el radar sur (San Rafael) usa la del pipeline actual;
            # cualquier otro (p. ej. 'norte', San Martín) va a /app/data/mdv_inbox_<radar>, que
            # el worker todavía no procesa (fase 1 del radar norte: solo acumular).
            if radar == "sur":
                inbox_dir, archive_dir = MDV_INBOX_DIR, MDV_ARCHIVE_DIR
            else:
                inbox_dir = os.path.join(os.path.dirname(MDV_INBOX_DIR.rstrip("/")), f"mdv_inbox_{radar}")
                archive_dir = os.path.join(os.path.dirname(MDV_ARCHIVE_DIR.rstrip("/")), f"mdv_archive_{radar}")
                os.makedirs(inbox_dir, exist_ok=True)

            # Idempotente: si ya llegó (en bandeja o ya procesado), no se vuelve a encolar.
            # Así el bridge de la PC del radar y el Drive Watcher pueden convivir.
            for existing_dir in (inbox_dir, archive_dir):
                if os.path.exists(os.path.join(existing_dir, filename)):
                    logging.info(f"[upload_mdv] {filename} ({radar}) ya estaba recibido; se ignora.")
                    return jsonify({"message": f"File {filename} already received"}), 200

            save_path = os.path.join(inbox_dir, filename)
            file.save(save_path)
            
            logging.info(f"Archivo MDV recibido ({radar}) y guardado: {save_path}")
            return jsonify({"message": f"File {filename} uploaded successfully", "path": save_path}), 201
        except Exception as e:
            logging.error(f"Error al guardar el archivo MDV: {e}")
            return jsonify({"error": "Failed to save file"}), 500
    else:
        return jsonify({"error": "Invalid file type. Only .mdv allowed"}), 400

# --- Comments Endpoints ---

@app.route('/api/comments', methods=['GET'])
def get_comments():
    limit = request.args.get('limit', default=5, type=int)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # Return dict-like rows
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT comments.id, content, created_at, username as author
            FROM comments 
            JOIN users ON comments.author_id = users.id 
            WHERE is_active = 1 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        comments = [dict(row) for row in rows]
        return jsonify(comments), 200
    except Exception as e:
        logging.error(f"Error fetching comments: {e}")
        return jsonify({"error": "Internal error"}), 500
    finally:
        conn.close()

@app.route('/api/comments', methods=['POST'])
def create_comment():
    # 1. Verificar Auth & Rol Admin
    token = request.headers.get('Authorization')
    if not token or not token.startswith("Bearer "):
        return jsonify({"error": "Missing token"}), 401
    
    token = token.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload or payload.get('role') not in ['admin', 'superadmin']:
        return jsonify({"error": "Unauthorized: Admins only"}), 403

    # 2. Obtener datos
    data = request.get_json()
    content = data.get('content')
    if not content:
        return jsonify({"error": "Content required"}), 400

    author_id = payload.get('id')
    # Use timezone-aware UTC timestamp
    created_at = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # NO des-activamos comentarios anteriores. Permitimos historial.
        
        # 4. Insertar nuevo
        cursor.execute('''
            INSERT INTO comments (content, author_id, created_at, is_active)
            VALUES (?, ?, ?, 1)
        ''', (content, author_id, created_at))
        
        conn.commit()
        
        # --- AUTO-PUSH NOTIFICATION ---
        try:
            # Launch in background thread ideally, but for now blocking is fine (it's fast-ish)
            logging.info("Auto-triggering push for new comment...")
            _send_push_to_all(title="Nueva alerta", message=content, url="/")
        except Exception as e:
            logging.error(f"Failed to auto-send push: {e}")

        return jsonify({"message": "Comment posted successfully"}), 201
    except Exception as e:
        logging.error(f"Error posting comment: {e}")
        return jsonify({"error": "Internal error"}), 500
    finally:
        conn.close()

@app.route('/api/comments/<int:comment_id>', methods=['PUT', 'DELETE'])
def manage_comment(comment_id):
    # 1. Verificar Auth & Rol Admin
    token = request.headers.get('Authorization')
    if not token or not token.startswith("Bearer "):
        return jsonify({"error": "Missing token"}), 401
    
    token = token.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload or payload.get('role') not in ['admin', 'superadmin']:
        return jsonify({"error": "Unauthorized: Admins only"}), 403

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        if request.method == 'DELETE':
            # Soft Delete
            cursor.execute("UPDATE comments SET is_active = 0 WHERE id = ?", (comment_id,))
            if cursor.rowcount == 0:
                return jsonify({"error": "Comment not found"}), 404
            conn.commit()
            return jsonify({"message": "Comment deleted"}), 200

    finally:
        conn.close()

# --- Reports Endpoints (Crowdsourcing) ---

@app.route('/api/reports', methods=['GET'])
def get_reports():
    # Helper to check for optional token
    current_user_id = None
    token = request.headers.get('Authorization')
    if token and token.startswith("Bearer "):
        try:
            payload = auth.decode_access_token(token.split(" ")[1])
            if payload:
                current_user_id = payload.get('id')
        except:
            pass

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc - timedelta(hours=3)
    
    if now_local.hour < 8:
        start_local = now_local.replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=1)
    else:
        start_local = now_local.replace(hour=8, minute=0, second=0, microsecond=0)
        
    start_utc = start_local + timedelta(hours=3)
    cutoff_time = start_utc.isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        query = '''
            SELECT r.id, r.user_id, r.report_type, r.latitude, r.longitude, r.timestamp, r.description, r.image_url, u.username,
                   (SELECT COUNT(*) FROM report_likes l WHERE l.report_id = r.id) as likes_count
        '''
        if current_user_id:
            query += f', CASE WHEN EXISTS (SELECT 1 FROM report_likes l WHERE l.report_id = r.id AND l.user_id = {current_user_id}) THEN 1 ELSE 0 END as user_liked'
        else:
            query += ', 0 as user_liked'

        query += '''
            FROM weather_reports r
            LEFT JOIN users u ON r.user_id = u.id
            WHERE r.timestamp >= ?
            ORDER BY r.timestamp DESC
        '''
        
        cursor.execute(query, (cutoff_time,))
        
        rows = cursor.fetchall()
        reports = [dict(row) for row in rows]
        # Convert user_liked to boolean explicitly
        for r in reports:
            r['user_liked'] = bool(r.get('user_liked', 0))
            
        return jsonify(reports), 200
    except Exception as e:
        logging.error(f"Error fetching reports: {e}")
        return jsonify({"error": "Internal error"}), 500
    finally:
        conn.close()

@app.route('/api/reports/<int:report_id>/like', methods=['POST'])
def toggle_report_like(report_id):
    token = request.headers.get('Authorization')
    if not token or not token.startswith("Bearer "):
        return jsonify({"error": "Missing token"}), 401
    
    token = token.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401

    user_id = payload.get('id')
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        # Check if report exists
        cursor.execute("SELECT id FROM weather_reports WHERE id = ?", (report_id,))
        if not cursor.fetchone():
            return jsonify({"error": "Report not found"}), 404
            
        # Toggle like
        cursor.execute("SELECT id FROM report_likes WHERE report_id = ? AND user_id = ?", (report_id, user_id))
        existing_like = cursor.fetchone()
        
        if existing_like:
            # Unlike
            cursor.execute("DELETE FROM report_likes WHERE id = ?", (existing_like[0],))
            action = "unliked"
        else:
            # Like
            cursor.execute("INSERT INTO report_likes (report_id, user_id) VALUES (?, ?)", (report_id, user_id))
            action = "liked"
            
        # Get new count
        cursor.execute("SELECT COUNT(*) FROM report_likes WHERE report_id = ?", (report_id,))
        new_count = cursor.fetchone()[0]
            
        conn.commit()
        return jsonify({"message": f"Successfully {action}", "likes_count": new_count, "user_liked": action == "liked"}), 200
        
    except Exception as e:
        logging.error(f"Error toggling report like: {e}")
        return jsonify({"error": "Internal error"}), 500
    finally:
        conn.close()

# --- Aircraft Telemetry Endpoint ---
from services import aircraft_tracker

@app.route('/api/aircraft', methods=['GET'])
def get_aircraft():
    try:
        data = aircraft_tracker.get_aircraft_data()
        return jsonify(data)
    except Exception as e:
        logging.error(f"Error fetching aircraft: {e}")
        return jsonify({"error": "Failed to fetch aircraft data"}), 500

# --- TITAN Local Telemetry Ingest Endpoint ---

@app.route('/api/aircraft/ingest', methods=['POST'])
def ingest_aircraft():
    """
    Receives real-time position from the local telemetry_streamer.py 
    running on the work PC. Secured via shared secret key in the header.
    """
    # 1. Verify shared secret
    provided_key = request.headers.get("X-Ingest-Key", "")
    if provided_key != INGEST_SECRET_KEY:
        logging.warning("[Ingest] Unauthorized attempt.")
        return jsonify({"error": "Unauthorized"}), 401

    # 2. Parse body
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    required_fields = ["callsign", "lat", "lon"]
    if not all(f in data for f in required_fields):
        return jsonify({"error": f"Missing required fields: {required_fields}"}), 400

    # 3. Forward to in-memory tracker
    try:
        aircraft_tracker.update_local_aircraft(data)
        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logging.error(f"[Ingest] Error updating local aircraft: {e}")
        return jsonify({"error": "Internal error"}), 500

@app.route('/api/reports', methods=['POST'])
def create_report():
    # 1. Verify Auth
    token = request.headers.get('Authorization')
    if not token or not token.startswith("Bearer "):
        return jsonify({"error": "Missing token"}), 401
    
    token = token.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401

    user_id = payload.get('id')
    
    # 2. Get Data
    # 2. Get Data (supports multipart/form-data)
    if 'report_type' not in request.form and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json or multipart/form-data"}), 400

    report_type = request.form.get('report_type') or (request.json.get('report_type') if request.is_json else None)
    try:
        latitude = float(request.form.get('latitude') or (request.json.get('latitude') if request.is_json else 0))
        longitude = float(request.form.get('longitude') or (request.json.get('longitude') if request.is_json else 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Latitude/Longitude must be numbers"}), 400

    description = request.form.get('description', '') if not request.is_json else request.json.get('description', '')

    image_url = None
    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename != '':
            image_url, err = _save_report_image(file)
            if err:
                return jsonify({"error": err}), 400

    if not all([report_type, latitude, longitude]):
        return jsonify({"error": "Missing required fields (type, lat, lon)"}), 400

    # 3. Save
    timestamp = datetime.now(timezone.utc).isoformat()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO weather_reports (user_id, report_type, latitude, longitude, timestamp, description, image_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, report_type, latitude, longitude, timestamp, description, image_url))
        conn.commit()
        return jsonify({"message": "Report submitted successfully", "image_url": image_url}), 201
    except Exception as e:
        logging.error(f"Error submitting report: {e}")
        return jsonify({"error": "Internal error"}), 500
    finally:
        conn.close()

@app.route('/api/reports/<int:report_id>', methods=['DELETE'])
def delete_report(report_id):
    # 1. Verify Auth & Admin Role
    token = request.headers.get('Authorization')
    if not token or not token.startswith("Bearer "):
        return jsonify({"error": "Missing token"}), 401
    
    token = token.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload or payload.get('role') not in ['admin', 'superadmin']:
        return jsonify({"error": "Unauthorized: Admins only"}), 403

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        # Check if report exists
        cursor.execute("SELECT id FROM weather_reports WHERE id = ?", (report_id,))
        if not cursor.fetchone():
            return jsonify({"error": "Report not found"}), 404

        # Delete
        cursor.execute("DELETE FROM weather_reports WHERE id = ?", (report_id,))
        conn.commit()
        return jsonify({"message": "Report deleted successfully"}), 200
    except Exception as e:
        logging.error(f"Error deleting report: {e}")
        return jsonify({"error": "Internal error"}), 500
    finally:
        conn.close()

@app.route('/api/reports/<int:report_id>', methods=['PUT'])
def update_report(report_id):
    # 1. Verify Auth
    token = request.headers.get('Authorization')
    if not token or not token.startswith("Bearer "):
        return jsonify({"error": "Missing token"}), 401
    
    token = token.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401

    user_id = payload.get('id')
    user_role = payload.get('role')

    # 2. Get Data
    description = request.form.get('description') or (request.json.get('description') if request.is_json else None)

    image_url = None
    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename != '':
            image_url, err = _save_report_image(file)
            if err:
                return jsonify({"error": err}), 400

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        # 3. Check Ownership
        cursor.execute("SELECT user_id, image_url FROM weather_reports WHERE id = ?", (report_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Report not found"}), 404
        
        owner_id, current_image_url = row

        # Only owner can edit (Admins can delete, but maybe not edit content? Let's restrict to owner for now)
        if owner_id != user_id:
             return jsonify({"error": "Unauthorized: You can only edit your own reports"}), 403

        # 4. Update
        # If new image, use it. If not, keep old (unless maybe explicitly cleared? MVP: overwrite if new provided)
        final_image_url = image_url if image_url else current_image_url
        
        # If description is None (not provided), keep old? Or allow clearing?
        # If frontend sends description="", it means clear. If it sends nothing, maybe keep old.
        # Let's assume frontend sends strictly what it wants to set.
        # But wait, multipart/form-data might not send 'description' key if empty?
        # Let's update description ONLY if it's not None. If it's empty string, we set it to empty string.
        
        query_parts = []
        params = []

        if description is not None:
             query_parts.append("description = ?")
             params.append(description)
        
        if image_url is not None:
             query_parts.append("image_url = ?")
             params.append(image_url)

        if not query_parts:
             return jsonify({"message": "No changes detected"}), 200

        params.append(report_id)
        sql = f"UPDATE weather_reports SET {', '.join(query_parts)} WHERE id = ?"
        
        cursor.execute(sql, tuple(params))
        conn.commit()
        
        return jsonify({
            "message": "Report updated successfully", 
            "image_url": final_image_url,
            "description": description 
        }), 200

    except Exception as e:
        logging.error(f"Error updating report: {e}")
        return jsonify({"error": "Internal error"}), 500
    finally:
        conn.close()

# --- Story Mode v2 Endpoints ---
from core.config import MDV_INBOX_DIR, STORIES_PREVIEWS_DIR

@app.route('/api/story/list', methods=['GET'])
def list_stories():
    stories = story_state.list_stories()
    return jsonify({"stories": stories}), 200

@app.route('/api/story/prepare', methods=['POST'])
def prepare_story():
    """Trigger preview PNG generation for a storm (background thread)."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401
    token = auth_header.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload or payload.get('role') != 'superadmin':
        return jsonify({"error": "Solo superadmin"}), 403

    data = request.get_json()
    story_id = data.get('story_id')
    if not story_id:
        return jsonify({"error": "story_id is required"}), 400

    try:
        result = story_state.generate_previews(story_id)
        return jsonify(result), 200
    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error preparing story previews: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/story/previews/<story_id>', methods=['GET'])
def get_story_previews(story_id):
    """List available preview images for a storm."""
    previews = story_state.get_preview_list(story_id)
    # Also return preview generation status
    state = story_state.get_status()
    preview_status = state.get("preview_status", "idle")
    return jsonify({
        "previews": previews,
        "preview_status": preview_status,
        "preview_story_id": state.get("preview_story_id")
    }), 200

@app.route('/api/story/preview-image/<story_id>/<filename>', methods=['GET'])
def serve_story_preview(story_id, filename):
    """Serve a preview PNG image."""
    preview_dir = os.path.join(STORIES_PREVIEWS_DIR, story_id)
    if not os.path.exists(os.path.join(preview_dir, filename)):
        return jsonify({"error": "Preview not found"}), 404
    return send_from_directory(preview_dir, filename)

@app.route('/api/story/start', methods=['POST'])
def start_story():
    # Only allow superadmin to start story mode
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401
    
    token = auth_header.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload or payload.get('role') != 'superadmin':
        return jsonify({"error": "Solo superadmin puede ejecutar el Modo Historia"}), 403

    # Check if radar is active (MDV files in inbox) — only block if worker is NOT paused
    current_story_status = story_state.get_status()
    worker_is_paused = current_story_status.get("worker_paused", False)
    try:
        if os.path.exists(MDV_INBOX_DIR) and not worker_is_paused:
            mdv_in_inbox = [f for f in os.listdir(MDV_INBOX_DIR) if f.endswith('.mdv')]
            if mdv_in_inbox:
                return jsonify({
                    "error": "El radar está activo. Hay archivos MDV en la bandeja de entrada. "
                             "Pausá el worker primero o esperá a que termine de procesar."
                }), 409
    except Exception as e:
        logging.warning(f"Could not check MDV inbox: {e}")

    data = request.get_json()
    story_id = data.get('story_id')
    start_index = data.get('start_index')  # Optional: start from specific scan
    
    if not story_id:
        return jsonify({"error": "story_id is required"}), 400
    
    try:
        state = story_state.start(story_id, start_index=start_index)
        return jsonify({
            "message": f"Story mode iniciado para {story_id}. Convirtiendo archivos MDV...",
            "status": story_state.get_status()
        }), 200
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error starting story mode: {e}")
        return jsonify({"error": f"Error interno: {str(e)}"}), 500

@app.route('/api/story/stop', methods=['POST'])
def stop_story():
    # Only superadmin can stop
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401
    
    token = auth_header.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload or payload.get('role') != 'superadmin':
        return jsonify({"error": "Solo superadmin puede detener el Modo Historia"}), 403

    story_state.stop()
    return jsonify({
        "message": "Story mode detenido. Los archivos NC se eliminarán en 1 hora.",
        "status": story_state.get_status()
    }), 200

@app.route('/api/story/status', methods=['GET'])
def get_story_status():
    return jsonify(story_state.get_status()), 200

@app.route('/api/story/worker/pause', methods=['POST'])
def pause_worker():
    """Pause the normal radar pipeline worker to free VPS resources."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401
    
    token = auth_header.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload or payload.get('role') != 'superadmin':
        return jsonify({"error": "Solo superadmin"}), 403

    story_state.pause_worker()
    return jsonify({"message": "Worker normal pausado", "worker_paused": True}), 200

@app.route('/api/story/worker/resume', methods=['POST'])
def resume_worker():
    """Resume the normal radar pipeline worker."""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401
    
    token = auth_header.split(" ")[1]
    payload = auth.decode_access_token(token)
    if not payload or payload.get('role') != 'superadmin':
        return jsonify({"error": "Solo superadmin"}), 403

    story_state.resume_worker()
    return jsonify({"message": "Worker normal reanudado", "worker_paused": False}), 200


if __name__ == "__main__":
    # Solo para desarrollo local. En producción se lanza con gunicorn (ver Dockerfile).
    # debug=True exponía el debugger de Werkzeug en el VPS; ahora hay que pedirlo explícitamente.
    debug = os.getenv("FLASK_DEBUG") == "1"
    logging.info(f"Iniciando servidor Flask API de desarrollo (debug={debug})...")
    app.run(host='0.0.0.0', port=8000, debug=debug)

# End of file

