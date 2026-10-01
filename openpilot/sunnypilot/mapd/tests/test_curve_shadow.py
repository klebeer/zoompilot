from openpilot.sunnypilot.mapd.curve_shadow import Shadow, load_known_curves
from openpilot.sunnypilot.mapd.curve_warning import known_curve_ahead, tight_curve_ahead

# car at the origin heading north; one degree of latitude is about 111 km
LAT, LON = -0.16, -78.50
NORTH = 0.0


def point(north_m, east_m, radius_m):
  return {"latitude": LAT + north_m / 111000.0, "longitude": LON + east_m / 111000.0, "curvature": 1.0 / radius_m}


def test_finds_the_tightest_curve_ahead():
  hit = tight_curve_ahead(LAT, LON, NORTH, [point(60, 0, 30), point(80, 5, 12), point(40, 0, 200)])
  assert hit is not None and round(hit.radius) == 12 and 75 < hit.distance < 85


def test_ignores_curves_behind_too_far_or_gentle():
  assert tight_curve_ahead(LAT, LON, NORTH, [point(-50, 0, 10)]) is None
  assert tight_curve_ahead(LAT, LON, NORTH, [point(150, 0, 10)]) is None
  assert tight_curve_ahead(LAT, LON, NORTH, [point(50, 0, 60)]) is None


def test_shadow_fires_once_per_curve():
  s, curve = Shadow(), [point(70, 0, 12)]
  assert s.step(0.0, True, 35, LAT, LON, NORTH, curve, "Av") is not None
  assert s.step(5.0, True, 35, LAT, LON, NORTH, curve, "Av") is None
  assert s.step(30.0, True, 35, LAT, LON, NORTH, curve, "Av") is None, "same curve inside 60 s"
  assert s.step(70.0, True, 35, LAT, LON, NORTH, curve, "Av") is not None


def test_shadow_waits_20_s_between_curves():
  s = Shadow()
  assert s.step(0.0, True, 35, LAT, LON, NORTH, [point(70, 0, 12)], None) is not None
  assert s.step(10.0, True, 35, LAT, LON, NORTH, [point(90, 40, 10)], None) is None
  assert s.step(25.0, True, 35, LAT, LON, NORTH, [point(90, 40, 10)], None) is not None


def test_shadow_only_while_steering_in_town_speeds():
  curve = [point(70, 0, 12)]
  assert Shadow().step(0.0, False, 35, LAT, LON, NORTH, curve, None) is None
  assert Shadow().step(0.0, True, 5, LAT, LON, NORTH, curve, None) is None
  assert Shadow().step(0.0, True, 80, LAT, LON, NORTH, curve, None) is None


def test_record_carries_the_rule():
  rec = Shadow(known_curves=[]).step(0.0, True, 35, LAT, LON, NORTH, [point(70, 0, 12)], "Stacey Leonor")
  assert rec["road"] == "Stacey Leonor" and rec["radius_m"] == 12.0 and rec["rule"]["min_radius_m"] == 45.0
  assert rec["rules"] == ["map"]


def place(north_m, east_m):
  return {"latitude": LAT + north_m / 111000.0, "longitude": LON + east_m / 111000.0}


def test_known_curve_takes_the_nearest_ahead():
  hit = known_curve_ahead(LAT, LON, NORTH, [place(90, 0), place(40, 0)])
  assert hit is not None and 35 < hit.distance < 45


def test_known_curve_ignores_behind_and_too_far():
  assert known_curve_ahead(LAT, LON, NORTH, [place(-40, 0)]) is None
  assert known_curve_ahead(LAT, LON, NORTH, [place(150, 0)]) is None


def test_learned_curve_fires_where_the_map_says_nothing():
  # a gentle curve the map rule skips, at a place that was hard on earlier drives
  s = Shadow(known_curves=[place(60, 0)])
  rec = s.step(0.0, True, 35, LAT, LON, NORTH, [point(60, 0, 200)], "Oe9a")
  assert rec is not None and rec["rules"] == ["learned"] and rec["radius_m"] == 0.0


def test_both_rules_recorded_and_the_nearer_leads():
  s = Shadow(known_curves=[place(30, 0)])
  rec = s.step(0.0, True, 35, LAT, LON, NORTH, [point(80, 0, 12)], None)
  assert rec["rules"] == ["map", "learned"] and 25 < rec["distance_m"] < 35, "the learned point is nearer"


def test_no_curve_list_behaves_exactly_as_before():
  s = Shadow(known_curves=[])
  assert s.step(0.0, True, 35, LAT, LON, NORTH, [point(60, 0, 200)], None) is None
  assert s.step(0.0, True, 35, LAT, LON, NORTH, [point(70, 0, 12)], None) is not None


def test_missing_curve_file_is_an_empty_list():
  assert load_known_curves("/nonexistent/known_hard_curves.json") == []
