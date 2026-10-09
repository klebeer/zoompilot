import math

from openpilot.sunnypilot.mapd.curve_shadow import RouteCurveLog
from openpilot.sunnypilot.mapd.route_curves import curves, held_kph

LAT0, LON0 = -0.2201, -78.5123
M = math.radians(1.0) * 6371000.0


def at(x, y):
  return [LAT0 + y / M, LON0 + x / (M * math.cos(math.radians(LAT0)))]


def bend(radius, turn_deg=90.0, right=True):
  """North 200 m, a bend of `radius` to one side, then 200 m on the new heading."""
  pts = [at(0, y) for y in range(0, 201, 10)]
  cx = radius if right else -radius
  steps = max(4, int(radius * math.radians(turn_deg) / 2))
  for k in range(1, steps + 1):
    a = math.radians(turn_deg) * k / steps
    pts.append(at(cx - math.copysign(radius * math.cos(a), cx), 200 + radius * math.sin(a)))
  h = math.radians(turn_deg if right else -turn_deg)
  end = (cx - math.copysign(radius * math.cos(math.radians(turn_deg)), cx), 200 + radius * math.sin(math.radians(turn_deg)))
  return pts + [at(end[0] + math.sin(h) * d, end[1] + math.cos(h) * d) for d in range(10, 201, 10)]


def test_a_bend_is_one_curve_with_its_radius_side_and_apex():
  found = curves(bend(30.0))
  assert len(found) == 1
  c = found[0]
  assert 27 < c["radius_m"] < 33 and c["side"] == "right"
  assert 215 < c["apex_m"] < 235 and c["start_m"] < c["apex_m"] < c["end_m"]
  assert curves(bend(12.0, right=False))[0]["side"] == "left"
  assert curves([at(0, y) for y in range(0, 501, 10)]) == [], "a straight road has none"
  assert curves(bend(400.0)) == [], "gentler than CURVE_R_M"


def test_held_speed_by_radius():
  assert [held_kph(r) for r in (10, 25, 40, 70, 200)] == [10.0, 20.0, 30.0, 40.0, 50.0]


def nav(progress, route_id="perucho", state="following"):
  return {"state": state, "route_id": route_id, "progress_m": progress}


def test_each_curve_is_logged_once_ahead_and_once_at_its_apex():
  log = RouteCurveLog({"perucho": bend(30.0)})
  assert log.records(nav(50.0), 35.0, True, None, False, 0.0) == [], "still beyond the lookahead"
  ahead = log.records(nav(110.0), 35.0, True, None, False, 0.0)
  assert [(r["event"], r["held_kph"], r["side"]) for r in ahead] == [("ahead", 20.0, "right")] and 100 < ahead[0]["distance_m"] < 125
  assert log.records(nav(150.0), 30.0, True, None, False, 0.0) == [], "already told"
  apex = log.records(nav(240.0), 18.0, False, "right", True, 45.0)
  assert [(r["event"], r["lat_active"], r["steering_pressed"], r["steering_torque"]) for r in apex] == [("apex", False, True, 45.0)]
  assert log.records(nav(250.0), 20.0, True, None, False, 0.0) == []
  assert log.records(nav(10.0, state="off_route"), 20.0, True, None, False, 0.0) == []
  assert log.records(nav(10.0, route_id="unknown"), 20.0, True, None, False, 0.0) == [], "a route with no shape has no curves"
