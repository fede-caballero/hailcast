"""
Seguimiento simple de celdas de tormenta entre scans consecutivos.

Para cada celda del scan actual busca la celda más cercana del scan anterior
(dentro de ``MAX_MATCH_KM``) y estima dirección y velocidad de desplazamiento.
Es deliberadamente simple: alcanza para el campo "Desplazamiento" de las alertas
y para saber si una tormenta se acerca o se aleja de un punto.

Solo debe alimentarse con scans OBSERVADOS (no con predicciones del modelo).
"""
import math
from datetime import datetime

MAX_MATCH_KM = 15.0     # Más lejos que esto, no se considera la misma celda
MIN_GAP_MINUTES = 1.0   # Menos que esto es el mismo scan reprocesado
MAX_GAP_MINUTES = 20.0  # Más que esto, se perdió la continuidad

CARDINALES = ["Norte", "Noreste", "Este", "Sureste", "Sur", "Suroeste", "Oeste", "Noroeste"]



def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2) -> float:
    """Rumbo (0..360, 0 = Norte) desde el punto 1 hacia el punto 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    x = math.sin(dlmb) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlmb)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def cardinal(deg) -> str:
    if deg is None:
        return "sin dato"
    return CARDINALES[int(round(deg / 45.0)) % 8]


class Tracker:
    """Estado del seguimiento de UN radar (cada contexto de radar tiene el suyo)."""

    def __init__(self):
        self.cells = []
        self.time = None

    def update(self, cells: list, scan_time: datetime) -> list:
        """Devuelve copias de ``cells`` con ``motion_deg``, ``motion_dir`` y ``speed_kmh`` (None si no se pudo estimar).

        Actualiza el estado interno con este scan, salvo que sea el mismo scan repetido.
        """
        prev_cells, prev_time = self.cells, self.time
        gap_min = None
        if prev_time is not None and scan_time is not None:
            gap_min = (scan_time - prev_time).total_seconds() / 60.0

        enriched = []
        for cell in cells:
            c = dict(cell)
            c.setdefault("motion_deg", None)
            c.setdefault("motion_dir", None)
            c.setdefault("speed_kmh", None)
            if gap_min is not None and MIN_GAP_MINUTES <= gap_min <= MAX_GAP_MINUTES and prev_cells:
                best, best_d = None, MAX_MATCH_KM
                for p in prev_cells:
                    d = haversine_km(p["lat"], p["lon"], c["lat"], c["lon"])
                    if d < best_d:
                        best, best_d = p, d
                if best is not None:
                    if best_d >= 0.5:
                        c["motion_deg"] = round(bearing_deg(best["lat"], best["lon"], c["lat"], c["lon"]), 1)
                        c["motion_dir"] = cardinal(c["motion_deg"])
                        c["speed_kmh"] = round(best_d / (gap_min / 60.0), 1)
                    else:
                        # Prácticamente quieta: dirección indefinida, velocidad ~0
                        c["motion_dir"] = "estacionaria"
                        c["speed_kmh"] = 0.0
            enriched.append(c)

        if gap_min is None or gap_min >= MIN_GAP_MINUTES:
            self.cells = [dict(c) for c in enriched]
            self.time = scan_time
        return enriched

    def reset(self) -> None:
        self.cells, self.time = [], None


# Tracker por defecto (compatibilidad con código que usaba las funciones del módulo).
_default = Tracker()


def update(cells: list, scan_time: datetime) -> list:
    return _default.update(cells, scan_time)


def reset() -> None:
    _default.reset()
