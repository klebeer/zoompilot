"""
The current speed limit as a road sign over the camera view, labelled with where it came from.
Source label and change pulse after StarPilot's speed limit widget (MIT, firestar5683 and StarPilot contributors).
"""
import raylib_nb as rl

from openpilot.cereal import custom
from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

Source = custom.LongitudinalPlanSP.SpeedLimit.Source
# DASH: the car's own traffic sign recognition. OSM: the offline map.
SOURCE_LABELS = {Source.car: "DASH", Source.map: "OSM"}

RADIUS = 30
RING = 6
MARGIN_TOP = 10
MARGIN_RIGHT = 14
LABEL_SIZE = 16
PULSE_SECONDS = 3.0
PULSE_COLOR = rl.Color(160, 110, 255, 255)
SIGN_RED = rl.Color(214, 32, 48, 255)


class SpeedLimitSign:
  def __init__(self):
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_semi_bold = gui_app.font(FontWeight.SEMI_BOLD)
    self._alpha = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._value = 0
    self._label = ""
    self._changed_time = float('-inf')

  def _update(self) -> bool:
    resolver = ui_state.sm['longitudinalPlanSP'].speedLimit.resolver
    if not ui_state.speed_limit_mode or not resolver.speedLimitValid:
      return False
    value = round(resolver.speedLimit * (CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH))
    if value != self._value:
      # a sign that just faded in is new by itself
      if self._alpha.x > 0.5:
        self._changed_time = rl.get_time()
      self._value = value
    offset = round(resolver.speedLimitOffset * (CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH))
    self._label = SOURCE_LABELS.get(resolver.source.raw, "") + (f" {offset:+d}" if offset else "")
    return True

  def render(self, rect: rl.Rectangle, visible: bool) -> None:
    alpha = self._alpha.update(float(self._update() and visible))
    if alpha < 1e-2:
      return

    center = rl.Vector2(rect.x + rect.width - MARGIN_RIGHT - RADIUS, rect.y + MARGIN_TOP + RADIUS)
    pulse = max(0.0, 1 - (rl.get_time() - self._changed_time) / PULSE_SECONDS)
    if pulse > 0:
      rl.draw_circle_v(center, RADIUS + 5, rl.fade(PULSE_COLOR, alpha * pulse))
    rl.draw_circle_v(center, RADIUS, rl.fade(SIGN_RED, alpha))
    rl.draw_circle_v(center, RADIUS - RING, rl.fade(rl.WHITE, alpha))

    text = str(self._value)
    size = 34 if len(text) < 3 else 26
    text_size = measure_text_cached(self._font_bold, text, size)
    rl.draw_text_ex(self._font_bold, text, rl.Vector2(center.x - text_size.x / 2, center.y - text_size.y / 2), size, 0,
                    rl.fade(rl.BLACK, alpha))

    if self._label:
      label_size = measure_text_cached(self._font_semi_bold, self._label, LABEL_SIZE)
      pos = rl.Vector2(center.x - label_size.x / 2, center.y + RADIUS + 3)
      rl.draw_text_ex(self._font_semi_bold, self._label, rl.Vector2(pos.x + 1, pos.y + 1), LABEL_SIZE, 0, rl.fade(rl.BLACK, alpha * 0.7))
      rl.draw_text_ex(self._font_semi_bold, self._label, pos, LABEL_SIZE, 0, rl.fade(rl.WHITE, alpha * 0.9))
