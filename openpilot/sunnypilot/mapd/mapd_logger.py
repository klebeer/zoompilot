"""
Records what mapd publishes while onroad, for offline analysis of map-based curve warnings.

mapd writes its outputs straight into /dev/shm/params and none of them reach the route logs.
One JSON line per mapd update, stamped with the same monotonic clock as logMonoTime so it can
be joined to the rlog of the drive.
"""
import json
import os
import time

from openpilot.common.swaglog import cloudlog
from openpilot.common.hardware import PC

SHM = "/dev/shm/params/d"
KEYS = ("LastGPSPosition", "MapCurvatures", "MapTargetVelocities", "MapSpeedLimit", "MapAdvisoryLimit", "RoadName")
TRIGGER = "MapCurvatures"
LOG_DIR = os.path.expanduser("~/.comma/mapd_log") if PC else "/data/media/0/mapd_log"
MAX_BYTES = 200 * 1024 * 1024
POLL_S = 0.2


def read(key: str) -> str | None:
  try:
    with open(os.path.join(SHM, key)) as f:
      return f.read()
  except OSError:
    return None


def decode(raw: str | None):
  if not raw:
    return None
  try:
    return json.loads(raw)
  except ValueError:
    return raw


def prune(log_dir: str, keep: str) -> None:
  files = sorted((os.path.join(log_dir, f) for f in os.listdir(log_dir)), key=os.path.getmtime)
  total = sum(os.path.getsize(f) for f in files)
  for f in files:
    if total <= MAX_BYTES or f == keep:
      break
    total -= os.path.getsize(f)
    os.remove(f)


def record_if_updated(out, last_mtime: int) -> int:
  """Append one line when mapd has published since last_mtime. Returns the mtime seen."""
  try:
    mtime = os.stat(os.path.join(SHM, TRIGGER)).st_mtime_ns
  except FileNotFoundError:
    return last_mtime
  if mtime != last_mtime:
    rec = {"mono_ns": time.monotonic_ns(), "wall": time.time()}
    rec.update({k: decode(read(k)) for k in KEYS})
    out.write(json.dumps(rec, separators=(",", ":")) + "\n")
    out.flush()
  return mtime


def main() -> None:
  os.makedirs(LOG_DIR, exist_ok=True)
  path = os.path.join(LOG_DIR, time.strftime("%Y-%m-%d--%H-%M-%S") + f"--{time.monotonic_ns()}.jsonl")
  prune(LOG_DIR, path)
  last_mtime = 0
  with open(path, "a") as out:
    while True:
      try:
        last_mtime = record_if_updated(out, last_mtime)
      except Exception:
        cloudlog.exception("mapd_logger: failed to record")
        time.sleep(1.0)
      time.sleep(POLL_S)


if __name__ == "__main__":
  main()
