"""
The next maneuver as a card over the top of the camera view. Layout after StarPilot's turn-by-turn card
(MIT, firestar5683 and StarPilot contributors); the arrows are drawn, not loaded.
"""
import math
import raylib_nb as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad.navigation import format_distance
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets.label import UnifiedLabel

MARGIN = 10
LEFT = 78  # past the DMoji in the top left corner
WIDTH = 304  # leaves the top right corner to the speed limit sign
HEIGHT = 86
ICON_BOX = 62
BACKGROUND = rl.Color(13, 13, 28, 235)
OUTLINE = rl.Color(122, 98, 190, 255)
ACCENT = rl.Color(155, 230, 185, 255)
MUTED = rl.Color(190, 196, 215, 255)
STROKE = 6
HEAD_LENGTH = 9
HEAD_ANGLE = math.radians(40)

# Paths on a grid of about +-15 around the icon center, y down, drawn for a maneuver to the right.
PATHS = {
  'straight': [(0, 14), (0, -13)],
  'slight': [(-5, 14), (-5, 1), (7, -12)],
  'turn': [(-9, 14), (-9, -8), (10, -8)],
  'sharp': [(-7, 14), (-7, -12), (8, 4)],
  'uturn': [(-8, 14), (-8, -9), (8, -9), (8, 7)],
}
# a second maneuver this soon after the next one is announced with it
THEN_WITHIN_M = 400
THEN_WIDTH = 76
LANE_ROW = 36  # added to the card while the instruction carries lanes
LANE_BOX = 28
LANE_GAP = 5
MAX_LANES = 7
# NavInstruction.Direction -> the arrow of the same bend
LANE_ARROWS = {'left': ('turn', 'left'), 'right': ('turn', 'right'), 'straight': ('', 'straight'),
               'slightLeft': ('turn', 'slight left'), 'slightRight': ('turn', 'slight right')}
ROUNDABOUT_TYPES = ('roundabout', 'rotary', 'roundabout turn', 'exit roundabout', 'exit rotary')


def maneuver_path(maneuver_type: str, modifier: str) -> list[tuple[float, float]]:
  """The arrow for a Mapbox maneuver type and modifier, mirrored for the left."""
  shape = next((name for name in ('uturn', 'sharp', 'slight') if name in modifier), None)
  if shape is None:
    shape = 'turn' if modifier in ('left', 'right') else 'straight'
  # a U-turn bends to the left as drawn, on the side traffic drives here
  flip = -1 if ('left' in modifier) != (shape == 'uturn') else 1
  return [(flip * x, y) for x, y in PATHS[shape]]


def following_maneuver(maneuvers):
  """The maneuver right after the next one, when the two come close together."""
  ahead = [m for m in maneuvers if m.distance >= 0]
  if len(ahead) > 1 and ahead[1].distance - ahead[0].distance <= THEN_WITHIN_M and ahead[1].type != 'arrive':
    return ahead[1]
  return None


