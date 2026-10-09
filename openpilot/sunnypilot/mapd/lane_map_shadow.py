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

The same loop logs the map-gated anticipation rule (curve_anticipation) to ANTICIPATION_LOG_DIR.
"""
import json
import math
import os
import time

import numpy as np

import openpilot.cereal.messaging as messaging
from openpilot.common.hardware import PC
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd.curve_anticipation import BOOST_S, anticipation
from openpilot.sunnypilot.mapd.curve_anticipation import MAX_KPH as MAX_ANTICIPATION_KPH
from openpilot.sunnypilot.mapd.curve_anticipation import MIN_KPH as MIN_ANTICIPATION_KPH
from openpilot.sunnypilot.mapd.map_ways import MapWays

LOG_DIR = os.path.expanduser("~/.comma/lane_shadow") if PC else "/data/media/0/lane_shadow"
ANTICIPATION_LOG_DIR = os.path.expanduser("~/.comma/anticipation_shadow") if PC else "/data/media/0/anticipation_shadow"
SHM = "/dev/shm/params/d"
LOG_PERIOD_S = 0.5
MIN_KPH, MAX_KPH = 10.0, 50.0
MAX_EDGE_STD = 1.0
MIN_ROAD_M, MAX_ROAD_M = 5.0, 12.0   # outside this the edges are likely a parking area or a median
CURB_MARGIN = 0.8                    # m kept from the right road edge, as ContextOffset
MAX_SHIFT = 0.5                      # what the deployed context shift allows
HALF_WIDTH = 0.92                    # m, half the CX-5's body width
# Beside parked vehicles the model is unsure where the right edge is, and usually places it at the
# curb behind them: 13 of 15 labelled parked moments were at or above this, 15 of 44 clear ones.
PARKED_EDGE_STD = 0.70


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
  would_apply = round(float(np.clip(remaining, 0.0, MAX_SHIFT)), 2)
  parked_guard = bool(model_v2.roadEdgeStds[1] >= PARKED_EDGE_STD)
  return {"v_kph": round(v_kph, 1), "road": way.name, "lanes": way.lanes, "match_m": round(way.distance, 1),
          "width_m": round(width, 2), "middle_m": round(middle, 2), "over_middle_m": round(over_middle, 2),
          "remaining_m": round(remaining, 2), "would_apply_m": would_apply,
          "right_edge_std": round(model_v2.roadEdgeStds[1], 2), "parked_guard": parked_guard,
          "would_apply_guarded_m": 0.0 if parked_guard else would_apply,
          "lines": [round(model_v2.laneLineProbs[1], 2), round(model_v2.laneLineProbs[2], 2)]}


def read_curvatures(state: dict) -> list[dict] | None:
  """mapd's MapCurvatures, re-read only when the file changes; state keeps the mtime and the last list."""
  try:
    mtime = os.stat(os.path.join(SHM, "MapCurvatures")).st_mtime_ns
  except OSError:
    return state.get("curvatures")
  if mtime != state.get("mtime"):
    state["mtime"], state["read_at"] = mtime, time.monotonic()
    try:
      with open(os.path.join(SHM, "MapCurvatures")) as f:
        raw = f.read()
      state["curvatures"] = json.loads(raw) if raw else None
    except (OSError, ValueError):
      state["curvatures"] = None
  return state.get("curvatures")


def main() -> None:
  os.makedirs(LOG_DIR, exist_ok=True)
  os.makedirs(ANTICIPATION_LOG_DIR, exist_ok=True)
  name = time.strftime("%Y-%m-%d--%H-%M-%S") + f"--{time.monotonic_ns()}.jsonl"
  sm = messaging.SubMaster(["modelV2", "carState", "carControl", "liveLocationKalman"])
  ways, last_log, last_anticipation, curv_state = None, 0.0, 0.0, {}
  with open(os.path.join(LOG_DIR, name), "a") as out, open(os.path.join(ANTICIPATION_LOG_DIR, name), "a") as ant_out:
    while True:
      try:
        sm.update(100)
        if ways is None:
          ways = MapWays()
        if not sm.updated["modelV2"]:
          continue
        loc = sm["liveLocationKalman"]
        if not loc.positionGeodetic.valid:
          continue
        now = time.monotonic()
        lat, lon = loc.positionGeodetic.value[0], loc.positionGeodetic.value[1]
        heading = math.degrees(loc.calibratedOrientationNED.value[2]) % 360
        v_kph, lat_active = sm["carState"].vEgo * 3.6, sm["carControl"].latActive

        if now - last_anticipation >= LOG_PERIOD_S and MIN_ANTICIPATION_KPH <= v_kph <= MAX_ANTICIPATION_KPH:
          curvatures = read_curvatures(curv_state)
          a = anticipation(lat, lon, heading, v_kph, curvatures)
          ant_out.write(json.dumps({
            "mono_ns": time.monotonic_ns(), "lat": round(lat, 6), "lon": round(lon, 6), "heading": round(heading, 1),
            "v_kph": round(v_kph, 1), "lat_active": lat_active, "active": a.active, "would_boost_s": BOOST_S if a.active else 0.0,
            "radius_m": a.radius, "distance_m": a.distance, "time_to_s": a.time_to,
            "map_points": len(curvatures or []), "map_age_s": round(now - curv_state.get("read_at", now), 1),
          }, separators=(",", ":")) + "\n")
          ant_out.flush()
          last_anticipation = now

        if now - last_log < LOG_PERIOD_S:
          continue
        way = ways.match(lat, lon, heading)
        rec = sample(sm["modelV2"], v_kph, lat_active, way)
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
