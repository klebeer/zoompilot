"""
While the route and the blinker confirm a turn and the driver pushes the wheel toward it, the comma does not steer
against the driver.

In a tight corner the model often plans a wider line than the one the driver takes. With the comma steering and the
driver helping through route turns, the comma's torque opposed the driver in 44% of the pressed frames, at a median of
38% of maximum torque. The side of such a turn is already confirmed twice, by the route (route_desire_live) and by the
blinker, so torque against a push toward that side is dropped: the comma may only help toward the turn or stay at
zero. Off unless ENABLE_PATH exists: `rm` it to go back, no reboot.
"""
import os
import time

from openpilot.common.hardware import PC
from openpilot.sunnypilot.mapd.route_desire_live import Reader

ENABLE_PATH = os.path.expanduser("~/.comma/zoompilot_turn_yield_on") if PC else "/data/zoompilot_turn_yield_on"
CHECK_EVERY_NS = 1_000_000_000  # how often the switch file is looked at
PUSH_ON = 15.0                  # driver torque toward the turn, in car units, that starts yielding
PUSH_OFF = 8.0                  # and below which yielding stops


class TurnYield:
  def __init__(self, route=None):
    self.route = route if route is not None else Reader()
    self.enabled = False
    self.checked_ns: int | None = None
    self.active = False

  def update(self, torque: float, steering_torque: float, left_blinker: bool, right_blinker: bool, now_ns: int | None = None) -> float:
    """The torque command to send. `torque` and `steering_torque` are positive to the left."""
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    if self.checked_ns is None or now_ns - self.checked_ns >= CHECK_EVERY_NS:
      self.checked_ns = now_ns
      self.enabled = os.path.exists(ENABLE_PATH)
    side = self.route.get(now_ns) if self.enabled else None
    blinker = "left" if left_blinker and not right_blinker else "right" if right_blinker and not left_blinker else None
    if side is None or blinker != side:
      self.active = False
      return torque
    toward = steering_torque if side == "left" else -steering_torque
    self.active = toward > (PUSH_OFF if self.active else PUSH_ON)
    if not self.active:
      return torque
    return max(torque, 0.0) if side == "left" else min(torque, 0.0)