class TurnCard:
  def __init__(self):
    self._font_bold = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)
    self._alpha = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._street = UnifiedLabel('', 22, FontWeight.MEDIUM, MUTED, scroll=True)
    self._height = HEIGHT

  @staticmethod
  def _routing() -> bool:
    return ui_state.sm.valid['navInstruction'] and ui_state.sm.alive['navInstruction']

  @property
  def wanted(self) -> bool:
    # route_guide publishes the comma-nav route with comma's navigation off, so the instruction alone is enough
    return self._routing() or (ui_state.navigation_enabled and ui_state.nav_destination_set)

  def bottom(self, rect: rl.Rectangle) -> float:
    return rect.y + MARGIN + self._height

  def _draw_lanes(self, card: rl.Rectangle, lanes, alpha: float) -> None:
    lanes = list(lanes)[:MAX_LANES]
    x = card.x + (card.width - len(lanes) * LANE_BOX - (len(lanes) - 1) * LANE_GAP) / 2
    y = card.y + HEIGHT - 4
    for lane in lanes:
      box = rl.Rectangle(x, y, LANE_BOX, LANE_BOX)
      rl.draw_rectangle_rounded(box, 0.3, 6, rl.fade(OUTLINE, alpha * (0.9 if lane.active else 0.25)))
      # the way to take from an active lane, else the first thing the lane allows
      direction = str(lane.activeDirection) if lane.active else next((str(d) for d in lane.directions), 'none')
      if direction in LANE_ARROWS:
        self._draw_arrow(rl.Vector2(x + LANE_BOX / 2, y + LANE_BOX / 2), maneuver_path(*LANE_ARROWS[direction]),
                         rl.fade(rl.WHITE if lane.active else MUTED, alpha * (1.0 if lane.active else 0.6)), 0.6)
      x += LANE_BOX + LANE_GAP

  @staticmethod
  def _draw_arrow(center: rl.Vector2, path: list[tuple[float, float]], color: rl.Color, scale: float = 1.5) -> None:
    stroke = STROKE * scale / 1.5
    points = [rl.Vector2(center.x + scale * x, center.y + scale * y) for x, y in path]
    tip, before = points[-1], points[-2]
    heading = math.atan2(tip.y - before.y, tip.x - before.x)
    wings = [rl.Vector2(tip.x - scale * HEAD_LENGTH * math.cos(heading + a), tip.y - scale * HEAD_LENGTH * math.sin(heading + a))
             for a in (HEAD_ANGLE, -HEAD_ANGLE)]
    segments = list(zip(points[:-1], points[1:], strict=True)) + [(wing, tip) for wing in wings]
    for a, b in segments:
      rl.draw_line_ex(a, b, stroke, color)
    # round joins and caps for the thick stroke
    for point in points + wings:
      rl.draw_circle_v(point, stroke / 2, color)

  def _draw_icon(self, center: rl.Vector2, maneuver_type: str, modifier: str, color: rl.Color) -> None:
    if maneuver_type == 'arrive':
      rl.draw_ring(center, 12, 17, 0, 360, 32, color)
      rl.draw_circle_v(center, 6, color)
    elif maneuver_type in ROUNDABOUT_TYPES:
      rl.draw_ring(rl.Vector2(center.x, center.y - 4), 8, 8 + STROKE, 0, 360, 32, color)
      rl.draw_line_ex(rl.Vector2(center.x, center.y + 10), rl.Vector2(center.x, center.y + 21), STROKE, color)
    else:
      self._draw_arrow(center, maneuver_path(maneuver_type, modifier), color)

  def render(self, rect: rl.Rectangle, visible: bool) -> None:
    alpha = self._alpha.update(float(self.wanted and visible))
    if alpha < 1e-2:
      return
    sm = ui_state.sm
    lanes = sm['navInstruction'].lanes if self._routing() else []
    self._height = HEIGHT + (LANE_ROW if len(lanes) else 0)
    card = rl.Rectangle(rect.x + LEFT, rect.y + MARGIN, min(WIDTH, rect.width - LEFT - MARGIN), self._height)
    rl.draw_rectangle_rounded(card, 0.3 * HEIGHT / self._height, 10, rl.fade(BACKGROUND, alpha * BACKGROUND.a / 255))
    rl.draw_rectangle_rounded_lines_ex(card, 0.3 * HEIGHT / self._height, 10, 2, rl.fade(OUTLINE, alpha))

    text_x = card.x + ICON_BOX
    text_width = card.width - ICON_BOX - 12
    if not self._routing():
      rl.draw_text_ex(self._font_bold, "Waiting for route", rl.Vector2(card.x + 16, card.y + 28), 26, 0, rl.fade(rl.WHITE, alpha))
      return

    instruction = sm['navInstruction']
    self._draw_icon(rl.Vector2(card.x + ICON_BOX / 2 + 2, card.y + HEIGHT / 2), instruction.maneuverType, instruction.maneuverModifier,
                    rl.fade(ACCENT, alpha))

    rl.draw_text_ex(self._font_bold, format_distance(max(0, instruction.maneuverDistance)), rl.Vector2(text_x, card.y + 6), 34, 0,
                    rl.fade(rl.WHITE, alpha))
    remaining = f"{max(1, round(max(0, instruction.timeRemaining) / 60))} min / {format_distance(max(0, instruction.distanceRemaining))}"
    remaining_size = measure_text_cached(self._font_medium, remaining, 18)
    rl.draw_text_ex(self._font_medium, remaining, rl.Vector2(card.x + card.width - 12 - remaining_size.x, card.y + 16), 18, 0,
                    rl.fade(MUTED, alpha))

    if len(lanes):
      self._draw_lanes(card, lanes, alpha)

    then = following_maneuver(instruction.allManeuvers)
    if then is not None:
      chip = rl.Rectangle(card.x + card.width - THEN_WIDTH - 8, card.y + HEIGHT - 34, THEN_WIDTH, 26)
      rl.draw_rectangle_rounded(chip, 0.5, 8, rl.fade(OUTLINE, alpha * 0.45))
      rl.draw_text_ex(self._font_medium, "then", rl.Vector2(chip.x + 8, chip.y + 4), 17, 0, rl.fade(rl.WHITE, alpha))
      self._draw_arrow(rl.Vector2(chip.x + THEN_WIDTH - 16, chip.y + 13), maneuver_path(then.type, then.modifier), rl.fade(rl.WHITE, alpha), 0.6)
      text_width -= THEN_WIDTH + 6

    if alpha > 0.5:
      self._street.set_text(instruction.maneuverPrimaryText)
      self._street.render(rl.Rectangle(text_x, card.y + 50, text_width, 28))
