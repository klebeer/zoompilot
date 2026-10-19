"""
The route ahead as the driving model's route_input, from the route route_guide follows to modeld.

A model trained with the route (comma-models finetune/train_route.py) has a route_input: POINTS points on the road
centerline every STEP_M ahead of the car, taken relative to the first point and turned so the first step points
along x, divided by ROUTE_SCALE, flattened, then a valid flag. Turning the shape to the road's own direction keeps it
independent of the car's heading, which the device knows to about a degree.

route_guide writes it to PATH at every fix; modeld reads it for every frame and feeds zeros when there is none or it
is older than MAX_AGE_S, which leaves such a model the original. Models without the input never read it.
"""
import math
import os
import struct

import numpy as np

from openpilot.common.hardware import PC

PATH = os.path.expanduser("~/.comma/route_input") if PC else "/dev/shm/zoompilot_route_input"
POINTS, STEP_M = 20, 5.0
MATCH_M = 15.0                     # the car's projection must be this close to the route
ROUTE_SCALE = np.array([100.0, 20.0], np.float32)
WIDTH = POINTS * 2 + 1
MAX_AGE_S = 0.5
EARTH_RADIUS_M = 6371000.0
_FORMAT = f"<q{WIDTH}f"


def points_ahead(shape: np.ndarray, lat: float, lon: float, yaw_deg: float) -> np.ndarray | None:
  """POINTS points every STEP_M along `shape` ([lat, lon] rows) from the car's projection onto it, in the car's frame
  (x forward, y right, metres); None when the car is not on it running its way. `yaw_deg` is clockwise from north."""
  k = math.radians(1.0) * EARTH_RADIUS_M
  north = (shape[:, 0] - lat) * k
  east = (shape[:, 1] - lon) * k * math.cos(math.radians(lat))
  c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
  local = np.column_stack([north * c + east * s, -north * s + east * c])
  seg = np.diff(local, axis=0)
  length = np.linalg.norm(seg, axis=1)
  ok = length > 1e-3
  if not ok.any():
    return None
  t = np.clip(np.einsum("ij,ij->i", -local[:-1], seg) / np.maximum(length, 1e-9) ** 2, 0.0, 1.0)
  dist = np.linalg.norm(local[:-1] + seg * t[:, None], axis=1)
  dist[~ok | (seg[:, 0] <= 0.0)] = np.inf   # the route must run the car's way where the car meets it
  i = int(np.argmin(dist))
  if dist[i] > MATCH_M:
    return None
  along = np.concatenate([[0.0], np.cumsum(length)])
  want = np.minimum(along[i] + t[i] * length[i] + STEP_M * np.arange(1, POINTS + 1), along[-1])
  return np.column_stack([np.interp(want, along, local[:, 0]), np.interp(want, along, local[:, 1])]).astype(np.float32)


def encode(points: np.ndarray | None) -> np.ndarray:
  """route_input for these points, or all zeros without them."""
  out = np.zeros(WIDTH, np.float32)
  if points is not None:
    rel = points - points[0]
    d = rel[1] / max(float(np.linalg.norm(rel[1])), 1e-6)
    road = np.column_stack([rel[:, 0] * d[0] + rel[:, 1] * d[1], -rel[:, 0] * d[1] + rel[:, 1] * d[0]])
    out[:-1] = (road / ROUTE_SCALE).ravel()
    out[-1] = 1.0
  return out


def publish(value: np.ndarray, mono_ns: int, path: str = PATH) -> None:
  tmp = f"{path}.tmp"
  with open(tmp, "wb") as f:
    f.write(struct.pack(_FORMAT, mono_ns, *value.tolist()))
  os.replace(tmp, path)


def read(now_ns: int, path: str = PATH) -> np.ndarray:
  """The latest route_input, or zeros when there is none or it is older than MAX_AGE_S."""
  try:
    with open(path, "rb") as f:
      raw = f.read()
    stamp, *value = struct.unpack(_FORMAT, raw)
  except (OSError, struct.error):
    return np.zeros(WIDTH, np.float32)
  if now_ns - stamp > MAX_AGE_S * 1e9:
    return np.zeros(WIDTH, np.float32)
  return np.array(value, np.float32)
