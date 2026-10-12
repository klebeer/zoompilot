import pytest

from openpilot.sunnypilot.selfdrive.controls.lib import turn_yield
from openpilot.sunnypilot.selfdrive.controls.lib.turn_yield import TurnYield, CHECK_EVERY_NS


class FixedRoute:
  """Stands in for route_desire_live.Reader: the side the route confirms, or None."""
  def __init__(self, side):
    self.side = side

  def get(self, now_ns=None):
    return self.side


@pytest.fixture
def switch(tmp_path, monkeypatch):
  path = tmp_path / "zoompilot_turn_yield_on"
  monkeypatch.setattr(turn_yield, "ENABLE_PATH", str(path))
  return path


def right_turn(switch):
  switch.touch()
  return TurnYield(FixedRoute("right"))


def test_switch_off_leaves_the_command(switch):
  ty = TurnYield(FixedRoute("right"))
  assert ty.update(0.4, -20.0, False, True, now_ns=0) == 0.4


def test_push_toward_the_turn_drops_torque_against_it(switch):
  ty = right_turn(switch)
  assert ty.update(0.4, -20.0, False, True, now_ns=0) == 0.0


def test_torque_helping_the_turn_is_kept(switch):
  ty = right_turn(switch)
  assert ty.update(-0.3, -20.0, False, True, now_ns=0) == -0.3


def test_left_turn_mirrors(switch):
  switch.touch()
  ty = TurnYield(FixedRoute("left"))
  assert ty.update(-0.4, 20.0, True, False, now_ns=0) == 0.0
  assert ty.update(0.3, 20.0, True, False, now_ns=1) == 0.3


def test_push_against_the_turn_leaves_the_command(switch):
  ty = right_turn(switch)
  assert ty.update(-0.4, 20.0, False, True, now_ns=0) == -0.4


def test_light_hands_do_not_yield(switch):
  ty = right_turn(switch)
  assert ty.update(0.4, -10.0, False, True, now_ns=0) == 0.4


@pytest.mark.parametrize("route_side, left, right", [(None, False, True), ("right", True, False), ("right", False, False), ("right", True, True)])
def test_needs_the_route_and_the_blinker_to_the_same_side(switch, route_side, left, right):
  switch.touch()
  ty = TurnYield(FixedRoute(route_side))
  assert ty.update(0.4, -20.0, left, right, now_ns=0) == 0.4


def test_hysteresis_between_push_on_and_push_off(switch):
  ty = right_turn(switch)
  assert ty.update(0.4, -20.0, False, True, now_ns=0) == 0.0
  assert ty.update(0.4, -10.0, False, True, now_ns=1) == 0.0
  assert ty.update(0.4, -5.0, False, True, now_ns=2) == 0.4
  assert ty.update(0.4, -10.0, False, True, now_ns=3) == 0.4


def test_blinker_off_ends_yielding(switch):
  ty = right_turn(switch)
  assert ty.update(0.4, -20.0, False, True, now_ns=0) == 0.0
  assert ty.update(0.4, -20.0, False, False, now_ns=1) == 0.4
  assert not ty.active


def test_switch_is_read_at_most_once_per_interval(switch):
  ty = TurnYield(FixedRoute("right"))
  assert ty.update(0.4, -20.0, False, True, now_ns=0) == 0.4
  switch.touch()
  assert ty.update(0.4, -20.0, False, True, now_ns=CHECK_EVERY_NS - 1) == 0.4
  assert ty.update(0.4, -20.0, False, True, now_ns=CHECK_EVERY_NS) == 0.0
  switch.unlink()
  assert ty.update(0.4, -20.0, False, True, now_ns=2 * CHECK_EVERY_NS) == 0.4
