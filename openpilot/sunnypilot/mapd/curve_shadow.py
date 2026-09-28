"""
Shadow run of the tight-curve warning: records where it would fire, never alerts.

Runs onroad. While openpilot steers at MIN_KPH-MAX_KPH it applies curve_warning's rule to the
latest mapd curvature points and appends one JSON line per would-be warning to LOG_DIR, stamped
with time.monotonic_ns() so it joins the rlog on logMonoTime. Outcomes are judged offline.
"""
import json
import math
import os
import time

import openpilot.cereal.messaging as messaging
from openpilot.common.hardware import PC
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd.curve_warning import LOOKAHEAD_M, MIN_RADIUS_M, distance_m, tight_curve_ahead

SHM = "/dev/shm/params/d"
LOG_DIR = os.path.expanduser("~/.comma/curve_shadow") if PC else "/data/media/0/curve_shadow"
MIN_KPH, MAX_KPH = 10.0, 60.0
HOLDOFF_S = 20.0            # at most one would-be warning every 20 s
SAME_CURVE_HOLDOFF_S = 60.0  # and once per curve
SAME_CURVE_M = 30.0


def _read_json(key: str):
  try:
    with open(os.path.join(SHM, key)) as f:
      raw = f.read()
    return json.loads(raw) if raw else None
  except (OSError, ValueError):
    return None


class Shadow:
  def __init__(self):
    self.last_warn_t = -1e9
    self.last_warn_point = None

  def step(self, now: float, lat_active: bool, v_kph: float, lat: float, lon: float, heading: float, curvatures, road):
    """The record to write when the rule fires on a new curve, else None."""
    if not lat_active or not (MIN_KPH <= v_kph <= MAX_KPH) or not curvatures:
      return None
    hit = tight_curve_ahead(lat, lon, heading, curvatures)
    if hit is None:
      return None
    if now - self.last_warn_t < HOLDOFF_S:
      return None
    if self.last_warn_point is not None and now - self.last_warn_t < SAME_CURVE_HOLDOFF_S and \
       distance_m(hit.latitude, hit.longitude, *self.last_warn_point) < SAME_CURVE_M:
      return None
    self.last_warn_t = now
    self.last_warn_point = (hit.latitude, hit.longitude)
    return {"lat": lat, "lon": lon, "heading": round(heading, 1), "v_kph": round(v_kph, 1),
            "distance_m": round(hit.distance, 1), "radius_m": round(hit.radius, 1),
            "curve_lat": hit.latitude, "curve_lon": hit.longitude, "road": road,
            "rule": {"lookahead_m": LOOKAHEAD_M, "min_radius_m": MIN_RADIUS_M, "kph": [MIN_KPH, MAX_KPH]}}


def main() -> None:
  os.makedirs(LOG_DIR, exist_ok=True)
  path = os.path.join(LOG_DIR, time.strftime("%Y-%m-%d--%H-%M-%S") + f"--{time.monotonic_ns()}.jsonl")
  sm = messaging.SubMaster(["carState", "carControl", "liveLocationKalman"])
  shadow = Shadow()
  curvatures, road, last_mtime = None, None, 0
  with open(path, "a") as out:
    while True:
      try:
        sm.update(100)
        try:
          mtime = os.stat(os.path.join(SHM, "MapCurvatures")).st_mtime_ns
        except FileNotFoundError:
          mtime = last_mtime
        if mtime != last_mtime:
          last_mtime = mtime
          curvatures = _read_json("MapCurvatures")
          try:
            with open(os.path.join(SHM, "RoadName")) as f:
              road = f.read() or None
          except OSError:
            road = None
        loc = sm["liveLocationKalman"]
        if not loc.positionGeodetic.valid:
          continue
        rec = shadow.step(time.monotonic(), sm["carControl"].latActive, sm["carState"].vEgo * 3.6,
                          loc.positionGeodetic.value[0], loc.positionGeodetic.value[1],
                          math.degrees(loc.calibratedOrientationNED.value[2]) % 360, curvatures, road)
        if rec is not None:
          rec["mono_ns"] = time.monotonic_ns()
          out.write(json.dumps(rec, separators=(",", ":")) + "\n")
          out.flush()
      except Exception:
        cloudlog.exception("curve_shadow: step failed")
        time.sleep(1.0)


if __name__ == "__main__":
  main()
