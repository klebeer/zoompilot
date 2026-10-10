"""
Shadow run of the tight-curve warning: records where it would fire, never alerts.

Runs onroad. While openpilot steers at MIN_KPH-MAX_KPH it applies five rules: the map's
tight-curve rule on the latest mapd curvature points, a list of curves learned from this driver's
own logs (KNOWN_CURVES_PATH, written by commaia mapd/known_hard_curves.py), the turn ahead on any
route the car is on (BUNDLE_PATH, written by comma-nav), the consensus of those routes: a turn
only when every route the car is on takes it, at the same place and to the same side, and the turn
ahead on the route the driver chose for this trip (CHOICE_PATH, written by comma-nav or by the
comma's own screen), while the car is on it. It appends one JSON line per
would-be warning to LOG_DIR, stamped with time.monotonic_ns() so it joins the rlog on logMonoTime,
and records which rules fired so they can be compared offline. None alerts.

Beside the warnings the log gets speed advice, with "kind": "speed_advice": an open curve ahead where
this car's steering ran out on earlier drives (ADVICE_CURVES_PATH, written by commaia
mapd/learned_advice_eval.py --write), approached the same way, while the car steers above
ADVICE_KPH, once per curve. Nothing is shown.

The route rules need a bundle. Without one, or with one that has expired, the other two rules run
as before. What the route follower saw goes to NAV_LOG_DIR as well, free of the warning's holdoff:
each change of state, each turn each route rule announced with the blinker at that moment, so
the rules can be scored on their own and against what the driver signalled, and each change of the
choice. The choice is read again whenever its file changes, since it can be made with the car on; when
the car reaches the chosen route's end, the file is marked arrived and the choice no longer holds.

The followed route's own curves, read from its geometry (route_curves), go to NAV_LOG_DIR as well, with
"kind": "route_curve": when each comes within CURVE_LOOKAHEAD_M, with its radius, the speed the steering
holds it at and what the car was doing; and when the car passes its apex, with whether openpilot still
steered and the driver's hand on the wheel. Nothing is shown.
"""
import json
import math
import os
import time
from bisect import bisect_left

import openpilot.cereal.messaging as messaging
from openpilot.common.hardware import PC
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd.curve_warning import (ADVICE_KPH, LOOKAHEAD_M, MIN_RADIUS_M, CurveAhead, distance_m, known_curve_ahead,
                                                     learned_curve_ahead, tight_curve_ahead)
from openpilot.sunnypilot.mapd.route_choice import BUNDLE_PATH, load_bundle
from openpilot.sunnypilot.mapd.route_curves import curves, held_kph
from openpilot.sunnypilot.mapd.route_follower import Follower
from openpilot.sunnypilot.mapd.route_guide import (FOLLOW_STEP_M, ROUTE_TURN_DEG, ChoiceWatch, is_route_turn, route_desire,
                                                   route_turn_agreed, route_turn_chosen)

SHM = "/dev/shm/params/d"
KNOWN_CURVES_PATH = os.path.expanduser("~/.comma/known_hard_curves.json") if PC else "/data/known_hard_curves.json"
ADVICE_CURVES_PATH = os.path.expanduser("~/.comma/advice_curves.json") if PC else "/data/advice_curves.json"
LOG_DIR = os.path.expanduser("~/.comma/curve_shadow") if PC else "/data/media/0/curve_shadow"
NAV_LOG_DIR = os.path.expanduser("~/.comma/nav_shadow") if PC else "/data/media/0/nav_shadow"
MIN_KPH, MAX_KPH = 10.0, 60.0
HOLDOFF_S = 20.0            # at most one would-be warning every 20 s
SAME_CURVE_HOLDOFF_S = 60.0  # and once per curve
SAME_CURVE_M = 30.0
SAME_TURN_S = 120.0          # a turn announced again after this long is another pass by it
CURVE_LOOKAHEAD_M = 120.0    # a route curve is logged when its apex comes this close
APEX_PASSED_M = 30.0         # and again when the car is past its apex by up to this much


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


def load_advice_curves(path: str = ADVICE_CURVES_PATH) -> list[dict]:
  """The learned open curves, or an empty list when the file is absent or unreadable."""
  try:
    with open(path) as f:
      curves = json.load(f)
    return [c for c in curves if {"latitude", "longitude", "heading"} <= c.keys()]
  except (OSError, ValueError, TypeError, AttributeError):
    return []


