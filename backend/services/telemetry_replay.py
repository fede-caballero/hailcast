"""
Story mode aircraft telemetry replay.

A background thread polls the active story state and injects historical SPDB
aircraft positions into the live aircraft_tracker at the story's accelerated
radar clock. The full day's points are loaded once via SpdbQuery, parsed and
cached in memory; the thread then ingests them at ~1.5s real intervals so the
map marker animates smoothly instead of jumping at frame advances.

Expected layout per story:
    STORIES_MDV_DIR/<story_id>/spdb/ac_posn/<story_id>.data
    STORIES_MDV_DIR/<story_id>/spdb/ac_posn/<story_id>.indx
"""

import os
import time
import logging
import threading
import subprocess
from typing import Optional
from datetime import datetime, timezone, timedelta

from core.config import STORIES_MDV_DIR, STORIES_NC_CACHE_DIR, STORY_FEED_INTERVAL_SECONDS
from services import aircraft_tracker

logger = logging.getLogger(__name__)

SPDB_QUERY_BIN = "/usr/local/lrose/bin/SpdbQuery"

# Hail-suppression fleet — same filter used by check_and_send_aircraft_alerts.
FLEET_CALLSIGNS = {"VBCR", "VBCT", "VBCU"}

CALLSIGN_TO_REG = {
    "VBCR": "LV-BCR",
    "VBCT": "LV-BCT",
    "VBCU": "LV-BCU",
}

# Argentine sanity bounds.
LAT_MIN, LAT_MAX = -55.0, -20.0
LON_MIN, LON_MAX = -75.0, -50.0

# Replay loop tuning.
REPLAY_TICK_SECONDS = 1.5      # how often the thread wakes to ingest points
INITIAL_LOOKBACK_SECONDS = 15  # radar-time lookback on first iteration of a story

# No in-memory cache: SpdbQuery is fast (~50ms) and reliable on narrow windows,
# but returns 0 chars silently when the interval spans days without a .data file.
# The replay loop queries per iteration with small windows (~7.5s of radar time).


def get_spdb_dir(story_id: str) -> str:
    return os.path.join(STORIES_MDV_DIR, story_id, "spdb", "ac_posn")


def has_telemetry(story_id: str) -> bool:
    """True if a .data file exists for the story."""
    spdb_dir = get_spdb_dir(story_id)
    if not os.path.isdir(spdb_dir):
        return False
    return any(f.endswith(".data") for f in os.listdir(spdb_dir))


# ---------------------------------------------------------------------------
# SpdbQuery + parser
# ---------------------------------------------------------------------------

def _fmt_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y %m %d %H %M %S")


