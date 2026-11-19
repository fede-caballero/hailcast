"""
Envío unificado de notificaciones: Web Push (VAPID) + Telegram para usuarios vinculados.

Reemplaza los cuatro bloques duplicados de ``webpush`` que había en
``worker/main.py`` y ``api/main.py``. Cada tipo de alerta (``kind``) respeta la
preferencia correspondiente de ``push_subscriptions``; para Telegram se usa la
misma preferencia (OR entre los dispositivos del usuario) y, si el usuario no
tiene ningún dispositivo suscripto, los defaults de ``TELEGRAM_DEFAULTS``.

Las suscripciones push que el navegador dio de baja (404/410) se borran.
"""
import json
import logging
import sqlite3
from urllib.parse import urlparse

from pywebpush import WebPushException, webpush

from core.config import DB_PATH, VAPID_CLAIM_EMAIL, VAPID_PRIVATE_KEY
from services import telegram_service

try:
    from py_vapid import Vapid
except ImportError:  # pragma: no cover
    Vapid = None

PREF_COLUMN = {
    "admin": "alert_admin",
    "proximity": "alert_proximity",
    "aircraft": "alert_aircraft",
    "forecast": "alert_forecast",
    "engagement": None,  # a todos
}
TELEGRAM_DEFAULTS = {"admin": 1, "proximity": 1, "aircraft": 0, "forecast": 1, "engagement": 1}

_vapid = {"obj": None, "tried": False}


# --- Web Push ------------------------------------------------------------------

def _vapid_obj():
    if not _vapid["tried"]:
        _vapid["tried"] = True
        if Vapid and VAPID_PRIVATE_KEY:
            try:
                _vapid["obj"] = Vapid.from_pem(VAPID_PRIVATE_KEY.encode("utf-8"))
            except Exception as e:
                logging.error(f"[Notifier] VAPID setup error: {e}")
    return _vapid["obj"]


def _auth_headers(endpoint: str) -> dict:
    vapid_obj = _vapid_obj()
    if not vapid_obj:
        return {}
    if hasattr(vapid_obj, "get_authorization_header"):
        header_value = vapid_obj.get_authorization_header(endpoint, VAPID_CLAIM_EMAIL)
        if isinstance(header_value, bytes):
            header_value = header_value.decode("utf-8")
        if isinstance(header_value, str):
            return {"Authorization": header_value}
        if isinstance(header_value, dict):
            return header_value
        return {}
    parsed = urlparse(endpoint)
    token = vapid_obj.sign({"aud": f"{parsed.scheme}://{parsed.netloc}", "sub": VAPID_CLAIM_EMAIL})
    if isinstance(token, dict):
        return token
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return {"Authorization": token if "vapid t=" in token else f"WebPush {token}"}


def payload(title: str, body: str, url: str = "/", icon: str = "/icon-192x192.png") -> str:
    return json.dumps({"title": title, "body": body, "url": url, "icon": icon})


def delete_subscription(endpoint: str) -> None:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        conn.commit()
        conn.close()
        logging.info(f"[Notifier] Suscripción push vencida eliminada: {endpoint[:60]}...")
    except Exception as e:
        logging.warning(f"[Notifier] No se pudo borrar la suscripción vencida: {e}")


def send_push(endpoint: str, p256dh: str, auth_key: str, data_json: str, ttl: str = "60") -> bool:
    headers = {"TTL": str(ttl), "Urgency": "high"}
    try:
        headers.update(_auth_headers(endpoint))
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth_key}},
            data=data_json,
            vapid_private_key=None,
            vapid_claims=None,
            headers=headers,
        )
        return True
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410) or "410" in str(e) or "404" in str(e):
            delete_subscription(endpoint)
        else:
            logging.error(f"[Notifier] Push error: {e}")
        return False
    except Exception as e:
        logging.error(f"[Notifier] Push error genérico: {e}")
        return False


# --- Historial (tabla notifications) --------------------------------------------------