def load_follower(path: str = BUNDLE_PATH) -> Follower | None:
  """A follower for the route bundle, or None when there is no bundle the device should follow."""
  bundle = load_bundle(path)
  return Follower(bundle) if bundle is not None else None



def route_turns_ahead(nav: dict | None) -> list[dict]:
  """The turns and roundabouts within LOOKAHEAD_M on the routes the car is on, nearest first."""
  if nav is None or nav["state"] != "following":
    return []
  return [n for n in nav["ahead"] if n["distance_m"] <= LOOKAHEAD_M and is_route_turn(n)]





def as_curve(n: dict | None) -> CurveAhead | None:
  return CurveAhead(distance=n["distance_m"], radius=0.0, latitude=n["lat"], longitude=n["lon"]) if n else None


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
    turns, sure, picked = route_turns_ahead(nav), route_turn_agreed(nav), route_turn_chosen(nav)
    turn, consensus, chosen = as_curve(turns[0] if turns else None), as_curve(sure), as_curve(picked)
    # The map and learned rules stay as they were: silent while mapd has no curvature points.
    hit = tight_curve_ahead(lat, lon, heading, curvatures) if curvatures else None
    learned = known_curve_ahead(lat, lon, heading, self.known_curves) if curvatures else None
    fired = [(name, h) for name, h in (("map", hit), ("learned", learned), ("route", turn), ("consensus", consensus),
                                         ("chosen", chosen)) if h is not None]
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
    if sure:
      rec["consensus"] = describe_turn(sure)
    if picked:
      rec["chosen"] = describe_turn(picked)
    return rec


class SpeedAdvice:
  """Where the car would be told to slow to ADVICE_KPH for a learned open curve ahead: once per curve, while openpilot steers."""

  def __init__(self, curves: list[dict] | None = None):
    self.curves = curves if curves is not None else load_advice_curves()
    self.advised: list[tuple[float, float, float]] = []   # (time, latitude, longitude) of each curve advised for

  def step(self, now: float, lat_active: bool, v_kph: float, lat: float, lon: float, heading: float, road=None):
    if not self.curves or not lat_active or not (ADVICE_KPH < v_kph <= MAX_KPH):
      return None
    curve = learned_curve_ahead(lat, lon, heading, self.curves)
    if curve is None:
      return None
    self.advised = [a for a in self.advised if now - a[0] < SAME_CURVE_HOLDOFF_S]
    if any(distance_m(curve.latitude, curve.longitude, a[1], a[2]) < SAME_CURVE_M for a in self.advised):
      return None
    self.advised.append((now, curve.latitude, curve.longitude))
    return {"kind": "speed_advice", "lat": lat, "lon": lon, "heading": round(heading, 1), "v_kph": round(v_kph, 1),
            "distance_m": round(curve.distance, 1), "curve_lat": curve.latitude, "curve_lon": curve.longitude,
            "advice_kph": ADVICE_KPH, "advice_curves": len(self.curves), "road": road}



class RouteCurveLog:
  """The followed route's curves: each once when it comes within CURVE_LOOKAHEAD_M, and once at its apex."""

  def __init__(self, shapes: dict[str, list]):
    self.shapes = shapes
    self.curves: dict[str, tuple[list[float], list[dict]]] = {}
    self.done: set[tuple[str, int, str]] = set()

  def _of(self, route_id: str) -> tuple[list[float], list[dict]]:
    if route_id not in self.curves:
      found = curves(self.shapes[route_id]) if route_id in self.shapes else []
      self.curves[route_id] = ([c["apex_m"] for c in found], found)
    return self.curves[route_id]

  def records(self, nav: dict | None, v_kph: float, lat_active: bool, blinker: str | None, pressed: bool, torque: float) -> list[dict]:
    if nav is None or nav["state"] not in ("following", "arrived"):
      return []
    route_id, at = nav["route_id"], nav["progress_m"]
    apexes, found = self._of(route_id)
    out = []
    for n in range(bisect_left(apexes, at - APEX_PASSED_M), len(found)):
      c, ahead = found[n], found[n]["apex_m"] - at
      if ahead > CURVE_LOOKAHEAD_M:
        break
      event = "ahead" if ahead > 0 else "apex"
      if (route_id, n, event) in self.done:
        continue
      self.done.add((route_id, n, event))
      rec = {"kind": "route_curve", "event": event, "route_id": route_id, "curve": n, "radius_m": c["radius_m"], "side": c["side"],
             "held_kph": held_kph(c["radius_m"]), "distance_m": round(ahead, 1), "start_m": c["start_m"], "end_m": c["end_m"],
             "curve_lat": c["lat"], "curve_lon": c["lon"], "v_kph": round(v_kph, 1), "lat_active": lat_active, "blinker": blinker}
      if event == "apex":
        rec |= {"steering_pressed": pressed, "steering_torque": round(torque, 1)}
      out.append(rec)
    return out


