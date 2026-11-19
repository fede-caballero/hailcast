"""
Limpieza de archivos temporales del pipeline.

Dos mecanismos, en este orden:
1. Retención por antigüedad, con un plazo distinto por directorio (los NetCDF
   del radar en formato 1000x1000x36 pesan ~44 MB; con ~400 volúmenes/día,
   48 h de archivo son ~35 GB y llenan el disco del VPS).
2. Guardia por espacio libre: si después de (1) queda menos de MIN_FREE_GB,
   borra los archivos más viejos de los directorios de archivo hasta
   recuperar TARGET_FREE_GB.

Se invoca desde el worker cada CLEANUP_INTERVAL_MINUTES y puede correrse a mano:
    python3 backend/scripts/cleanup.py
"""
import os
import sys
import time
import json
import shutil
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import (
    INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR,
    MDV_INBOX_DIR, MDV_ARCHIVE_DIR, MDV_OUTPUT_DIR,
    IMAGE_OUTPUT_DIR, STORIES_NC_CACHE_DIR, STORY_STATE_FILE,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# --- Política de retención (horas). Sobrescribible por env RETENTION_<NOMBRE>_HOURS ---
def _hours(name, default):
    try:
        return float(os.environ.get(f"RETENTION_{name}_HOURS", default))
    except ValueError:
        return default


# Misma política para cada radar activo (RADARS): el sur usa las rutas históricas y los
# demás sus carpetas sufijadas (ver core/radar_context.py).
from core.radar_context import get_radar_contexts
_CONTEXTS = get_radar_contexts()

RETENTION = {}
PRESSURE_ORDER = []
for _ctx in _CONTEXTS:
    RETENTION.update({
        _ctx.archive_dir:     _hours("ARCHIVE_SCANS", 6),        # NC de entrada ya procesados (~44 MB c/u)
        _ctx.output_dir:      _hours("OUTPUT_PREDICTIONS", 12),  # NC de predicción (ya convertidos a MDV y PNG)
        _ctx.mdv_archive_dir: _hours("MDV_ARCHIVE", 6),
        _ctx.mdv_output_dir:  _hours("MDV_PREDICTIONS", 24),
        _ctx.mdv_inbox_dir:   _hours("MDV_INBOX", 24),
        _ctx.input_dir:       _hours("INPUT_SCANS", 24),         # el worker gestiona la ventana; esto es red de seguridad
    })
    # De qué directorios borrar primero cuando falta espacio (del más prescindible al menos)
    PRESSURE_ORDER += [_ctx.archive_dir, _ctx.mdv_archive_dir, _ctx.output_dir, _ctx.mdv_output_dir]
# Las imágenes de todos los radares cuelgan de IMAGE_OUTPUT_DIR (subcarpeta por radar) y se recorren con os.walk
RETENTION[IMAGE_OUTPUT_DIR] = _hours("IMAGES", 48)              # PNG + JSON, livianos
PRESSURE_ORDER.append(IMAGE_OUTPUT_DIR)

MIN_FREE_GB = float(os.environ.get("MIN_FREE_GB", 8))
TARGET_FREE_GB = float(os.environ.get("TARGET_FREE_GB", 12))
STORY_CACHE_GRACE_HOURS = float(os.environ.get("RETENTION_STORY_CACHE_HOURS", 2))

EXTENSIONS = ('.nc', '.mdv', '.png', '.json')


def get_dir_size(path):
    """Tamaño total de un directorio en MB."""
    total = 0
    if not os.path.exists(path):
        return 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1024 * 1024)


def free_gb(path=None):
    path = path or os.path.dirname(ARCHIVE_DIR.rstrip("/"))
    try:
        return shutil.disk_usage(path).free / (1024 ** 3)
    except FileNotFoundError:
        return float("inf")


# Carpetas que deben sobrevivir aunque queden vacías: las imágenes por radar (images/<radar>)
PROTECTED_DIRS = {os.path.abspath(_ctx.image_dir) for _ctx in _CONTEXTS}


def _remove_empty_dirs(directory):
    for root, _, _ in os.walk(directory, topdown=False):
        if root == directory or os.path.abspath(root) in PROTECTED_DIRS:
            continue
        try:
            if not os.listdir(root):
                os.rmdir(root)
        except OSError:
            pass


