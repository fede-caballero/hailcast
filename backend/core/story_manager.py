import os
import json
import glob
import shutil
import logging
import subprocess
import threading
from datetime import datetime, timezone, timedelta

from core.config import (
    STORIES_MDV_DIR, STORIES_NC_CACHE_DIR, STORIES_PREVIEWS_DIR,
    STORY_STATE_FILE, STORY_FEED_INTERVAL_SECONDS, SECUENCE_LENGHT,
    IMAGE_OUTPUT_DIR
)

logger = logging.getLogger(__name__)


class StoryModeState:
    """
    Story Mode v2 — Manages the full lifecycle:
    1. List available storm days (from MDV folders)
    2. Start: batch-convert MDV → NC, track playback position
    3. Feed: advance position every STORY_FEED_INTERVAL_SECONDS
    4. Stop: mark for deferred NC cleanup (1 hour)
    5. Cleanup: delete expired NC caches
    """

    def _read_state(self):
        if not os.path.exists(STORY_STATE_FILE):
            return self._default_state()
        try:
            with open(STORY_STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return self._default_state()

    def _default_state(self):
        return {
            "is_active": False,
            "story_id": None,
            "current_index": 0,          # Position in the NC file sequence
            "total_frames": 0,           # Total NC files available
            "last_feed_time": None,      # When the last frame was fed
            "conversion_status": "idle", # idle | converting | ready | error
            "conversion_error": None,
            "worker_paused": False,      # If True, normal radar pipeline is paused
            "stop_scheduled_at": None,   # ISO timestamp: when NC cleanup should happen
            "started_at": None,
            "known_story_ids": [],       # All story_ids ever started — used to filter their PNGs out of live listings
        }

    def _write_state(self, state):
        os.makedirs(os.path.dirname(STORY_STATE_FILE), exist_ok=True)
        with open(STORY_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)

    # ------------------------------------------------------------------ #
    # List available storm days
    # ------------------------------------------------------------------ #
    def list_stories(self):
        """
        Scans STORIES_MDV_DIR for subdirectories in YYYYMMDD format.
        Returns metadata: id, formatted name, file count, time range.
        """
        if not os.path.exists(STORIES_MDV_DIR):
            os.makedirs(STORIES_MDV_DIR, exist_ok=True)
            return []

        folders = sorted([
            d for d in os.listdir(STORIES_MDV_DIR)
            if os.path.isdir(os.path.join(STORIES_MDV_DIR, d))
        ])

        stories = []
        for d in folders:
            folder_path = os.path.join(STORIES_MDV_DIR, d)
            mdv_files = sorted([f for f in os.listdir(folder_path) if f.endswith('.mdv')])
            if not mdv_files:
                continue

            # Parse date from folder name (YYYYMMDD)
            MONTHS_ES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
            MONTHS_EN = ["January","February","March","April","May","June",
                         "July","August","September","October","November","December"]
            try:
                date_obj = datetime.strptime(d, "%Y%m%d")
                m_idx = date_obj.month - 1
                name_es = f"{date_obj.day} de {MONTHS_ES[m_idx]} de {date_obj.year}"
                name_en = f"{MONTHS_EN[m_idx]} {date_obj.day}, {date_obj.year}"
            except ValueError:
                name_es = d
                name_en = d

            # Parse time range from first/last MDV file (HHMMSS.mdv)
            try:
                first_time = mdv_files[0].replace('.mdv', '')
                last_time = mdv_files[-1].replace('.mdv', '')
                first_formatted = f"{first_time[:2]}:{first_time[2:4]} UTC"
                last_formatted = f"{last_time[:2]}:{last_time[2:4]} UTC"
                time_range = f"{first_formatted} — {last_formatted}"
            except Exception:
                time_range = "?"

            stories.append({
                "id": d,
                "name": name_es,       # Default (backward compat)
                "name_es": name_es,     # Spanish
                "name_en": name_en,     # English
                "file_count": len(mdv_files),
                "time_range": time_range,
            })

        return stories

    # ------------------------------------------------------------------ #
    # Preview generation (permanent, for storm exploration)
    # ------------------------------------------------------------------ #
    def generate_previews(self, story_id):
        """
        Generates preview PNGs for all scans of a storm.
        Converts MDV → NC (temp) → PNG and stores in STORIES_PREVIEWS_DIR.
        These are PERMANENT and cached for future visits.
        Runs in a background thread.
        """
        mdv_folder = os.path.join(STORIES_MDV_DIR, story_id)
        if not os.path.exists(mdv_folder):
            raise FileNotFoundError(f"Storm folder not found: {mdv_folder}")

        mdv_files = sorted([f for f in os.listdir(mdv_folder) if f.endswith('.mdv')])
        if not mdv_files:
            raise ValueError(f"No MDV files in {mdv_folder}")

        preview_dir = os.path.join(STORIES_PREVIEWS_DIR, story_id)
        os.makedirs(preview_dir, exist_ok=True)

        # Check if previews already exist (count matching PNG files)
        existing_pngs = [f for f in os.listdir(preview_dir) if f.endswith('.png') and not f.endswith('_smoothed.png')]
        if len(existing_pngs) >= len(mdv_files):
            logger.info(f"[Story] Previews already cached for {story_id} ({len(existing_pngs)} files)")
            return {"status": "ready", "count": len(existing_pngs)}

        # Update state to show preview generation is in progress
        state = self._read_state()
        state["preview_status"] = "generating"
        state["preview_story_id"] = story_id
        self._write_state(state)

        # Launch in background
        thread = threading.Thread(
            target=self._batch_generate_previews,
            args=(story_id, mdv_folder, preview_dir, mdv_files),
            daemon=True
        )
        thread.start()

        return {"status": "generating", "count": 0, "total": len(mdv_files)}

    def _batch_generate_previews(self, story_id, mdv_folder, preview_dir, mdv_files):
        """
        Background thread: convert each MDV to NC, generate PNG preview, cleanup NC.
        """
        import tempfile
        params_path = "/app/lrose_params/Mdv2NetCDF.params"
        logger.info(f"[Story Preview] Starting preview generation for {story_id} ({len(mdv_files)} files)")

        for i, mdv_filename in enumerate(mdv_files):
            mdv_path = os.path.join(mdv_folder, mdv_filename)
            time_part = mdv_filename.replace('.mdv', '')
            nc_name = f"{story_id}{time_part}"
            preview_png_path = os.path.join(preview_dir, f"{nc_name}.png")

            # Skip if preview already exists
            if os.path.exists(preview_png_path):
                continue

            temp_work_dir = tempfile.mkdtemp(prefix="story_prev_")
            try:
                # 1. Convert MDV → NC
                command = ["Mdv2NetCDF", "-params", params_path, "-f", os.path.abspath(mdv_path)]
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=120, cwd=temp_work_dir)

                nc_files_found = glob.glob(os.path.join(temp_work_dir, "netCDF", "**", "*.nc"), recursive=True)
                if not nc_files_found:
                    nc_files_found = glob.glob(os.path.join(temp_work_dir, "netCDF", "*.nc"))

                if nc_files_found:
                    nc_path = nc_files_found[0]
                    # 2. Generate PNG using the worker's image function
                    from worker.main import generar_imagen_transparente_y_bounds
                    bounds, cells = generar_imagen_transparente_y_bounds(nc_path, preview_png_path, skip_levels=3)
                    if bounds:
                        import json as json_module
                        with open(f"{preview_png_path}.json", 'w') as f:
                            json_module.dump({"bounds": bounds, "cells": cells, "timestamp": nc_name}, f)
                    logger.info(f"[Story Preview] {i+1}/{len(mdv_files)}: {nc_name}.png")
                else:
                    logger.warning(f"[Story Preview] No NC output for {mdv_filename}")

            except Exception as e:
                logger.error(f"[Story Preview] Error generating preview for {mdv_filename}: {e}")
            finally:
                if os.path.exists(temp_work_dir):
                    shutil.rmtree(temp_work_dir, ignore_errors=True)

        # Update state
        state = self._read_state()
        state["preview_status"] = "ready"
        self._write_state(state)
        logger.info(f"[Story Preview] Done generating previews for {story_id}")

    def get_preview_list(self, story_id):
        """
        Returns list of available preview images for a storm.
        Each entry has: index, timestamp (YYYYMMDDHHMMSS), url, bounds.
        """
        preview_dir = os.path.join(STORIES_PREVIEWS_DIR, story_id)
        if not os.path.exists(preview_dir):
            return []

        png_files = sorted([f for f in os.listdir(preview_dir) if f.endswith('.png') and not f.endswith('_smoothed.png')])
        previews = []
        for i, f in enumerate(png_files):
            ts = os.path.splitext(f)[0]  # e.g. 20260331190530
            # Try to load bounds from JSON sidecar
            json_path = os.path.join(preview_dir, f"{f}.json")
            bounds = None
            if os.path.exists(json_path):
                try:
                    import json as json_module
                    with open(json_path, 'r') as jf:
                        data = json_module.load(jf)
                        bounds = data.get("bounds")
                except Exception:
                    pass

            # Format time for display
            try:
                time_str = f"{ts[8:10]}:{ts[10:12]} UTC"
            except Exception:
                time_str = ts

            previews.append({
                "index": i,
                "timestamp": ts,
                "time_display": time_str,
                "url": f"/api/story/preview-image/{story_id}/{f}",
                "bounds": bounds,
            })

        return previews

    # ------------------------------------------------------------------ #
    # Start story mode
    # ------------------------------------------------------------------ #
    def start(self, story_id, start_index=None):
        """
        Start story mode:
        1. Verify MDV folder exists
        2. Launch background conversion MDV → NC
        3. Initialize playback state at the given start_index (or beginning)
        """
        mdv_folder = os.path.join(STORIES_MDV_DIR, story_id)
        if not os.path.exists(mdv_folder):
            raise FileNotFoundError(f"Storm folder not found: {mdv_folder}")

        mdv_files = sorted([f for f in os.listdir(mdv_folder) if f.endswith('.mdv')])
        if len(mdv_files) < SECUENCE_LENGHT:
            raise ValueError(f"Need at least {SECUENCE_LENGHT} MDV files, found {len(mdv_files)}")

        # Calculate starting position
        if start_index is not None and start_index >= 0:
            # User selected a starting scan — model needs 8 frames, so
            # current_index = start_index + 7 (end of the first window)
            initial_index = min(start_index + SECUENCE_LENGHT - 1, len(mdv_files) - 1)
        else:
            initial_index = SECUENCE_LENGHT - 1  # Default: start from the beginning

        # Prepare NC cache directory — clear old cache to handle MDV changes
        nc_cache_dir = os.path.join(STORIES_NC_CACHE_DIR, story_id)
        if os.path.exists(nc_cache_dir):
            logger.info(f"[Story] Clearing old NC cache for {story_id}")
            shutil.rmtree(nc_cache_dir, ignore_errors=True)
        os.makedirs(nc_cache_dir, exist_ok=True)

        # Clear old prediction/input PNGs for this story so a re-run replays the
        # sliding window from scratch. Otherwise /api/images shows the highest
        # run_id (the previous run's last window) instead of the current window.
        if os.path.isdir(IMAGE_OUTPUT_DIR):
            removed = 0
            for f in os.listdir(IMAGE_OUTPUT_DIR):
                if story_id in f:
                    try:
                        os.remove(os.path.join(IMAGE_OUTPUT_DIR, f))
                        removed += 1
                    except OSError:
                        pass
            if removed:
                logger.info(f"[Story] Cleared {removed} old PNG/JSON files for {story_id}")

        prev_state = self._read_state()
        known_ids = list(prev_state.get("known_story_ids") or [])
        if story_id not in known_ids:
            known_ids.append(story_id)

        state = {
            "is_active": True,
            "story_id": story_id,
            "current_index": initial_index,
            "total_frames": len(mdv_files),
            "last_feed_time": None,
            "conversion_status": "converting",
            "conversion_error": None,
            "worker_paused": True,  # Auto-pause normal worker when story starts
            "stop_scheduled_at": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "known_story_ids": known_ids,
        }
        self._write_state(state)

        # Launch batch conversion in background thread
        thread = threading.Thread(
            target=self._batch_convert_mdv_to_nc,
            args=(story_id, mdv_folder, nc_cache_dir, mdv_files),
            daemon=True
        )
        thread.start()

        return state

    def _batch_convert_mdv_to_nc(self, story_id, mdv_folder, nc_cache_dir, mdv_files):
        """
        Background thread: converts all MDV files to NC using LROSE Mdv2NetCDF.
        Updates conversion_status in the state file as it progresses.
        """
        import tempfile

        params_path = "/app/lrose_params/Mdv2NetCDF.params"
        logger.info(f"[Story] Starting batch conversion of {len(mdv_files)} MDV files for {story_id}")

        converted_count = 0
        for mdv_filename in mdv_files:
            mdv_path = os.path.join(mdv_folder, mdv_filename)

            # Check if NC already exists (skip re-conversion)
            # MDV filename: HHMMSS.mdv → NC: YYYYMMDDHHMMSS.nc
            time_part = mdv_filename.replace('.mdv', '')
            expected_nc_name = f"{story_id}{time_part}.nc"
            expected_nc_path = os.path.join(nc_cache_dir, expected_nc_name)

            if os.path.exists(expected_nc_path):
                converted_count += 1
                logger.info(f"[Story] NC already exists, skipping: {expected_nc_name}")
                continue

            # Create temp workspace for LROSE (use cwd instead of os.chdir for thread safety)
            temp_work_dir = tempfile.mkdtemp(prefix="story_conv_")

            try:
                command = ["Mdv2NetCDF", "-params", params_path, "-f", os.path.abspath(mdv_path)]
                result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120, cwd=temp_work_dir)

                # Find generated NC file (LROSE may create subdirs like netCDF/g_HHMMSS/)
                nc_files_found = glob.glob(os.path.join(temp_work_dir, "netCDF", "**", "*.nc"), recursive=True)
                if not nc_files_found:
                    # Fallback: check directly in netCDF/
                    nc_files_found = glob.glob(os.path.join(temp_work_dir, "netCDF", "*.nc"))
                if nc_files_found:
                    created_nc = nc_files_found[0]
                    shutil.move(created_nc, expected_nc_path)
                    converted_count += 1
                    logger.info(f"[Story] Converted {mdv_filename} → {expected_nc_name} ({converted_count}/{len(mdv_files)})")
                else:
                    logger.warning(f"[Story] No NC output for {mdv_filename}")
            except subprocess.TimeoutExpired:
                logger.error(f"[Story] Timeout converting {mdv_filename}")
            except subprocess.CalledProcessError as e:
                logger.error(f"[Story] LROSE error for {mdv_filename}: {e.stderr[:500]}")
            except Exception as e:
                logger.error(f"[Story] Error converting {mdv_filename}: {e}")
            finally:
                if os.path.exists(temp_work_dir):
                    shutil.rmtree(temp_work_dir, ignore_errors=True)

        # Update state with final conversion result
        state = self._read_state()
        if state.get("story_id") == story_id:  # Only update if still the same story
            # Recount actual NC files
            actual_nc_files = sorted([f for f in os.listdir(nc_cache_dir) if f.endswith('.nc')])
            state["total_frames"] = len(actual_nc_files)

            if len(actual_nc_files) >= SECUENCE_LENGHT:
                state["conversion_status"] = "ready"
                logger.info(f"[Story] Conversion complete: {len(actual_nc_files)} NC files ready")
            else:
                state["conversion_status"] = "error"
                state["conversion_error"] = f"Only {len(actual_nc_files)} NC files converted (need {SECUENCE_LENGHT}+)"
                logger.error(f"[Story] Conversion failed: only {len(actual_nc_files)} files")

            self._write_state(state)

    # ------------------------------------------------------------------ #
    # Feed control (called by worker)
    # ------------------------------------------------------------------ #
    def should_advance(self):
        """
        Called by the worker loop. Returns True if it's time to advance
        to the next frame (based on STORY_FEED_INTERVAL_SECONDS).
        """
        state = self._read_state()
        if not state.get("is_active") or state.get("conversion_status") != "ready":
            return False

        last_feed = state.get("last_feed_time")
        if last_feed is None:
            return True  # First feed, go immediately

        try:
            last_dt = datetime.fromisoformat(last_feed)
            elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
            return elapsed >= STORY_FEED_INTERVAL_SECONDS
        except Exception:
            return True

    def advance_position(self):
        """
        Advances current_index by 1 and updates last_feed_time.
        Returns the new state, or None if story is finished.
        """
        state = self._read_state()
        new_index = state.get("current_index", SECUENCE_LENGHT - 1) + 1

        if new_index >= state.get("total_frames", 0):
            # Story finished
            logger.info("[Story] All frames processed. Auto-stopping.")
            self.stop()
            return None

        state["current_index"] = new_index
        state["last_feed_time"] = datetime.now(timezone.utc).isoformat()
        self._write_state(state)
        return state

    def get_current_nc_window(self):
        """
        Returns the list of NC file paths for the current 8-frame window.
        """
        state = self._read_state()
        story_id = state.get("story_id")
        if not story_id:
            return []

        nc_cache_dir = os.path.join(STORIES_NC_CACHE_DIR, story_id)
        if not os.path.exists(nc_cache_dir):
            return []

        all_nc = sorted([f for f in os.listdir(nc_cache_dir) if f.endswith('.nc')])
        current_idx = state.get("current_index", SECUENCE_LENGHT - 1)

        # Window: [current_idx - 7, current_idx] (8 files)
        start = max(0, current_idx - SECUENCE_LENGHT + 1)
        end = current_idx + 1
        window_files = all_nc[start:end]

        return [os.path.join(nc_cache_dir, f) for f in window_files]

    # ------------------------------------------------------------------ #
    # Stop and cleanup
    # ------------------------------------------------------------------ #
    def stop(self):
        """Stop story mode and schedule NC cleanup for 1 hour later."""
        state = self._read_state()
        cleanup_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        story_id = state.get("story_id")

        state["is_active"] = False
        state["conversion_status"] = "idle"
        state["stop_scheduled_at"] = cleanup_at
        state["worker_paused"] = False  # Auto-resume normal worker
        self._write_state(state)
        logger.info(f"[Story] Stopped. Worker resumed. NC cleanup scheduled for {cleanup_at}")

    def cleanup_expired_nc(self):
        """
        Check if deferred NC cleanup is due. Delete the NC cache folder
        but keep the original MDV files untouched.
        """
        state = self._read_state()
        cleanup_at = state.get("stop_scheduled_at")
        story_id = state.get("story_id")

        if not cleanup_at or not story_id:
            return

        if state.get("is_active"):
            return  # Don't cleanup while active

        try:
            cleanup_dt = datetime.fromisoformat(cleanup_at)
            if datetime.now(timezone.utc) >= cleanup_dt:
                nc_cache_dir = os.path.join(STORIES_NC_CACHE_DIR, story_id)
                if os.path.exists(nc_cache_dir):
                    shutil.rmtree(nc_cache_dir)
                    logger.info(f"[Story] Cleaned up NC cache for {story_id}")

                state["stop_scheduled_at"] = None
                state["story_id"] = None
                self._write_state(state)
        except Exception as e:
            logger.error(f"[Story] Cleanup error: {e}")

    # ------------------------------------------------------------------ #
    # Worker pause/resume
    # ------------------------------------------------------------------ #
    def pause_worker(self):
        """Pause the normal radar pipeline worker."""
        state = self._read_state()
        state["worker_paused"] = True
        self._write_state(state)
        logger.info("[Story] Normal worker PAUSED")

    def resume_worker(self):
        """Resume the normal radar pipeline worker."""
        state = self._read_state()
        state["worker_paused"] = False
        self._write_state(state)
        logger.info("[Story] Normal worker RESUMED")

    def is_worker_paused(self):
        """Check if the normal worker should be paused."""
        state = self._read_state()
        return state.get("worker_paused", False)

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def get_status(self):
        state = self._read_state()
        total = state.get("total_frames", 0)
        current = state.get("current_index", 0)

        progress = 0
        if total > 0:
            progress = round((current / (total - 1)) * 100, 1) if total > 1 else 100

        return {
            "is_active": state.get("is_active", False),
            "story_id": state.get("story_id"),
            "current_index": current,
            "total_frames": total,
            "progress_percent": progress,
            "conversion_status": state.get("conversion_status", "idle"),
            "conversion_error": state.get("conversion_error"),
            "worker_paused": state.get("worker_paused", False),
            "stop_scheduled_at": state.get("stop_scheduled_at"),
            "last_feed_time": state.get("last_feed_time"),
            "preview_status": state.get("preview_status", "idle"),
            "preview_story_id": state.get("preview_story_id"),
        }

    def get_known_story_ids(self):
        """All story_ids ever started — PNGs containing any of these in their filename
        belong to story mode and must be hidden from non-superadmin users."""
        return list(self._read_state().get("known_story_ids") or [])

    # ------------------------------------------------------------------ #
    # Legacy compatibility: aircraft data for story mode (kept as-is)
    # ------------------------------------------------------------------ #
    def get_story_aircraft_data(self):
        """Kept for backward compatibility. Returns empty for now in v2."""
        state = self._read_state()
        if not state.get("is_active"):
            return []
        # In v2, we don't simulate aircraft telemetry in story mode
        return []


# Global singleton
story_state = StoryModeState()
