from types import SimpleNamespace

from openpilot.sunnypilot.modeld_v2.context_offset import MAX_SHIFT, ContextOffset

CX5 = "MAZDA_CX5_2022_NON_MRCC"
TOWN = 30 / 3.6


def model(left_line=-1.5, right_line=1.5, right_edge=3.4, p_left=0.8, p_right=0.1, edge_std=0.6):
  line = lambda y: SimpleNamespace(y=[y])
  return SimpleNamespace(laneLines=[line(-5.0), line(left_line), line(right_line), line(5.0)],
                         laneLineProbs=[0.0, p_left, p_right, 0.0],
                         roadEdges=[line(-6.0), line(right_edge)],
                         roadEdgeStds=[1.0, edge_std])


def run(co, msg, v, seconds):
  for _ in range(int(seconds / 0.05)):
    shift = co.update(msg, v)
  return shift


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
  assert co.camera_offset(-0.10) == -0.10 - MAX_SHIFT
  assert run(ContextOffset("MAZDA_CX5_2022", 0.05), model(), TOWN, 30.0) == 0.0
