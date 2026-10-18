"""
A new route when the car leaves the one the driver chose, planned on the device so it works offline.

Once the car has been on the chosen route and the follower drops it, while moving and not near the
destination, the car is routed back onto the chosen route at the first of REJOIN_AHEAD_M past where it left that
gives the shortest whole trip, and follows the chosen route from there: the driver's own way, even where it is
longer than the planner's. Only when every way back is longer than REJOIN_MAX_HEAD_M, a deliberate change of
course, does the new route go straight to the destination. The result is added to the bundle and chosen, so
route_guide follows it as it follows a route the phone sends.

Planning runs on a worker thread; the files are written from route_guide's loop. A bundle that changed on disk
since route_guide loaded it, because the phone sent a new one, is never overwritten: the phone's route wins.
"""
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

from openpilot.sunnypilot.mapd.route_choice import write_json
from openpilot.sunnypilot.mapd.route_follower import stamp
from openpilot.sunnypilot.mapd.route_planner import PlannerError, cumulative_m

REJOIN_AHEAD_M = (300.0, 700.0, 1200.0)  # where a way back may meet the chosen route, past where the car left it
REJOIN_MAX_HEAD_M = 1500.0  # a way back longer than this means the driver changed course
END_MARGIN_M = 150.0        # no reroute this close to the destination
MIN_KPH = 5.0               # nor while the car is parked or creeping
COOLDOWN_S = 20.0           # between two reroutes


def cut(route: dict, at_m: float) -> tuple[list[float], dict]:
  """The point `at_m` along `route`, and the rest of the route from there."""
  shape = route["shape"]
  along = cumulative_m(shape)
  i = next((k for k in range(1, len(along)) if along[k] >= at_m), len(along) - 1)
  span = max(along[i] - along[i - 1], 1e-9)
  f = min(max((at_m - along[i - 1]) / span, 0.0), 1.0)
  point = [shape[i - 1][0] + f * (shape[i][0] - shape[i - 1][0]), shape[i - 1][1] + f * (shape[i][1] - shape[i - 1][1])]
  maneuvers = [{**m, "shape_index": max(m["shape_index"] - i + 1, 0), "distance_m": round(m["distance_m"] - at_m, 1)}
               for m in route["maneuvers"] if m["distance_m"] > at_m and m["type"] != "depart"]
  tail = {"route_id": route["route_id"], "label": route["label"], "length_m": round(route["length_m"] - at_m, 1),
          "shape": [point] + shape[i:], "maneuvers": maneuvers}
  return point, tail


def join(head: dict, tail: dict, route_id: str, label: str) -> dict:
  """`head` up to where it arrives, then `tail`, as one route: the arrival between them is not a maneuver."""
  offset = len(head["shape"]) - 1
  maneuvers = [m for m in head["maneuvers"] if m["type"] != "arrive"]
  maneuvers += [{**m, "shape_index": m["shape_index"] + offset, "distance_m": round(m["distance_m"] + head["length_m"], 1)}
                for m in tail["maneuvers"]]
  return {"route_id": route_id, "label": label, "length_m": round(head["length_m"] + tail["length_m"], 1),
          "shape": head["shape"] + tail["shape"][1:], "maneuvers": maneuvers}


def plan(planner, chosen: dict, progress_m: float, car: tuple[float, float], heading: float, route_id: str) -> dict:
  """The new route and how it was picked. Raises PlannerError when neither way can be planned."""
  label = f"{chosen['label']} (desde aquí)"
  destination = tuple(chosen["shape"][-1])
  direct = planner.route(car, heading, destination, route_id, label)
  rejoin = None
  for ahead in REJOIN_AHEAD_M:
    at = progress_m + ahead
    if chosen["length_m"] - at <= END_MARGIN_M:
      break
    point, tail = cut(chosen, at)
    try:
      head = planner.route(car, heading, tuple(point), route_id, label)
    except PlannerError:
      continue
    if head["length_m"] <= REJOIN_MAX_HEAD_M:
      candidate = join(head, tail, route_id, label)
      if rejoin is None or candidate["length_m"] < rejoin["length_m"]:
        rejoin = candidate
  if rejoin is not None:
    return {"route": rejoin, "strategy": "rejoin", "direct_m": direct["length_m"]}
  return {"route": direct, "strategy": "direct", "direct_m": direct["length_m"]}


class Rerouter:
  def __init__(self, planner):
    self.planner = planner
    self.pool = ThreadPoolExecutor(max_workers=1)
    self.job: Future | None = None
    self.route_id: str | None = None      # the chosen route being watched
    self.was_on = False
    self.progress_m = 0.0
    self.last_at = -1e9
    self.count = 0

  def step(self, now: float, nav: dict | None, follower, choice: dict | None, car: tuple[float, float], heading: float,
           v_kph: float, bundle_path: str, choice_path: str, bundle_mtime: int | None) -> dict | None:
    """A record when a reroute was written or failed, else None."""
    if self.job is not None:
      return self._finish(follower, choice, bundle_path, choice_path, bundle_mtime) if self.job.done() else None
    chosen = nav.get("chosen") if nav is not None else None
    if chosen is None or follower is None:
      self.route_id, self.was_on = None, False
      return None
    if chosen["route_id"] != self.route_id:
      self.route_id, self.was_on, self.progress_m = chosen["route_id"], False, 0.0
    if chosen["on_route"]:
      self.was_on = True
      if nav["route_id"] == chosen["route_id"] and nav["progress_m"] is not None:
        self.progress_m = nav["progress_m"]
      return None
    if not self.was_on or chosen["arrived"] or v_kph < MIN_KPH or now - self.last_at < COOLDOWN_S:
      return None
    route = next((r for r in follower.bundle["routes"] if r["route_id"] == chosen["route_id"]), None)
    if route is None or route["length_m"] - self.progress_m < END_MARGIN_M:
      return None
    self.last_at, self.count = now, self.count + 1
    route_id = f"device-{datetime.now(UTC):%Y%m%d%H%M%S}-{self.count}"
    started, progress, left = time.monotonic(), self.progress_m, chosen["route_id"]
    self.job = self.pool.submit(lambda: {**plan(self.planner, route, progress, car, heading, route_id),
                                         "from": left, "started": started})
    return None

  def _finish(self, follower, choice, bundle_path, choice_path, bundle_mtime) -> dict:
    job, self.job = self.job, None
    try:
      result = job.result()
    except Exception as e:
      return {"kind": "reroute", "error": str(e)[:200]}
    record = {"kind": "reroute", "strategy": result["strategy"], "from": result["from"], "route_id": result["route"]["route_id"],
              "length_m": result["route"]["length_m"], "direct_m": result["direct_m"],
              "ms": round(1000 * (time.monotonic() - result["started"]))}
    try:
      if follower is None or choice is None or os.stat(bundle_path).st_mtime_ns != bundle_mtime:
        return {**record, "written": False}
    except OSError:
      return {**record, "written": False}
    bundle = {**follower.bundle, "routes": follower.bundle["routes"] + [result["route"]]}
    write_json(bundle, bundle_path)
    write_json({**choice, "route_id": result["route"]["route_id"], "chosen_by": "device",
                "chosen_at": stamp(datetime.now(UTC))}, choice_path)
    return {**record, "written": True}