class NavLog:
  """What the route follower saw, apart from the warning: each change of state, and each turn each route
  rule announced. `rule` is "ahead" (any route the car is on), "consensus" (every one of them), "chosen" (the
  route the driver chose) or "route_desire" (where the route, confirmed by the blinker, would ask the model for the
  turn; `source` says which route rule it came from); `on_chosen` says whether the car was on the chosen route then."""

  def __init__(self):
    self.state = None
    self.announced: dict[tuple[str, float, float], float] = {}

  def records(self, now: float, nav: dict, lat: float, lon: float, v_kph: float, lat_active: bool, blinker: str | None = None) -> list[dict]:
    out = []
    state = (nav["state"], nav["route_id"])
    if state != self.state:
      self.state = state
      out.append({"kind": "state", "state": nav["state"], "route_id": nav["route_id"], "progress_m": nav["progress_m"],
                  "lat": lat, "lon": lon})
    sure, picked, desire = route_turn_agreed(nav), route_turn_chosen(nav), route_desire(nav, v_kph, blinker)
    on_chosen = bool(nav.get("chosen") and nav["chosen"]["on_route"])
    for rule, turns in (("ahead", route_turns_ahead(nav)), ("consensus", [sure] if sure else []), ("chosen", [picked] if picked else []),
                        ("route_desire", [desire] if desire else [])):
      for n in turns:
        place = (rule, n["lat"], n["lon"])
        if now - self.announced.get(place, -1e9) >= SAME_TURN_S:
          out.append({"kind": "turn", "rule": rule, **describe_turn(n), "turn_lat": n["lat"], "turn_lon": n["lon"], "lat": lat, "lon": lon,
                      "v_kph": round(v_kph, 1), "lat_active": lat_active, "blinker": blinker, "on_chosen": on_chosen,
                      **({"source": n["source"]} if rule == "route_desire" else {})})
        self.announced[place] = now
    return out


def main() -> None:
  os.makedirs(LOG_DIR, exist_ok=True)
  path = os.path.join(LOG_DIR, time.strftime("%Y-%m-%d--%H-%M-%S") + f"--{time.monotonic_ns()}.jsonl")
  sm = messaging.SubMaster(["carState", "carControl", "liveLocationKalman"])
  shadow, advice = Shadow(), SpeedAdvice()
  follower, nav_log, choices, nav, fed_at, nav_out = load_follower(), NavLog(), ChoiceWatch(), None, None, None
  curve_log = RouteCurveLog({t.route.route_id: t.route.shape for t in follower.trackers} if follower is not None else {})
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
        left, right = sm["carState"].leftBlinker, sm["carState"].rightBlinker
        blinker = None if left == right else "left" if left else "right"
        if follower is not None:
          seen = [choices.poll(time.monotonic(), follower)]
          if fed_at is None or distance_m(lat, lon, *fed_at) >= FOLLOW_STEP_M:
            fed_at = (lat, lon)
            nav = follower.update(lat, lon, heading)
            seen += nav_log.records(time.monotonic(), nav, lat, lon, v_kph, lat_active, blinker)
            seen += curve_log.records(nav, v_kph, lat_active, blinker, sm["carState"].steeringPressed, sm["carState"].steeringTorque)
            if nav["chosen"] and nav["chosen"]["arrived"]:
              seen.append(choices.arrived(follower))
          for rec in seen:
            if rec is not None:
              rec["mono_ns"] = time.monotonic_ns()
              nav_out.write(json.dumps(rec, separators=(",", ":")) + "\n")
              nav_out.flush()
        now = time.monotonic()
        for rec in (shadow.step(now, lat_active, v_kph, lat, lon, heading, curvatures, road, nav),
                    advice.step(now, lat_active, v_kph, lat, lon, heading, road)):
          if rec is not None:
            rec["mono_ns"] = time.monotonic_ns()
            out.write(json.dumps(rec, separators=(",", ":")) + "\n")
            out.flush()
      except Exception:
        cloudlog.exception("curve_shadow: step failed")
        time.sleep(1.0)


if __name__ == "__main__":
  main()
