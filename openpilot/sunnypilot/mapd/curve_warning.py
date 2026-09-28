"""
Tight curve ahead, from mapd's curvature points and the car's position.

On the CX-5 without MRCC the EPS cannot make curves of a few metres radius at any practical
speed; mapd sees most of them about 10 s ahead. This is the rule a warning would use.
"""
import math
from dataclasses import dataclass

LOOKAHEAD_M = 100.0
MIN_RADIUS_M = 45.0
MAX_BEARING_OFF_DEG = 90.0
EARTH_RADIUS_M = 6371000.0


@dataclass(frozen=True)
class CurveAhead:
  distance: float  # m from the car to the tightest point ahead
  radius: float    # m
  latitude: float
  longitude: float


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  la1, lo1, la2, lo2 = map(math.radians, (lat1, lon1, lat2, lon2))
  h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
  return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  la1, lo1, la2, lo2 = map(math.radians, (lat1, lon1, lat2, lon2))
  y = math.sin(lo2 - lo1) * math.cos(la2)
  x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
  return math.degrees(math.atan2(y, x)) % 360


def tight_curve_ahead(lat: float, lon: float, heading_deg: float, curvatures: list[dict],
                      lookahead_m: float = LOOKAHEAD_M, min_radius_m: float = MIN_RADIUS_M) -> CurveAhead | None:
  """Tightest mapd curvature point ahead within lookahead_m whose radius is below min_radius_m."""
  best = None
  for p in curvatures:
    k = abs(p.get("curvature", 0.0))
    if k <= 0.0 or 1.0 / k >= min_radius_m:
      continue
    plat, plon = p["latitude"], p["longitude"]
    d = distance_m(lat, lon, plat, plon)
    if d > lookahead_m:
      continue
    off = abs((bearing_deg(lat, lon, plat, plon) - heading_deg + 180) % 360 - 180)
    if off > MAX_BEARING_OFF_DEG:
      continue
    if best is None or 1.0 / k < best.radius:
      best = CurveAhead(distance=d, radius=1.0 / k, latitude=plat, longitude=plon)
  return best
