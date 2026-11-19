import os
import time
import subprocess
import logging
import argparse
from datetime import datetime, timezone
import glob

# --- Configuración ---
# Ajustar según la estructura del contenedor Docker
MDV_INBOX_DIR = "/app/data/mdv_inbox"
MDV_ARCHIVE_DIR = "/app/data/mdv_archive" # Files processed by worker are moved here
INPUT_DIR = "/app/data/input_scans"       # Files converted to NC end up here

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("DriveWatcher")

def today_folder_name():
    # Asumimos estructura plana por día: YYYYMMDD (Común en LROSE/TITAN)
    # Ejemplo: .../cart_no_clutter/20260125
    return datetime.now(timezone.utc).strftime('%Y%m%d')


class DayFolder:
    """Una carpeta del día en el remoto. Google Drive permite varias carpetas con el MISMO
    nombre (pasó el 2026-10-22: dos '20261022', una con un solo archivo). Por nombre, rclone
    resuelve solo la primera que lista; por ID de Drive se puede leer cada una por separado.
    """

    def __init__(self, path: str = None, remote: str = None, folder_id: str = None):
        self.path = path          # modo clásico: 'gdrive:base/20261022'
        self.remote = remote      # modo por ID: 'gdrive:'
        self.folder_id = folder_id

    def __str__(self):
        return self.path if self.path else f"{self.remote}[id={self.folder_id}]"

    def _id_flags(self):
        return ["--drive-root-folder-id", self.folder_id] if self.folder_id else []

    def list_files(self):
        """Nombres de archivos .mdv en la carpeta (rclone lsf devuelve uno por línea)."""
        target = self.path if self.path else self.remote
        cmd = ["rclone", "lsf", target, "--files-only"] + self._id_flags()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
        except subprocess.CalledProcessError as e:
            logger.warning(f"No se pudo listar archivos en {self}: {e.stderr}")
            return []
        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout listando {self}")
            return []
        return [f for f in result.stdout.strip().split('\n') if f.endswith('.mdv')]

    def copy_cmd(self, filename: str, local_dest_file: str):
        """Descarga `filename` como `local_dest_file` (ruta completa; permite renombrar al bajar)."""
        source = f"{self.path}/{filename}" if self.path else f"{self.remote}{filename}"
        return ["rclone", "copyto", source, local_dest_file] + self._id_flags()


def split_remote(base_remote_path: str):
    """'gdrive:RadarMendoza/cart' -> ('gdrive:', 'RadarMendoza/cart')."""
    name, sep, path = base_remote_path.partition(':')
    if not sep:
        return None, base_remote_path
    return name + ':', path.strip('/')


_folders_cache = {"base": None, "day": None, "at": 0.0, "folders": [], "count": 0}
FOLDERS_CACHE_SECONDS = 60


def resolve_day_folders(base_remote_path: str, day: str):
    """Todas las carpetas del remoto llamadas `day`, direccionadas por ID de Drive.

    Si el remoto no expone IDs (no es Drive) o la consulta falla, cae al modo clásico por ruta.
    Se cachea un minuto para no duplicar llamadas a la API en cada ciclo.
    """
    now = time.time()
    same_target = _folders_cache["base"] == base_remote_path and _folders_cache["day"] == day
    if same_target and now - _folders_cache["at"] < FOLDERS_CACHE_SECONDS:
        return _folders_cache["folders"]

    fallback = [DayFolder(path=f"{base_remote_path}/{day}")]
    remote, base_path = split_remote(base_remote_path)
    folders = fallback
    if remote:
        cmd = ["rclone", "lsf", f"{remote}{base_path}", "--dirs-only", "--format", "pi", "--separator", ";"]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120).stdout
            found = []
            for line in out.splitlines():
                name, _, folder_id = line.strip().partition(';')
                if name.rstrip('/') == day and folder_id:
                    found.append(DayFolder(remote=remote, folder_id=folder_id))
            if found:
                folders = found
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"No se pudieron listar las carpetas de {base_remote_path}; se usa la ruta por nombre. {getattr(e, 'stderr', e)}")

    if len(folders) != _folders_cache["count"] or not same_target:
        if len(folders) > 1:
            logger.warning(f"⚠️ Hay {len(folders)} carpetas '{day}' en Drive (nombres duplicados). Se leen TODAS: "
                           + ", ".join(str(f) for f in folders))
        else:
            logger.info(f"📁 Carpeta del día: {folders[0]}")
    _folders_cache.update(base=base_remote_path, day=day, at=now, folders=folders, count=len(folders))
    return folders


def list_remote_files(folders):
    """{nombre_de_archivo: carpeta} uniendo todas las carpetas del día (las duplicadas se deduplican por nombre)."""
    files = {}
    for folder in folders:
        for f in folder.list_files():
            files.setdefault(f, folder)
    return files


