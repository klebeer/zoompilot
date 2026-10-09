"""The routes the driver can choose from on the device, and the choice itself.

comma-nav puts the route bundle at BUNDLE_PATH; the routes of saved trips carry the trip's name, and
routes that end at a saved place carry it in to_place. The driver picks one of them for this trip, on
the comma's screen (from a trip, or by where to go) or in comma-nav's page, and the choice goes to
CHOICE_PATH (contract: comma-nav's route-choice.schema.json). curve_shadow follows it.
"""
import json
import os

from openpilot.common.hardware import PC
from openpilot.sunnypilot.mapd.route_follower import choice_holds, distance_m, make_choice, usable

BUNDLE_PATH = os.path.expanduser("~/.comma/nav/route_bundle.json") if PC else "/data/nav/route_bundle.json"
CHOICE_PATH = os.path.expanduser("~/.comma/nav/chosen.json") if PC else "/data/nav/chosen.json"
ORIGIN_M = 400.0   # a route starts where the car is when its first point is this close


def read_json(path: str) -> dict | None:
  try:
    with open(path) as f:
      content = json.load(f)
  except (OSError, ValueError):
    return None
  return content if isinstance(content, dict) else None


def write_json(content: dict, path: str) -> None:
  """Written under another name and renamed, so a reader never sees half of it."""
  tmp = f"{path}.tmp"
  with open(tmp, "w") as f:
    json.dump(content, f, separators=(",", ":"))
  os.replace(tmp, path)


def load_bundle(path: str = BUNDLE_PATH) -> dict | None:
  """The bundle, or None when there is none the device should follow."""
  bundle = read_json(path)
  return bundle if bundle is not None and usable(bundle) else None


def trips(bundle: dict | None) -> list[tuple[str, list[dict]]]:
  """The saved trips in the bundle, in its order, each with its routes."""
  out: dict[str, list[dict]] = {}
  for route in (bundle or {}).get("routes", []):
    if route.get("trip"):
      out.setdefault(route["trip"], []).append(route)
  return list(out.items())


def places(bundle: dict | None) -> list[dict]:
  """The saved places some route of the bundle goes to, in the bundle's order."""
  reached = {r.get("to_place") for r in (bundle or {}).get("routes", [])}
  return [p for p in (bundle or {}).get("places", []) if p["name"] in reached]


def routes_to(bundle: dict | None, place: str, here: tuple[float, float] | None = None) -> list[dict]:
  """The routes to a saved place. Knowing where the car is, those that start near it, nearest first, when any does."""
  routes = [r for r in (bundle or {}).get("routes", []) if r.get("to_place") == place]
  if here is None:
    return routes
  near = sorted(((distance_m(here, tuple(r["shape"][0])), i) for i, r in enumerate(routes)))
  return [routes[i] for d, i in near if d <= ORIGIN_M] or routes


def streets(route: dict) -> str:
  """The streets a route takes, as its label gives them."""
  return route["label"].removeprefix(f"{route['trip']}: ") if route.get("trip") else route["label"]


def chosen_route(bundle: dict | None, path: str = CHOICE_PATH) -> dict | None:
  """The route chosen for this trip, while the choice holds."""
  choice = read_json(path)
  if bundle is None or not choice_holds(choice, bundle):
    return None
  return next(r for r in bundle["routes"] if r["route_id"] == choice["route_id"])


def choose(bundle: dict, route_id: str, path: str = CHOICE_PATH) -> None:
  write_json(make_choice(bundle["bundle_id"], route_id, "device"), path)


def clear(path: str = CHOICE_PATH) -> None:
  try:
    os.remove(path)
  except FileNotFoundError:
    pass
