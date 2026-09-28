"""
Lane position shift for streets whose right lane line is not painted.

With only the left line visible (often the centre line of a two-way street), the model keeps a
standard lane off that line and leaves the room up to the curb unused, so the car rides close to
oncoming traffic. When the right line is missing and the right road edge is confident, this moves
the virtual camera so the car centres between the left line and the curb minus a margin. The
model outputs it reads are already in the shifted frame, so the measured error is the remaining
correction.
"""
import numpy as np

# measured on the CX-5 non-MRCC in Quito: the right line is missing 17.5% of the time at
# 15-50 km/h, with a median of 0.23 m (p90 0.73 m) of room to the curb margin
PLATFORMS = {"MAZDA_CX5_2022_NON_MRCC"}
MIN_SPEED = 10 / 3.6
MAX_SPEED = 50 / 3.6
RIGHT_LINE_MISSING = 0.3
LEFT_LINE_SEEN = 0.4
MAX_EDGE_STD = 1.0
CURB_MARGIN = 0.8   # m kept from the right road edge (parked cars, curb)
MAX_SHIFT = 0.5     # m
RATE = 0.05         # m/s
# measured on the same car: with both lines the car rides centred below 50 km/h, close to oncoming
# traffic, and 0.16 m right above 70 km/h, so the right bias is carried by speed, not CameraOffset
TOWN_BIAS = 0.25    # m to the right
TOWN_BIAS_SPEEDS = [50 / 3.6, 70 / 3.6]


class ContextOffset:
  def __init__(self, fingerprint: str, dt: float):
    self.enabled = fingerprint in PLATFORMS
    self.step = RATE * dt
    self.shift = 0.0   # m to the right, currently applied
    self.bias = 0.0    # m to the right, from speed

  def update(self, model_v2, v_ego: float) -> float:
    """Advance toward the wanted shift from the latest model output. Returns metres to the right."""
    if not self.enabled:
      return 0.0
    self.bias = float(np.interp(v_ego, TOWN_BIAS_SPEEDS, [TOWN_BIAS, 0.0]))
    target = 0.0
    if MIN_SPEED <= v_ego <= MAX_SPEED and len(model_v2.laneLines) >= 4 and len(model_v2.roadEdges) >= 2:
      probs, stds = model_v2.laneLineProbs, model_v2.roadEdgeStds
      if probs[2] < RIGHT_LINE_MISSING and probs[1] > LEFT_LINE_SEEN and stds[1] < MAX_EDGE_STD:
        left_line = model_v2.laneLines[1].y[0]
        right_edge = model_v2.roadEdges[1].y[0]
        remaining = (left_line + (right_edge - CURB_MARGIN)) / 2
        target = float(np.clip(self.shift + remaining, 0.0, MAX_SHIFT))
    self.shift += float(np.clip(target - self.shift, -self.step, self.step))
    return self.shift

  def camera_offset(self, base_offset: float) -> float:
    """CameraOffset moves the model centre left for positive values, so a right shift subtracts."""
    return base_offset - self.bias - self.shift
