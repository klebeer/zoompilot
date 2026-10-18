"""
comma's map renderer (selfdrive/navigation/mapsd.py) over a basemap on the device instead of comma's hosted one.

The archive at MAP_PATH is Protomaps' build cut to Ecuador (`pmtiles extract`), the same schema as comma's, so the
frames match the style comma's nav models train on, with no network and no comma account. The route it draws is
the one route_guide publishes from the comma-nav bundle (nav_messages.py).

Runs only with ENABLE_PATH present and comma's navigation off: both would serve the same map stream.
"""
import os

from openpilot.common.hardware import PC

MAP_PATH = os.path.expanduser("~/.comma/navigation/ecuador.pmtiles") if PC else "/data/navigation/ecuador.pmtiles"
ENABLE_PATH = os.path.expanduser("~/.comma/zoompilot_map_render_on") if PC else "/data/zoompilot_map_render_on"


def enabled() -> bool:
  return os.path.exists(ENABLE_PATH) and os.path.exists(MAP_PATH)


class LocalTiles:
  """A PMTiles byte source over a file, the interface RemoteTiles gives PMTiles."""

  def __init__(self, path: str = MAP_PATH):
    self.fd: int | None = os.open(path, os.O_RDONLY)
    self.size = os.fstat(self.fd).st_size

  def read(self, offset: int, length: int) -> bytes:
    return os.pread(self.fd, length, offset)

  def prefetch(self, ranges) -> None:
    pass  # the whole archive is on disk

  def close(self) -> None:
    if self.fd is not None:
      os.close(self.fd)
      self.fd = None


def main() -> None:
  from openpilot.selfdrive.navigation import mapsd
  mapsd.RemoteTiles = LocalTiles
  mapsd.main()


if __name__ == "__main__":
  main()
