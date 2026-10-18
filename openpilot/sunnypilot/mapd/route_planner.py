"""
Routes planned on the device with Valhalla, as routes of the comma-nav bundle.

The package and the routing tiles live in VALHALLA_DIR, outside the openpilot tree, so an update keeps them:
`site-packages/` holds pyvalhalla (pip install --target) and `valhalla_tiles/` the tiles comma-nav builds. Without
them the planner is unavailable and nothing changes. The trip conversion is comma-nav's bundle.leg_to_route;
keep the two in step.
"""
import importlib.util
import math
import os
import sys

from openpilot.common.hardware import PC

VALHALLA_DIR = os.path.expanduser("~/.comma/valhalla") if PC else "/data/valhalla"
HEADING_TOLERANCE = 60  # degrees either side of the car's heading that the first road may run
EARTH_RADIUS_M = 6371000.0

# Valhalla maneuver type -> (type, modifier) in the vocabulary of openpilot's NavInstruction.
MANEUVERS = {
  1: ("depart", None), 2: ("depart", "right"), 3: ("depart", "left"),
  4: ("arrive", None), 5: ("arrive", "right"), 6: ("arrive", "left"),
  7: ("new name", "straight"), 8: ("continue", "straight"),
  9: ("turn", "slight right"), 10: ("turn", "right"), 11: ("turn", "sharp right"),
  12: ("turn", "uturn"), 13: ("turn", "uturn"),
  14: ("turn", "sharp left"), 15: ("turn", "left"), 16: ("turn", "slight left"),
  17: ("on ramp", "straight"), 18: ("on ramp", "right"), 19: ("on ramp", "left"),
  20: ("off ramp", "right"), 21: ("off ramp", "left"),
  22: ("fork", "straight"), 23: ("fork", "right"), 24: ("fork", "left"),
  25: ("merge", "straight"), 37: ("merge", "right"), 38: ("merge", "left"),
  26: ("roundabout", None), 27: ("exit roundabout", None),
}
UNKNOWN_MANEUVER = ("notification", None)


class PlannerError(Exception):
  pass


def decode_shape(encoded: str) -> list[list[float]]:
  """Valhalla's encoded polyline (6 decimals) as [latitude, longitude] pairs."""
  points, index, lat, lon = [], 0, 0, 0
  while index < len(encoded):
    for is_lon in (False, True):
      shift = result = 0
      while True:
        byte = ord(encoded[index]) - 63
        index += 1
        result |= (byte & 0x1F) << shift
        shift += 5
        if byte < 0x20:
          break
      delta = ~(result >> 1) if result & 1 else result >> 1
      if is_lon:
        lon += delta
      else:
        lat += delta
    points.append([lat / 1e6, lon / 1e6])
  return points


def distance_m(a: list[float], b: list[float]) -> float:
  la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
  h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
  return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def cumulative_m(shape: list[list[float]]) -> list[float]:
  out = [0.0]
  for a, b in zip(shape, shape[1:], strict=False):
    out.append(out[-1] + distance_m(a, b))
  return out


def turn_deg(bearing_before: int | None, bearing_after: int | None) -> float:
  """Signed heading change, positive to the right. Zero when either bearing is unknown."""
  if bearing_before is None or bearing_after is None:
    return 0.0
  return float((bearing_after - bearing_before + 180) % 360 - 180)


def leg_to_route(leg: dict, route_id: str, label: str) -> dict:
  shape = decode_shape(leg["shape"])
  along = cumulative_m(shape)
  maneuvers = []
  for m in leg["maneuvers"]:
    kind, modifier = MANEUVERS.get(m["type"], UNKNOWN_MANEUVER)
    index = m["begin_shape_index"]
    # Valhalla reports due north as either 0 or 360.
    before, after = (None if b is None else b % 360 for b in (m.get("bearing_before"), m.get("bearing_after")))
    maneuvers.append({
      "shape_index": index,
      "distance_m": round(along[index], 1),
      "type": kind,
      "modifier": modifier,
      "bearing_before": before,
      "bearing_after": after,
      "turn_deg": turn_deg(before, after),
      "street": (m.get("street_names") or [""])[0],
      "exit": m.get("roundabout_exit_count"),
    })
  return {"route_id": route_id, "label": label, "length_m": round(along[-1], 1), "shape": shape, "maneuvers": maneuvers}


class Planner:
  """A Valhalla actor over the tiles in `base`, built on first use."""

  def __init__(self, base: str = VALHALLA_DIR):
    self.packages = os.path.join(base, "site-packages")
    self.tiles = os.path.join(base, "valhalla_tiles")
    self._actor = None

  def available(self) -> bool:
    return os.path.isdir(self.tiles) and (os.path.isdir(self.packages) or importlib.util.find_spec("valhalla") is not None)

  def _get_actor(self):
    if self._actor is None:
      if os.path.isdir(self.packages) and self.packages not in sys.path:
        sys.path.append(self.packages)
      import valhalla
      self._actor = valhalla.Actor(valhalla.get_config(tile_extract="", tile_dir=self.tiles))
    return self._actor

  def route(self, origin: tuple[float, float], heading: float | None, destination: tuple[float, float],
            route_id: str, label: str) -> dict:
    """The best route from the car, leaving along `heading` (degrees clockwise from north), to `destination`."""
    start = {"lat": origin[0], "lon": origin[1], "type": "break"}
    if heading is not None:
      start.update({"heading": round(heading) % 360, "heading_tolerance": HEADING_TOLERANCE})
    request = {"locations": [start, {"lat": destination[0], "lon": destination[1], "type": "break"}],
               "costing": "auto", "units": "kilometers"}
    try:
      trip = self._get_actor().route(request)["trip"]
    except Exception as e:  # valhalla.ValhallaError and RuntimeError: no route between the points
      raise PlannerError(f"no route: {e}") from e
    if len(trip["legs"]) != 1:
      raise PlannerError(f"expected one leg, got {len(trip['legs'])}")
    return leg_to_route(trip["legs"][0], route_id, label)