def _run_spdb_query(spdb_dir: str, t_start: datetime, t_end: datetime) -> str:
    cmd = [
        SPDB_QUERY_BIN,
        "-url", spdb_dir,
        "-mode", "interval",
        "-start", _fmt_time(t_start),
        "-end", _fmt_time(t_end),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.stderr:
            print(f"[Telemetry-DBG] SpdbQuery stderr (rc={result.returncode}): {result.stderr[:500]}", flush=True)
        return result.stdout
    except FileNotFoundError:
        logger.error(f"[Telemetry] SpdbQuery not found at {SPDB_QUERY_BIN}")
        return ""
    except subprocess.TimeoutExpired:
        logger.warning("[Telemetry] SpdbQuery timeout")
        return ""
    except Exception as e:
        logger.error(f"[Telemetry] SpdbQuery error: {e}")
        return ""


def _parse_spdb_output(output: str) -> list[dict]:
    """
    Parse the SpdbQuery text output for ac_posn_wmod_t blocks.

    Block shape:
        valid_time: 2026/02/22 19:52:14
        callsign: VBCT
          lat:  -34.59010
          lon:  -68.40450
          alt: 2461.00000
          gs: 0
          headingDeg: 0
    """
    aircraft = []
    valid_time_str = ""
    callsign = None
    lat = lon = alt_ft = gs_kt = heading = None

    for raw in output.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()

        if key == "valid_time":
            valid_time_str = val
        elif key == "callsign":
            callsign = val
            lat = lon = alt_ft = gs_kt = heading = None
        elif key == "lat":
            try: lat = float(val)
            except ValueError: pass
        elif key == "lon":
            try: lon = float(val)
            except ValueError: pass
        elif key == "alt":
            try: alt_ft = float(val)
            except ValueError: pass
        elif key == "gs":
            try: gs_kt = float(val)
            except ValueError: gs_kt = 0.0
        elif key == "headingdeg":
            try: heading = float(val)
            except ValueError: heading = 0.0
            if (callsign in FLEET_CALLSIGNS
                    and lat is not None and lon is not None
                    and alt_ft is not None
                    and LAT_MIN <= lat <= LAT_MAX
                    and LON_MIN <= lon <= LON_MAX):
                aircraft.append({
                    "callsign": callsign,
                    "reg":      CALLSIGN_TO_REG.get(callsign, callsign),
                    "lat":      round(lat, 6),
                    "lon":      round(lon, 6),
                    "altitude": round(alt_ft * 0.3048),
                    "velocity": round((gs_kt or 0.0) * 0.514444, 2),
                    "heading":  round(heading or 0.0),
                    "valid_time": valid_time_str,
                })
            callsign = lat = lon = alt_ft = gs_kt = heading = None

    return aircraft


# ---------------------------------------------------------------------------
# Full-day cache
# ---------------------------------------------------------------------------

def _inject_from_cache(story_id: str, t_start: datetime, t_end: datetime) -> int:
    """
    Query SpdbQuery for the small (t_start, t_end] window and inject all
    fleet points found. No pre-caching: SpdbQuery returned 0 chars on
    multi-day intervals, so we stick to narrow windows that we know work.
    """
    spdb_dir = get_spdb_dir(story_id)
    output = _run_spdb_query(spdb_dir, t_start, t_end)
    if not output:
        return 0
    points = _parse_spdb_output(output)
    n = 0
    for p in points:
        try:
            t = datetime.strptime(p["valid_time"], "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
        # SpdbQuery interval bounds can be loose; tighten to exact window.
        if not (t_start < t <= t_end):
            continue
        try:
            aircraft_tracker.update_local_aircraft(p)
            n += 1
        except Exception as e:
            logger.warning(f"[Telemetry] inject failed for {p.get('callsign')}: {e}")
    return n


# ---------------------------------------------------------------------------
# Virtual radar clock (interpolates between frame advances)
# ---------------------------------------------------------------------------

def _list_nc_files(story_id: str) -> list[str]:
    cache_dir = os.path.join(STORIES_NC_CACHE_DIR, story_id)
    if not os.path.isdir(cache_dir):
        return []
    return sorted(f for f in os.listdir(cache_dir) if f.endswith(".nc"))


def _radar_interval_seconds(story_id: str) -> float:
    files = _list_nc_files(story_id)
    if len(files) < 2:
        return 300.0
    try:
        stem0 = os.path.splitext(files[0])[0]
        stem1 = os.path.splitext(files[1])[0]
        t0 = datetime.strptime(stem0, "%Y%m%d%H%M%S")
        t1 = datetime.strptime(stem1, "%Y%m%d%H%M%S")
        delta = (t1 - t0).total_seconds()
        if 60 <= delta <= 900:
            return delta
    except Exception:
        pass
    return 300.0


def _frame_time(story_id: str, index: int) -> Optional[datetime]:
    files = _list_nc_files(story_id)
    if not files or index < 0 or index >= len(files):
        return None
    try:
        return datetime.strptime(
            os.path.splitext(files[index])[0], "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _virtual_radar_time(status: dict) -> Optional[datetime]:
    """
    Translate real wallclock into the story's virtual radar clock by
    interpolating between the last frame advance and the next one.
    """
    story_id = status.get("story_id")
    current_index = status.get("current_index")
    if not story_id or current_index is None:
        return None

    T_current = _frame_time(story_id, current_index)
    if T_current is None:
        return None

    last_feed_iso = status.get("last_feed_time")
    if not last_feed_iso:
        return T_current  # haven't advanced yet — sit at current frame's time

    try:
        last_feed = datetime.fromisoformat(last_feed_iso)
        if last_feed.tzinfo is None:
            last_feed = last_feed.replace(tzinfo=timezone.utc)
    except Exception:
        return T_current

    radar_interval = _radar_interval_seconds(story_id)
    accel = radar_interval / STORY_FEED_INTERVAL_SECONDS
    elapsed_real = (datetime.now(timezone.utc) - last_feed).total_seconds()
    # Cap to avoid runaway extrapolation if worker stalls.
    elapsed_real = max(0.0, min(elapsed_real, STORY_FEED_INTERVAL_SECONDS * 1.5))
    return T_current + timedelta(seconds=elapsed_real * accel)


# ---------------------------------------------------------------------------
# Background replay thread
# ---------------------------------------------------------------------------

def _dbg(msg: str) -> None:
    """Diagnostic print that bypasses logging config — always reaches stdout."""
    print(f"[Telemetry-DBG] {msg}", flush=True)


def _replay_loop():
    last_injected: Optional[datetime] = None
    last_story_id: Optional[str] = None
    iter_count = 0
    _dbg("Replay loop started")

    while True:
        try:
            iter_count += 1
            from core.story_manager import story_state
            status = story_state.get_status()
            is_active = status.get("is_active")
            story_id = status.get("story_id")
            conv = status.get("conversion_status")

            # Heartbeat: ~once a minute, prove the thread is alive + state.
            if iter_count % 40 == 1:
                li_str = last_injected.isoformat() if last_injected else "None"
                _dbg(f"iter={iter_count} active={is_active} story_id={story_id} conv={conv} last_injected={li_str}")

            if not is_active:
                last_injected = None
                last_story_id = None
                time.sleep(3)
                continue

            if not story_id or not has_telemetry(story_id):
                time.sleep(3)
                continue

            if conv != "ready":
                time.sleep(2)
                continue

            if story_id != last_story_id:
                last_injected = None
                last_story_id = story_id
                _dbg(f"new story id={story_id}")

            virtual_now = _virtual_radar_time(status)
            if virtual_now is None:
                _dbg(f"virtual_now=None ci={status.get('current_index')} lft={status.get('last_feed_time')}")
                time.sleep(2)
                continue

            if last_injected is None:
                last_injected = virtual_now - timedelta(seconds=INITIAL_LOOKBACK_SECONDS)
                _dbg(f"first inject: virtual_now={virtual_now.isoformat()} from={last_injected.isoformat()}")

            if virtual_now > last_injected:
                n = _inject_from_cache(story_id, last_injected, virtual_now)
                if n > 0:
                    _dbg(f"injected {n} pts window {last_injected.strftime('%H:%M:%S')}-{virtual_now.strftime('%H:%M:%S')}")
                last_injected = virtual_now

            time.sleep(REPLAY_TICK_SECONDS)

        except Exception as e:
            import traceback
            _dbg(f"loop EXCEPTION: {type(e).__name__}: {e}")
            traceback.print_exc()
            time.sleep(5)


_replay_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()


def start_replay_thread() -> None:
    """Idempotent — spawns the replay thread on first call."""
    global _replay_thread
    with _thread_lock:
        if _replay_thread and _replay_thread.is_alive():
            return
        _replay_thread = threading.Thread(
            target=_replay_loop, daemon=True, name="telemetry_replay"
        )
        _replay_thread.start()


# Auto-start when the module is imported.
start_replay_thread()


# ---------------------------------------------------------------------------
# Backward-compatible synchronous injection (kept for tests/manual use)
# ---------------------------------------------------------------------------

def inject_window(story_id: str, t_start: datetime, t_end: datetime) -> int:
    """Inject points in [t_start, t_end] from cache. No-op if no telemetry."""
    if not has_telemetry(story_id):
        return 0
    n = _inject_from_cache(story_id, t_start, t_end)
    if n:
        logger.info(
            f"[Telemetry] Injected {n} points for {story_id} "
            f"window {t_start.strftime('%H:%M:%S')}–{t_end.strftime('%H:%M:%S')} UTC"
        )
    return n
