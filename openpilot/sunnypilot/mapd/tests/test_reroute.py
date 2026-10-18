import json
import os

import pytest

from openpilot.sunnypilot.mapd.reroute import COOLDOWN_S, REJOIN_MAX_HEAD_M, Rerouter, cut, join, plan
from openpilot.sunnypilot.mapd.route_planner import PlannerError, cumulative_m

LAT, LON = -0.16, -78.50
M = 1 / 111195.0  # degrees of latitude per metre


def straight(route_id, start, end, label="r"):
  shape = [list(start), list(end)]
  length = round(cumulative_m(shape)[-1], 1)
  maneuvers = [
    {"shape_index": 0, "distance_m": 0.0, "type": "depart", "modifier": None, "turn_deg": 0.0, "street": "A"},
    {"shape_index": 1, "distance_m": length, "type": "arrive", "modifier": None, "turn_deg": 0.0, "street": "A"},
  ]
  return {"route_id": route_id, "label": label, "length_m": length, "shape": shape, "maneuvers": maneuvers}


def chosen_route():
  """1000 m north, a right turn at 500 m: shape points every 250 m."""
  shape = [[LAT + k * 250 * M, LON] for k in range(5)]
  maneuvers = [
    {"shape_index": 0, "distance_m": 0.0, "type": "depart", "modifier": None, "turn_deg": 0.0, "street": "Uno"},
    {"shape_index": 2, "distance_m": 500.0, "type": "turn", "modifier": "right", "turn_deg": 90.0, "street": "Dos"},
    {"shape_index": 4, "distance_m": 1000.0, "type": "arrive", "modifier": None, "turn_deg": 0.0, "street": "Dos"},
  ]
  return {"route_id": "home", "label": "Casa", "length_m": 1000.0, "shape": shape, "maneuvers": maneuvers}


class FakePlanner:
  def __init__(self, detour=1.0, fail_to=None):
    self.detour, self.fail_to, self.calls = detour, fail_to, []

  def route(self, origin, heading, destination, route_id, label):
    self.calls.append(destination)
    if self.fail_to is not None and tuple(destination) == tuple(self.fail_to):
      raise PlannerError("no route")
    r = straight(route_id, origin, destination, label)
    r["length_m"] = round(r["length_m"] * self.detour, 1)
    return r


def test_cut_keeps_the_rest_of_the_route_from_the_point():
  point, tail = cut(chosen_route(), 400.0)
  assert point[0] == pytest.approx(LAT + 400 * M, abs=1e-7)
  assert tail["length_m"] == 600.0
  assert [(m["type"], m["distance_m"]) for m in tail["maneuvers"]] == [("turn", 100.0), ("arrive", 600.0)]
  assert tail["shape"][tail["maneuvers"][0]["shape_index"]] == chosen_route()["shape"][2]


def test_join_drops_the_arrival_between_the_two_parts():
  _, tail = cut(chosen_route(), 400.0)
  head = straight("x", (LAT, LON + 0.001), tail["shape"][0])
  route = join(head, tail, "new", "Casa")
  assert [m["type"] for m in route["maneuvers"]] == ["depart", "turn", "arrive"]
  assert route["length_m"] == pytest.approx(head["length_m"] + 600.0, abs=0.2)
  turn = route["maneuvers"][1]
  assert route["shape"][turn["shape_index"]] == chosen_route()["shape"][2]


def test_plan_rejoins_the_chosen_route_even_when_longer_than_the_planners():
  car = (LAT + 100 * M, LON + 50 * M)
  picked = plan(FakePlanner(), chosen_route(), 100.0, car, 0.0, "new")
  assert picked["strategy"] == "rejoin" and picked["route"]["shape"][-1] == chosen_route()["shape"][-1]
  assert plan(FakePlanner(detour=1.4), chosen_route(), 100.0, car, 0.0, "new")["strategy"] == "rejoin"


class BackOnly(FakePlanner):
  """Every way back to the chosen route `back_m` long, or unplannable when None."""

  def __init__(self, back_m):
    super().__init__()
    self.back_m = back_m

  def route(self, origin, heading, destination, route_id, label):
    r = super().route(origin, heading, destination, route_id, label)
    if tuple(destination) != tuple(chosen_route()["shape"][-1]):
      if self.back_m is None:
        raise PlannerError("no route")
      r["length_m"] = self.back_m
    return r


def test_plan_goes_straight_when_every_way_back_is_long():
  picked = plan(BackOnly(REJOIN_MAX_HEAD_M + 1.0), chosen_route(), 100.0, (LAT + 100 * M, LON + 50 * M), 0.0, "new")
  assert picked["strategy"] == "direct" and picked["route"]["shape"][-1] == chosen_route()["shape"][-1]


def test_plan_goes_straight_when_no_way_back_can_be_planned():
  picked = plan(BackOnly(None), chosen_route(), 100.0, (LAT + 100 * M, LON + 50 * M), 0.0, "new")
  assert picked["strategy"] == "direct"


