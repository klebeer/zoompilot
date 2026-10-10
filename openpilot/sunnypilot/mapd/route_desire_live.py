"""
Route desire, live: the route's turn, confirmed by the blinker, steers.

route_guide decides when (route_desire: the chosen route's next turn, else the one every route agrees on, the
blinker to that same side, the corner within DESIRE_M, the car at DESIRE_KPH or slower) and publishes the side
to SIDE_PATH. Two readers act on it while it is fresh: the blinker pause does not release the wheel for that
side (blinker_pause_lateral), and the model is asked for the turn to that side (lane_turn_desire), as "Use Lane
Turn Desires" would, but only at a turn the route confirms.

Once a turn is asked for it holds while the blinker stays on to that side, up to LATCH_S, so the wheel is not
released halfway through the corner when the route's next maneuver becomes a later one. It is off unless
ENABLE_PATH exists: `rm` it to go back to today's behaviour, no reboot.
"""
import json
import os
import time

from openpilot.common.hardware import PC

ENABLE_PATH = os.path.expanduser("~/.comma/zoompilot_route_desire_on") if PC else "/data/zoompilot_route_desire_on"
SIDE_PATH = os.path.expanduser("~/.comma/route_desire_side") if PC else "/dev/shm/zoompilot_route_desire"
STALE_NS = 500_000_000      # a side older than this is ignored: route_guide stopped or the car is off
REFRESH_NS = 200_000_000    # route_guide rewrites the side this often while it holds
LATCH_S = 12.0              # longest a turn holds once asked for
LATCH_MAX_KPH = 40.0        # and it ends above this speed: the turn is done


def enabled() -> bool:
  return os.path.exists(ENABLE_PATH)


def publish(side: str | None, now_ns: int) -> None:
  """Atomic, so a reader never sees half a file."""
  tmp = SIDE_PATH + ".tmp"
  with open(tmp, "w") as f:
    json.dump({"side": side, "mono_ns": now_ns}, f)
  os.replace(tmp, SIDE_PATH)


class Latch:
  """Holds a turn from the moment the route asks for it until the blinker leaves that side."""

  def __init__(self):
    self.side: str | None = None
    self.since = 0.0

  def update(self, now: float, wanted: str | None, blinker: str | None, v_kph: float) -> str | None:
    """`wanted` is the side route_desire asks for now, None when it asks for nothing."""
    if self.side is not None and (blinker != self.side or now - self.since > LATCH_S or v_kph > LATCH_MAX_KPH):
      self.side = None
    if self.side is None and wanted is not None:
      self.side, self.since = wanted, now
    return self.side


class Publisher:
  """Writes the latched side when it changes, and again every REFRESH_NS while it holds."""

  def __init__(self):
    self.side: str | None = None
    self.written_ns = 0

  def step(self, side: str | None, now_ns: int) -> bool:
    if side == self.side and (side is None or now_ns - self.written_ns < REFRESH_NS):
      return False
    publish(side, now_ns)
    self.side, self.written_ns = side, now_ns
    return True


class Reader:
  """The side route_guide published, if fresh. Reads the file at most every READ_EVERY_NS."""
  READ_EVERY_NS = 50_000_000

  def __init__(self):
    self.side: str | None = None
    self.mono_ns = 0
    self.read_ns = 0

  def get(self, now_ns: int | None = None) -> str | None:
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    if now_ns - self.read_ns >= self.READ_EVERY_NS:
      self.read_ns = now_ns
      try:
        with open(SIDE_PATH) as f:
          rec = json.load(f)
        self.side, self.mono_ns = rec.get("side"), int(rec.get("mono_ns", 0))
      except (OSError, ValueError):
        self.side, self.mono_ns = None, 0
    if self.side not in ("left", "right") or now_ns - self.mono_ns > STALE_NS:
      return None
    return self.side
