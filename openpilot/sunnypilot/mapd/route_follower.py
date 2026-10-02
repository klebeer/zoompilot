"""Follows the routes of a bundle from the car's position and heading, and reports the next maneuver.

Standard library only: this is the logic meant to run on the device.

Every route has its own tracker. A tracker locks once the car has driven LOCK_M along its route,
matched by position and heading, and drops the route after OFF_ROUTE_M without a match. Routes to
the same place share streets, so several trackers can be locked at once: the followed route stays
the same until its tracker drops, then the next locked one takes over.

Where the locked routes part ways, nothing tells which one the driver will take. `next` is the
maneuver ahead on the followed route; `ahead` holds the maneuver ahead on every locked route, so a
consumer that wants to hear about any possible turn reads `ahead`.
"""
import math
from bisect import bisect_left, bisect_right
from datetime import UTC, datetime

SCHEMA_VERSION = 1
MATCH_M = 25.0          # fix to route: GPS error plus the car's lane on a wide avenue
HEADING_DEG = 45.0      # car heading against the route's direction
LOCK_M = 50.0           # distance driven along a route before it counts as followed
OFF_ROUTE_M = 60.0      # distance driven without a match before the route is dropped
WINDOW_BACK_M = 30.0    # a locked tracker only looks this far behind its progress
WINDOW_AHEAD_M = 200.0  # and this far ahead, so a route that crosses itself cannot jump
ARRIVE_M = 30.0
CELL_M = 100.0
EARTH_RADIUS_M = 6371000.0


