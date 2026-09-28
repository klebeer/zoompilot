"""
Keeps every drive's rlog and mapd log outside the deleter's reach, for multi-week analysis.

The deleter frees space by removing whole segments from the log root, video and rlog alike,
once free space runs low. Runs offroad only: copies each finished segment's rlog (no video) and
the mapd_logger files into ARCHIVE_ROOT, and caps that directory by dropping the oldest drives.
"""
import os
import shutil
import time

from openpilot.common.hardware import PC
from openpilot.common.hardware.hw import Paths
from openpilot.common.swaglog import cloudlog

ARCHIVE_ROOT = os.path.expanduser("~/.comma/archive") if PC else "/data/media/0/archive"
MAPD_LOG_DIR = os.path.expanduser("~/.comma/mapd_log") if PC else "/data/media/0/mapd_log"
MAX_ARCHIVE_BYTES = 40 * 1024 ** 3
MIN_FREE_BYTES = 15 * 1024 ** 3   # stay clear of the deleter's 5 GB floor
SETTLE_S = 60.0                   # a file untouched this long is complete
SCAN_PERIOD_S = 600.0


def _settled(path: str, now: float) -> bool:
  return now - os.path.getmtime(path) >= SETTLE_S


def _copy(src: str, dst: str) -> None:
  os.makedirs(os.path.dirname(dst), exist_ok=True)
  tmp = dst + ".tmp"
  shutil.copyfile(src, tmp)
  os.replace(tmp, dst)


def _needs_copy(src: str, dst: str) -> bool:
  return not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src)


def _dir_bytes(root: str) -> int:
  return sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(root) for f in files)


def prune(archive_root: str, max_bytes: int) -> None:
  """Drop the oldest archived segments until the archive fits in max_bytes."""
  seg_root = os.path.join(archive_root, "segments")
  if not os.path.isdir(seg_root):
    return
  total = _dir_bytes(archive_root)
  for seg in sorted(os.listdir(seg_root), key=lambda s: os.path.getmtime(os.path.join(seg_root, s))):
    if total <= max_bytes:
      break
    path = os.path.join(seg_root, seg)
    size = _dir_bytes(path)
    shutil.rmtree(path)
    total -= size


def archive_once(log_root: str, mapd_dir: str, archive_root: str, now: float, free_bytes: int) -> int:
  """Copy settled rlogs and mapd logs not yet archived. Returns the number of files copied."""
  if free_bytes < MIN_FREE_BYTES:
    return 0
  copied = 0
  if os.path.isdir(log_root):
    for seg in sorted(os.listdir(log_root)):
      for name in ("rlog.zst", "rlog"):
        src = os.path.join(log_root, seg, name)
        if os.path.isfile(src) and _settled(src, now):
          dst = os.path.join(archive_root, "segments", seg, name)
          if _needs_copy(src, dst):
            _copy(src, dst)
            copied += 1
          break
  if os.path.isdir(mapd_dir):
    for name in sorted(os.listdir(mapd_dir)):
      src = os.path.join(mapd_dir, name)
      if os.path.isfile(src) and _settled(src, now):
        dst = os.path.join(archive_root, "mapd", name)
        if _needs_copy(src, dst):
          _copy(src, dst)
          copied += 1
  return copied


def main() -> None:
  while True:
    try:
      os.makedirs(ARCHIVE_ROOT, exist_ok=True)
      st = os.statvfs(ARCHIVE_ROOT)
      copied = archive_once(Paths.log_root(), MAPD_LOG_DIR, ARCHIVE_ROOT, time.time(), st.f_bavail * st.f_frsize)
      prune(ARCHIVE_ROOT, MAX_ARCHIVE_BYTES)
      if copied:
        cloudlog.info(f"log_archiver: archived {copied} files")
    except Exception:
      cloudlog.exception("log_archiver: pass failed")
    time.sleep(SCAN_PERIOD_S)


if __name__ == "__main__":
  main()
