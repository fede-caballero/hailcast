"""
Rayos en tiempo casi real desde el GLM (Geostationary Lightning Mapper) de GOES-19.

Fuente: NOAA Open Data en Amazon S3 (bucket público, sin credenciales). Cada 20 segundos
aparece un NetCDF L2 LCFA (~500 kB) con todos los destellos del disco terrestre; la latencia
total ronda los 30 segundos. Acá se bajan los archivos nuevos, se filtra el recuadro de
interés (Mendoza y San Juan por defecto), se descartan los de calidad dudosa y se mantienen
en memoria los últimos GLM_KEEP_MINUTES. El estado se vuelca a GLM_STATE_FILE para que la API
(otro proceso) lo sirva en /api/lightning.

Corre como hilo de fondo dentro del worker (``start_background``). Nada queda en disco salvo
el JSON de estado: el archivo del GLM se parsea en memoria y se descarta.

Variables de entorno (opcionales): GLM_ENABLED (1), GLM_BUCKET_URL, GLM_BBOX
(lat_min,lat_max,lon_min,lon_max), GLM_KEEP_MINUTES (60), GLM_POLL_SECONDS (30), GLM_STATE_FILE.

Limitaciones del GLM: resolución ~8-10 km, detecta rayos totales (intranube y nube-tierra sin
distinguir), eficiencia 70-90 % (menor de día).
"""
import json
import logging
import math
import os
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timedelta, timezone

ENABLED = os.getenv("GLM_ENABLED", "1").strip().lower() not in ("0", "false", "no")
BUCKET_URL = os.getenv("GLM_BUCKET_URL", "https://noaa-goes19.s3.amazonaws.com").rstrip("/")
PRODUCT = "GLM-L2-LCFA"
try:
    BBOX = tuple(float(v) for v in os.getenv("GLM_BBOX", "-37.5,-30.5,-71.0,-65.5").split(","))
    assert len(BBOX) == 4
except Exception:
    BBOX = (-37.5, -30.5, -71.0, -65.5)
KEEP_MINUTES = int(os.getenv("GLM_KEEP_MINUTES", "60"))
POLL_SECONDS = int(os.getenv("GLM_POLL_SECONDS", "30"))
STATE_FILE = os.getenv("GLM_STATE_FILE", "/app/data/lightning_recent.json")
BACKFILL_FILES = 30          # al arrancar: ~10 min de archivos (3 por minuto)
MAX_FILES_PER_POLL = 12      # tope por ciclo para ponerse al día sin bloquear
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

_lock = threading.Lock()
_flashes = deque()           # {"t": epoch s, "lat": float, "lon": float, "e": float}
_meta = {"last_key": None, "last_poll": None, "last_file_end": None, "files": 0, "errors": 0}
_thread = None


# --- S3 (listado y descarga anónimos) --------------------------------------------------

def _hour_prefix(t: datetime) -> str:
    return f"{PRODUCT}/{t:%Y/%j/%H}/"


def _list_keys(prefix: str, start_after: str = None) -> list:
    """Claves bajo un prefijo (S3 las devuelve en orden lexicográfico = cronológico)."""
    keys, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if start_after:
            params["start-after"] = start_after
        if token:
            params["continuation-token"] = token
        url = f"{BUCKET_URL}/?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=30) as r:
            root = ET.fromstring(r.read())
        keys += [c.findtext("s3:Key", namespaces=NS) for c in root.findall("s3:Contents", NS)]
        token = root.findtext("s3:NextContinuationToken", default=None, namespaces=NS)
        if not token:
            break
    return [k for k in keys if k and k.endswith(".nc")]


def _download(key: str) -> bytes:
    with urllib.request.urlopen(f"{BUCKET_URL}/{key}", timeout=60) as r:
        return r.read()


def _new_keys(now: datetime) -> list:
    """Claves posteriores a la última procesada, mirando la hora actual y la anterior."""
    last = _meta["last_key"]
    keys = []
    for t in (now - timedelta(hours=1), now):
        prefix = _hour_prefix(t)
        if last and last > prefix + "￿":   # hora ya completamente procesada
            continue
        start_after = last if (last and last.startswith(prefix)) else None
        keys += _list_keys(prefix, start_after=start_after)
    keys = sorted(k for k in set(keys) if not last or k > last)
    if not last:
        keys = keys[-BACKFILL_FILES:]           # primer arranque: solo los últimos ~10 min
    return keys


# --- Parseo ----------------------------------------------------------------------------

