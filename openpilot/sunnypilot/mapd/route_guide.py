"""
The trip's route, followed live: where the route turns and the blinker agrees, the car is asked to turn.

Runs onroad. Follows the route bundle comma-nav puts at BUNDLE_PATH and the driver's choice at CHOICE_PATH, taking
either again whenever its file changes, so what nav_channel delivers during a drive is used at once. When the car
reaches the chosen route's end the choice is marked arrived and no longer holds.

With route_desire_live enabled, the turn the route asks for (route_desire) is latched and published for the blinker
pause and the turn desire. Each new bundle, change of the follower's state, change of the choice and change of the
published side goes to LOG_DIR as one JSON line, stamped with time.monotonic_ns() so it joins the rlog on logMonoTime.
"""
import json
import math
import os
import time
from datetime import UTC, datetime

import numpy as np

import openpilot.cereal.messaging as messaging
from openpilot.common.hardware import PC
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd import route_desire_live, route_input
from openpilot.sunnypilot.mapd.curve_warning import LOOKAHEAD_M, distance_m
from openpilot.sunnypilot.mapd.nav_messages import NavPublisher
from openpilot.sunnypilot.mapd.reroute import Rerouter
from openpilot.sunnypilot.mapd.route_choice import BUNDLE_PATH, CHOICE_PATH, load_bundle, read_json, write_json
from openpilot.sunnypilot.mapd.route_follower import Follower, agreed, chosen_turn, side, stamp
from openpilot.sunnypilot.mapd.route_planner import Planner

LOG_DIR = os.path.expanduser("~/.comma/nav_shadow") if PC else "/data/media/0/nav_shadow"
ROUTE_TURN_DEG = 45.0        # a maneuver on the route counts as a turn from this heading change
ROUTE_ALWAYS = ("roundabout", "exit roundabout")  # their heading change at entry and exit is small
FOLLOW_STEP_M = 2.0          # the follower is fed a fix every this far, not every message
FILE_CHECK_S = 1.0           # how often the bundle and choice files are looked at
DESIRE_M = 40.0              # the route asks for its turn only this close to the corner
DESIRE_KPH = 30.0            # and at this speed or slower
NAV_CHECK_S = 1.0            # how often NavigationEnabled is read: comma's navd owns the nav messages while it is on


def is_route_turn(n: dict) -> bool:
  return abs(n["turn_deg"]) >= ROUTE_TURN_DEG or n["type"] in ROUTE_ALWAYS


def route_turn_agreed(nav: dict | None) -> dict | None:
  """The turn every route the car is on takes within LOOKAHEAD_M, or None where they part ways."""
  if nav is None or nav["state"] != "following":
    return None
  return agreed(nav, is_route_turn, LOOKAHEAD_M)


def route_turn_chosen(nav: dict | None) -> dict | None:
  """The turn within LOOKAHEAD_M on the route the driver chose, while the car is on it."""
  return chosen_turn(nav, is_route_turn, LOOKAHEAD_M) if nav is not None else None


def route_desire(nav: dict | None, v_kph: float, blinker: str | None) -> dict | None:
  """The turn the route asks the model for: the chosen route's next turn while the car is on it, else the turn
  every route agrees on. Only with the blinker to that same side, the corner within DESIRE_M and the car at
  DESIRE_KPH or slower: the blinker says which way, the route says where the corner is."""
  if blinker is None or v_kph > DESIRE_KPH:
    return None
  n = route_turn_chosen(nav) or route_turn_agreed(nav)
  if n is None or n["distance_m"] > DESIRE_M or side(n) != blinker:
    return None
  return {**n, "source": "chosen" if route_turn_chosen(nav) else "consensus"}


class ChoiceWatch:
  """The driver's route choice, taken again whenever its file changes."""

  def __init__(self, path: str = CHOICE_PATH):
    self.path, self.mtime, self.choice, self.checked = path, None, None, -1e9

  def poll(self, now: float, follower: Follower) -> dict | None:
    """A record when the choice changed, else None. Looks at the file at most every FILE_CHECK_S."""
    if now - self.checked < FILE_CHECK_S:
      return None
    self.checked = now
    try:
      mtime = os.stat(self.path).st_mtime_ns
    except OSError:
      mtime = None
    if mtime == self.mtime:
      return None
    self.mtime = mtime
    self.choice = read_json(self.path) if mtime is not None else None
    holds = follower.choose(self.choice)
    return {"kind": "choice", "route_id": (self.choice or {}).get("route_id"), "chosen_by": (self.choice or {}).get("chosen_by"),
            "holds": holds}

  def arrived(self, follower: Follower) -> dict:
    """The car reached the chosen route's end: the file says so, and the choice no longer holds, now or on later drives."""
    self.choice = {**self.choice, "arrived_at": stamp(datetime.now(UTC))}
    write_json(self.choice, self.path)
    self.mtime = os.stat(self.path).st_mtime_ns
    follower.choose(None)
    return {"kind": "choice", "route_id": self.choice["route_id"], "arrived": True}


