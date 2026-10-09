from openpilot.sunnypilot.mapd.curve_anticipation import LEAD_S, MAX_RADIUS_M, anticipation

LAT, LON = -0.2200, -78.5120          # Quito's historic centre
NORTH = 0.0
M_PER_DEG = 110540.0


def point_ahead(metres, radius):
  return {"latitude": LAT + metres / M_PER_DEG, "longitude": LON, "curvature": 1.0 / radius}


def test_curve_within_lead_time_is_active():
  # 30 km/h is 8.3 m/s: 3 s ahead is 25 m, plus the in-curve margin
  a = anticipation(LAT, LON, NORTH, 30.0, [point_ahead(30.0, 120.0)])
  assert a.active and a.radius == 120.0 and round(a.time_to, 1) == 3.6


def test_curve_beyond_lead_time_is_not():
  assert not anticipation(LAT, LON, NORTH, 30.0, [point_ahead(30.0 * LEAD_S, 120.0)]).active


def test_straight_road_and_junction_noise_are_ignored():
  assert not anticipation(LAT, LON, NORTH, 30.0, [point_ahead(20.0, MAX_RADIUS_M * 2)]).active
  assert not anticipation(LAT, LON, NORTH, 30.0, [point_ahead(20.0, 5.0)]).active


def test_curve_behind_the_car_counts_only_while_inside_it():
  behind_near = {"latitude": LAT - 10.0 / M_PER_DEG, "longitude": LON, "curvature": 1 / 100.0}
  behind_far = {"latitude": LAT - 25.0 / M_PER_DEG, "longitude": LON, "curvature": 1 / 100.0}
  assert anticipation(LAT, LON, NORTH, 30.0, [behind_near]).active
  assert not anticipation(LAT, LON, NORTH, 30.0, [behind_far]).active


def test_only_at_speeds_where_it_would_act():
  pts = [point_ahead(15.0, 120.0)]
  assert not anticipation(LAT, LON, NORTH, 10.0, pts).active
  assert not anticipation(LAT, LON, NORTH, 60.0, pts).active
  assert not anticipation(LAT, LON, NORTH, 30.0, None).active
