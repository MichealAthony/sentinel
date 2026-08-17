"""
vehicle_index.py

Defines the VehicleIndex — the structured output record the pipeline
produces per detection. This is the contract the DB team writes against.

Design decisions:
- schema_version field on every record so the DB team can handle
  records produced by different versions of the pipeline gracefully
- colour stored as both RGB and named label so UI can filter by name
  while DB retains full precision
- additional_features is an open dict — new extractors write here
  without changing the schema. DB team stores it as JSONB.
- embeddings stored under named keys so reid and clip embeddings
  coexist and new embedding types can be added without breaking old records
"""

import time
from dataclasses import dataclass, field, asdict
from typing import Optional

# Increment this when the schema changes in a breaking way
SCHEMA_VERSION = 1


@dataclass
class VehicleIndex:
    # ---- Identity ----
    vehicle_id: str
    schema_version: int = SCHEMA_VERSION

    # ---- Sighting context ----
    timestamp: float = field(default_factory=time.time)
    camera_id: str = ""
    bbox: Optional[list[int]] = None          # [x1, y1, x2, y2]

    # ---- Plate ----
    plate: Optional[str] = None
    plate_confidence: float = 0.0
    plate_confirmed: bool = False             # True once vote count >= threshold

    # ---- Vehicle attributes ----
    vehicle_type: Optional[str] = None        # car, truck, bus, motorcycle

    # Colour stored as both label and RGB
    # label: human readable name for UI filtering (e.g. "red", "dark_blue")
    # rgb:   raw BGR tuple from k-means for DB precision
    # distribution: full k-means colour profile [{label, rgb, weight, distance}]
    colour_label: Optional[str] = None
    colour_rgb: Optional[list[int]] = None
    colour_distribution: Optional[list[dict]] = None

    # ---- Embeddings ----
    # Stored under named keys so new embedding types can be added
    # without changing the schema. Old records simply won't have new keys.
    # reid_v1: OSNet ReID embedding (512-dim) for cross-camera tracking
    # clip_v1: CLIP image embedding (512-dim) for open-ended visual search
    embeddings: dict = field(default_factory=dict)

    # ---- Image ----
    image_path: Optional[str] = None         # path to saved vehicle crop

    # ---- Extension point ----
    # New feature extractors write here without changing the schema.
    # DB team stores this as JSONB.
    # Example entries added by future extractors:
    #   "make_model": {"make": "Toyota", "model": "Corolla", "confidence": 0.82}
    #   "damage":     {"detected": True, "regions": ["front_bumper"]}
    #   "decal":      {"detected": False}
    #   "occupants":  {"count": 2}
    additional_features: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """
        Serialise to a plain dict for JSON transmission or DB insertion.
        embeddings are converted to lists (not numpy arrays).
        """
        d = asdict(self)
        # Ensure embeddings are plain lists not numpy arrays
        for key in d.get("embeddings", {}):
            val = d["embeddings"][key]
            if val is not None and hasattr(val, "tolist"):
                d["embeddings"][key] = val.tolist()
        return d


def build_vehicle_index(
    vehicle_id: str,
    timestamp: float,
    camera_id: str,
    bbox: list[int],
    plate: Optional[str],
    plate_confidence: float,
    plate_confirmed: bool,
    vehicle_type: Optional[str],
    colour_label: Optional[str],
    colour_rgb: Optional[list[int]],
    colour_distribution: Optional[list[dict]],
    reid_embedding=None,
    clip_embedding=None,
    image_path: Optional[str] = None,
    additional_features: Optional[dict] = None
) -> VehicleIndex:
    """
    Factory function for building a VehicleIndex from pipeline outputs.
    Centralises all construction logic so VehicleWorker stays clean.
    """
    embeddings = {}
    if reid_embedding is not None:
        embeddings["reid_v1"] = (
            reid_embedding.tolist()
            if hasattr(reid_embedding, "tolist")
            else reid_embedding
        )
    if clip_embedding is not None:
        embeddings["clip_v1"] = (
            clip_embedding.tolist()
            if hasattr(clip_embedding, "tolist")
            else clip_embedding
        )

    return VehicleIndex(
        vehicle_id=vehicle_id,
        timestamp=timestamp,
        camera_id=camera_id,
        bbox=bbox,
        plate=plate,
        plate_confidence=plate_confidence,
        plate_confirmed=plate_confirmed,
        vehicle_type=vehicle_type,
        colour_label=colour_label,
        colour_rgb=colour_rgb,
        colour_distribution=colour_distribution,
        embeddings=embeddings,
        image_path=image_path,
        additional_features=additional_features or {}
    )
