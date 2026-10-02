"""
Shadow run of the tight-curve warning: records where it would fire, never alerts.

Runs onroad. While openpilot steers at MIN_KPH-MAX_KPH it applies three rules: the map's
tight-curve rule on the latest mapd curvature points, a list of curves learned from this driver's
own logs (KNOWN_CURVES_PATH, written by commaia mapd/known_hard_curves.py), and the turn ahead on
the route the car is following (BUNDLE_PATH, written by comma-nav). It appends one JSON line per
would-be warning to LOG_DIR, stamped with time.monotonic_ns() so it joins the rlog on logMonoTime,
and records which rules fired so they can be compared offline. None alerts.

The route rule needs a bundle. Without one, or with one that has expired, the other two rules run
as before. What the route follower saw goes to NAV_LOG_DIR as well, free of the warning's holdoff:
each change of state and each turn it announced, so the follower can be scored on its own.
"""
import json
import math
import os
import time

import openpilot.cereal.messaging as messaging
from openpilot.common.hardware import PC
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd.curve_warning import LOOKAHEAD_M, MIN_RADIUS_M, CurveAhead, distance_m, known_curve_ahead, tight_curve_ahead
from openpilot.sunnypilot.mapd.route_follower import Follower, usable

SHM = "/dev/shm/params/d"
KNOWN_CURVES_PATH = os.path.expanduser("~/.comma/known_hard_curves.json") if PC else "/data/known_hard_curves.json"
LOG_DIR = os.path.expanduser("~/.comma/curve_shadow") if PC else "/data/media/0/curve_shadow"
BUNDLE_PATH = os.path.expanduser("~/.comma/nav/route_bundle.json") if PC else "/data/nav/route_bundle.json"
NAV_LOG_DIR = os.path.expanduser("~/.comma/nav_shadow") if PC else "/data/media/0/nav_shadow"
MIN_KPH, MAX_KPH = 10.0, 60.0
HOLDOFF_S = 20.0            # at most one would-be warning every 20 s
SAME_CURVE_HOLDOFF_S = 60.0  # and once per curve
SAME_CURVE_M = 30.0
ROUTE_TURN_DEG = 45.0        # a maneuver on the route counts as a turn from this heading change
ROUTE_ALWAYS = ("roundabout", "exit roundabout")  # their heading change at entry and exit is small
FOLLOW_STEP_M = 2.0          # the follower is fed a fix every this far, not every message
SAME_TURN_S = 120.0          # a turn announced again after this long is another pass by it


def _read_json(key: str):
  try:
    with open(os.path.join(SHM, key)) as f:
      raw = f.read()
    return json.loads(raw) if raw else None
  except (OSError, ValueError):
    return None


def load_known_curves(path: str = KNOWN_CURVES_PATH) -> list[dict]:
  """The learned curve list, or an empty list when it is absent or unreadable."""
  try:
    with open(path) as f:
      curves = json.load(f)
    return [c for c in curves if "latitude" in c and "longitude" in c]
  except (OSError, ValueError, TypeError):
    return []


def load_follower(path: str = BUNDLE_PATH) -> Follower | None:
  """A follower for the route bundle, or None when there is no bundle the device should follow."""
  try:
    with open(path) as f:
      bundle = json.load(f)
  except (OSError, ValueError):
    return None
  return Follower(bundle) if usable(bundle) else None


def route_turns_ahead(nav: dict | None) -> list[dict]:
  """The turns and roundabouts within LOOKAHEAD_M on the routes the car is on, nearest first."""
  if nav is None or nav["state"] != "following":
    return []
  return [n for n in nav["ahead"]
          if n["distance_m"] <= LOOKAHEAD_M and (abs(n["turn_deg"]) >= ROUTE_TURN_DEG or n["type"] in ROUTE_ALWAYS)]


def describe_turn(n: dict) -> dict:
  return {"route_id": n["route_id"], "type": n["type"], "modifier": n["modifier"], "turn_deg": n["turn_deg"],
          "street": n["street"], "distance_m": n["distance_m"]}


