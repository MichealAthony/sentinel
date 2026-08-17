"""
colour_mapper.py

Maps raw RGB values to the nearest named colour using perceptual distance.
Stores both the enum label and the raw RGB so:
  - UI can filter by human-readable colour name
  - DB retains full RGB precision for future use

Design decision: We use CIE Lab colour space for distance calculation
because Euclidean distance in RGB does not correlate well with how humans
perceive colour difference. Two colours that are numerically close in RGB
can look very different, and vice versa.
"""

from enum import Enum
import numpy as np


class VehicleColour(str, Enum):
    WHITE = "white"
    SILVER = "silver"
    GREY = "grey"
    BLACK = "black"
    RED = "red"
    MAROON = "maroon"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    DARK_GREEN = "dark_green"
    BLUE = "blue"
    DARK_BLUE = "dark_blue"
    BROWN = "brown"
    BEIGE = "beige"
    PURPLE = "purple"
    GOLD = "gold"
    UNKNOWN = "unknown"


# Reference RGB values for each named colour
# These are calibrated for vehicle colours under typical outdoor lighting
_COLOUR_REFERENCES: dict[VehicleColour, tuple[int, int, int]] = {
    VehicleColour.WHITE:      (255, 255, 255),
    VehicleColour.SILVER:     (192, 192, 192),
    VehicleColour.GREY:       (128, 128, 128),
    VehicleColour.BLACK:      (30,  30,  30),
    VehicleColour.RED:        (200, 30,  30),
    VehicleColour.MAROON:     (120, 20,  20),
    VehicleColour.ORANGE:     (230, 120, 30),
    VehicleColour.YELLOW:     (240, 220, 50),
    VehicleColour.GREEN:      (50,  180, 50),
    VehicleColour.DARK_GREEN: (20,  90,  20),
    VehicleColour.BLUE:       (50,  100, 200),
    VehicleColour.DARK_BLUE:  (20,  40,  120),
    VehicleColour.BROWN:      (130, 70,  40),
    VehicleColour.BEIGE:      (210, 190, 150),
    VehicleColour.PURPLE:     (120, 40,  160),
    VehicleColour.GOLD:       (200, 170, 50),
}


def _rgb_to_lab(rgb: tuple[int, int, int]) -> np.ndarray:
    """
    Convert RGB to CIE Lab for perceptual distance calculation.
    Uses the standard D65 illuminant reference.
    """
    # Normalise to 0-1
    r, g, b = [x / 255.0 for x in rgb]

    # Linearise (inverse sRGB gamma)
    def linearise(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = linearise(r), linearise(g), linearise(b)

    # RGB to XYZ (D65)
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505

    # Normalise by D65 reference white
    x, y, z = x / 0.95047, y / 1.00000, z / 1.08883

    # XYZ to Lab
    def f(t):
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + (16 / 116)

    L = 116 * f(y) - 16
    a = 500 * (f(x) - f(y))
    b_ = 200 * (f(y) - f(z))

    return np.array([L, a, b_])


# Pre-compute Lab values for all reference colours at import time
_REFERENCE_LAB: dict[VehicleColour, np.ndarray] = {
    colour: _rgb_to_lab(rgb)
    for colour, rgb in _COLOUR_REFERENCES.items()
}


def map_colour(rgb: tuple[int, int, int]) -> dict:
    """
    Map a raw RGB tuple to the nearest named VehicleColour.

    Returns a dict containing:
      - label: VehicleColour enum value (string, for DB storage and UI filtering)
      - rgb:   original RGB tuple (for DB precision)
      - distance: perceptual distance to nearest match (confidence indicator)

    Example:
      map_colour((210, 35, 35))
      → {"label": "red", "rgb": [210, 35, 35], "distance": 12.4}
    """
    if rgb is None:
        return {"label": VehicleColour.UNKNOWN, "rgb": None, "distance": None}

    query_lab = _rgb_to_lab(rgb)
    best_colour = VehicleColour.UNKNOWN
    best_distance = float("inf")

    for colour, ref_lab in _REFERENCE_LAB.items():
        distance = float(np.linalg.norm(query_lab - ref_lab))
        if distance < best_distance:
            best_distance = distance
            best_colour = colour

    return {
        "label": best_colour.value,
        "rgb": list(rgb),
        "distance": round(best_distance, 2)
    }


def map_colour_distribution(distribution: list[dict]) -> list[dict]:
    """
    Map a full colour distribution (list of {rgb, weight} dicts from k-means)
    to named colours. Used when storing the full colour profile of a vehicle
    rather than just the dominant colour.

    Example input:
      [
        {"rgb": [200, 30, 30], "weight": 0.6},
        {"rgb": [210, 200, 195], "weight": 0.3},
        {"rgb": [40, 35, 30], "weight": 0.1}
      ]
    """
    result = []
    for entry in distribution:
        mapped = map_colour(tuple(entry["rgb"]))
        result.append({
            "label": mapped["label"],
            "rgb": mapped["rgb"],
            "weight": entry["weight"],
            "distance": mapped["distance"]
        })
    return result