# In-memory "processed" cache (for files skipped or downloaded in this session)
# This prevents re-evaluating old files if they are not in local folders (e.g. catch-up skipped)
metrics = {
    "downloaded": 0,
    "skipped": 0,
    "errors": 0
}
files_seen_this_session = set()

def is_processed(filename: str):
    """Verifica si el archivo ya existe localmente (en inbox, archivo o convertido) o fue saltado."""
    # 0. Check Session Cache
    if filename in files_seen_this_session:
        return True

    # 1. Check Inbox
    if os.path.exists(os.path.join(MDV_INBOX_DIR, filename)):
        return True
    
    # 2. Check Archive (Processed and moved)
    if os.path.exists(os.path.join(MDV_ARCHIVE_DIR, filename)):
        return True
    
    return False

def local_name(day: str, filename: str):
    """Nombre en la bandeja: AAAAMMDD_HHMMSS.mdv, el mismo que usa tools/radar_bridge.py.

    TITAN nombra los MDV solo por la hora; con la fecha adelante no se pisan dos días y el
    Drive Watcher y el Radar Bridge reconocen el mismo scan aunque llegue por las dos vías.
    """
    return filename if filename.startswith(f"{day}_") else f"{day}_{filename}"


def download_file(folder: DayFolder, filename: str, day: str):
    """Descarga un archivo específico a la bandeja de entrada (renombrado con la fecha)."""
    dest_name = local_name(day, filename)
    local_dest_file = os.path.join(MDV_INBOX_DIR, dest_name)
    
    logger.info(f"📥 Descargando nuevo archivo: {filename} -> {dest_name} (desde {folder})")
    try:
        cmd = folder.copy_cmd(filename, local_dest_file)
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        logger.info(f"✅ Descarga completada: {dest_name}")
        files_seen_this_session.add(dest_name) # Mark as seen
        metrics["downloaded"] += 1
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Error descargando {filename}: {e.stderr}")
        metrics["errors"] += 1
        return False

def main():
    parser = argparse.ArgumentParser(description="Google Drive Radar Watcher")
    parser.add_argument("--remote-base", required=True, help="Rclone remote base path (e.g. 'gdrive:Campaña/2025-2026')")
    parser.add_argument("--interval", type=int, default=10, help="Polling interval in seconds")
    
    args = parser.parse_args()
    
    logger.info("📡 Iniciando Drive Watcher...")
    
    # Asegurar directorios (aunque el worker debería crearlos)
    os.makedirs(MDV_INBOX_DIR, exist_ok=True)
    
    while True:
        try:
            # 1. Determinar carpeta(s) del día: Drive puede tener varias con el mismo nombre
            day = today_folder_name()
            folders = resolve_day_folders(args.remote_base, day)
            
            logger.debug(f"🔍 Consultando: {', '.join(str(f) for f in folders)}")
            
            # 2. Listar archivos remotos (unión de todas las carpetas del día)
            remote_by_name = list_remote_files(folders)
            remote_files = sorted(remote_by_name)
            
            if not remote_files:
                logger.debug("No se encontraron archivos remotos (o error de conexión).")
            
            # 3. Filtrar y descargar nuevos

            # IDENTIFY NEW FILES (Using updated is_processed with cache)
            new_files = [f for f in remote_files if not is_processed(local_name(day, f))]
            
            # --- LOGIC UPDATE: Catch-up Strategy ---
            MAX_CATCHUP = 8 # Sufficient for 1 sequence
            
            files_to_download = []
            
            if len(new_files) > MAX_CATCHUP:
                logger.warning(f"⚠️ Demasiados archivos pendientes ({len(new_files)}). Saltando a los últimos {MAX_CATCHUP} para estar en vivo.")
                
                # Split: Skipped vs Downloaded
                files_to_skip = new_files[:-MAX_CATCHUP]
                files_to_download = new_files[-MAX_CATCHUP:]
                
                # Mark skipped files as "seen" to prevent re-processing next loop
                for f in files_to_skip:
                    files_seen_this_session.add(local_name(day, f))
                metrics["skipped"] += len(files_to_skip)
                
                logger.info(f"⏭️ Saltados {len(files_to_skip)} archivos antiguos (ej: {files_to_skip[0]} ... {files_to_skip[-1]})")
            else:
                files_to_download = new_files

            for f in files_to_download:
                download_file(remote_by_name[f], f, day)
            
            if not files_to_download and new_files:
                 logger.info("No new files to download (all pending were skipped by catch-up logic).")
            elif not new_files:
                 logger.debug("Todo al día.")
            
        except Exception as e:
            logger.error(f"Error en bucle principal: {e}")
            
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