class Guide:
  """The follower for the current bundle, the driver's choice and, when live, the published turn."""

  def __init__(self, live: bool, bundle_path: str = BUNDLE_PATH, choice_path: str = CHOICE_PATH, rerouter: Rerouter | None = None):
    self.bundle_path, self.bundle_mtime, self.bundle_checked = bundle_path, None, -1e9
    self.follower: Follower | None = None
    self.choices = ChoiceWatch(choice_path)
    self.nav: dict | None = None
    self.fed_at: tuple[float, float] | None = None
    self.state: tuple | None = None
    self.latch = route_desire_live.Latch() if live else None
    self.publisher = route_desire_live.Publisher() if live else None
    self.rerouter = rerouter
    self._shape_id: str | None = None
    self._shape = None

  def route_points(self, lat: float, lon: float, heading: float):
    """route_input's points on the route the follower follows, or None while it follows none."""
    if self.follower is None or self.nav is None or self.nav["state"] != "following":
      return None
    route = next((t.route for t in self.follower.trackers if t.route.route_id == self.nav["route_id"]), None)
    if route is None:
      return None
    if self._shape_id != route.route_id:
      self._shape_id, self._shape = route.route_id, np.array(route.shape, dtype=np.float64)
    return route_input.points_ahead(self._shape, lat, lon, heading)

  def _reload(self, now: float) -> dict | None:
    """A record when the bundle file changed, after building a follower for it. Looks at it at most every FILE_CHECK_S."""
    if now - self.bundle_checked < FILE_CHECK_S:
      return None
    self.bundle_checked = now
    try:
      mtime = os.stat(self.bundle_path).st_mtime_ns
    except OSError:
      mtime = None
    if mtime == self.bundle_mtime:
      return None
    self.bundle_mtime = mtime
    bundle = load_bundle(self.bundle_path)
    self.follower = Follower(bundle) if bundle is not None else None
    self.nav, self.fed_at, self.state = None, None, None
    holds = self.follower.choose(self.choices.choice) if self.follower is not None else False
    return {"kind": "bundle", "bundle_id": bundle["bundle_id"] if bundle else None, "routes": len(bundle["routes"]) if bundle else 0,
            "choice_holds": holds}

  def step(self, now: float, now_ns: int, lat: float, lon: float, heading: float, v_kph: float, blinker: str | None,
           lat_active: bool) -> list[dict]:
    """Records of what changed at this fix. `heading` is degrees clockwise from north."""
    out = [self._reload(now)]
    if self.follower is not None:
      out.append(self.choices.poll(now, self.follower))
      if self.fed_at is None or distance_m(lat, lon, *self.fed_at) >= FOLLOW_STEP_M:
        self.fed_at = (lat, lon)
        self.nav = self.follower.update(lat, lon, heading)
        state = (self.nav["state"], self.nav["route_id"])
        if state != self.state:
          self.state = state
          out.append({"kind": "state", "state": self.nav["state"], "route_id": self.nav["route_id"],
                      "progress_m": self.nav["progress_m"], "lat": lat, "lon": lon})
        if self.nav["chosen"] and self.nav["chosen"]["arrived"]:
          out.append(self.choices.arrived(self.follower))
    if self.rerouter is not None:
      out.append(self.rerouter.step(now, self.nav, self.follower, self.choices.choice, (lat, lon), heading, v_kph,
                                    self.bundle_path, self.choices.path, self.bundle_mtime))
    if self.latch is not None:
      wanted = blinker if route_desire(self.nav, v_kph, blinker) is not None else None
      was = self.publisher.side
      side_now = self.latch.update(now, wanted, blinker, v_kph)
      self.publisher.step(side_now, now_ns)
      if side_now != was:
        out.append({"kind": "route_desire_live", "side": side_now, "lat": lat, "lon": lon, "v_kph": round(v_kph, 1),
                    "lat_active": lat_active, "blinker": blinker})
    return [r for r in out if r is not None]


def main() -> None:
  sm = messaging.SubMaster(["carState", "carControl", "liveLocationKalman"])
  params = Params()
  planner = Planner()
  guide = Guide(route_desire_live.enabled(), rerouter=Rerouter(planner) if planner.available() else None)
  nav_pub, nav_checked = None, -1e9
  name = time.strftime("%Y-%m-%d--%H-%M-%S") + f"--{time.monotonic_ns()}.jsonl"
  out = None
  while True:
    try:
      sm.update(100)
      now = time.monotonic()
      if now - nav_checked >= NAV_CHECK_S:
        nav_checked = now
        if params.get_bool("NavigationEnabled"):
          nav_pub = None
        elif nav_pub is None:
          nav_pub = NavPublisher(messaging.PubMaster(["navRoute", "navInstruction"]), messaging.new_message)
      loc = sm["liveLocationKalman"]
      if not loc.positionGeodetic.valid:
        continue
      left, right = sm["carState"].leftBlinker, sm["carState"].rightBlinker
      records = guide.step(now, time.monotonic_ns(), loc.positionGeodetic.value[0], loc.positionGeodetic.value[1],
                           math.degrees(loc.calibratedOrientationNED.value[2]) % 360, sm["carState"].vEgo * 3.6,
                           None if left == right else "left" if left else "right", sm["carControl"].latActive)
      if nav_pub is not None:
        nav_pub.step(now, guide.nav, guide.follower)
      route_input.publish(route_input.encode(guide.route_points(loc.positionGeodetic.value[0], loc.positionGeodetic.value[1],
                                                                math.degrees(loc.calibratedOrientationNED.value[2]))),
                          time.monotonic_ns())
      if records:
        if out is None:
          os.makedirs(LOG_DIR, exist_ok=True)
          out = open(os.path.join(LOG_DIR, name), "a")
        for rec in records:
          rec["mono_ns"] = time.monotonic_ns()
          out.write(json.dumps(rec, separators=(",", ":")) + "\n")
        out.flush()
    except Exception:
      cloudlog.exception("route_guide: step failed")
      time.sleep(1.0)


if __name__ == "__main__":
  main()
