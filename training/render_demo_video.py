#!/usr/bin/env python3
"""
Video de demo: observado vs. HailCast, lado a lado, desde los NetCDF de validación.

Para cada caso: los 8 scans de entrada (los dos paneles muestran lo observado; el derecho
indica que el modelo está "viendo"), y luego los 7 horizontes: izquierda lo que el radar
observó, derecha lo que HailCast predijo con los 8 scans anteriores. Misma paleta TITAN del
sistema en producción, mismo recorte espacial, reloj y anticipación en pantalla.

Uso:
  python training/render_demo_video.py --cases supercelda:20251226-182621 multicelda:20251213-230638 \
      --out training/results/demo --fps 1 --hold-pred 2
Produce out/frames/*.png, out/hailcast_demo.mp4 (1920x1080, h264) y out/hailcast_demo.gif (README).
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from pyproj import Proj

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from evaluate_cases import CASES, HORIZONS, N_INPUT, load_cases, load_dbz  # noqa: E402

RADAR_LON, RADAR_LAT = -68.01699829101562, -34.64799880981445   # grid_mapping de los NC
EXTENT_KM = 249.5                                                # grilla 500x500 de 1 km centrada en el radar
TITAN_BOUNDS = [5, 10, 20, 30, 35, 36, 39, 42, 45, 48, 51, 54, 57, 60, 65, 70, 80]
TITAN_COLORS = ['#483d8b', '#005a00', '#007000', '#087fdb', '#1c47e8', '#6e0dc6', '#c80f86', '#c06487',
                '#d2883b', '#fac431', '#fefa03', '#fe9a58', '#fe5f05', '#fd341c', '#bebebe', '#d3d3d3']
BG, PANEL, INK, MUTED, ACCENT, OBS_C, PRED_C = '#0b1220', '#101a2b', '#e9eef5', '#8b9bb0', '#36c5f0', '#ffd166', '#36c5f0'
TIPO_LABEL = {"supercelda": "Supercelda", "multicelda": "Multicelda", "adveccion": "Advección dominante", "debil": "Convección débil"}


def departamentos_km():
    """Contornos de departamentos de Mendoza proyectados a km (AEQD centrado en el radar)."""
    gj = REPO / "frontend" / "public" / "mendoza_departamentos.geojson"
    if not gj.exists():
        return []
    proj = Proj(proj="aeqd", lat_0=RADAR_LAT, lon_0=RADAR_LON, units="m")
    lines = []
    for feat in json.load(open(gj))["features"]:
        g = feat["geometry"]
        polys = g["coordinates"] if g["type"] == "Polygon" else [p for mp in g["coordinates"] for p in mp]
        for ring in polys:
            arr = np.array(ring[0] if isinstance(ring[0][0], (list, tuple)) else ring, dtype=float)
            x, y = proj(arr[:, 0], arr[:, 1])
            lines.append((np.asarray(x) / 1000.0, np.asarray(y) / 1000.0))
    return lines


def ts_from_name(p: Path):
    s = p.stem.replace("_", "")[-14:]
    return datetime.strptime(s, "%Y%m%d%H%M%S")


def crop_window(frames, pad_km=35, min_half=55, thr=40.0):
    """Ventana cuadrada (km) alrededor de la celda principal: componente conexa más grande
    (acumulada sobre todos los frames) de los ecos >= thr dBZ; el resto del campo no mueve el encuadre."""
    from scipy.ndimage import label
    mask = np.any(np.stack(frames) >= thr, axis=0)
    lab, n = label(mask)
    if n == 0:
        return (-EXTENT_KM, EXTENT_KM, -EXTENT_KM, EXTENT_KM)
    sizes = np.bincount(lab.ravel()); sizes[0] = 0
    keep = sizes >= max(1, 0.25 * sizes.max())          # la principal y las que la acompañan
    ys, xs = np.where(keep[lab])
    x = xs - 249.5; y = ys - 249.5
    cx, cy = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2
    half = max(min_half, (x.max() - x.min()) / 2 + pad_km, (y.max() - y.min()) / 2 + pad_km)
    half = min(half, EXTENT_KM)
    cx = float(np.clip(cx, -EXTENT_KM + half, EXTENT_KM - half)); cy = float(np.clip(cy, -EXTENT_KM + half, EXTENT_KM - half))
    return (cx - half, cx + half, cy - half, cy + half)


def draw_panel(ax, data, title, color, window, borders, cmap, norm, dim=False):
    ax.set_facecolor(PANEL)
    xs = np.arange(500) - 249.5
    if data is not None:
        ax.pcolormesh(xs, xs, data, cmap=cmap, norm=norm, shading="auto", alpha=0.35 if dim else 1.0, rasterized=True)
    for bx, by in borders:
        ax.plot(bx, by, color="#ffffff", lw=0.6, alpha=0.35)
    ax.plot([0], [0], marker="+", color="#ffffff", ms=10, mew=1.2, alpha=0.8)
    ax.set_xlim(window[0], window[1]); ax.set_ylim(window[2], window[3]); ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_edgecolor(color); s.set_linewidth(2)
    ax.set_title(title, color=color, fontsize=22, fontweight="bold", loc="left", pad=10, fontfamily="DejaVu Sans")
    # escala de 50 km
    x0, y0 = window[0] + 0.06 * (window[1] - window[0]), window[2] + 0.06 * (window[3] - window[2])
    ax.plot([x0, x0 + 50], [y0, y0], color="#ffffff", lw=2); ax.text(x0 + 25, y0 + 0.012 * (window[3] - window[2]), "50 km", color="#ffffff", ha="center", fontsize=11)


def render_case(case, out_dir, borders, cmap, norm, hold_in=1, hold_pred=2, frame_id=0):
    inputs = case["input_files"][-N_INPUT:]
    def post(a):
        a = a.copy(); a[a < 30.0] = -29.0; return a   # mismo umbral físico que el producto del modelo
    X = [post(load_dbz(f)) for f in inputs]
    Y = [post(load_dbz(f)) for f in case["truth_files"]]
    P = [post(load_dbz(f)) for f in case["pred_files"]]
    window = crop_window(X[-3:] + Y + P)
    t0 = ts_from_name(inputs[-1])
    tipo = TIPO_LABEL.get(case["tipo"], case["tipo"])
    fecha = (t0 - timedelta(hours=3)).strftime("%d/%m/%Y")
    seq = [("in", i, X[i], None) for i in range(N_INPUT)] + [("pred", k, Y[k], P[k]) for k in range(HORIZONS)]
    paths = []
    for kind, k, obs, pred in seq:
        fig = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor=BG)
        gs = fig.add_gridspec(2, 2, height_ratios=[1, 11], left=0.035, right=0.965, top=0.90, bottom=0.10, wspace=0.08, hspace=0.05)
        axL, axR = fig.add_subplot(gs[1, 0]), fig.add_subplot(gs[1, 1])
        if kind == "in":
            t = ts_from_name(inputs[k]) - timedelta(hours=3)
            draw_panel(axL, obs, "RADAR OBSERVADO", OBS_C, window, borders, cmap, norm)
            draw_panel(axR, obs, "HAILCAST · leyendo la secuencia", MUTED, window, borders, cmap, norm, dim=True)
            axR.text(0.5, 0.5, f"scan {k + 1} de {N_INPUT}\nel modelo todavía no predijo", transform=axR.transAxes, ha="center", va="center", color=INK, fontsize=20, alpha=0.9)
            sub = f"Entrada · scan {k + 1}/{N_INPUT} · {t:%H:%M} hora local"
        else:
            t = ts_from_name(case["truth_files"][k]) - timedelta(hours=3)
            lead = (ts_from_name(case["truth_files"][k]) - t0).total_seconds() / 60
            draw_panel(axL, obs, "RADAR OBSERVADO", OBS_C, window, borders, cmap, norm)
            draw_panel(axR, pred, f"HAILCAST · predicho {lead:.0f} min antes", PRED_C, window, borders, cmap, norm)
            sub = f"Pronóstico emitido a las {(t0 - timedelta(hours=3)):%H:%M} · válido {t:%H:%M} hora local · anticipación +{lead:.1f} min"
        fig.text(0.035, 0.955, f"{tipo} · {fecha} · radar San Rafael", color=INK, fontsize=26, fontweight="bold", va="center")
        fig.text(0.035, 0.918, sub, color=MUTED, fontsize=17, va="center")
        # barra de progreso de los 15 scans
        n_tot = N_INPUT + HORIZONS; idx = k if kind == "in" else N_INPUT + k
        for i in range(n_tot):
            x = 0.035 + i * (0.93 / n_tot)
            c = OBS_C if i < N_INPUT else PRED_C
            fig.patches.append(plt.Rectangle((x, 0.045), 0.93 / n_tot - 0.004, 0.018, transform=fig.transFigure, color=c, alpha=1.0 if i <= idx else 0.25))
        fig.text(0.035, 0.025, "8 scans de entrada", color=OBS_C, fontsize=12, va="center")
        fig.text(0.965, 0.025, "7 horizontes predichos · 25 min", color=PRED_C, fontsize=12, va="center", ha="right")
        fig.text(0.5, 0.025, "hail-cast-mendoza.com · ecos ≥ 30 dBZ en ambos paneles · paleta TITAN", color=MUTED, fontsize=12, va="center", ha="center")
        reps = hold_in if kind == "in" else hold_pred
        for _ in range(reps):
            p = out_dir / f"frame_{frame_id:04d}.png"; fig.savefig(p, facecolor=BG); paths.append(p); frame_id += 1
        plt.close(fig)
    return paths, frame_id


def title_card(text, sub, out_dir, frame_id, reps=2):
    fig = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor=BG)
    fig.text(0.5, 0.56, text, color=INK, fontsize=44, fontweight="bold", ha="center", va="center")
    fig.text(0.5, 0.46, sub, color=MUTED, fontsize=22, ha="center", va="center")
    paths = []
    for _ in range(reps):
        p = out_dir / f"frame_{frame_id:04d}.png"; fig.savefig(p, facecolor=BG); paths.append(p); frame_id += 1
    plt.close(fig)
    return paths, frame_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="+", required=True, help="tipo:pred_subdir, p. ej. supercelda:20251226-182621")
    ap.add_argument("--stories-nc", default=str(REPO / "data/validation_cases/stories_nc"))
    ap.add_argument("--predictions", default=str(REPO / "data/validation_cases/output_predictions"))
    ap.add_argument("--out", default=str(REPO / "training/results/demo"))
    ap.add_argument("--fps", type=int, default=1, help="frames por segundo del mp4 (1 = cada frame dura 1 s)")
    ap.add_argument("--hold-in", type=int, default=1, help="repeticiones de cada scan de entrada")
    ap.add_argument("--hold-pred", type=int, default=2, help="repeticiones de cada horizonte predicho")
    ap.add_argument("--gif", action="store_true", help="además, un GIF reducido para el README")
    ap.add_argument("--cases-spec", default=str(REPO / "training/results/baseline_ampliado/casos.csv"), help="manifiesto .csv o carpeta de casos (como --cases de baseline_persistencia.py); vacío = CASES fijo")
    a = ap.parse_args()

    out = Path(a.out); frames = out / "frames"; frames.mkdir(parents=True, exist_ok=True)
    for f in frames.glob("frame_*.png"):
        f.unlink()
    cmap = ListedColormap(TITAN_COLORS); cmap.set_under("none"); norm = BoundaryNorm(TITAN_BOUNDS, cmap.N)
    borders = departamentos_km()
    wanted = {c.split(":")[1]: c.split(":")[0] for c in a.cases}
    from evaluate_cases import iter_cases
    spec = a.cases_spec or None
    all_cases = load_cases(spec)[0] if spec else CASES
    found = {c["pred_subdir"]: c for c in iter_cases(a.stories_nc, a.predictions, cases=all_cases) if c["pred_subdir"] in wanted}
    fid = 0
    fid = title_card("HailCast", "Nowcasting de tormentas con deep learning · radar San Rafael, Mendoza", frames, fid, reps=2)[1]
    for sub, tipo in wanted.items():
        case = found.get(sub)
        if case is None:
            print(f"[aviso] caso {sub} no encontrado en CASES/iter_cases; se omite"); continue
        case["tipo"] = tipo
        fid = title_card(TIPO_LABEL.get(tipo, tipo), f"{sub[:4]}-{sub[4:6]}-{sub[6:8]} · 8 scans de entrada → 7 horizontes", frames, fid, reps=1)[1]
        _, fid = render_case(case, frames, borders, cmap, norm, a.hold_in, a.hold_pred, fid)
    fid = title_card("hail-cast-mendoza.com", "Código abierto: github.com/fede-caballero/hailcast", frames, fid, reps=3)[1]
    mp4 = out / "hailcast_demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(a.fps), "-i", str(frames / "frame_%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(mp4)], check=True)
    print(f"mp4: {mp4} · {fid} frames · {fid / a.fps:.0f} s")
    if a.gif:
        gif = out / "hailcast_demo.gif"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(a.fps), "-i", str(frames / "frame_%04d.png"),
                        "-vf", "fps=2,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer",
                        str(gif)], check=True)
        print(f"gif: {gif}")


if __name__ == "__main__":
    main()
