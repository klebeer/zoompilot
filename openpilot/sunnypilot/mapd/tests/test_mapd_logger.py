import io
import json
import os

from openpilot.sunnypilot.mapd import mapd_logger


def publish(shm, key, value):
  with open(os.path.join(shm, key), "w") as f:
    f.write(value)


def test_records_one_line_per_mapd_update(tmp_path, monkeypatch):
  monkeypatch.setattr(mapd_logger, "SHM", str(tmp_path))
  publish(tmp_path, "LastGPSPosition", '{"latitude": -0.1, "longitude": -78.4}')
  publish(tmp_path, "MapCurvatures", '[{"latitude": -0.1, "longitude": -78.4, "curvature": 0.01}]')
  publish(tmp_path, "RoadName", "Avenida Mariscal Sucre")
  out = io.StringIO()

  mtime = mapd_logger.record_if_updated(out, 0)
  assert mapd_logger.record_if_updated(out, mtime) == mtime, "no new line without a new mapd publish"

  lines = out.getvalue().splitlines()
  assert len(lines) == 1
  rec = json.loads(lines[0])
  assert rec["MapCurvatures"][0]["curvature"] == 0.01
  assert rec["LastGPSPosition"]["latitude"] == -0.1
  assert rec["RoadName"] == "Avenida Mariscal Sucre"
  assert rec["MapTargetVelocities"] is None
  assert rec["mono_ns"] > 0


def test_nothing_before_mapd_publishes(tmp_path, monkeypatch):
  monkeypatch.setattr(mapd_logger, "SHM", str(tmp_path))
  out = io.StringIO()
  assert mapd_logger.record_if_updated(out, 0) == 0
  assert out.getvalue() == ""


def test_prune_drops_oldest_first(tmp_path, monkeypatch):
  monkeypatch.setattr(mapd_logger, "MAX_BYTES", 10)
  for i, name in enumerate(("a.jsonl", "b.jsonl", "c.jsonl")):
    p = tmp_path / name
    p.write_text("x" * 6)
    os.utime(p, (i, i))
  mapd_logger.prune(str(tmp_path), str(tmp_path / "new.jsonl"))
  assert sorted(os.listdir(tmp_path)) == ["c.jsonl"]