class Shadow:
  def __init__(self, known_curves: list[dict] | None = None):
    self.last_warn_t = -1e9
    self.last_warn_point = None
    self.known_curves = known_curves if known_curves is not None else load_known_curves()

  def step(self, now: float, lat_active: bool, v_kph: float, lat: float, lon: float, heading: float, curvatures, road,
           nav: dict | None = None):
    """The record to write when a rule fires on a new curve, else None."""
    if not lat_active or not (MIN_KPH <= v_kph <= MAX_KPH):
      return None
    turns = route_turns_ahead(nav)
    turn = CurveAhead(distance=turns[0]["distance_m"], radius=0.0, latitude=turns[0]["lat"], longitude=turns[0]["lon"]) if turns else None
    # The map and learned rules stay as they were: silent while mapd has no curvature points.
    hit = tight_curve_ahead(lat, lon, heading, curvatures) if curvatures else None
    learned = known_curve_ahead(lat, lon, heading, self.known_curves) if curvatures else None
    fired = [(name, h) for name, h in (("map", hit), ("learned", learned), ("route", turn)) if h is not None]
    if not fired:
      return None
    # The nearest leads the record; "rules" says which would have fired on their own.
    rules = [name for name, _ in fired]
    hit = min((h for _, h in fired), key=lambda h: h.distance)
    if now - self.last_warn_t < HOLDOFF_S:
      return None
    if self.last_warn_point is not None and now - self.last_warn_t < SAME_CURVE_HOLDOFF_S and \
       distance_m(hit.latitude, hit.longitude, *self.last_warn_point) < SAME_CURVE_M:
      return None
    self.last_warn_t = now
    self.last_warn_point = (hit.latitude, hit.longitude)
    rec = {"lat": lat, "lon": lon, "heading": round(heading, 1), "v_kph": round(v_kph, 1),
           "distance_m": round(hit.distance, 1), "radius_m": round(hit.radius, 1),
           "curve_lat": hit.latitude, "curve_lon": hit.longitude, "road": road, "rules": rules,
           "rule": {"lookahead_m": LOOKAHEAD_M, "min_radius_m": MIN_RADIUS_M, "kph": [MIN_KPH, MAX_KPH],
                    "known_curves": len(self.known_curves), "route_turn_deg": ROUTE_TURN_DEG}}
    if turns:
      rec["route"] = describe_turn(turns[0])
    return rec


class NavLog:
  """What the route follower saw, apart from the warning: each change of state and each turn announced."""

  def __init__(self):
    self.state = None
    self.announced: dict[tuple[float, float], float] = {}

  def records(self, now: float, nav: dict, lat: float, lon: float, v_kph: float, lat_active: bool) -> list[dict]:
    out = []
    state = (nav["state"], nav["route_id"])
    if state != self.state:
      self.state = state
      out.append({"kind": "state", "state": nav["state"], "route_id": nav["route_id"], "progress_m": nav["progress_m"],
                  "lat": lat, "lon": lon})
    for n in route_turns_ahead(nav):
      place = (n["lat"], n["lon"])
      if now - self.announced.get(place, -1e9) >= SAME_TURN_S:
        out.append({"kind": "turn", **describe_turn(n), "turn_lat": n["lat"], "turn_lon": n["lon"], "lat": lat, "lon": lon,
                    "v_kph": round(v_kph, 1), "lat_active": lat_active})
      self.announced[place] = now
    return out


def main() -> None:
  os.makedirs(LOG_DIR, exist_ok=True)
  path = os.path.join(LOG_DIR, time.strftime("%Y-%m-%d--%H-%M-%S") + f"--{time.monotonic_ns()}.jsonl")
  sm = messaging.SubMaster(["carState", "carControl", "liveLocationKalman"])
  shadow = Shadow()
  follower, nav_log, nav, fed_at, nav_out = load_follower(), NavLog(), None, None, None
  if follower is not None:
    os.makedirs(NAV_LOG_DIR, exist_ok=True)
    nav_out = open(os.path.join(NAV_LOG_DIR, os.path.basename(path)), "a")
  curvatures, road, last_mtime = None, None, 0
  with open(path, "a") as out:
    while True:
      try:
        sm.update(100)
        try:
          mtime = os.stat(os.path.join(SHM, "MapCurvatures")).st_mtime_ns
        except FileNotFoundError:
          mtime = last_mtime
        if mtime != last_mtime:
          last_mtime = mtime
          curvatures = _read_json("MapCurvatures")
          try:
            with open(os.path.join(SHM, "RoadName")) as f:
              road = f.read() or None
          except OSError:
            road = None
        loc = sm["liveLocationKalman"]
        if not loc.positionGeodetic.valid:
          continue
        lat, lon = loc.positionGeodetic.value[0], loc.positionGeodetic.value[1]
        heading = math.degrees(loc.calibratedOrientationNED.value[2]) % 360
        lat_active, v_kph = sm["carControl"].latActive, sm["carState"].vEgo * 3.6
        if follower is not None and (fed_at is None or distance_m(lat, lon, *fed_at) >= FOLLOW_STEP_M):
          fed_at = (lat, lon)
          nav = follower.update(lat, lon, heading)
          for seen in nav_log.records(time.monotonic(), nav, lat, lon, v_kph, lat_active):
            seen["mono_ns"] = time.monotonic_ns()
            nav_out.write(json.dumps(seen, separators=(",", ":")) + "\n")
            nav_out.flush()
        rec = shadow.step(time.monotonic(), lat_active, v_kph, lat, lon, heading, curvatures, road, nav)
        if rec is not None:
          rec["mono_ns"] = time.monotonic_ns()
          out.write(json.dumps(rec, separators=(",", ":")) + "\n")
          out.flush()
      except Exception:
        cloudlog.exception("curve_shadow: step failed")
        time.sleep(1.0)


if __name__ == "__main__":
  main()
