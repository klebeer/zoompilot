import os
from types import SimpleNamespace

import capnp

from openpilot.sunnypilot.mapd.lane_map_shadow import MAX_SHIFT, sample, two_way_residual
from openpilot.sunnypilot.mapd.map_ways import SCHEMA_PATH, MapWays, WayMatch, heading_fits

LAT, LON = -0.16163, -78.49663
NORTH, SOUTH = 0.0, 180.0


def model(left_edge, right_edge, edge_std=0.3):
  edge = lambda y: SimpleNamespace(y=[y])  # noqa: E731
  return SimpleNamespace(roadEdges=[edge(left_edge), edge(right_edge)], roadEdgeStds=[edge_std, edge_std],
                         laneLines=[None] * 4, laneLineProbs=[0.0, 0.7, 0.05, 0.0])


TWO_WAY = WayMatch(name="Oe9a", one_way=False, lanes=0, distance=3.0)
ONE_WAY = WayMatch(name="Alonso de Torres", one_way=True, lanes=0, distance=3.0)


def test_residual_on_the_parking_exit():
  # Oe9a as logged: 7.6 m road, car centre 0.9 m right of the middle, left flank on the middle
  width, middle, remaining, over_middle = two_way_residual(-4.7, 2.9)
  assert round(width, 1) == 7.6 and round(middle, 1) == -0.9
  assert round(remaining, 1) == 0.6, "midway between the middle and the curb margin is 0.6 m further right"
  assert abs(over_middle) < 0.05


def test_records_only_on_a_shared_carriageway():
  assert sample(model(-4.7, 2.9), 30, True, TWO_WAY) is not None
  assert sample(model(-4.7, 2.9), 30, True, ONE_WAY) is None, "no oncoming traffic on a one-way carriageway"
  assert sample(model(-4.7, 2.9), 30, True, None) is None


def test_armed_only_while_steering_in_town_speeds():
  assert sample(model(-4.7, 2.9), 30, False, TWO_WAY) is None
  assert sample(model(-4.7, 2.9), 5, True, TWO_WAY) is None
  assert sample(model(-4.7, 2.9), 70, True, TWO_WAY) is None


def test_ignores_unsure_or_implausible_edges():
  assert sample(model(-4.7, 2.9, edge_std=1.5), 30, True, TWO_WAY) is None
  assert sample(model(-2.0, 2.0), 30, True, TWO_WAY) is None, "4 m is a single lane, not a shared road"
  assert sample(model(-9.0, 6.0), 30, True, TWO_WAY) is None, "15 m is a parking area or a median"


def test_would_apply_is_capped_and_never_left():
  far_left = sample(model(-6.5, 4.5), 30, True, TWO_WAY)    # 11 m road, car well left of the middle
  assert far_left["remaining_m"] > MAX_SHIFT and far_left["would_apply_m"] == MAX_SHIFT
  right_of_target = sample(model(-6.0, 0.5), 30, True, TWO_WAY)
  assert right_of_target["remaining_m"] < 0 and right_of_target["would_apply_m"] == 0.0


def test_one_way_segments_match_only_in_their_direction():
  assert heading_fits(NORTH, 0.0, one_way=True)
  assert not heading_fits(SOUTH, 0.0, one_way=True)
  assert heading_fits(NORTH, 0.0, one_way=False) and heading_fits(SOUTH, 0.0, one_way=False)


def write_tile(tiles_dir, ways):
  """A packed offline tile around LAT, LON with the given (name, one_way, [(lat, lon), ...]) ways."""
  schema = capnp.load(SCHEMA_PATH)
  tile = schema.Offline.new_message(minLat=-0.25, minLon=-78.5, maxLat=0.0, maxLon=-78.25)
  ws = tile.init("ways", len(ways))
  for w, (name, one_way, nodes) in zip(ws, ways, strict=True):
    w.name, w.oneWay = name, one_way
    ns = w.init("nodes", len(nodes))
    for n, (la, lo) in zip(ns, nodes, strict=True):
      n.latitude, n.longitude = la, lo
  path = os.path.join(tiles_dir, "-2", "-80", "-0.250000_-78.500000_0.000000_-78.250000")
  os.makedirs(os.path.dirname(path))
  with open(path, "wb") as f:
    tile.write_packed(f)


def test_divided_road_matches_the_carriageway_the_car_is_on(tmp_path):
  # Alonso de Torres: two one-way carriageways about 10 m apart, northbound east of southbound
  east, west = LON + 0.00005, LON - 0.00004
  write_tile(str(tmp_path), [("Alonso de Torres", True, [(LAT - 0.001, east), (LAT + 0.001, east)]),
                             ("Alonso de Torres", True, [(LAT + 0.001, west), (LAT - 0.001, west)])])
  ways = MapWays(str(tmp_path))
  # the car sits nearer the northbound carriageway but drives south: it must match the southbound one
  hit = ways.match(LAT, LON + 0.00002, SOUTH)
  assert hit is not None and hit.one_way
  north = ways.match(LAT, LON + 0.00002, NORTH)
  assert north is not None and north.distance < hit.distance, "northbound is the nearer carriageway"


def test_no_tile_means_no_match(tmp_path):
  assert MapWays(str(tmp_path)).match(LAT, LON, NORTH) is None
