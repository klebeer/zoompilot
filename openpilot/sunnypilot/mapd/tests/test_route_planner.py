import os

import pytest

from openpilot.sunnypilot.mapd.route_planner import Planner, decode_shape, leg_to_route

# A planner directory laid out as on the device (site-packages/ and valhalla_tiles/); the routing test runs only
# where one exists
PLANNER_BASE = os.environ.get("PLANNER_BASE", "")


def encode(points):
  """Polyline6, the inverse of decode_shape."""
  out, prev = [], (0, 0)
  for lat, lon in points:
    cur = (round(lat * 1e6), round(lon * 1e6))
    for delta in (cur[0] - prev[0], cur[1] - prev[1]):
      v = ~(delta << 1) if delta < 0 else delta << 1
      while v >= 0x20:
        out.append(chr((0x20 | (v & 0x1F)) + 63))
        v >>= 5
      out.append(chr(v + 63))
    prev = cur
  return "".join(out)


def test_decode_shape_reads_valhalla_polyline6():
  points = [[-0.2201, -78.5123], [-0.22009, -78.51228], [-0.2146, -78.5072]]
  assert decode_shape(encode(points)) == points


def test_leg_to_route_names_the_maneuvers_as_navinstruction_does():
  leg = {"shape": encode([[1.0, 2.0], [1.00001, 2.0], [1.00001, 2.00001]]),
         "maneuvers": [{"type": 1, "begin_shape_index": 0, "bearing_after": 360, "street_names": ["Uno"]},
                       {"type": 10, "begin_shape_index": 1, "bearing_before": 0, "bearing_after": 90, "street_names": ["Dos"]},
                       {"type": 4, "begin_shape_index": 2}]}
  route = leg_to_route(leg, "r", "label")
  assert [(m["type"], m["modifier"], m["turn_deg"], m["street"]) for m in route["maneuvers"]] == [
    ("depart", None, 0.0, "Uno"), ("turn", "right", 90.0, "Dos"), ("arrive", None, 0.0, "")]
  assert route["maneuvers"][0]["bearing_after"] == 0, "due north is 0, not 360"


@pytest.mark.skipif(not os.path.isdir(os.path.join(PLANNER_BASE, "valhalla_tiles")), reason="no planner directory")
def test_a_route_across_quito_from_the_cars_heading():
  planner = Planner(PLANNER_BASE)
  assert planner.available()
  # Plaza Grande to the Basilica, about 1.5 km
  route = planner.route((-0.2201, -78.5123), 0.0, (-0.2146, -78.5072), "r", "label")
  assert 800 < route["length_m"] < 4000
  assert route["maneuvers"][0]["type"] == "depart" and route["maneuvers"][-1]["type"] == "arrive"
