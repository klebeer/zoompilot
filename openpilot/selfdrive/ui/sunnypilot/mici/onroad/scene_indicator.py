"""
What the driving model sees ahead, as one icon in the side panel: a stop, a lead being closed on, a curve.
It reports the model's view on every car. Nothing acts on it: without openpilot longitudinal the driver
does all the braking. Icon set after StarPilot's conditional mode rail (MIT, firestar5683 and StarPilot contributors).
"""
import numpy as np
import raylib_nb as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.selfdrive.controls.lib.e2e_alerts_helper import GREEN_LIGHT_X_THRESHOLD
from openpilot.system.ui.lib.application import gui_app

# the model plans to be stopped by the end of its 10 s horizon
STOP_END_SPEED = 1.0  # m/s
STOP_MIN_SPEED = 0.5  # m/s, below it only a standstill counts, judged like the green light alert does
LEAD_CLOSING_SPEED = -1.5  # m/s relative
LEAD_CLOSE_GAP = 1.2  # s
CURVE_LAT_ACCEL = 1.5  # m/s^2 planned within CURVE_HORIZON
CURVE_HORIZON = 5.0  # s
# a scene has to hold about this long to show, so a single odd frame does not flash an icon
SCENE_RC = 0.4
SHOW, HIDE = 0.6, 0.3
# how long the light stays green after the green light alert chimes
GREEN_SECONDS = 3.0

WHITE = rl.Color(255, 255, 255, 230)
CYAN = rl.Color(120, 200, 230, 255)
RED = rl.Color(255, 40, 50, 255)
GREEN = rl.Color(0, 255, 64, 255)
DIM = rl.Color(255, 255, 255, 70)
HOUSING = rl.Color(40, 44, 52, 255)


def model_stops(velocity: list[float], distance: list[float], v_ego: float, standstill: bool) -> bool:
  """Moving: the plan ends stopped. Stopped: the plan stays short of what the green light alert takes for a go."""
  if standstill:
    return len(distance) > 1 and distance[-1] < GREEN_LIGHT_X_THRESHOLD
  return len(velocity) > 1 and velocity[-1] < STOP_END_SPEED and v_ego > STOP_MIN_SPEED


def lead_closing(present: bool, distance: float, relative_speed: float, v_ego: float) -> bool:
  return present and (relative_speed < LEAD_CLOSING_SPEED or distance / max(v_ego, 1.0) < LEAD_CLOSE_GAP)


def curve_ahead(times: list[float], yaw_rates: list[float], velocity: list[float]) -> float:
  """Signed peak lateral acceleration the model plans within the horizon, left positive, or 0 for none."""
  if not (len(times) == len(yaw_rates) == len(velocity)) or not len(times):
    return 0.0
  lat_accel = (np.array(yaw_rates) * np.array(velocity))[np.array(times) <= CURVE_HORIZON]
  peak = float(lat_accel[np.argmax(np.abs(lat_accel))]) if len(lat_accel) else 0.0
  return peak if abs(peak) > CURVE_LAT_ACCEL else 0.0


