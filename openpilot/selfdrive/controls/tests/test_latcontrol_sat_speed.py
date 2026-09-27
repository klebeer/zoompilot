from types import SimpleNamespace

from opendbc.car import structs
from openpilot.selfdrive.controls.lib.latcontrol import LatControl


class _Lat(LatControl):
  def update(self, *args, **kwargs):
    pass


def _saturates(fingerprint, v_ego, seconds=2.0):
  CP = structs.CarParams(carFingerprint=fingerprint, steerLimitTimer=0.8)
  lat = _Lat(CP, None, None, 0.01)
  CS = SimpleNamespace(vEgo=v_ego, steeringPressed=False)
  sat = False
  for _ in range(int(seconds / lat.dt)):
    sat = lat._check_saturation(True, CS, False, False)
  return sat


def test_cx5_non_mrcc_warns_in_town_turns():
  assert _saturates("MAZDA_CX5_2022_NON_MRCC", 18 / 3.6)


def test_cx5_non_mrcc_quiet_at_a_crawl():
  assert not _saturates("MAZDA_CX5_2022_NON_MRCC", 8 / 3.6)


def test_other_cars_keep_the_36_kph_floor():
  assert not _saturates("MAZDA_CX5_2022", 18 / 3.6)
  assert _saturates("MAZDA_CX5_2022", 40 / 3.6)
