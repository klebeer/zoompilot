"""
Shadow run of a map-aware lane position rule: records how far right the car would move, never moves it.

On a street both directions share, the car belongs in the right half. Where the paint is faded or
missing the model keeps a lane near the road's middle, and the CX-5 is 1.84 m wide, so with its
centre under a metre right of the middle its left side rides on the middle: measured leaving the
driver's parking onto Oe9a, 0.5 to 1.0 m right of the middle on a 7.6 m road. The map's oneWay tells
a shared carriageway from a one-way street or one side of a divided road, which the model cannot.

The residual follows ContextOffset's shape against the road's middle instead of the left line: the
model outputs are already in its shifted frame, so this is the correction still missing. Runs
onroad, appends one JSON line per sample at most every LOG_PERIOD_S, and never raises out of main.
"""
import json
import math
import os
import time

import numpy as np

import openpilot.cereal.messaging as messaging
from openpilot.common.hardware import PC
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd.map_ways import MapWays

LOG_DIR = os.path.expanduser("~/.comma/lane_shadow") if PC else "/data/media/0/lane_shadow"
LOG_PERIOD_S = 0.5
MIN_KPH, MAX_KPH = 10.0, 50.0
MAX_EDGE_STD = 1.0
MIN_ROAD_M, MAX_ROAD_M = 5.0, 12.0   # outside this the edges are likely a parking area or a median
CURB_MARGIN = 0.8                    # m kept from the right road edge, as ContextOffset
MAX_SHIFT = 0.5                      # what the deployed context shift allows
HALF_WIDTH = 0.92                    # m, half the CX-5's body width


def two_way_residual(left_edge: float, right_edge: float) -> tuple[float, float, float, float]:
  """(road width, road middle, correction still needed, left side past the middle), y to the right.

  The correction is how far right the car's centre would move to sit midway between the road's
  middle and the right edge less CURB_MARGIN. Left side past the middle is positive when the
  car's left flank is in the oncoming half.
  """
  width = right_edge - left_edge
  middle = (left_edge + right_edge) / 2
  remaining = (middle + (right_edge - CURB_MARGIN)) / 2
  over_middle = middle + HALF_WIDTH
  return width, middle, remaining, over_middle


def sample(model_v2, v_kph: float, lat_active: bool, way) -> dict | None:
  """The record for this model frame when the rule would be armed on a shared carriageway, else None."""
  if not lat_active or not (MIN_KPH <= v_kph <= MAX_KPH) or way is None or way.one_way:
    return None
  if len(model_v2.roadEdges) < 2 or len(model_v2.roadEdgeStds) < 2 or len(model_v2.laneLines) < 4:
    return None
  if max(model_v2.roadEdgeStds[0], model_v2.roadEdgeStds[1]) > MAX_EDGE_STD:
    return None
  width, middle, remaining, over_middle = two_way_residual(model_v2.roadEdges[0].y[0], model_v2.roadEdges[1].y[0])
  if not (MIN_ROAD_M <= width <= MAX_ROAD_M):
    return None
  return {"v_kph": round(v_kph, 1), "road": way.name, "lanes": way.lanes, "match_m": round(way.distance, 1),
          "width_m": round(width, 2), "middle_m": round(middle, 2), "over_middle_m": round(over_middle, 2),
          "remaining_m": round(remaining, 2), "would_apply_m": round(float(np.clip(remaining, 0.0, MAX_SHIFT)), 2),
          "lines": [round(model_v2.laneLineProbs[1], 2), round(model_v2.laneLineProbs[2], 2)]}


def main() -> None:
  os.makedirs(LOG_DIR, exist_ok=True)
  path = os.path.join(LOG_DIR, time.strftime("%Y-%m-%d--%H-%M-%S") + f"--{time.monotonic_ns()}.jsonl")
  sm = messaging.SubMaster(["modelV2", "carState", "carControl", "liveLocationKalman"])
  ways, last_log = None, 0.0
  with open(path, "a") as out:
    while True:
      try:
        sm.update(100)
        if ways is None:
          ways = MapWays()
        if not sm.updated["modelV2"]:
          continue
        now = time.monotonic()
        if now - last_log < LOG_PERIOD_S:
          continue
        loc = sm["liveLocationKalman"]
        if not loc.positionGeodetic.valid:
          continue
        lat, lon = loc.positionGeodetic.value[0], loc.positionGeodetic.value[1]
        heading = math.degrees(loc.calibratedOrientationNED.value[2]) % 360
        way = ways.match(lat, lon, heading)
        rec = sample(sm["modelV2"], sm["carState"].vEgo * 3.6, sm["carControl"].latActive, way)
        if rec is not None:
          rec["mono_ns"] = time.monotonic_ns()
          rec["lat"], rec["lon"] = round(lat, 6), round(lon, 6)
          out.write(json.dumps(rec, separators=(",", ":")) + "\n")
          out.flush()
          last_log = now
      except Exception:
        cloudlog.exception("lane_map_shadow: step failed")
        time.sleep(1.0)


if __name__ == "__main__":
  main()
