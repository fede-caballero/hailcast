"""
Cliente mínimo de la Bot API de Telegram + vinculación de cuentas.

Dos usos:
  1. Avisos privados al chat del administrador (``TELEGRAM_CHAT_ID``): hoy, las
     alertas de parques solares (ver ``solar_parks.py``). Es el mismo chat al que
     el watchdog manda sus avisos de operación.
  2. Usuarios registrados que vinculan su cuenta desde Ajustes y reciben por
     Telegram las mismas alertas que por push (ver ``notifier.py``).

Flujo de vinculación: la API genera un token de un solo uso y arma el link
``https://t.me/<bot>?start=<token>``; el usuario lo abre, Telegram manda
``/start <token>`` al bot, y el worker (único proceso que hace ``getUpdates``)
asocia ese chat con la cuenta. ``/stop`` desactiva los avisos.

No importa ``core.config`` para poder probarse sin torch ni SECRET_KEY: lee las
variables de entorno directamente, igual que ``deploy/watchdog.sh``.
"""
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

API_BASE = "https://api.telegram.org/bot{token}/{method}"
LINK_TOKEN_TTL_MINUTES = 15
_bot_username_cache = None


# --- Configuración -----------------------------------------------------------

def bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def admin_chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "").strip()


def is_configured() -> bool:
    return bool(bot_token())


# --- Bot API -----------------------------------------------------------------

def _call(method: str, payload: dict = None, timeout: int = 15):
    """Llama a un método de la Bot API. Devuelve el JSON de respuesta (ok o no) o None si falló la red."""
    if not is_configured():
        return None
    try:
        r = requests.post(API_BASE.format(token=bot_token(), method=method), json=payload or {}, timeout=timeout)
        data = r.json()
    except Exception as e:
        logging.warning(f"[Telegram] {method} falló: {e}")
        return None
    if not data.get("ok"):
        logging.warning(f"[Telegram] {method}: {data.get('error_code')} {data.get('description')}")
    return data


def send_message(chat_id, text: str, disable_notification: bool = False):
    """Envía texto plano. Devuelve la respuesta de la API (dict) o None."""
    return _call("sendMessage", {
        "chat_id": str(chat_id),
        "text": text,
        "disable_notification": disable_notification,
    })


def send_admin(text: str) -> bool:
    """Mensaje al chat privado del administrador (TELEGRAM_CHAT_ID)."""
    if not admin_chat_id():
        logging.info("[Telegram] TELEGRAM_CHAT_ID no configurado; se omite el aviso al admin.")
        return False
    data = send_message(admin_chat_id(), text)
    return bool(data and data.get("ok"))


def get_bot_username():
    """Username del bot para armar el link t.me. Toma TELEGRAM_BOT_USERNAME o consulta getMe (cacheado)."""
    global _bot_username_cache
    env = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    if env:
        return env
    if _bot_username_cache:
        return _bot_username_cache
    data = _call("getMe")
    if data and data.get("ok"):
        _bot_username_cache = data["result"].get("username")
    return _bot_username_cache


# --- Vinculación de cuentas (lado API) -----------------------------------------

