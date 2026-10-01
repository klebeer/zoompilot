"""
The map way under the car, from mapd's offline tiles, with its oneWay and lane count.

mapd keeps oneWay and lanes in its tiles but publishes neither, so this reads the tiles directly.
OpenStreetMap draws a divided road as two one-way ways a few metres apart, so a one-way segment
only matches when the car travels its way: otherwise the far carriageway of a divided road wins on
distance alone and the car reads as driving against the traffic.
"""
import glob
import math
import os
from dataclasses import dataclass

import capnp

from openpilot.common.hardware import PC

TILES_DIR = os.path.expanduser("~/.comma/osm/offline") if PC else "/data/media/0/osm/offline"
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "offline.capnp")
MATCH_M = 12.0
ONE_WAY_HEADING_DEG = 60.0   # a one-way segment matches only within this of its direction
TWO_WAY_HEADING_DEG = 60.0   # a two-way segment matches within this of either direction
CELL_DEG = 0.001             # index cell, about 110 m


@dataclass(frozen=True)
class WayMatch:
  name: str
  one_way: bool
  lanes: int
  distance: float   # m from the car to the segment


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
  la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
  y = math.sin(lo2 - lo1) * math.cos(la2)
  x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
  return math.degrees(math.atan2(y, x)) % 360


def angle_diff(a: float, b: float) -> float:
  return abs((a - b + 180) % 360 - 180)


def segment_distance_m(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
  """Distance from p to segment ab on a local flat projection, which is exact enough at street scale."""
  kx, ky = 111320.0 * math.cos(math.radians(p[0])), 110540.0
  ax, ay = (a[1] - p[1]) * kx, (a[0] - p[0]) * ky
  bx, by = (b[1] - p[1]) * kx, (b[0] - p[0]) * ky
  dx, dy = bx - ax, by - ay
  denom = dx * dx + dy * dy
  t = 0.0 if denom == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denom))
  return math.hypot(ax + t * dx, ay + t * dy)


def heading_fits(heading: float, seg_bearing: float, one_way: bool) -> bool:
  if one_way:
    return angle_diff(heading, seg_bearing) <= ONE_WAY_HEADING_DEG
  return min(angle_diff(heading, seg_bearing), angle_diff(heading, seg_bearing + 180)) <= TWO_WAY_HEADING_DEG


class MapWays:
  """Segments of the tiles around the car, indexed by cell, loaded as the car reaches each tile."""

  def __init__(self, tiles_dir: str = TILES_DIR):
    self.schema = capnp.load(SCHEMA_PATH)
    self.tiles = {}       # path -> (min_lat, min_lon, max_lat, max_lon)
    for path in glob.glob(os.path.join(tiles_dir, "*", "*", "*")):
      try:
        bounds = tuple(float(x) for x in os.path.basename(path).split("_"))
      except ValueError:
        continue
      if len(bounds) == 4:
        self.tiles[path] = bounds
    self.loaded: set[str] = set()
    self.cells: dict[tuple[int, int], list] = {}

  def _load(self, path: str) -> None:
    self.loaded.add(path)
    with open(path, "rb") as f:
      tile = self.schema.Offline.from_bytes_packed(f.read(), traversal_limit_in_words=2**63 - 1)
    for w in tile.ways:
      nodes = [(c.latitude, c.longitude) for c in w.nodes]
      for a, b in zip(nodes, nodes[1:], strict=False):
        seg = (a, b, bearing_deg(a, b), w.name, w.oneWay, w.lanes)
        for cell in {(int(a[0] / CELL_DEG), int(a[1] / CELL_DEG)), (int(b[0] / CELL_DEG), int(b[1] / CELL_DEG))}:
          self.cells.setdefault(cell, []).append(seg)

  def _ensure(self, lat: float, lon: float) -> None:
    for path, (la0, lo0, la1, lo1) in self.tiles.items():
      if path not in self.loaded and la0 <= lat <= la1 and lo0 <= lon <= lo1:
        self._load(path)

  def match(self, lat: float, lon: float, heading: float) -> WayMatch | None:
    """The nearest segment within MATCH_M whose direction fits the car's heading, or None."""
    self._ensure(lat, lon)
    cx, cy = int(lat / CELL_DEG), int(lon / CELL_DEG)
    best = None
    for dx in (-1, 0, 1):
      for dy in (-1, 0, 1):
        for a, b, seg_bearing, name, one_way, lanes in self.cells.get((cx + dx, cy + dy), ()):
          d = segment_distance_m((lat, lon), a, b)
          if d < MATCH_M and (best is None or d < best.distance) and heading_fits(heading, seg_bearing, one_way):
            best = WayMatch(name=name, one_way=one_way, lanes=lanes, distance=d)
    return best
