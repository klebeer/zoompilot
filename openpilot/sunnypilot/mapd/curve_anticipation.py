"""
Map-gated steering anticipation: whether a curve from mapd's curvature points is close enough ahead that
the model should look further along its plan.

A longer lateral delay makes the model turn in earlier, which helped curves at 20-30 km/h, but applied
everywhere it also turns in earlier on straights and at walking pace. Gated by the map, the extra delay
would apply only from LEAD_S before a curve until the car is through it. mapd also knows curves the
camera cannot see yet, behind a crest or a building. Shadow only for now: nothing here changes steering.
"""
from dataclasses import dataclass

from openpilot.sunnypilot.mapd.curve_warning import MAX_BEARING_OFF_DEG, bearing_deg, distance_m

MIN_KPH, MAX_KPH = 15.0, 45.0
LEAD_S = 3.0             # start this long before the car reaches the curve
IN_CURVE_M = 15.0        # a point this close counts whatever its bearing: the car is in the curve
MIN_RADIUS_M = 15.0      # tighter points are mapping noise at junctions
MAX_RADIUS_M = 500.0     # straighter than this is not a curve for the steering
BOOST_S = 0.10           # extra lateral delay the rule would add


@dataclass(frozen=True)
class Anticipation:
  active: bool
  radius: float      # m, tightest qualifying point in the window, 0 when none
  distance: float    # m to the nearest qualifying point, -1 when none
  time_to: float     # s at the current speed, -1 when none


def anticipation(lat: float, lon: float, heading_deg: float, v_kph: float, curvatures: list[dict] | None) -> Anticipation:
  """Curve within LEAD_S ahead (or under the car) at a speed where anticipation would act."""
  none = Anticipation(False, 0.0, -1.0, -1.0)
  if not curvatures or not (MIN_KPH <= v_kph <= MAX_KPH):
    return none
  v = v_kph / 3.6
  window = v * LEAD_S + IN_CURVE_M
  nearest, tightest = None, None
  for p in curvatures:
    k = abs(p.get("curvature", 0.0))
    if k <= 0.0 or not (MIN_RADIUS_M <= 1.0 / k <= MAX_RADIUS_M):
      continue
    d = distance_m(lat, lon, p["latitude"], p["longitude"])
    if d > window:
      continue
    if d > IN_CURVE_M:
      off = abs((bearing_deg(lat, lon, p["latitude"], p["longitude"]) - heading_deg + 180) % 360 - 180)
      if off > MAX_BEARING_OFF_DEG:
        continue
    nearest = d if nearest is None else min(nearest, d)
    tightest = 1.0 / k if tightest is None else min(tightest, 1.0 / k)
  if nearest is None:
    return none
  return Anticipation(True, round(tightest, 1), round(nearest, 1), round(nearest / max(v, 0.1), 2))