def usable(bundle: dict, now: datetime | None = None) -> bool:
  """Whether a device should follow this bundle: a known version that has not expired."""
  try:
    if bundle["schema"] != SCHEMA_VERSION or not bundle["routes"]:
      return False
    expires = datetime.strptime(bundle["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
  except (KeyError, TypeError, ValueError):
    return False
  return (now or datetime.now(UTC)) < expires


def angle_diff(a: float, b: float) -> float:
  return abs((a - b + 180) % 360 - 180)


class Route:
  """A route's shape as flat segments in metres around its first point, indexed by grid cell."""

  def __init__(self, route: dict):
    self.route_id = route["route_id"]
    self.maneuvers = route["maneuvers"]
    self.shape = route["shape"]
    self.lat0, self.lon0 = self.shape[0]
    self.kx = math.radians(1.0) * EARTH_RADIUS_M * math.cos(math.radians(self.lat0))
    self.ky = math.radians(1.0) * EARTH_RADIUS_M
    points = [self.to_xy(lat, lon) for lat, lon in self.shape]
    self.segments = []   # (ax, ay, dx, dy, length, bearing, distance along the route at a)
    along = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
      dx, dy = bx - ax, by - ay
      length = math.hypot(dx, dy)
      if length > 0.0:
        self.segments.append((ax, ay, dx, dy, length, math.degrees(math.atan2(dx, dy)) % 360, along))
        along += length
    self.length = along
    self.starts = [s[6] for s in self.segments]
    self.cells: dict[tuple[int, int], list[int]] = {}
    for i, (ax, ay, dx, dy, length, _, _) in enumerate(self.segments):
      steps = max(1, int(length / (CELL_M / 2)))
      for k in range(steps + 1):
        cell = (int((ax + dx * k / steps) // CELL_M), int((ay + dy * k / steps) // CELL_M))
        if i not in self.cells.setdefault(cell, []):
          self.cells[cell].append(i)
    # The shape and the segments differ in length by at most rounding; maneuvers keep their own distances.
    self.maneuver_at = [m["distance_m"] for m in self.maneuvers]

  def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
    return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

  def project(self, i: int, x: float, y: float) -> tuple[float, float]:
    """(distance from the point to segment i, distance along the route of its nearest point)."""
    ax, ay, dx, dy, length, _, along = self.segments[i]
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (length * length)))
    return math.hypot(x - (ax + t * dx), y - (ay + t * dy)), along + t * length

  def match(self, x: float, y: float, heading: float, candidates) -> tuple[float, float] | None:
    """Nearest candidate segment within MATCH_M whose direction fits the heading, as (distance, along)."""
    best = None
    for i in candidates:
      if angle_diff(heading, self.segments[i][5]) > HEADING_DEG:
        continue
      d, along = self.project(i, x, y)
      if d <= MATCH_M and (best is None or d < best[0]):
        best = (d, along)
    return best

  def near(self, x: float, y: float) -> list[int]:
    cx, cy = int(x // CELL_M), int(y // CELL_M)
    out = []
    for ix in (cx - 1, cx, cx + 1):
      for iy in (cy - 1, cy, cy + 1):
        out.extend(self.cells.get((ix, iy), ()))
    return out

  def window(self, along: float) -> range:
    lo = max(0, bisect_right(self.starts, along - WINDOW_BACK_M) - 1)
    hi = bisect_left(self.starts, along + WINDOW_AHEAD_M)
    return range(lo, hi)


class Tracker:
  def __init__(self, route: Route):
    self.route = route
    self.along = None      # progress along the route, None while the car is not on it
    self.followed = 0.0    # distance driven along the route since the tracker picked it up
    self.unmatched = 0.0   # distance driven since the last match
    self.match_m = 0.0

  @property
  def locked(self) -> bool:
    return self.along is not None and self.followed >= LOCK_M

  def update(self, lat: float, lon: float, heading: float, moved: float) -> None:
    x, y = self.route.to_xy(lat, lon)
    if self.along is None:
      hit = self.route.match(x, y, heading, self.route.near(x, y))
      if hit is not None:
        self.match_m, self.along = hit
        self.followed = self.unmatched = 0.0
      return
    hit = self.route.match(x, y, heading, self.route.window(self.along))
    if hit is None:
      self.unmatched += moved
      if self.unmatched > OFF_ROUTE_M:
        self.along = None
      return
    self.followed += max(0.0, hit[1] - self.along)
    self.match_m, self.along = hit
    self.unmatched = 0.0


class Follower:
  def __init__(self, bundle: dict):
    self.trackers = [Tracker(Route(r)) for r in bundle.get("routes", [])]
    self.active: Tracker | None = None
    self.was_following = False
    self.last = None

  def update(self, lat: float, lon: float, heading: float) -> dict:
    """The state to publish for this fix. `heading` is degrees clockwise from north."""
    moved = 0.0
    if self.last is not None:
      k = math.radians(1.0) * EARTH_RADIUS_M
      moved = math.hypot((lat - self.last[0]) * k, (lon - self.last[1]) * k * math.cos(math.radians(lat)))
    self.last = (lat, lon)
    for t in self.trackers:
      t.update(lat, lon, heading, moved)
    if self.active is None or not self.active.locked:
      self.active = next((t for t in self.trackers if t.locked), None)
    if self.active is None:
      state = "no_route" if not self.trackers else "off_route" if self.was_following else "searching"
      return {"state": state, "route_id": None, "progress_m": None, "remaining_m": None, "match_m": None,
              "next": None, "ahead": []}
    self.was_following = True
    t, route = self.active, self.active.route
    remaining = max(0.0, route.length - t.along)
    return {
      "state": "arrived" if remaining <= ARRIVE_M else "following",
      "route_id": route.route_id,
      "progress_m": round(t.along, 1),
      "remaining_m": round(remaining, 1),
      "match_m": round(t.match_m, 1),
      "next": self._next(route, t.along),
      "ahead": self._ahead(),
    }

  def _ahead(self) -> list[dict]:
    """The maneuver ahead on each locked route, nearest first, one entry per place."""
    by_place = {}
    for t in [self.active] + [t for t in self.trackers if t.locked and t is not self.active]:
      n = self._next(t.route, t.along)
      if n is None:
        continue
      place = (n["lat"], n["lon"])
      if place not in by_place or n["distance_m"] < by_place[place]["distance_m"]:
        by_place[place] = {"route_id": t.route.route_id, **n}
    return sorted(by_place.values(), key=lambda n: n["distance_m"])

  @staticmethod
  def _next(route: Route, along: float) -> dict | None:
    i = bisect_right(route.maneuver_at, along)
    if i >= len(route.maneuvers):
      return None
    m = route.maneuvers[i]
    lat, lon = route.shape[m["shape_index"]]
    return {"index": i, "type": m["type"], "modifier": m["modifier"], "distance_m": round(m["distance_m"] - along, 1),
            "turn_deg": m["turn_deg"], "lat": lat, "lon": lon, "street": m["street"]}