def cleanup_by_age():
    """Borra archivos más viejos que la retención de su directorio."""
    now = time.time()
    deleted, freed = 0, 0
    for directory, hours in RETENTION.items():
        if not os.path.exists(directory):
            continue
        cutoff = now - hours * 3600
        for root, _, files in os.walk(directory):
            for filename in files:
                if not filename.endswith(EXTENSIONS):
                    continue
                path = os.path.join(root, filename)
                try:
                    st = os.stat(path)
                    if st.st_mtime < cutoff:
                        os.remove(path)
                        deleted += 1
                        freed += st.st_size
                except OSError as e:
                    logging.error(f"Error borrando {path}: {e}")
        _remove_empty_dirs(directory)
    return deleted, freed


def cleanup_story_cache():
    """
    Borra cachés NC de Story Mode huérfanas: toda carpeta que no sea la de la
    historia activa y tenga más de STORY_CACHE_GRACE_HOURS. story_manager solo
    limpia la última historia detenida; si se preparaba otra antes, la anterior
    quedaba para siempre.
    """
    if not os.path.exists(STORIES_NC_CACHE_DIR):
        return 0, 0
    active_id = None
    try:
        with open(STORY_STATE_FILE) as f:
            state = json.load(f)
        if state.get("is_active") or state.get("conversion_status") == "converting":
            active_id = state.get("story_id")
    except Exception:
        pass

    deleted, freed = 0, 0
    cutoff = time.time() - STORY_CACHE_GRACE_HOURS * 3600
    for name in os.listdir(STORIES_NC_CACHE_DIR):
        path = os.path.join(STORIES_NC_CACHE_DIR, name)
        if not os.path.isdir(path) or name == active_id:
            continue
        try:
            if os.stat(path).st_mtime < cutoff:
                size = get_dir_size(path) * 1024 * 1024
                shutil.rmtree(path)
                deleted += 1
                freed += size
                logging.info(f"Caché de Story Mode huérfana eliminada: {name}")
        except OSError as e:
            logging.error(f"Error borrando caché {path}: {e}")
    return deleted, freed


def cleanup_by_pressure():
    """Si queda poco espacio, borra lo más viejo de los archivos hasta TARGET_FREE_GB."""
    free = free_gb()
    if free >= MIN_FREE_GB:
        return 0, 0
    logging.warning(f"Poco espacio libre ({free:.1f} GB < {MIN_FREE_GB} GB): liberando hasta {TARGET_FREE_GB} GB")
    candidates = []
    for directory in PRESSURE_ORDER:
        if not os.path.exists(directory):
            continue
        for root, _, files in os.walk(directory):
            for filename in files:
                if filename.endswith(EXTENSIONS):
                    path = os.path.join(root, filename)
                    try:
                        candidates.append((os.stat(path).st_mtime, path))
                    except OSError:
                        pass
    candidates.sort()  # más viejos primero
    deleted, freed = 0, 0
    for _, path in candidates:
        if free_gb() >= TARGET_FREE_GB:
            break
        try:
            size = os.path.getsize(path)
            os.remove(path)
            deleted += 1
            freed += size
        except OSError:
            pass
    for directory in PRESSURE_ORDER:
        if os.path.exists(directory):
            _remove_empty_dirs(directory)
    return deleted, freed


def perform_cleanup():
    logging.info(f"Limpieza: inicio (libre: {free_gb():.1f} GB)")
    d1, f1 = cleanup_by_age()
    d2, f2 = cleanup_story_cache()
    d3, f3 = cleanup_by_pressure()
    total_mb = (f1 + f2 + f3) / (1024 * 1024)
    logging.info(
        f"Limpieza: {d1} por antigüedad, {d2} cachés de story, {d3} por falta de espacio; "
        f"{total_mb:.0f} MB liberados (libre ahora: {free_gb():.1f} GB)"
    )
    for directory in RETENTION:
        size = get_dir_size(directory)
        if size > 100:
            logging.info(f"  {directory}: {size:.0f} MB")
    return d1 + d2 + d3, total_mb


if __name__ == "__main__":
    perform_cleanup()
