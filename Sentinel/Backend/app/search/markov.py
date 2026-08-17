"""
app/search/markov.py

Markov Chain transition matrix for vehicle route prediction.

DESIGN
------
The transition matrix P[i][j] = P(next camera = j | current camera = i)
is estimated from observed vehicle trajectories using maximum likelihood:

    P(j | i) = count(i -> j) / count(i -> any camera)

GLOBAL vs PER-VEHICLE
---------------------
Per-vehicle data is almost always too sparse for reliable prediction —
a single vehicle may only appear on 3-4 cameras before being lost.
The global matrix aggregates transitions across ALL vehicles, giving
a statistically reliable estimate of movement patterns.

The predictor uses both:
1. Per-vehicle observed transitions (if the vehicle has been seen
   moving between cameras, honour those specific paths)
2. Global matrix as a prior (smoothing) and fallback when per-vehicle
   data is absent or sparse

PERSISTENCE
-----------
Counts are persisted to a JSON file (transition_counts.json) so the
matrix survives process restarts and accumulates over time.
The DB team can optionally migrate this to a PostgreSQL table by
implementing client.save_transition_counts and client.load_transition_counts.

TOPOLOGY PRIOR
--------------
At cold start (no observed data), predictions fall back to a topology
prior derived from camera coordinates. Closer cameras get higher prior
weight. This ensures the system gives reasonable predictions from day one.
"""

import json
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from app.utils.geo import haversine_metres

TRANSITION_FILE = Path(__file__).parent / "transition_counts.json"

# Whether to also persist counts to DB (requires client implementation)
PERSIST_TRANSITIONS_TO_DB = False

# Minimum observations before per-vehicle data is trusted over global prior
MIN_VEHICLE_OBSERVATIONS = 3

# Laplace smoothing alpha — adds this count to every transition to prevent
# zero probability for unobserved but physically possible routes
LAPLACE_ALPHA = 0.1

# Maximum distance (metres) for topology prior — cameras beyond this
# distance from the current camera get zero prior weight
TOPOLOGY_MAX_RADIUS_METRES = 2000



