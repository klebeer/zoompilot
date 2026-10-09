"""The curves of a route, read from its geometry, and what the steering holds in each.

A route's shape is resampled every STEP_M metres and the radius at each point is that of the circle
through the points CHORD_POINTS steps before and after it. A curve is a run of points tighter than
CURVE_R_M; its radius is the tightest one, at its apex. Distances are along the shape, the same
measure the route follower's progress uses.

HELD says up to what speed this car's steering holds a radius: the median radius it held at each
speed band over the logged drives (commaia mapd/curve_capability.py): 21 m at 10-20 km/h, 38 m at
20-30, 57 m at 30-40, 125 m at 40-50. Under 21 m the curve is the driver's at any speed.
"""
import math

STEP_M = 5.0
CHORD_POINTS = 3
CURVE_R_M = 100.0
HELD = ((125.0, 50.0), (57.0, 40.0), (38.0, 30.0), (21.0, 20.0))   # (radius at least, held up to km/h)
EARTH_RADIUS_M = 6371000.0


def held_kph(radius_m: float) -> float:
  """The highest speed at which the steering holds this radius; 10 when only a crawl does."""
  return next((kph for least, kph in HELD if radius_m >= least), 10.0)


def _radius(a, b, c) -> float:
  ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
  twice_area = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
  return math.inf if twice_area < 1e-6 else ab * bc * ca / (2 * twice_area)


def curves(shape: list[list[float]]) -> list[dict]:
  """The route's curves in travel order: where each starts, peaks and ends along the route, its radius, side and apex."""
  lat0, lon0 = shape[0]
  kx, ky = math.radians(1.0) * EARTH_RADIUS_M * math.cos(math.radians(lat0)), math.radians(1.0) * EARTH_RADIUS_M
  pts = [((lon - lon0) * kx, (lat - lat0) * ky) for lat, lon in shape]
  samples, left_over = [pts[0]], 0.0
  for a, b in zip(pts, pts[1:], strict=False):
    d, t = math.dist(a, b), STEP_M - left_over
    while d > 0 and t <= d:
      samples.append((a[0] + (b[0] - a[0]) * t / d, a[1] + (b[1] - a[1]) * t / d))
      t += STEP_M
    left_over = (left_over + d) % STEP_M
  k = CHORD_POINTS
  radii = [math.inf] * len(samples)
  for i in range(k, len(samples) - k):
    radii[i] = _radius(samples[i - k], samples[i], samples[i + k])
  out, i = [], 0
  while i < len(radii):
    if radii[i] >= CURVE_R_M:
      i += 1
      continue
    j = i
    while j < len(radii) and radii[j] < CURVE_R_M:
      j += 1
    apex = min(range(i, j), key=lambda n: radii[n])
    a, b, c = samples[apex - k], samples[apex], samples[apex + k]
    cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
    out.append({"start_m": i * STEP_M, "apex_m": apex * STEP_M, "end_m": (j - 1) * STEP_M, "radius_m": round(radii[apex], 1),
                "side": "left" if cross > 0 else "right", "lat": round(lat0 + b[1] / ky, 6), "lon": round(lon0 + b[0] / kx, 6)})
    i = j
  return out