def store_notification(title: str, body: str, kind: str, url: str = "/"):
    """Guarda la notificación en el historial y devuelve (id, url con ?notification_id=)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT INTO notifications (title, body, type, url) VALUES (?, ?, ?, ?)", (title, body, kind, url))
        notification_id = cur.lastrowid
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"[Notifier] Error guardando en notifications: {e}")
        return None, url
    if not url or url == "/":
        url = f"/?notification_id={notification_id}"
    elif "?" not in url:
        url = f"{url}?notification_id={notification_id}"
    else:
        url = f"{url}&notification_id={notification_id}"
    return notification_id, url


# --- Destinatarios -------------------------------------------------------------------

def push_subscriptions(kind: str, conn) -> list:
    col = PREF_COLUMN.get(kind)
    sql = "SELECT DISTINCT endpoint, p256dh, auth FROM push_subscriptions"
    if col:
        sql += f" WHERE {col} = 1"
    return conn.execute(sql).fetchall()


def _telegram_pref(kind: str, user_id: int, conn) -> bool:
    col = PREF_COLUMN.get(kind)
    if not col:
        return True
    row = conn.execute(f"SELECT MAX({col}) FROM push_subscriptions WHERE user_id = ?", (user_id,)).fetchone()
    if row is None or row[0] is None:  # sin dispositivos suscriptos → defaults
        return bool(TELEGRAM_DEFAULTS.get(kind, 1))
    return bool(row[0])


def telegram_recipients(kind: str, conn, admin_only: bool = False) -> list:
    """[(user_id, chat_id, lat, lon, last_proximity_alert)] de usuarios vinculados con Telegram activo y la preferencia encendida."""
    sql = """SELECT id, telegram_chat_id, latitude, longitude, last_proximity_alert
             FROM users WHERE telegram_chat_id IS NOT NULL AND telegram_enabled = 1"""
    if admin_only:
        sql += " AND role IN ('admin', 'superadmin')"
    rows = conn.execute(sql).fetchall()
    return [r for r in rows if _telegram_pref(kind, r[0], conn)]


def proximity_targets(conn, admin_only: bool = False) -> list:
    """Destinos con ubicación para alertas de proximidad: un dict por dispositivo push y uno por usuario con Telegram."""
    sql = """SELECT u.id, s.latitude, s.longitude, u.last_proximity_alert, s.endpoint, s.p256dh, s.auth
             FROM users u JOIN push_subscriptions s ON u.id = s.user_id
             WHERE s.latitude IS NOT NULL AND s.longitude IS NOT NULL AND s.alert_proximity = 1"""
    if admin_only:
        sql += " AND u.role IN ('admin', 'superadmin')"
    targets = [
        {"user_id": r[0], "lat": r[1], "lon": r[2], "last_alert": r[3], "channel": "push",
         "endpoint": r[4], "p256dh": r[5], "auth": r[6]}
        for r in conn.execute(sql).fetchall()
    ]
    for user_id, chat_id, lat, lon, last_alert in telegram_recipients("proximity", conn, admin_only):
        if lat is None or lon is None:
            continue
        targets.append({"user_id": user_id, "lat": lat, "lon": lon, "last_alert": last_alert,
                        "channel": "telegram", "chat_id": chat_id})
    return targets


# --- Telegram a usuarios --------------------------------------------------------------

def send_telegram_user(user_id: int, chat_id, text: str) -> bool:
    data = telegram_service.send_message(chat_id, text)
    if data and data.get("ok"):
        return True
    if data and data.get("error_code") == 403:
        # El usuario bloqueó al bot: se desvincula para no insistir.
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE users SET telegram_chat_id = NULL, telegram_username = NULL WHERE id = ?", (user_id,))
            conn.commit()
            conn.close()
            logging.info(f"[Notifier] Usuario {user_id} bloqueó al bot de Telegram; desvinculado.")
        except Exception as e:
            logging.warning(f"[Notifier] No se pudo desvincular al usuario {user_id}: {e}")
    return False


def telegram_text(title: str, body: str) -> str:
    return f"{title}\n{body}"


# --- Difusión --------------------------------------------------------------------------

def broadcast(kind: str, title: str, body: str, url: str = "/", ttl: str = "60", store: bool = True) -> dict:
    """Guarda en el historial (opcional), manda push a las suscripciones con la preferencia activa y Telegram a los vinculados."""
    if store:
        _, url = store_notification(title, body, kind, url)
    data_json = payload(title, body, url)
    result = {"kind": kind, "push_sent": 0, "push_total": 0, "telegram_sent": 0, "telegram_total": 0}
    try:
        conn = sqlite3.connect(DB_PATH)
        subs = push_subscriptions(kind, conn)
        tg = telegram_recipients(kind, conn)
        conn.close()
    except Exception as e:
        logging.error(f"[Notifier] DB error buscando destinatarios: {e}")
        return result
    result["push_total"], result["telegram_total"] = len(subs), len(tg)
    for endpoint, p256dh, auth_key in subs:
        if send_push(endpoint, p256dh, auth_key, data_json, ttl):
            result["push_sent"] += 1
    text = telegram_text(title, body)
    for user_id, chat_id, *_ in tg:
        if send_telegram_user(user_id, chat_id, text):
            result["telegram_sent"] += 1
    logging.info(f"[Notifier] {kind}: push {result['push_sent']}/{result['push_total']}, telegram {result['telegram_sent']}/{result['telegram_total']}")
    return result
