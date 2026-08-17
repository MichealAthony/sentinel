"""
app/search/confidence.py

Overall match confidence scoring for search results.

This module computes a single confidence score [0.0, 1.0] for a search
result, derived from multiple evidence signals. The score is presented
to the operator so they can gauge how reliable the result is before
acting on it.

SIGNALS AND WEIGHTS
-------------------
The confidence score is a weighted combination of four signals:

1. Plate confidence (weight 0.40)
   A confirmed plate match is the strongest identity signal available.
   An unconfirmed or absent plate significantly reduces confidence.

2. Sighting count (weight 0.25)
   More sightings across more cameras means the identity engine has
   had more opportunities to confirm the vehicle. Saturates at 5+ sightings.

3. Cross-camera consistency (weight 0.20)
   The number of distinct cameras that have seen this vehicle.
   A vehicle seen on only one camera could be a misidentification.
   A vehicle seen on 3+ cameras is much more likely to be correctly identified.

4. Recency (weight 0.15)
   How recently the vehicle was last seen. A vehicle last seen 2 minutes
   ago is a more actionable result than one last seen 4 hours ago.
   Decays over a 2-hour window.

CONFIDENCE BANDS
----------------
  0.85 - 1.00  High confidence    — plate confirmed, multiple cameras
  0.65 - 0.84  Medium confidence  — some signals strong, others weak
  0.40 - 0.64  Low confidence     — limited evidence, treat with caution
  0.00 - 0.39  Very low           — insufficient evidence
"""

import time
import math
from typing import Optional


# Signal weights — must sum to 1.0
W_PLATE     = 0.40
W_SIGHTINGS = 0.25
W_CAMERAS   = 0.20
W_RECENCY   = 0.15

# Recency decay: confidence halves every RECENCY_HALF_LIFE_SECONDS
RECENCY_HALF_LIFE_SECONDS = 3600  # 1 hour


def score_plate(plate: Optional[str], plate_confirmed: bool) -> float:
    """Plate confidence signal [0.0, 1.0]."""
    if not plate:
        return 0.0
    if plate_confirmed:
        return 1.0
    # Plate present but not confirmed — partial credit
    return 0.4


def score_sightings(sighting_count: int) -> float:
    """Sighting count signal [0.0, 1.0]. Saturates at 5+."""
    if sighting_count == 0:
        return 0.0
    # Logarithmic growth — each additional sighting adds diminishing confidence
    return min(1.0, math.log(sighting_count + 1) / math.log(6))


def score_cameras(camera_count: int) -> float:
    """Cross-camera consistency signal [0.0, 1.0]. Saturates at 4+."""
    if camera_count == 0:
        return 0.0
    if camera_count == 1:
        return 0.35   # single camera — could be misidentification
    if camera_count == 2:
        return 0.70
    if camera_count == 3:
        return 0.90
    return 1.0         # 4+ cameras — very high identity confidence


def score_recency(last_known_timestamp: Optional[float]) -> float:
    """Recency signal [0.0, 1.0]. Decays exponentially over time."""
    if not last_known_timestamp:
        return 0.0
    age_seconds = time.time() - last_known_timestamp
    if age_seconds <= 0:
        return 1.0
    # Exponential decay: score = 0.5 ^ (age / half_life)
    return math.pow(0.5, age_seconds / RECENCY_HALF_LIFE_SECONDS)


def compute_confidence(
    plate: Optional[str],
    plate_confirmed: bool,
    sightings: list[dict],
    last_known_timestamp: Optional[float],
) -> dict:
    """
    Compute overall match confidence from available evidence signals.

    Returns a dict with the overall score and breakdown by signal,
    so the UI can show which signals are strong and which are weak.

    Example return:
    {
      "overall": 0.82,
      "band": "medium",
      "signals": {
        "plate":     {"score": 1.0,  "weight": 0.40, "note": "Confirmed plate match"},
        "sightings": {"score": 0.60, "weight": 0.25, "note": "3 confirmed sightings"},
        "cameras":   {"score": 0.70, "weight": 0.20, "note": "2 cameras"},
        "recency":   {"score": 0.88, "weight": 0.15, "note": "Last seen 8m ago"}
      }
    }
    """
    confirmed_sightings = [s for s in sightings if not s.get("inferred")]
    unique_cameras = len(set(s["camera_id"] for s in confirmed_sightings if s.get("camera_id")))

    s_plate     = score_plate(plate, plate_confirmed)
    s_sightings = score_sightings(len(confirmed_sightings))
    s_cameras   = score_cameras(unique_cameras)
    s_recency   = score_recency(last_known_timestamp)

    overall = (
        W_PLATE     * s_plate +
        W_SIGHTINGS * s_sightings +
        W_CAMERAS   * s_cameras +
        W_RECENCY   * s_recency
    )
    overall = round(overall, 3)

    if overall >= 0.85:
        band = "high"
    elif overall >= 0.65:
        band = "medium"
    elif overall >= 0.40:
        band = "low"
    else:
        band = "very_low"

    # Human-readable notes per signal
    age_seconds = (time.time() - last_known_timestamp) if last_known_timestamp else None
    if age_seconds is not None:
        if age_seconds < 60:
            recency_note = f"Last seen {int(age_seconds)}s ago"
        elif age_seconds < 3600:
            recency_note = f"Last seen {int(age_seconds // 60)}m ago"
        else:
            recency_note = f"Last seen {round(age_seconds / 3600, 1)}h ago"
    else:
        recency_note = "No recent sighting"

    return {
        "overall": overall,
        "band": band,
        "signals": {
            "plate": {
                "score": round(s_plate, 3),
                "weight": W_PLATE,
                "note": (
                    "Confirmed plate match" if plate_confirmed and plate
                    else "Plate present, not yet confirmed" if plate
                    else "No plate available"
                ),
            },
            "sightings": {
                "score": round(s_sightings, 3),
                "weight": W_SIGHTINGS,
                "note": f"{len(confirmed_sightings)} confirmed sighting{'s' if len(confirmed_sightings) != 1 else ''}",
            },
            "cameras": {
                "score": round(s_cameras, 3),
                "weight": W_CAMERAS,
                "note": f"{unique_cameras} camera{'s' if unique_cameras != 1 else ''}",
            },
            "recency": {
                "score": round(s_recency, 3),
                "weight": W_RECENCY,
                "note": recency_note,
            },
        }
    }
