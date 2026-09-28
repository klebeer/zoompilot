import os

from openpilot.sunnypilot.system import log_archiver as la

GB = 1024 ** 3


def _write(path, data=b"x" * 10, age_s=3600.0, now=1e6):
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with open(path, "wb") as f:
    f.write(data)
  os.utime(path, (now - age_s, now - age_s))


def test_copies_rlog_and_mapd_but_not_video(tmp_path):
  now = 1e6
  logs, mapd, arch = tmp_path / "realdata", tmp_path / "mapd", tmp_path / "archive"
  _write(str(logs / "00000024--9de3eaeb9b--0" / "rlog.zst"), now=now)
  _write(str(logs / "00000024--9de3eaeb9b--0" / "fcamera.hevc"), now=now)
  _write(str(mapd / "drive.jsonl"), now=now)

  assert la.archive_once(str(logs), str(mapd), str(arch), now, 60 * GB) == 2
  assert (arch / "segments" / "00000024--9de3eaeb9b--0" / "rlog.zst").exists()
  assert not (arch / "segments" / "00000024--9de3eaeb9b--0" / "fcamera.hevc").exists()
  assert (arch / "mapd" / "drive.jsonl").exists()
  assert la.archive_once(str(logs), str(mapd), str(arch), now, 60 * GB) == 0, "nothing copied twice"


def test_skips_files_still_being_written(tmp_path):
  now = 1e6
  logs = tmp_path / "realdata"
  _write(str(logs / "seg--0" / "rlog.zst"), age_s=5.0, now=now)
  assert la.archive_once(str(logs), str(tmp_path / "none"), str(tmp_path / "archive"), now, 60 * GB) == 0


def test_stops_when_disk_is_low(tmp_path):
  now = 1e6
  logs = tmp_path / "realdata"
  _write(str(logs / "seg--0" / "rlog.zst"), now=now)
  assert la.archive_once(str(logs), str(tmp_path / "none"), str(tmp_path / "archive"), now, 10 * GB) == 0


def test_recopies_a_grown_mapd_file(tmp_path):
  now = 1e6
  mapd, arch = tmp_path / "mapd", tmp_path / "archive"
  _write(str(mapd / "drive.jsonl"), b"a", now=now)
  la.archive_once(str(tmp_path / "none"), str(mapd), str(arch), now, 60 * GB)
  _write(str(mapd / "drive.jsonl"), b"ab", now=now)
  assert la.archive_once(str(tmp_path / "none"), str(mapd), str(arch), now, 60 * GB) == 1


def test_prune_drops_oldest_segments(tmp_path):
  arch = tmp_path / "archive"
  for i, seg in enumerate(("a--0", "b--0", "c--0")):
    p = arch / "segments" / seg / "rlog.zst"
    _write(str(p), b"x" * 100)
    os.utime(arch / "segments" / seg, (i, i))
  la.prune(str(arch), 150)
  assert sorted(os.listdir(arch / "segments")) == ["c--0"]
