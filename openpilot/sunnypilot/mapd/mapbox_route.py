"""
A Mapbox Directions route in the forms the rest of the car uses.

navd asks Mapbox for the route and follows its steps. From those steps come the lanes and the list of maneuvers
ahead for navInstruction, and a route bundle in comma-nav's format with that single route chosen, so route_guide
follows a Mapbox route as it follows one the phone sends: the turn desire and the turn yield keep their route.

The token is the driver's own. It is read from MAPBOX_TOKEN or from TOKEN_PATH and never stored anywhere else.
"""
import hashlib
import os
from datetime import UTC, datetime, timedelta

from openpilot.common.hardware import PC
from openpilot.sunnypilot.mapd.route_follower import SCHEMA_VERSION, make_choice, stamp
from openpilot.sunnypilot.mapd.route_choice import write_json
from openpilot.sunnypilot.mapd.route_planner import cumulative_m, turn_deg

TOKEN_PATH = os.path.expanduser("~/.comma/nav/mapbox_token") if PC else "/data/nav/mapbox_token"
# apart from comma-nav's bundle and choice, which stay as the phone left them
BUNDLE_PATH = os.path.expanduser("~/.comma/nav/mapbox_bundle.json") if PC else "/data/nav/mapbox_bundle.json"
CHOICE_PATH = os.path.expanduser("~/.comma/nav/mapbox_chosen.json") if PC else "/data/nav/mapbox_chosen.json"
BUNDLE_HOURS = 24.0
ROUTE_LABEL = "Mapbox"

# Mapbox lane indications -> NavInstruction.Direction. The enum has no sharp turn or U-turn of its own.
LANE_DIRECTIONS = {
  "left": "left", "sharp left": "left", "uturn": "left", "slight left": "slightLeft",
  "right": "right", "sharp right": "right", "slight right": "slightRight", "straight": "straight",
}
# Maneuver types the follower does not know, as the nearest one it does.
MANEUVER_TYPES = {"end of road": "turn", "rotary": "roundabout", "exit rotary": "exit roundabout", "roundabout turn": "turn"}


def read_token(path: str = TOKEN_PATH) -> str | None:
  token = os.environ.get("MAPBOX_TOKEN")
  if token:
    return token
  try:
    with open(path) as f:
      return f.read().strip() or None
  except OSError:
    return None


def banner_lanes(banner: dict) -> list[dict]:
  """The lane strip of a banner instruction as NavInstruction.Lane fields, left to right. Empty without lane data."""
  lanes = []
  for component in (banner.get("sub") or {}).get("components") or []:
    if component.get("type") != "lane":
      continue
    directions = [LANE_DIRECTIONS.get(d, "none") for d in component.get("directions") or []]
    active = bool(component.get("active"))
    lanes.append({"directions": directions, "active": active,
                  "activeDirection": LANE_DIRECTIONS.get(component.get("active_direction"), directions[0] if directions else "none")})
  return lanes


def maneuvers_ahead(steps: list[dict], step_idx: int, to_step_end: float) -> list[dict]:
  """The maneuvers past the current step as NavInstruction.Maneuver fields. A step's maneuver is where it begins,
  so the next one is `to_step_end` metres ahead."""
  out, distance = [], to_step_end
  for step in steps[step_idx + 1:]:
    maneuver = step.get("maneuver") or {}
    out.append({"distance": float(distance), "type": maneuver.get("type") or "", "modifier": maneuver.get("modifier") or ""})
    distance += step["distance"]
  return out


def to_route(steps: list[dict], route_id: str) -> dict:
  """The steps of one Mapbox leg as a route of the bundle. Mapbox gives longitude first; the bundle latitude first."""
  shape: list[list[float]] = []
  maneuvers = []
  for step in steps:
    points = [[lat, lon] for lon, lat in step["geometry"]["coordinates"]]
    # consecutive steps share the point where they meet, and a step's maneuver is at its first point
    index = len(shape) - 1 if shape and points and shape[-1] == points[0] else len(shape)
    for point in points:
      if not shape or shape[-1] != point:
        shape.append(point)
    index = min(index, len(shape) - 1)
    maneuver = step.get("maneuver") or {}
    before, after = maneuver.get("bearing_before"), maneuver.get("bearing_after")
    kind = maneuver.get("type") or "notification"
    # the first and the last maneuver have no road on one side: Mapbox reports that bearing as 0
    turning = kind not in ("depart", "arrive")
    maneuvers.append({
      "shape_index": index,
      "type": MANEUVER_TYPES.get(kind, kind),
      "modifier": maneuver.get("modifier"),
      "bearing_before": before if turning else None,
      "bearing_after": after if turning else None,
      "turn_deg": turn_deg(before, after) if turning else 0.0,
      "street": step.get("name") or "",
      "exit": maneuver.get("exit"),
    })
  along = cumulative_m(shape)
  for maneuver in maneuvers:
    maneuver["distance_m"] = round(along[maneuver["shape_index"]], 1)
  return {"route_id": route_id, "label": ROUTE_LABEL, "length_m": round(along[-1], 1), "shape": shape, "maneuvers": maneuvers}


def to_bundle(steps: list[dict], now: datetime | None = None) -> tuple[dict, dict]:
  """(bundle, choice) for route_guide: the Mapbox route alone, already chosen. A new route gets a new id."""
  now = now or datetime.now(UTC)
  route = to_route(steps, "mapbox")
  digest = hashlib.sha256(repr(route["shape"]).encode()).hexdigest()[:12]
  bundle_id = f"mapbox-{digest}"
  bundle = {"schema": SCHEMA_VERSION, "bundle_id": bundle_id, "generated_at": stamp(now),
            "expires_at": stamp(now + timedelta(hours=BUNDLE_HOURS)), "places": [], "routes": [route]}
  return bundle, make_choice(bundle_id, route["route_id"], "device", now)


def publish(steps: list[dict], bundle_path: str | None = None, choice_path: str | None = None) -> None:
  """Hands a new route to route_guide. The choice goes last: it names the bundle it was made for."""
  bundle_path, choice_path = bundle_path or BUNDLE_PATH, choice_path or CHOICE_PATH
  bundle, choice = to_bundle(steps)
  os.makedirs(os.path.dirname(bundle_path), exist_ok=True)
  write_json(bundle, bundle_path)
  write_json(choice, choice_path)


def withdraw(bundle_path: str | None = None, choice_path: str | None = None) -> None:
  """No route any more: route_guide stops following once the files are gone."""
  bundle_path, choice_path = bundle_path or BUNDLE_PATH, choice_path or CHOICE_PATH
  for path in (choice_path, bundle_path):
    try:
      os.remove(path)
    except OSError:
      pass
