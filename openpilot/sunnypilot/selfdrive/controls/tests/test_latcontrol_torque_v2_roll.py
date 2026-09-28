from openpilot.sunnypilot.selfdrive.controls.lib.latcontrol_torque_v2 import get_roll_comp_scale

CX5 = "MAZDA_CX5_2022_NON_MRCC"


def test_half_roll_compensation_in_town():
  assert get_roll_comp_scale(CX5, 25 / 3.6) == 0.5
  assert get_roll_comp_scale(CX5, 40 / 3.6) == 0.5


def test_full_roll_compensation_from_52_kph():
  assert get_roll_comp_scale(CX5, 52 / 3.6) == 1.0
  assert get_roll_comp_scale(CX5, 90 / 3.6) == 1.0


def test_blends_between():
  assert 0.5 < get_roll_comp_scale(CX5, 46 / 3.6) < 1.0


def test_other_cars_unchanged():
  assert get_roll_comp_scale("MAZDA_CX5_2022", 25 / 3.6) == 1.0