class SceneIndicator:
  def __init__(self):
    self._filters = {scene: FirstOrderFilter(0.0, SCENE_RC, 1 / gui_app.target_fps) for scene in ('stop', 'lead', 'curve')}
    self._shown = dict.fromkeys(self._filters, False)
    self._curve_left = False
    self._alpha = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._scene: str | None = None
    self._green_time = float('-inf')
    self._txt_car = gui_app.texture('icons_mici/longitudinal/car.png', 35, 27, keep_aspect_ratio=False)

  def _update(self) -> str | None:
    sm = ui_state.sm
    if not ui_state.started or not sm.alive['modelV2']:
      return None
    model, car_state, lead = sm['modelV2'], sm['carState'], sm['radarState'].leadOne
    velocity = list(model.velocity.x)
    closing = lead_closing(sm.alive['radarState'] and lead.present, lead.dRel, lead.vRel, car_state.vEgo)
    curve = curve_ahead(list(model.orientationRate.t), list(model.orientationRate.z), velocity)
    if curve:
      self._curve_left = curve > 0
    seen = {
      # a stop behind a lead is the lead's
      'stop': model_stops(velocity, list(model.position.x), car_state.vEgo, car_state.standstill) and not (sm.alive['radarState'] and lead.present),
      'lead': closing,
      'curve': bool(curve),
    }
    for scene, value in seen.items():
      level = self._filters[scene].update(float(value))
      self._shown[scene] = level > (HIDE if self._shown[scene] else SHOW)
    # the same moment the green light alert chimes: the model stopped wanting to stay put
    if sm.updated['longitudinalPlanSP'] and sm['longitudinalPlanSP'].e2eAlerts.greenLightAlert:
      self._green_time = rl.get_time()
    if self._green:
      return 'stop'
    return next((scene for scene, shown in self._shown.items() if shown), None)

  @property
  def _green(self) -> bool:
    return 0 <= rl.get_time() - self._green_time < GREEN_SECONDS

  def render(self, center: rl.Vector2) -> None:
    scene = self._update()
    if scene is not None:
      self._scene = scene
    alpha = self._alpha.update(float(scene is not None and scene == self._scene))
    if alpha < 1e-2 or self._scene is None:
      return
    {'stop': self._draw_stop, 'lead': self._draw_lead, 'curve': self._draw_curve}[self._scene](center, alpha)

  def _draw_stop(self, center: rl.Vector2, alpha: float) -> None:
    rl.draw_rectangle_rounded(rl.Rectangle(center.x - 12, center.y - 24, 24, 48), 0.5, 8, rl.fade(HOUSING, alpha))
    rl.draw_rectangle_rounded_lines_ex(rl.Rectangle(center.x - 12, center.y - 24, 24, 48), 0.5, 8, 2, rl.fade(WHITE, alpha * 0.9))
    for dy, color in ((-14, DIM if self._green else RED), (0, DIM), (14, GREEN if self._green else DIM)):
      rl.draw_circle_v(rl.Vector2(center.x, center.y + dy), 5.5, rl.fade(color, alpha * color.a / 255))

  def _draw_lead(self, center: rl.Vector2, alpha: float) -> None:
    txt = self._txt_car
    rl.draw_texture_ex(txt, rl.Vector2(center.x - txt.width / 2, center.y - txt.height / 2), 0.0, 1.0, rl.Color(255, 255, 255, round(255 * alpha)))
    color = rl.fade(CYAN, alpha)
    half_w, half_h, arm = 25, 22, 8
    for sx in (-1, 1):
      for sy in (-1, 1):
        corner = rl.Vector2(center.x + sx * half_w, center.y + sy * half_h)
        rl.draw_line_ex(corner, rl.Vector2(corner.x - sx * arm, corner.y), 3, color)
        rl.draw_line_ex(corner, rl.Vector2(corner.x, corner.y - sy * arm), 3, color)

  def _draw_curve(self, center: rl.Vector2, alpha: float) -> None:
    # a road seen from behind the wheel, bending away near the top
    bend = -1 if self._curve_left else 1

    def line(x_bottom: float, steps: int = 8) -> list[rl.Vector2]:
      points = []
      for i in range(steps + 1):
        t = i / steps
        x = x_bottom * (1 - 0.45 * t) + bend * 16 * t * t
        points.append(rl.Vector2(center.x + x, center.y + 22 - 44 * t))
      return points

    for x_bottom in (-17, 17):
      points = line(x_bottom)
      for a, b in zip(points[:-1], points[1:], strict=True):
        rl.draw_line_ex(a, b, 3, rl.fade(WHITE, alpha))
    dashes = line(0, steps=7)
    for a, b in list(zip(dashes[:-1], dashes[1:], strict=True))[::2]:
      rl.draw_line_ex(a, b, 3, rl.fade(CYAN, alpha))
