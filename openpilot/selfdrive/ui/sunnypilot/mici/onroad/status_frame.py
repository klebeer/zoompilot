"""
A frame around the camera view that shows who is driving, with each half taking over for that side's
blinker and blind spot. Frame idea and colors after StarPilot's onroad border (MIT, firestar5683 and
StarPilot contributors).
"""
import raylib_nb as rl

from openpilot.selfdrive.ui.mici.onroad.status import status_fade_mads
from openpilot.selfdrive.ui.sunnypilot.onroad.augmented_road_view import BORDER_COLORS_SP
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus

THICKNESS = 6
CORNER_RADIUS = 24.5  # outer edge, the radius of the black mask that rounds the camera view
SEGMENTS = 10

STATUS_COLORS = {
  UIStatus.DISENGAGED: rl.Color(0, 0, 0, 0),
  UIStatus.ENGAGED: rl.Color(22, 134, 68, 255),
  UIStatus.OVERRIDE: rl.Color(145, 155, 149, 255),
  **BORDER_COLORS_SP,
}
BLINKER_AMBER = rl.Color(255, 200, 0, 255)
BLINDSPOT_RED = rl.Color(214, 32, 48, 255)
BLINK_HALF_PERIOD = 0.5
# the driver is signaling toward an occupied lane
BLINK_HALF_PERIOD_WARNING = 0.25


def side_color(blinker: bool, blindspot: bool, now: float) -> rl.Color | None:
  """What one half of the frame shows instead of the status color, if anything."""
  show_blindspot = blindspot and ui_state.blindspot
  show_blinker = blinker and ui_state.turn_signals
  if show_blindspot and blinker:
    return BLINDSPOT_RED if now % (2 * BLINK_HALF_PERIOD_WARNING) < BLINK_HALF_PERIOD_WARNING else None
  if show_blindspot:
    return BLINDSPOT_RED
  if show_blinker and now % (2 * BLINK_HALF_PERIOD) < BLINK_HALF_PERIOD:
    return BLINKER_AMBER
  return None


class StatusFrame:
  @staticmethod
  def _draw(rect: rl.Rectangle, color: rl.Color) -> None:
    if color.a == 0:
      return
    # the lines grow outward from the rectangle they are given
    inner = rl.Rectangle(rect.x + THICKNESS, rect.y + THICKNESS, rect.width - 2 * THICKNESS, rect.height - 2 * THICKNESS)
    roundness = 2 * (CORNER_RADIUS - THICKNESS) / min(inner.width, inner.height)
    rl.draw_rectangle_rounded_lines_ex(inner, roundness, SEGMENTS, THICKNESS, color)

  def render(self, rect: rl.Rectangle) -> None:
    self._draw(rect, status_fade_mads.color(STATUS_COLORS))

    car_state = ui_state.sm['carState']
    now = rl.get_time()
    half = rect.width / 2
    for x, blinker, blindspot in ((rect.x, car_state.leftBlinker, car_state.leftBlindspot),
                                  (rect.x + half, car_state.rightBlinker, car_state.rightBlindspot)):
      color = side_color(blinker, blindspot, now)
      if color is not None:
        rl.begin_scissor_mode(int(x), int(rect.y), int(half), int(rect.height))
        self._draw(rect, color)
        rl.end_scissor_mode()
