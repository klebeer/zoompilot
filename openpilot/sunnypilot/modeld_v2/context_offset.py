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
# against the driver's own line on these streets: a 0.5 m cap rode 0.15 m right of it, 0.25 m rides 0.03 m
MAX_SHIFT = 0.25    # m
RATE = 0.05         # m/s toward the curb
RELEASE_RATE = 0.15  # m/s back, so the shift does not carry into the next stretch
# The road edge does not show what is parked along it, so a vehicle ahead on the right takes the
# shift off quickly and keeps it off until it is passed.
RIGHT_VEHICLE_RANGE = 40.0             # m ahead
RIGHT_VEHICLE_Y = (1.2, 4.5)           # m right of the car's centre
RIGHT_VEHICLE_HOLD = 3.0               # s after it was last seen
RIGHT_VEHICLE_RELEASE_RATE = 0.25      # m/s
# measured on the same car: with both lines the car rides centred below 50 km/h, close to oncoming
# traffic, and 0.16 m right above 70 km/h, so the right bias is carried by speed, not CameraOffset
TOWN_BIAS = 0.25    # m to the right
TOWN_BIAS_SPEEDS = [50 / 3.6, 70 / 3.6]


class ContextOffset:
  def __init__(self, fingerprint: str, dt: float):
    self.enabled = fingerprint in PLATFORMS
    self.dt = dt
    self.shift = 0.0   # m to the right, currently applied
    self.bias = 0.0    # m to the right, from speed
    self.base = 0.0    # the last CameraOffset passed to camera_offset()
    self.hold = 0.0    # s the shift stays off for a vehicle on the right

  def update(self, model_v2, v_ego: float) -> float:
    """Advance toward the wanted shift from the latest model output. Returns metres to the right."""
    if not self.enabled:
      return 0.0
    self.bias = float(np.interp(v_ego, TOWN_BIAS_SPEEDS, [TOWN_BIAS, 0.0]))
    self.hold = RIGHT_VEHICLE_HOLD if self._right_vehicle(model_v2) else max(self.hold - self.dt, 0.0)
    target = 0.0
    if self.hold == 0.0 and MIN_SPEED <= v_ego <= MAX_SPEED and len(model_v2.laneLines) >= 4 and len(model_v2.roadEdges) >= 2:
      probs, stds = model_v2.laneLineProbs, model_v2.roadEdgeStds
      if probs[2] < RIGHT_LINE_MISSING and probs[1] > LEFT_LINE_SEEN and stds[1] < MAX_EDGE_STD:
        left_line = model_v2.laneLines[1].y[0]
        right_edge = model_v2.roadEdges[1].y[0]
        remaining = (left_line + (right_edge - CURB_MARGIN)) / 2
        target = float(np.clip(self.shift + remaining, 0.0, MAX_SHIFT))
    rate = RATE if target > self.shift else RIGHT_VEHICLE_RELEASE_RATE if self.hold > 0.0 else RELEASE_RATE
    self.shift += float(np.clip(target - self.shift, -rate * self.dt, rate * self.dt))
    return self.shift

  def _right_vehicle(self, model_v2) -> bool:
    # leads are in the shifted model frame; the offset in use maps them back onto the car
    offset = self.camera_offset(self.base)
    lo, hi = RIGHT_VEHICLE_Y
    return any(lead.prob > 0.5 and lead.x[0] < RIGHT_VEHICLE_RANGE and lo < lead.y[0] + offset < hi
               for lead in getattr(model_v2, 'leadsV3', []))

  def camera_offset(self, base_offset: float) -> float:
    """CameraOffset moves the model centre left for positive values, so a right shift subtracts."""
    self.base = base_offset
    return base_offset - self.bias - self.shift