def parse_lcfa(data: bytes, bbox=BBOX) -> tuple:
    """Destellos dentro del recuadro, con calidad buena. Devuelve (lista, fin_de_ventana_epoch)."""
    import numpy as np
    import netCDF4
    ds = netCDF4.Dataset("glm_inmemory.nc", memory=data)
    try:
        lat = np.asarray(ds["flash_lat"][:], dtype=float)
        lon = np.asarray(ds["flash_lon"][:], dtype=float)
        energy = np.asarray(ds["flash_energy"][:], dtype=float)
        offset = np.asarray(ds["flash_time_offset_of_first_event"][:], dtype=float)
        qf = np.asarray(ds["flash_quality_flag"][:]) if "flash_quality_flag" in ds.variables else np.zeros(len(lat))
        start = datetime.strptime(ds.time_coverage_start, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        end = datetime.strptime(ds.time_coverage_end, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    finally:
        ds.close()
    lat_min, lat_max, lon_min, lon_max = bbox
    keep = (lat >= lat_min) & (lat <= lat_max) & (lon >= lon_min) & (lon <= lon_max) & (qf == 0)
    base = start.timestamp()
    out = [{"t": round(base + float(offset[i]), 1), "lat": round(float(lat[i]), 4), "lon": round(float(lon[i]), 4),
            "e": float(f"{energy[i]:.3g}")} for i in np.where(keep)[0]]
    return out, end.timestamp()


# --- Estado -------------------------------------------------------------------------------

def _prune(now_ts: float) -> None:
    cutoff = now_ts - KEEP_MINUTES * 60
    while _flashes and _flashes[0]["t"] < cutoff:
        _flashes.popleft()


def _save() -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"updated": _meta["last_poll"], "last_file_end": _meta["last_file_end"],
                       "keep_minutes": KEEP_MINUTES, "bbox": BBOX, "flashes": list(_flashes)}, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logging.warning(f"[GLM] No se pudo guardar el estado: {e}")


def poll_once(now: datetime = None) -> int:
    """Baja y procesa los archivos nuevos. Devuelve cuántos destellos sumó en el recuadro."""
    now = now or datetime.now(timezone.utc)
    keys = _new_keys(now)
    if len(keys) > MAX_FILES_PER_POLL:
        logging.info(f"[GLM] {len(keys)} archivos pendientes; se procesan {MAX_FILES_PER_POLL} por ciclo")
        keys = keys[:MAX_FILES_PER_POLL]
    added = 0
    for key in keys:
        try:
            flashes, end_ts = parse_lcfa(_download(key))
        except Exception as e:
            _meta["errors"] += 1
            logging.warning(f"[GLM] Error con {key.split('/')[-1]}: {e}")
            continue
        with _lock:
            _flashes.extend(sorted(flashes, key=lambda f: f["t"]))
            _meta["last_key"] = key
            _meta["last_file_end"] = datetime.fromtimestamp(end_ts, timezone.utc).isoformat()
            _meta["files"] += 1
        added += len(flashes)
    with _lock:
        _meta["last_poll"] = now.isoformat()
        _prune(now.timestamp())
        total = len(_flashes)
    _save()
    if added:
        logging.info(f"[GLM] +{added} destellos en el recuadro ({total} en los últimos {KEEP_MINUTES} min)")
    return added


def recent(minutes: int = 30, now_ts: float = None) -> list:
    now_ts = now_ts or time.time()
    cutoff = now_ts - minutes * 60
    with _lock:
        return [f for f in _flashes if f["t"] >= cutoff]


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def count_near(lat: float, lon: float, km: float = 15.0, minutes: int = 10, now_ts: float = None) -> int:
    """Destellos de los últimos `minutes` a menos de `km` de un punto (p. ej. una celda)."""
    return sum(1 for f in recent(minutes, now_ts) if _haversine_km(lat, lon, f["lat"], f["lon"]) <= km)


def load_recent_from_file(minutes: int = 30, path: str = STATE_FILE) -> dict:
    """Para la API (otro proceso): lee el estado volcado por el worker."""
    try:
        with open(path) as f:
            state = json.load(f)
    except Exception:
        return {"updated": None, "flashes": [], "stale": True}
    cutoff = time.time() - minutes * 60
    state["flashes"] = [fl for fl in state.get("flashes", []) if fl["t"] >= cutoff]
    try:
        age = time.time() - datetime.fromisoformat(state["updated"]).timestamp()
    except Exception:
        age = None
    state["stale"] = age is None or age > 180
    return state


# --- Hilo de fondo ---------------------------------------------------------------------------

def _loop() -> None:
    logging.info(f"[GLM] Hilo de rayos iniciado: {BUCKET_URL}/{PRODUCT}, recuadro {BBOX}, cada {POLL_SECONDS}s")
    failures = 0
    while True:
        try:
            poll_once()
            failures = 0
        except Exception as e:
            failures += 1
            logging.warning(f"[GLM] Ciclo fallido ({failures}): {e}")
        time.sleep(min(POLL_SECONDS * (2 ** min(failures, 4)), 600) if failures else POLL_SECONDS)


def start_background() -> bool:
    """Arranca el hilo (idempotente). False si GLM_ENABLED=0."""
    global _thread
    if not ENABLED:
        logging.info("[GLM] Deshabilitado (GLM_ENABLED=0)")
        return False
    with _lock:
        if _thread and _thread.is_alive():
            return True
        _thread = threading.Thread(target=_loop, daemon=True, name="glm_lightning")
        _thread.start()
    return True
