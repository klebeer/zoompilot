import math

import numpy as np
import pytest

from openpilot.sunnypilot.mapd.route_input import MAX_AGE_S, POINTS, STEP_M, WIDTH, encode, points_ahead, publish, read

LAT, LON = -0.16, -78.50
M = 1 / 111195.0


def north_then_east():
  """200 m north, then 200 m east, a point every 10 m."""
  north = [[LAT + d * M, LON] for d in range(0, 201, 10)]
  east = [[LAT + 200 * M, LON + d * M / math.cos(math.radians(LAT))] for d in range(10, 201, 10)]
  return np.array(north + east)


def test_points_run_ahead_of_the_car_along_the_road():
  p = points_ahead(north_then_east(), LAT, LON, 0.0)
  assert p.shape == (POINTS, 2)
  assert p[:, 0] == pytest.approx(STEP_M * np.arange(1, POINTS + 1), abs=0.5)
  assert np.abs(p[:, 1]).max() < 0.5


def test_the_turn_ahead_bends_the_points_to_the_right():
  p = points_ahead(north_then_east(), LAT + 150 * M, LON, 0.0)
  assert p[0] == pytest.approx([5.0, 0.0], abs=0.5)
  assert p[-1, 0] == pytest.approx(50.0, abs=1.0) and p[-1, 1] == pytest.approx(50.0, abs=1.0), "50 m north, then 50 m east"


def test_no_points_off_the_route_or_against_it():
  shape = north_then_east()
  assert points_ahead(shape, LAT + 50 * M, LON + 20 * M, 0.0) is None, "20 m to the side"
  assert points_ahead(shape, LAT + 50 * M, LON, 180.0) is None, "driving it the other way"


def test_encoding_is_the_shape_alone_whatever_the_heading_error():
  p = points_ahead(north_then_east(), LAT + 150 * M, LON, 0.0)
  turned = points_ahead(north_then_east(), LAT + 150 * M, LON, 1.0)
  a, b = encode(p), encode(turned)
  assert a[-1] == 1.0 and a[:2] == pytest.approx([0.0, 0.0])
  assert a == pytest.approx(b, abs=0.01), "a degree of heading error does not change the input"
  assert not encode(None).any() and encode(None).shape == (WIDTH,)


def test_read_gives_zeros_when_missing_or_stale(tmp_path):
  path = str(tmp_path / "route_input")
  value = encode(points_ahead(north_then_east(), LAT, LON, 0.0))
  assert not read(0, path).any()
  publish(value, 1_000_000_000, path)
  assert read(1_000_000_000 + 100_000_000, path) == pytest.approx(value)
  assert not read(1_000_000_000 + int((MAX_AGE_S + 0.1) * 1e9), path).any()
