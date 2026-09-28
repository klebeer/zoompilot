from openpilot.sunnypilot.mapd.curve_shadow import Shadow
from openpilot.sunnypilot.mapd.curve_warning import tight_curve_ahead

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
  rec = Shadow().step(0.0, True, 35, LAT, LON, NORTH, [point(70, 0, 12)], "Stacey Leonor")
  assert rec["road"] == "Stacey Leonor" and rec["radius_m"] == 12.0 and rec["rule"]["min_radius_m"] == 45.0
