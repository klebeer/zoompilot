import math
import os

import pytest

from openpilot.selfdrive.navigation.pmtiles import PMTiles
from openpilot.selfdrive.navigation.vector_tile import decode_roads
from openpilot.sunnypilot.mapd import local_map
from openpilot.sunnypilot.mapd.local_map import LocalTiles

# the Ecuador cut of Protomaps' build; the archive tests run only where it exists
ARCHIVE = os.environ.get("LOCAL_MAP", os.path.expanduser("~/Projects/comma-nav/data/ecuador.pmtiles"))


def test_reads_byte_ranges_of_a_file(tmp_path):
  path = tmp_path / "a.bin"
  path.write_bytes(bytes(range(256)))
  tiles = LocalTiles(str(path))
  assert tiles.size == 256 and tiles.read(10, 4) == bytes([10, 11, 12, 13])
  tiles.prefetch([(0, 4)])
  tiles.close()
  tiles.close()


def test_off_without_the_switch_or_the_archive(tmp_path, monkeypatch):
  monkeypatch.setattr(local_map, "ENABLE_PATH", str(tmp_path / "on"))
  monkeypatch.setattr(local_map, "MAP_PATH", str(tmp_path / "map.pmtiles"))
  assert not local_map.enabled()
  (tmp_path / "on").touch()
  assert not local_map.enabled(), "the switch alone"
  (tmp_path / "map.pmtiles").touch()
  assert local_map.enabled()


@pytest.mark.skipif(not os.path.exists(ARCHIVE), reason="no Ecuador archive here")
def test_the_archive_has_quitos_roads_in_the_schema_the_renderer_reads():
  archive = PMTiles(LocalTiles(ARCHIVE))
  assert archive.max_zoom >= 15
  # the z15 tile over Plaza Grande, Quito
  lat, lon, n = math.radians(-0.2201), -78.5123, 2 ** 15
  x, y = int((lon + 180.0) / 360.0 * n), int((1.0 - math.asinh(math.tan(lat)) / math.pi) / 2.0 * n)
  roads = decode_roads(archive.get(15, x, y))
  assert roads and {"major_road", "minor_road"} & {r.kind for r in roads}
  archive.close()
