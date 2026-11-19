"""
Contexto de radar: todo lo que el pipeline necesita saber de UN radar.

El worker fue escrito para un solo radar (San Rafael, "sur") con rutas y estado en
variables globales. Para atender también el radar norte (San Martín) desde el mismo
proceso, cada radar lleva su propio contexto: bandejas, ventana de scans, salidas,
archivo de estado, manga de granizo, seguimiento de celdas, estado de engagement,
coordenadas del sensor y rutas temporales para las conversiones de LROSE.

El radar "sur" conserva exactamente las rutas y nombres de siempre (sin migración);
cualquier otro radar usa carpetas hermanas con sufijo `_<id>` e imágenes en una
subcarpeta propia.
"""
import copy
import os
from dataclasses import dataclass, field

from core.config import (ARCHIVE_DIR, DATA_CONFIG, DATA_DIR, IMAGE_OUTPUT_DIR, INPUT_DIR,
                         MDV_ARCHIVE_DIR, MDV_INBOX_DIR, MDV_OUTPUT_DIR, OUTPUT_DIR,
                         RADAR_DEFINITIONS, RADARS, STATUS_FILE_PATH, alert_min_level_km)
from services.storm_tracking import Tracker

PRIMARY_RADAR_ID = "sur"


def _suffixed(path: str, radar_id: str) -> str:
    """'/app/data/input_scans/' -> '/app/data/input_scans_norte/'."""
    return path.rstrip("/") + f"_{radar_id}/"


@dataclass
class RadarContext:
    radar_id: str
    name: str                      # Para títulos y logs ("SAN_RAFAEL")
    sensor_latitude: float
    sensor_longitude: float
    mdv_inbox_dir: str
    mdv_archive_dir: str
    input_dir: str                 # Ventana de NC listos para el modelo
    archive_dir: str               # NC ya procesados
    output_dir: str                # NC de predicción
    mdv_output_dir: str            # MDV de predicción (los lee TITAN)
    image_dir: str                 # PNG + JSON para el mapa
    image_url_prefix: str          # "" para el sur, "norte/" para los demás (URL /images/<prefix><file>)
    status_file: str
    engagement_daily_file: str
    temp_work_dir: str             # Workspace de Mdv2NetCDF (uno por radar: no se pisan)
    temp_params_path: str          # Params de NcGeneric2Mdv rellenados (uno por radar)
    min_level_km: float = 5.0      # Filtro de ecos fijos: solo cuenta la reflectividad sobre esta altura (km MSL)
    data_config: dict = field(default_factory=dict)
    tracker: Tracker = field(default_factory=Tracker)
    engagement_state: dict = field(default_factory=lambda: {"first_detection_time": None})

    @property
    def is_primary(self) -> bool:
        return self.radar_id == PRIMARY_RADAR_ID

    def hail_swath_file(self, day_str: str) -> str:
        """Manga diaria. El sur conserva el nombre histórico hail_swath_<día>.json."""
        suffix = "" if self.is_primary else f"_{self.radar_id}"
        return os.path.join(DATA_DIR, f"hail_swath_{day_str}{suffix}.json")

    @property
    def cells_file(self) -> str:
        """Últimas celdas observadas por este radar (para alertas que miran todos los radares)."""
        return os.path.join(DATA_DIR, f"cells_latest_{self.radar_id}.json")

    def ensure_dirs(self) -> None:
        for path in (self.mdv_inbox_dir, self.mdv_archive_dir, self.input_dir, self.archive_dir,
                     self.output_dir, self.mdv_output_dir, self.image_dir):
            os.makedirs(path, exist_ok=True)

    def __str__(self) -> str:
        return f"radar {self.radar_id} ({self.name})"


def build_context(radar_id: str, name: str, sensor_latitude: float, sensor_longitude: float) -> RadarContext:
    """Arma el contexto. El radar primario usa las rutas históricas de core.config; los demás, sufijadas."""
    primary = radar_id == PRIMARY_RADAR_ID
    sfx = (lambda p: p) if primary else (lambda p: _suffixed(p, radar_id))
    data_config = copy.deepcopy(DATA_CONFIG)
    data_config["sensor_latitude"] = sensor_latitude
    data_config["sensor_longitude"] = sensor_longitude
    data_config["radar_title"] = f"{name}_PRED"
    status_dir = os.path.dirname(STATUS_FILE_PATH)
    return RadarContext(
        radar_id=radar_id,
        name=name,
        sensor_latitude=sensor_latitude,
        sensor_longitude=sensor_longitude,
        mdv_inbox_dir=sfx(MDV_INBOX_DIR),
        mdv_archive_dir=sfx(MDV_ARCHIVE_DIR),
        input_dir=sfx(INPUT_DIR),
        archive_dir=sfx(ARCHIVE_DIR),
        output_dir=sfx(OUTPUT_DIR),
        mdv_output_dir=sfx(MDV_OUTPUT_DIR),
        image_dir=IMAGE_OUTPUT_DIR if primary else os.path.join(IMAGE_OUTPUT_DIR, radar_id),
        image_url_prefix="" if primary else f"{radar_id}/",
        status_file=STATUS_FILE_PATH if primary else os.path.join(status_dir, f"status_{radar_id}.json"),
        engagement_daily_file=os.path.join(DATA_DIR, "last_daily_engagement.txt" if primary else f"last_daily_engagement_{radar_id}.txt"),
        temp_work_dir="/app/temp_conversion_workspace" if primary else f"/app/temp_conversion_workspace_{radar_id}",
        temp_params_path="/app/lrose_params/temp.params.final" if primary else f"/app/lrose_params/temp.params.{radar_id}.final",
        min_level_km=alert_min_level_km(radar_id),
        data_config=data_config,
    )


def get_radar_contexts() -> list:
    """Radares activos (RADARS en el entorno), el primario primero. Siempre incluye al sur."""
    return [build_context(rid, RADAR_DEFINITIONS[rid]["name"], RADAR_DEFINITIONS[rid]["lat"], RADAR_DEFINITIONS[rid]["lon"])
            for rid in RADARS]


def radar_info(ctx: RadarContext) -> dict:
    """Descripción pública del radar (para /api/radars)."""
    d = RADAR_DEFINITIONS[ctx.radar_id]
    return {"id": ctx.radar_id, "name": ctx.name, "label": d["label"], "lat": ctx.sensor_latitude,
            "lon": ctx.sensor_longitude, "is_primary": ctx.is_primary}
