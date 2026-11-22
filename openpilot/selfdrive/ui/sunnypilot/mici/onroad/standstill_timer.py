"""How long the car has been stopped, over the top of the camera view."""
import raylib_nb as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad.status import lerp_color
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

# a stop this short is a pause in traffic, not worth a counter
MIN_SECONDS = 5
FONT_SIZE = 40
MARGIN_TOP = 12
GREEN = rl.Color(0, 255, 64, 255)
ORANGE = rl.Color(255, 115, 0, 255)
RED = rl.Color(255, 0, 21, 255)


def timer_color(seconds: float) -> rl.Color:
  """Green through the first minute, orange by three, red by five."""
  if seconds < 180:
    return lerp_color(GREEN, ORANGE, max(0.0, (seconds - 60) / 120))
  return lerp_color(ORANGE, RED, min(1.0, (seconds - 180) / 120))


class StandstillTimer:
  def __init__(self):
    self._font = gui_app.font(FontWeight.BOLD)
    self._alpha = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._start_ns: int | None = None
    self._seconds = 0.0

  def _update(self) -> bool:
    sm = ui_state.sm
    if not ui_state.standstill_timer or not sm['carState'].standstill:
      self._start_ns = None
      return False
    # the log clock, so a replayed drive counts the same as the live one
    now_ns = sm.logMonoTime['carState']
    if self._start_ns is None or now_ns < self._start_ns:
      self._start_ns = now_ns
    self._seconds = (now_ns - self._start_ns) / 1e9
    return self._seconds >= MIN_SECONDS

  def render(self, rect: rl.Rectangle, visible: bool, below: float | None = None) -> None:
    """below: the bottom of whatever holds the top of the view, to draw under it."""
    alpha = self._alpha.update(float(self._update() and visible))
    if alpha < 1e-2:
      return
    text = f"{int(self._seconds) // 60}:{int(self._seconds) % 60:02d}"
    size = measure_text_cached(self._font, text, FONT_SIZE)
    pos = rl.Vector2(rect.x + (rect.width - size.x) / 2, (rect.y if below is None else below) + MARGIN_TOP)
    rl.draw_text_ex(self._font, text, rl.Vector2(pos.x + 2, pos.y + 2), FONT_SIZE, 0, rl.fade(rl.BLACK, alpha * 0.7))
    rl.draw_text_ex(self._font, text, pos, FONT_SIZE, 0, rl.fade(timer_color(self._seconds), alpha))