class TransitionMatrix:
    """
    Thread-safe Markov transition matrix that:
    - accumulates global counts from observed trajectories
    - supports per-vehicle transition history
    - falls back to topology prior at cold start
    - persists to JSON and optionally to DB
    """

    def __init__(self):
        # Global counts: {from_cam: {to_cam: count}}
        self._global_counts: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()
        self._load()

    # =========================================================
    # PERSISTENCE
    # =========================================================
    def _load(self):
        if TRANSITION_FILE.exists():
            try:
                with open(TRANSITION_FILE) as f:
                    self._global_counts = json.load(f)
                print(f"[TransitionMatrix] Loaded counts from {TRANSITION_FILE}")
            except Exception as e:
                print(f"[TransitionMatrix] Failed to load counts: {e}")
                self._global_counts = {}
        else:
            self._global_counts = {}

    def _persist(self):
        try:
            with open(TRANSITION_FILE, "w") as f:
                json.dump(self._global_counts, f, indent=2)
        except Exception as e:
            print(f"[TransitionMatrix] Failed to persist counts: {e}")

    # =========================================================
    # RECORD OBSERVED TRANSITION
    # =========================================================
    def record(self, from_camera: str, to_camera: str):
        """
        Record an observed vehicle transition from_camera -> to_camera.
        Call this whenever a vehicle is confirmed to have moved between cameras.
        Thread-safe.
        """
        if from_camera == to_camera:
            return
        with self._lock:
            if from_camera not in self._global_counts:
                self._global_counts[from_camera] = {}
            self._global_counts[from_camera][to_camera] = (
                self._global_counts[from_camera].get(to_camera, 0) + 1
            )
            self._persist()

    def record_trajectory(self, sightings: list[dict]):
        """
        Record all transitions implied by a sighting sequence.
        sightings: list of {camera_id, timestamp} ordered by timestamp.
        """
        for i in range(len(sightings) - 1):
            from_cam = sightings[i].get("camera_id")
            to_cam = sightings[i + 1].get("camera_id")
            if from_cam and to_cam and from_cam != to_cam:
                self.record(from_cam, to_cam)

    # =========================================================
    # PREDICT NEXT CAMERAS
    # =========================================================
    def predict(
        self,
        from_camera: str,
        cameras: dict[str, dict],
        vehicle_sightings: Optional[list[dict]] = None,
        top_k: int = 5,
    ) -> list[dict]:
        """
        Predict the top_k most probable next cameras given the current camera.

        Combines three sources with weighted interpolation:
        1. Per-vehicle observed transitions (if sufficient observations)
        2. Global transition matrix (accumulated across all vehicles)
        3. Topology prior (inverse distance, cold start fallback)

        cameras: dict of camera metadata keyed by camera_id
                 each entry must have lat and lng
        vehicle_sightings: optional list of this vehicle's sightings
                           used to build per-vehicle transitions
        top_k: number of predictions to return

        Returns:
        [
          {
            "camera_id": "CAM_03",
            "label": "South Exit",
            "lat": 18.01,
            "lng": -76.80,
            "probability": 0.62
          },
          ...
        ]
        """
        all_camera_ids = [cid for cid in cameras if cid != from_camera]
        if not all_camera_ids:
            return []

        # --- Source 1: per-vehicle transitions ---
        vehicle_counts: dict[str, int] = {}
        vehicle_total = 0
        if vehicle_sightings and len(vehicle_sightings) >= MIN_VEHICLE_OBSERVATIONS:
            for i in range(len(vehicle_sightings) - 1):
                if vehicle_sightings[i].get("camera_id") == from_camera:
                    to_cam = vehicle_sightings[i + 1].get("camera_id")
                    if to_cam and to_cam != from_camera:
                        vehicle_counts[to_cam] = vehicle_counts.get(to_cam, 0) + 1
                        vehicle_total += 1

        # --- Source 2: global matrix with Laplace smoothing ---
        global_counts = {}
        with self._lock:
            global_counts = dict(self._global_counts.get(from_camera, {}))
        global_total = sum(global_counts.values()) + LAPLACE_ALPHA * len(all_camera_ids)

        # --- Source 3: topology prior ---
        current_cam = cameras.get(from_camera, {})
        topology_scores: dict[str, float] = {}
        topology_total = 0.0
        if current_cam.get("lat") and current_cam.get("lng"):
            for cid in all_camera_ids:
                other = cameras.get(cid, {})
                if not other.get("lat") or not other.get("lng"):
                    continue
                dist = haversine_metres(
                    current_cam["lat"], current_cam["lng"],
                    other["lat"], other["lng"]
                )
                if dist <= TOPOLOGY_MAX_RADIUS_METRES and dist > 0:
                    # Inverse distance weight — closer cameras more likely
                    score = 1.0 / dist
                    topology_scores[cid] = score
                    topology_total += score

        # Normalise topology
        if topology_total > 0:
            topology_scores = {k: v / topology_total for k, v in topology_scores.items()}

        # --- Combine sources ---
        # Weights: per-vehicle gets more weight if it has data,
        # topology dominates at cold start
        has_vehicle_data = vehicle_total >= MIN_VEHICLE_OBSERVATIONS
        has_global_data = global_total > LAPLACE_ALPHA * len(all_camera_ids)
        has_topology = bool(topology_scores)

        if has_vehicle_data and has_global_data:
            w_vehicle, w_global, w_topology = 0.5, 0.35, 0.15
        elif has_vehicle_data:
            w_vehicle, w_global, w_topology = 0.6, 0.0, 0.4
        elif has_global_data:
            w_vehicle, w_global, w_topology = 0.0, 0.65, 0.35
        else:
            w_vehicle, w_global, w_topology = 0.0, 0.0, 1.0

        scores: dict[str, float] = {}
        for cid in all_camera_ids:
            # Per-vehicle probability
            p_vehicle = (vehicle_counts.get(cid, 0) / vehicle_total) if vehicle_total > 0 else 0.0

            # Global probability with Laplace smoothing
            p_global = (
                (global_counts.get(cid, 0) + LAPLACE_ALPHA)
                / global_total
            ) if global_total > 0 else 0.0

            # Topology prior
            p_topology = topology_scores.get(cid, 0.0)

            scores[cid] = (
                w_vehicle * p_vehicle
                + w_global * p_global
                + w_topology * p_topology
            )

        # Normalise final scores to sum to 1
        total_score = sum(scores.values())
        if total_score > 0:
            scores = {k: v / total_score for k, v in scores.items()}

        # Sort and return top_k
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]

        result = []
        for cid, prob in ranked:
            if prob < 0.01:
                continue
            cam_meta = cameras.get(cid, {})
            result.append({
                "camera_id": cid,
                "label": cam_meta.get("label"),
                "lat": cam_meta.get("lat"),
                "lng": cam_meta.get("lng"),
                "probability": round(prob, 3),
            })

        return result

    # =========================================================
    # DIAGNOSTICS
    # =========================================================
    def summary(self) -> dict:
        with self._lock:
            total_transitions = sum(
                sum(v.values()) for v in self._global_counts.values()
            )
            return {
                "cameras_with_outgoing_data": len(self._global_counts),
                "total_observed_transitions": total_transitions,
                "file": str(TRANSITION_FILE),
            }


# Module-level singleton — imported by pipeline.py and routes
transition_matrix = TransitionMatrix()
