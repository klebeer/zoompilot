"""
Copyright (c) 2026-, Zeph Leggett.

This file is part of zoompilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

mici's long indicator on whole pixels: upstream centres the car at 100 and it is 27 tall, so its top sits
at 86.5 and the textures blur across two rows. The stack draws half a pixel lower.
"""
import math

import raylib_nb as rl

from openpilot.selfdrive.ui.mici.onroad.long_indicator import LongIndicator
from openpilot.selfdrive.ui.ui_state import ui_state

STACK_TOP = 86.5  # upstream's car top
# seconds to the lead, from the farthest bar to the nearest
GAP_BARS = (2.0, 1.2)


def gap_bars(distance: float, v_ego: float) -> int:
  """How many distance bars the gap to the lead lights: three with room to spare, one when close."""
  gap = distance / max(v_ego, 1.0)
  return 1 + sum(gap >= threshold for threshold in GAP_BARS)


class LongIndicatorSP(LongIndicator):
  def _render(self, rect: rl.Rectangle) -> None:
    top = rect.y + STACK_TOP
    rect = rl.Rectangle(rect.x, rect.y + math.floor(top + 0.5) - top, rect.width, rect.height)
    if ui_state.has_longitudinal_control or not ui_state.started:
      super()._render(rect)
    else:
      self._render_lead_view(rect)

  def _render_lead_view(self, rect: rl.Rectangle) -> None:
    """Without openpilot longitudinal the car keeps no gap: show the lead the model sees and how far it is."""
    sm = ui_state.sm
    lead = sm['radarState'].leadOne
    has_lead = sm.alive['radarState'] and lead.present
    white_f, green_f = self._lead_car_filters
    white_alpha, green_alpha = white_f.update(0.0 if has_lead else 0.35), green_f.update(float(has_lead))
    white, green = self._txt_lead_car
    self._draw_centered(white, rect, 100, white_alpha)
    self._draw_centered(green, rect, 100, green_alpha)

    count = gap_bars(lead.dRel, sm['carState'].vEgo) if has_lead else 0
    for i, ((white_bar, _), y, (active_f, _)) in enumerate(zip(self._txt_distance, (122, 136, 152), self._distance_filters, strict=True)):
      active = active_f.update(float(i < count))
      self._draw_centered(white_bar, rect, y, 0.35 + 0.55 * active)
