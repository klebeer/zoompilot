from types import SimpleNamespace

from openpilot.sunnypilot.modeld_v2.context_offset import MAX_SHIFT, RELEASE_RATE, RIGHT_VEHICLE_HOLD, TOWN_BIAS, ContextOffset

CX5 = "MAZDA_CX5_2022_NON_MRCC"
TOWN = 30 / 3.6


def model(left_line=-1.5, right_line=1.5, right_edge=3.4, p_left=0.8, p_right=0.1, edge_std=0.6, leads=()):
  def line(y):
    return SimpleNamespace(y=[y])
  return SimpleNamespace(laneLines=[line(-5.0), line(left_line), line(right_line), line(5.0)],
                         laneLineProbs=[0.0, p_left, p_right, 0.0],
                         roadEdges=[line(-6.0), line(right_edge)],
                         roadEdgeStds=[1.0, edge_std],
                         leadsV3=[SimpleNamespace(prob=0.9, x=[x], y=[y]) for x, y in leads])


def run(co, msg, v, seconds, base=0.0):
  # modeld asks for the offset before each model run and updates after it
  for _ in range(round(seconds / 0.05)):
    co.camera_offset(base)
    shift = co.update(msg, v)
  return shift


def capped():
  co = ContextOffset(CX5, 0.05)
  run(co, model(), TOWN, 30.0)
  assert co.shift == MAX_SHIFT
  return co


def lead_on_the_right(co, real_y, x=30.0):
  # leads come in the shifted model frame: right of the car by real_y once the offset in use is added back
  return (x, real_y - co.camera_offset(0.0))


def test_moves_right_toward_the_curb_when_the_right_line_is_missing():
  # centre between left line (-1.5) and curb-margin (3.4 - 0.8) is 0.55 m right: capped at MAX_SHIFT
  assert run(ContextOffset(CX5, 0.05), model(), TOWN, 30.0) == MAX_SHIFT


def test_ramps_slowly():
  assert abs(run(ContextOffset(CX5, 0.05), model(), TOWN, 2.0) - 0.1) < 1e-6


def test_stays_put_when_the_right_line_is_painted():
  assert run(ContextOffset(CX5, 0.05), model(p_right=0.9), TOWN, 30.0) == 0.0


def test_no_shift_when_already_centred_to_the_curb():
  assert run(ContextOffset(CX5, 0.05), model(right_edge=1.7), TOWN, 30.0) == 0.0


def test_ignores_an_uncertain_edge_and_high_speed():
  assert run(ContextOffset(CX5, 0.05), model(edge_std=1.5), TOWN, 30.0) == 0.0
  assert run(ContextOffset(CX5, 0.05), model(), 70 / 3.6, 30.0) == 0.0


def test_fades_back_when_the_line_reappears():
  co = ContextOffset(CX5, 0.05)
  run(co, model(), TOWN, 30.0)
  assert run(co, model(p_right=0.9), TOWN, 30.0) == 0.0


def test_camera_offset_sign_and_other_cars():
  co = ContextOffset(CX5, 0.05)
  run(co, model(), TOWN, 30.0)
  assert co.camera_offset(0.0) == -TOWN_BIAS - MAX_SHIFT
  assert run(ContextOffset("MAZDA_CX5_2022", 0.05), model(), TOWN, 30.0) == 0.0


def test_town_bias_fades_out_with_speed():
  co = ContextOffset(CX5, 0.05)
  for kph, bias in ((30, TOWN_BIAS), (50, TOWN_BIAS), (60, TOWN_BIAS / 2), (70, 0.0), (100, 0.0)):
    run(co, model(p_right=0.9), kph / 3.6, 0.05)
    assert abs(co.camera_offset(0.0) + bias) < 1e-6, kph


def test_no_town_bias_on_other_cars():
  co = ContextOffset("MAZDA_CX5_2022", 0.05)
  run(co, model(p_right=0.9), TOWN, 0.05)
  assert co.camera_offset(0.0) == 0.0


def test_releases_faster_than_it_ramps():
  co = capped()
  assert abs(run(co, model(p_right=0.9), TOWN, 1.0) - (MAX_SHIFT - RELEASE_RATE)) < 1e-6
  assert run(co, model(p_right=0.9), TOWN, 1.0) == 0.0


def test_a_vehicle_ahead_on_the_right_takes_the_shift_off():
  co = capped()
  assert run(co, model(leads=[lead_on_the_right(co, 2.5)]), TOWN, 1.0) == 0.0


def test_stays_off_until_the_vehicle_is_passed():
  co = capped()
  run(co, model(leads=[lead_on_the_right(co, 2.5)]), TOWN, 1.0)
  assert run(co, model(), TOWN, RIGHT_VEHICLE_HOLD - 0.1) == 0.0
  assert run(co, model(), TOWN, 1.1) > 0.0


def test_ignores_vehicles_in_the_lane_oncoming_or_far():
  co = capped()
  for real_y, x in ((0.0, 30.0), (-2.5, 30.0), (2.5, 60.0), (5.5, 20.0)):
    assert run(co, model(leads=[lead_on_the_right(co, real_y, x)]), TOWN, 1.0) == MAX_SHIFT, (real_y, x)