def create_link_token(db_path: str, user_id: int):
    """Genera un token de vinculación de un solo uso y devuelve (token, url, expiry_iso) o None si no hay bot."""
    username = get_bot_username()
    if not username:
        return None
    token = secrets.token_urlsafe(24)
    expiry = datetime.now(timezone.utc) + timedelta(minutes=LINK_TOKEN_TTL_MINUTES)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE users SET telegram_link_token = ?, telegram_link_expiry = ? WHERE id = ?",
            (token, expiry.isoformat(), user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return token, f"https://t.me/{username}?start={token}", expiry.isoformat()


def link_status(db_path: str, user_id: int) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT telegram_chat_id, telegram_username, telegram_enabled, telegram_linked_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    chat_id, tg_username, enabled, linked_at = row if row else (None, None, None, None)
    return {
        "configured": is_configured(),
        "bot_username": get_bot_username() if is_configured() else None,
        "linked": bool(chat_id),
        "enabled": bool(enabled) if chat_id else False,
        "telegram_username": tg_username,
        "linked_at": linked_at,
    }


def set_enabled(db_path: str, user_id: int, enabled: bool) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE users SET telegram_enabled = ? WHERE id = ?", (1 if enabled else 0, user_id))
        conn.commit()
    finally:
        conn.close()


def unlink(db_path: str, user_id: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        chat = conn.execute("SELECT telegram_chat_id FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.execute(
            """UPDATE users SET telegram_chat_id = NULL, telegram_username = NULL, telegram_enabled = 1,
                      telegram_link_token = NULL, telegram_link_expiry = NULL, telegram_linked_at = NULL
               WHERE id = ?""",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()
    if chat and chat[0]:
        send_message(chat[0], "Tu cuenta de HailCast se desvinculó de este chat. Podés volver a vincularla desde Ajustes.")


# --- Vinculación de cuentas (lado worker: getUpdates) ---------------------------

def _read_offset(offset_file: str) -> int:
    try:
        with open(offset_file) as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def _write_offset(offset_file: str, offset: int) -> None:
    try:
        os.makedirs(os.path.dirname(offset_file), exist_ok=True)
        with open(offset_file, "w") as f:
            f.write(str(offset))
    except Exception as e:
        logging.warning(f"[Telegram] No se pudo guardar el offset de updates: {e}")


def _complete_link(db_path: str, token: str, chat_id, tg_username) -> None:
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, username, telegram_link_expiry FROM users WHERE telegram_link_token = ?", (token,)
        ).fetchone()
        if not row:
            send_message(chat_id, "Ese link de vinculación no es válido o ya se usó. Generá uno nuevo desde Ajustes → Telegram.")
            return
        user_id, username, expiry = row
        try:
            expired = datetime.fromisoformat(expiry) < now
        except Exception:
            expired = True
        if expired:
            conn.execute("UPDATE users SET telegram_link_token = NULL, telegram_link_expiry = NULL WHERE id = ?", (user_id,))
            conn.commit()
            send_message(chat_id, "El link de vinculación venció (dura 15 minutos). Generá uno nuevo desde Ajustes → Telegram.")
            return
        # Un chat de Telegram pertenece a una sola cuenta: si estaba atado a otra, se libera.
        conn.execute(
            "UPDATE users SET telegram_chat_id = NULL, telegram_username = NULL WHERE telegram_chat_id = ? AND id != ?",
            (str(chat_id), user_id),
        )
        conn.execute(
            """UPDATE users SET telegram_chat_id = ?, telegram_username = ?, telegram_enabled = 1,
                      telegram_linked_at = ?, telegram_link_token = NULL, telegram_link_expiry = NULL
               WHERE id = ?""",
            (str(chat_id), tg_username, now.isoformat(), user_id),
        )
        conn.commit()
    finally:
        conn.close()
    logging.info(f"[Telegram] Cuenta '{username}' (id {user_id}) vinculada al chat {chat_id}")
    send_message(
        chat_id,
        f"✅ Listo, {username}. Este chat quedó vinculado a tu cuenta de HailCast y vas a recibir por acá "
        "las mismas alertas que por notificaciones push.\n\n"
        "Enviá /stop para pausarlas (podés reactivarlas desde Ajustes o con /start).",
    )


def _set_enabled_by_chat(db_path: str, chat_id, enabled: bool) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE users SET telegram_enabled = ? WHERE telegram_chat_id = ?", (1 if enabled else 0, str(chat_id))
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def process_updates(db_path: str, offset_file: str) -> None:
    """Lee los mensajes nuevos que recibió el bot y atiende /start <token>, /start y /stop.

    Debe llamarse desde UN solo proceso (el worker): dos pollers sobre el mismo bot se pisan (409).
    """
    if not is_configured():
        return
    offset = _read_offset(offset_file)
    data = _call("getUpdates", {"offset": offset + 1, "timeout": 0, "allowed_updates": ["message"]}, timeout=20)
    if not data or not data.get("ok"):
        if data and data.get("error_code") == 409:
            logging.warning("[Telegram] getUpdates en conflicto: hay un webhook activo u otra instancia polleando.")
        return
    for update in data.get("result", []):
        offset = max(offset, int(update.get("update_id", offset)))
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not chat_id or not text:
            continue
        tg_username = (msg.get("from") or {}).get("username")
        try:
            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                token = parts[1].strip() if len(parts) > 1 else ""
                if token:
                    _complete_link(db_path, token, chat_id, tg_username)
                elif _set_enabled_by_chat(db_path, chat_id, True):
                    send_message(chat_id, "✅ Alertas reactivadas. Enviá /stop para pausarlas.")
                else:
                    send_message(
                        chat_id,
                        "Hola 👋 Soy el bot de HailCast. Para recibir alertas por acá, abrí la app → Ajustes → "
                        "Telegram → \"Vincular\" y tocá el link que te genera.",
                    )
            elif text.startswith("/stop"):
                if _set_enabled_by_chat(db_path, chat_id, False):
                    send_message(chat_id, "Listo, pausé las alertas. Enviá /start para volver a recibirlas.")
                else:
                    send_message(chat_id, "Este chat no está vinculado a ninguna cuenta de HailCast.")
        except Exception as e:
            logging.error(f"[Telegram] Error procesando update {update.get('update_id')}: {e}")
    _write_offset(offset_file, offset)
