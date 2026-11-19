"""
Alertas de tormenta/granizo para parques solares, enviadas por Telegram al chat
del administrador (``TELEGRAM_CHAT_ID``). Son privadas: no pasan por la tabla
``notifications`` ni por push, y el endpoint que expone su estado es solo para
superadmin.

Configuración (variables de entorno, todas opcionales):
  SOLAR_PARKS_FILE            JSON con [{"id","name","lat","lon"}]. Default /app/data/solar_parks.json;
                              si no existe se usa /app/deploy/solar_parks.json (versionado).
  SOLAR_PARK_STATE_FILE       Estado persistente (alertas activas, historial). Default /app/data/solar_park_alerts.json
  SOLAR_PARK_ALERT_KM         Distancia celda-parque que dispara la alerta (20).
  SOLAR_PARK_CLEAR_KM         Distancia a partir de la cual se manda el CESE (25). La banda 20–25 es histéresis.
  SOLAR_PARK_REPEAT_MINUTES   Cada cuánto se repite el aviso mientras siga la condición (30).
  SOLAR_PARK_HAIL_DBZ         dBZ a partir del cual el tipo es GRANIZO (51). Por debajo es TORMENTA.

Las celdas vienen de ``detect_storm_cells`` (> CELL_DBZ_THRESHOLD sobre ALERT_MIN_LEVEL_KM) enriquecidas por
``storm_tracking.update`` (desplazamiento) y con ``top_km`` cuando el scan es 3D.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from services.storm_tracking import bearing_deg, cardinal, haversine_km

PARKS_FILE = os.getenv("SOLAR_PARKS_FILE", "/app/data/solar_parks.json")
PARKS_FALLBACK_FILE = "/app/deploy/solar_parks.json"
STATE_FILE = os.getenv("SOLAR_PARK_STATE_FILE", "/app/data/solar_park_alerts.json")
ALERT_KM = float(os.getenv("SOLAR_PARK_ALERT_KM", "20"))
CLEAR_KM = float(os.getenv("SOLAR_PARK_CLEAR_KM", "25"))
REPEAT_MINUTES = int(os.getenv("SOLAR_PARK_REPEAT_MINUTES", "30"))
HAIL_DBZ = float(os.getenv("SOLAR_PARK_HAIL_DBZ", "51"))
CELL_DBZ = float(os.getenv("CELL_DBZ_THRESHOLD", "45"))
SEVERE_DBZ = 58.0
MAX_CEASE_CELL_KM = 80.0   # Para describir hacia dónde se fue la tormenta en el CESE
HISTORY_LEN = 30
SOURCE = "Radares AEMSA"

TZ_MENDOZA = timezone(timedelta(hours=-3))  # Argentina no tiene horario de verano

_parks_cache = {"path": None, "mtime": None, "parks": []}


# --- Carga de parques y estado ------------------------------------------------

def _parks_path():
    if os.path.exists(PARKS_FILE):
        return PARKS_FILE
    if os.path.exists(PARKS_FALLBACK_FILE):
        return PARKS_FALLBACK_FILE
    return None


def load_parks() -> list:
    path = _parks_path()
    if not path:
        return []
    try:
        mtime = os.path.getmtime(path)
        if _parks_cache["path"] == path and _parks_cache["mtime"] == mtime:
            return _parks_cache["parks"]
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        parks = []
        for p in raw:
            if not all(k in p for k in ("name", "lat", "lon")):
                logging.warning(f"[SolarParks] Parque incompleto ignorado: {p}")
                continue
            parks.append({
                "id": p.get("id") or p["name"].lower().replace(" ", "_"),
                "name": p["name"],
                "lat": float(p["lat"]),
                "lon": float(p["lon"]),
            })
        _parks_cache.update(path=path, mtime=mtime, parks=parks)
        return parks
    except Exception as e:
        logging.error(f"[SolarParks] No se pudo leer {path}: {e}")
        return _parks_cache["parks"]


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logging.error(f"[SolarParks] No se pudo guardar el estado: {e}")


# --- Reglas ----------------------------------------------------------------------

def classify(max_dbz: float):
    """Devuelve (tipo, observación) según el dBZ máximo de la celda."""
    if max_dbz >= SEVERE_DBZ:
        return "GRANIZO", "Tormenta con granizo severo"
    if max_dbz >= HAIL_DBZ:
        return "GRANIZO", "Tormenta con probable granizo"
    if max_dbz > 50:
        return "TORMENTA", "Lluvia torrencial"
    return "TORMENTA", "Tormenta intensa"


def nearest_cell(park: dict, cells: list):
    """(celda, distancia_km, rumbo_parque→celda) de la celda más cercana, o (None, None, None)."""
    best, best_d, best_b = None, None, None
    for c in cells:
        d = haversine_km(park["lat"], park["lon"], c["lat"], c["lon"])
        if best_d is None or d < best_d:
            best, best_d = c, d
            best_b = bearing_deg(park["lat"], park["lon"], c["lat"], c["lon"])
    return best, best_d, best_b


def _hhmm(now_local: datetime) -> str:
    return now_local.strftime("%H:%M")


def _desplazamiento(cell: dict) -> str:
    d = cell.get("motion_dir")
    if d == "estacionaria":
        return "Celda estacionaria"
    if not d:
        return "Desplazamiento: sin dato"
    v = cell.get("speed_kmh")
    return f"Desplazamiento al {d}" + (f" {int(round(v))} km/h" if v is not None else "")


def _parametro(cell: dict, with_dbz: bool = True) -> str:
    parts = []
    if with_dbz:
        parts.append(f"{int(round(cell['max_dbz']))} dBZ")
    if cell.get("top_km") is not None:
        parts.append(f"Altura Tope: {cell['top_km']:g} km")
    parts.append(_desplazamiento(cell))
    return ". ".join(parts) + "."


def format_alert(park: dict, cell: dict, dist_km: float, bearing: float, now_local: datetime) -> str:
    tipo, obs = classify(cell["max_dbz"])
    return (
        f"Parque Afectado: {park['name']}\n"
        f"Hora alerta: {_hhmm(now_local)} hs\n"
        f"Tipo de alerta: {tipo}\n"
        f"Parámetro: {_parametro(cell)}\n"
        f"Observación: {obs}\n"
        f"Distancia al Parque: {int(round(dist_km))} km al {cardinal(bearing)} del PS\n"
        f"Fuente: {SOURCE}"
    )


def format_cease(park: dict, prev_type: str, cell, dist_km, bearing, now_local: datetime) -> str:
    if cell is not None:
        parametro = _parametro(cell, with_dbz=False)
        obs = f"Tormenta se aleja por el {cardinal(bearing)} del PS"
        dist = f"{int(round(dist_km))} km al {cardinal(bearing)} del PS"
    else:
        parametro = f"Sin celdas significativas (> {CELL_DBZ:g} dBZ) en la zona."
        obs = "Sin tormentas significativas cerca del PS"
        dist = "--"
    return (
        f"Parque Afectado: {park['name']}\n"
        f"Hora alerta: {_hhmm(now_local)} hs\n"
        f"Tipo de alerta: Cese de Alerta por {prev_type}\n"
        f"Parámetro: {parametro}\n"
        f"Observación: {obs}\n"
        f"Distancia al Parque: {dist}\n"
        f"Fuente: {SOURCE}"
    )


def _push_history(pstate: dict, entry: dict) -> None:
    hist = pstate.setdefault("history", [])
    hist.append(entry)
    del hist[:-HISTORY_LEN]


def evaluate(parks: list, cells: list, state: dict, now_utc: datetime) -> list:
    """Aplica las reglas y devuelve [(park, texto)] a enviar. Muta ``state`` (sin persistirlo).

    Pura (sin I/O) para poder testearla con celdas sintéticas.
    """
    now_local = now_utc.astimezone(TZ_MENDOZA)
    messages = []
    for park in parks:
        pstate = state.setdefault(park["id"], {"active": False})
        cell, dist, bearing = nearest_cell(park, cells)
        pstate["nearest_km"] = round(dist, 1) if dist is not None else None
        pstate["nearest_dbz"] = cell["max_dbz"] if cell else None
        pstate["last_eval"] = now_utc.isoformat()

        if cell is not None and dist <= ALERT_KM:
            tipo, _ = classify(cell["max_dbz"])
            last_sent = pstate.get("last_sent")
            try:
                minutes_since = (now_utc - datetime.fromisoformat(last_sent)).total_seconds() / 60.0 if last_sent else None
            except Exception:
                minutes_since = None
            escalated = pstate.get("active") and pstate.get("type") == "TORMENTA" and tipo == "GRANIZO"
            due = minutes_since is None or minutes_since >= REPEAT_MINUTES
            if not pstate.get("active") or escalated or due:
                text = format_alert(park, cell, dist, bearing, now_local)
                if not pstate.get("active"):
                    pstate["since"] = now_utc.isoformat()
                pstate.update(active=True, type=tipo, last_sent=now_utc.isoformat(),
                              last_dbz=cell["max_dbz"], last_distance_km=round(dist, 1))
                _push_history(pstate, {"time": now_utc.isoformat(), "kind": "ALERTA", "type": tipo,
                                       "dbz": cell["max_dbz"], "distance_km": round(dist, 1)})
                messages.append((park, text))
            else:
                # Sigue la condición: refrescar el tipo si subió sin cambiar el cooldown
                pstate["type"] = tipo if tipo == "GRANIZO" else pstate.get("type", tipo)
        elif pstate.get("active") and (cell is None or dist >= CLEAR_KM):
            prev_type = pstate.get("type", "TORMENTA")
            far_cell = cell if (cell is not None and dist <= MAX_CEASE_CELL_KM) else None
            text = format_cease(park, prev_type, far_cell, dist if far_cell else None, bearing if far_cell else None, now_local)
            pstate.update(active=False, type=None, last_sent=now_utc.isoformat(), cleared_at=now_utc.isoformat())
            _push_history(pstate, {"time": now_utc.isoformat(), "kind": "CESE", "type": prev_type,
                                   "distance_km": round(dist, 1) if dist is not None else None})
            messages.append((park, text))
        # Entre ALERT_KM y CLEAR_KM con alerta activa: histéresis, no se hace nada.
    return messages


# --- Glue para el worker y la API -----------------------------------------------------

def check_alerts(cells: list, now_utc: datetime = None, send=None) -> int:
    """Evalúa todos los parques con las celdas del último scan observado y manda los mensajes. Devuelve cuántos envió."""
    parks = load_parks()
    if not parks:
        return 0
    if send is None:
        from services import telegram_service
        send = telegram_service.send_admin
    now_utc = now_utc or datetime.now(timezone.utc)
    state = load_state()
    messages = evaluate(parks, cells, state, now_utc)
    sent = 0
    for park, text in messages:
        logging.info(f"[SolarParks] {park['name']}: {text.splitlines()[2]}")
        try:
            if send(text):
                sent += 1
        except Exception as e:
            logging.error(f"[SolarParks] Error enviando aviso de {park['name']}: {e}")
    save_state(state)
    return sent


def get_status() -> dict:
    """Parques + estado actual, para el mapa del superadmin."""
    parks = load_parks()
    state = load_state()
    out = []
    for p in parks:
        s = state.get(p["id"], {})
        out.append({
            **p,
            "active": bool(s.get("active")),
            "type": s.get("type"),
            "since": s.get("since") if s.get("active") else None,
            "last_sent": s.get("last_sent"),
            "last_dbz": s.get("last_dbz"),
            "last_distance_km": s.get("last_distance_km"),
            "nearest_km": s.get("nearest_km"),
            "nearest_dbz": s.get("nearest_dbz"),
            "last_eval": s.get("last_eval"),
            "history": list(reversed(s.get("history", [])))[:10],
        })
    return {
        "parks": out,
        "config": {"alert_km": ALERT_KM, "clear_km": CLEAR_KM, "repeat_minutes": REPEAT_MINUTES, "hail_dbz": HAIL_DBZ},
        "source_file": _parks_path(),
    }