class HeadLengths(FakePlanner):
  """The trip straight to the destination first, then each way back in turn, with the lengths given."""

  def __init__(self, lengths):
    super().__init__()
    self.lengths = list(lengths)

  def route(self, origin, heading, destination, route_id, label):
    r = super().route(origin, heading, destination, route_id, label)
    r["length_m"] = self.lengths.pop(0)
    return r


def test_plan_takes_the_way_back_that_makes_the_shortest_trip():
  route = chosen_route()
  route["shape"] = [[LAT + k * 500 * M, LON] for k in range(5)]
  route["length_m"] = 2000.0
  route["maneuvers"][-1]["distance_m"] = 2000.0
  car = (LAT + 100 * M, LON + 50 * M)
  # ways back to 400, 800 and 1300 m along, 300, 900 and 1400 m long: trips of 1900, 2100 and 2100 m
  picked = plan(HeadLengths([1500.0, 300.0, 900.0, 1400.0]), route, 100.0, car, 0.0, "new")
  assert picked["strategy"] == "rejoin" and picked["route"]["length_m"] == pytest.approx(1900.0, abs=0.2)
  picked = plan(HeadLengths([1500.0, 900.0, 300.0, 1400.0]), route, 100.0, car, 0.0, "new")
  assert picked["route"]["length_m"] == pytest.approx(1500.0, abs=0.2), "the second way back gives 1500 m"


def test_plan_near_the_end_goes_straight_to_the_destination():
  planner = FakePlanner()
  picked = plan(planner, chosen_route(), 700.0, (LAT + 700 * M, LON + 50 * M), 0.0, "new")
  assert picked["strategy"] == "direct" and len(planner.calls) == 1


class FakeFollower:
  def __init__(self, bundle):
    self.bundle = bundle


def nav(on_route, progress=200.0, arrived=False):
  return {"state": "following" if on_route else "searching", "route_id": "home" if on_route else None,
          "progress_m": progress if on_route else None, "chosen": {"route_id": "home", "on_route": on_route, "arrived": arrived}}


@pytest.fixture
def files(tmp_path):
  bundle = {"schema": 1, "bundle_id": "b1", "expires_at": "2099-01-01T00:00:00Z", "routes": [chosen_route()]}
  choice = {"schema": 1, "bundle_id": "b1", "route_id": "home", "chosen_by": "app", "chosen_at": "2026-10-18T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z"}
  bundle_path, choice_path = tmp_path / "route_bundle.json", tmp_path / "chosen.json"
  bundle_path.write_text(json.dumps(bundle))
  choice_path.write_text(json.dumps(choice))
  return bundle, choice, str(bundle_path), str(choice_path)


def step(r, now, state, files, v_kph=30.0, mtime=None):
  bundle, choice, bundle_path, choice_path = files
  mtime = os.stat(bundle_path).st_mtime_ns if mtime is None else mtime
  return r.step(now, state, FakeFollower(bundle), choice, (LAT + 200 * M, LON + 60 * M), 90.0, v_kph, bundle_path, choice_path, mtime)


def finish(r, now, state, files, **kw):
  r.job.result()
  return step(r, now, state, files, **kw)


def test_no_reroute_before_the_car_was_on_the_chosen_route(files):
  r = Rerouter(FakePlanner())
  assert step(r, 0.0, nav(False), files) is None and r.job is None


def test_leaving_the_chosen_route_adds_a_route_and_chooses_it(files):
  r = Rerouter(FakePlanner())
  assert step(r, 0.0, nav(True), files) is None
  assert step(r, 1.0, nav(False), files) is None and r.job is not None
  record = finish(r, 1.1, nav(False), files)
  assert record["kind"] == "reroute" and record["written"] and record["strategy"] == "rejoin"
  bundle = json.loads(open(files[2]).read())
  choice = json.loads(open(files[3]).read())
  assert [x["route_id"] for x in bundle["routes"]] == ["home", record["route_id"]]
  assert choice["route_id"] == record["route_id"] and choice["chosen_by"] == "device" and choice["bundle_id"] == "b1"


def test_no_reroute_parked_near_the_end_or_within_the_cooldown(files):
  r = Rerouter(FakePlanner())
  step(r, 0.0, nav(True), files)
  assert step(r, 1.0, nav(False), files, v_kph=2.0) is None and r.job is None, "parked"
  r2 = Rerouter(FakePlanner())
  step(r2, 0.0, nav(True, progress=900.0), files)
  assert step(r2, 1.0, nav(False), files) is None and r2.job is None, "near the destination"
  step(r, 2.0, nav(False), files)
  finish(r, 2.1, nav(False), files)
  step(r, 3.0, nav(True), files)
  assert step(r, 4.0, nav(False), files) is None and r.job is None, "within the cooldown"
  step(r, 2.0 + COOLDOWN_S, nav(False), files)
  assert r.job is not None


def test_a_bundle_the_phone_replaced_meanwhile_is_not_overwritten(files):
  r = Rerouter(FakePlanner())
  step(r, 0.0, nav(True), files)
  step(r, 1.0, nav(False), files)
  record = finish(r, 1.1, nav(False), files, mtime=1)
  assert record["written"] is False
  assert [x["route_id"] for x in json.loads(open(files[2]).read())["routes"]] == ["home"]
