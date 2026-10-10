import json
import os

from openpilot.sunnypilot.mapd import route_desire_live, route_guide
from openpilot.sunnypilot.mapd.route_guide import ChoiceWatch, Guide, route_desire, route_turn_agreed, route_turn_chosen

# car at the origin heading north; one degree of latitude is about 111 km
LAT, LON = -0.16, -78.50
NORTH = 0.0


def maneuver(distance_m, turn_deg=90.0, kind="turn", north_m=None, route_id="main"):
  north = distance_m if north_m is None else north_m
  return {"route_id": route_id, "index": 3, "type": kind, "modifier": "right" if kind == "turn" else None,
          "distance_m": distance_m, "turn_deg": turn_deg, "lat": LAT + north / 111000.0, "lon": LON, "street": "Calle Uno"}


def following(*ahead, state="following", locked=None, chosen=None):
  """The follower's state. `locked` is {route_id: [its upcoming maneuvers]}; by default every route in `ahead` with that one.
  `chosen` is the follower's view of the chosen route, or None when there is no choice."""
  if locked is None:
    locked = {}
    for n in ahead:
      locked.setdefault(n["route_id"], []).append(n)
  return {"state": state, "route_id": "main", "progress_m": 500.0, "remaining_m": 3000.0, "match_m": 2.0,
          "next": ahead[0] if ahead else None, "ahead": list(ahead),
          "locked": [{"route_id": r, "upcoming": ms} for r, ms in locked.items()], "chosen": chosen}


def on_chosen(*upcoming, route_id="main"):
  return {"route_id": route_id, "on_route": True, "arrived": False, "upcoming": list(upcoming)}


def bundle(expires_at, bundle_id="0123456789abcdef"):
  shape = [[LAT, LON], [LAT + 0.003, LON]]
  ends = [{"shape_index": i, "distance_m": d, "type": t, "modifier": None, "bearing_before": None, "bearing_after": None,
           "turn_deg": 0.0, "street": "", "exit": None} for i, d, t in ((0, 0.0, "depart"), (1, 333.6, "arrive"))]
  return {"schema": 1, "bundle_id": bundle_id, "expires_at": expires_at,
          "routes": [{"route_id": "north", "label": "north", "length_m": 333.6, "shape": shape, "maneuvers": ends}]}


def test_consensus_is_a_turn_every_route_the_car_is_on_takes():
  turn = maneuver(80)
  assert route_turn_agreed(following(turn, locked={"main": [turn], "alt": [{**turn, "route_id": "alt"}]}))["distance_m"] == 80
  straight_on = maneuver(200, kind="continue", turn_deg=0.0, route_id="alt")
  assert route_turn_agreed(following(turn, locked={"main": [turn], "alt": [straight_on]})) is None, "the alternate goes straight"
  other_way = maneuver(80, turn_deg=-90.0, route_id="alt")
  assert route_turn_agreed(following(turn, locked={"main": [turn], "alt": [other_way]})) is None, "same corner, the other way"
  assert route_turn_agreed(following(turn, state="off_route")) is None
  assert route_turn_agreed(following(maneuver(150))) is None, "beyond the lookahead"
  gentle_then_turn = [maneuver(40, turn_deg=10.0, kind="continue"), maneuver(90, turn_deg=-88.0)]
  assert route_turn_agreed(following(*gentle_then_turn, locked={"main": gentle_then_turn}))["distance_m"] == 90


def test_chosen_rule_reads_the_chosen_route_only_while_the_car_is_on_it():
  turn, straight_on = maneuver(80), maneuver(200, kind="continue", turn_deg=0.0, route_id="alt")
  nav = following(turn, locked={"main": [turn], "alt": [straight_on]}, chosen=on_chosen(turn))
  assert route_turn_agreed(nav) is None and route_turn_chosen(nav)["distance_m"] == 80, "the routes part, the choice says which"
  off = {"route_id": "main", "on_route": False, "arrived": False, "upcoming": []}
  assert route_turn_chosen(following(turn, chosen=off)) is None
  assert route_turn_chosen(following(turn)) is None and route_turn_chosen(None) is None


def test_the_route_asks_for_its_turn_only_near_the_corner_slow_and_with_the_blinker_to_that_side():
  right = maneuver(30)
  nav = following(right)
  assert route_desire(nav, 20.0, "right")["source"] == "consensus"
  assert route_desire(nav, 20.0, None) is None, "no blinker: as today"
  assert route_desire(nav, 20.0, "left") is None, "the blinker says the other way"
  assert route_desire(nav, 35.0, "right") is None, "too fast"
  assert route_desire(following(maneuver(60)), 20.0, "right") is None, "the corner is still too far"
  roundabout = maneuver(20, kind="roundabout", turn_deg=20.0)
  assert route_desire(following(roundabout), 15.0, "right") is None, "a roundabout has no side"
  straight_on = maneuver(200, kind="continue", turn_deg=0.0, route_id="alt")
  parted = following(right, locked={"main": [right], "alt": [straight_on]})
  assert route_desire(parted, 20.0, "right") is None, "the routes part and none was chosen"
  assert route_desire({**parted, "chosen": on_chosen(right)}, 20.0, "right")["source"] == "chosen"
  assert route_desire(None, 20.0, "right") is None, "no route"


class FakeFollower:
  def __init__(self, bundle=None, nav=None):
    self.given = []
    self.nav = nav

  def choose(self, choice):
    self.given.append(choice)
    return bool(choice) and choice.get("route_id") == "t1-2"

  def update(self, lat, lon, heading):
    return self.nav


def test_the_choice_is_taken_again_when_its_file_changes_and_marked_on_arrival(tmp_path):
  path, follower = tmp_path / "chosen.json", FakeFollower()
  watch = ChoiceWatch(str(path))
  assert watch.poll(0.0, follower) is None and follower.given == [], "no file, nothing changed"
  path.write_text(json.dumps({"route_id": "t1-2", "chosen_by": "device"}))
  assert watch.poll(0.5, follower) is None, "not looked at again within FILE_CHECK_S"
  assert watch.poll(1.5, follower) == {"kind": "choice", "route_id": "t1-2", "chosen_by": "device", "holds": True}
  assert watch.poll(3.0, follower) is None, "the same file"
  path.write_text(json.dumps({"route_id": "t9-1", "chosen_by": "app"}))
  os.utime(path, ns=(10**18, 10**18))
  assert watch.poll(4.5, follower) == {"kind": "choice", "route_id": "t9-1", "chosen_by": "app", "holds": False}
  path.write_text(json.dumps({"route_id": "t1-2", "chosen_by": "device"}))
  os.utime(path, ns=(2 * 10**18, 2 * 10**18))
  watch.poll(6.0, follower)
  assert watch.arrived(follower) == {"kind": "choice", "route_id": "t1-2", "arrived": True}
  assert json.loads(path.read_text())["arrived_at"][10] == "T" and follower.given[-1] is None
  assert watch.poll(8.0, follower) is None, "its own mark is not a new choice"
  path.unlink()
  assert watch.poll(10.0, follower) == {"kind": "choice", "route_id": None, "chosen_by": None, "holds": False}


def drive(guide, t0, steps, start_m=0.0):
  """Steps the guide north one metre a step, half a second apart; all records in order."""
  out = []
  for k in range(steps):
    t = t0 + 0.5 * k
    out += guide.step(t, int(t * 1e9), LAT + (start_m + k) / 111000.0, LON, NORTH, 30.0, None, True)
  return out


def test_a_bundle_that_arrives_during_the_drive_is_followed_at_once(tmp_path):
  path = tmp_path / "route_bundle.json"
  guide = Guide(False, str(path), str(tmp_path / "chosen.json"))
  assert drive(guide, 0.0, 4) == [] and guide.follower is None, "no bundle yet"
  path.write_text(json.dumps(bundle("2099-01-01T00:00:00Z")))
  seen = drive(guide, 2.0, 120, start_m=4.0)
  assert seen[0] == {"kind": "bundle", "bundle_id": "0123456789abcdef", "routes": 1, "choice_holds": False}
  assert [r["state"] for r in seen if r["kind"] == "state"] == ["searching", "following"]
  path.write_text(json.dumps(bundle("2099-01-01T00:00:00Z", bundle_id="fedcba9876543210")))
  os.utime(path, ns=(10**18, 10**18))
  seen = drive(guide, 70.0, 4, start_m=124.0)
  assert seen[0]["bundle_id"] == "fedcba9876543210" and seen[1]["state"] == "searching", "a new bundle starts over"
  path.unlink()
  assert drive(guide, 80.0, 4, start_m=128.0)[0] == {"kind": "bundle", "bundle_id": None, "routes": 0, "choice_holds": False}
  assert guide.follower is None


def test_an_expired_bundle_is_not_followed(tmp_path):
  path = tmp_path / "route_bundle.json"
  path.write_text(json.dumps(bundle("2020-01-01T00:00:00Z")))
  guide = Guide(False, str(path), str(tmp_path / "chosen.json"))
  assert drive(guide, 0.0, 4)[0]["bundle_id"] is None and guide.follower is None


def test_live_the_turn_is_published_while_the_blinker_holds_it(tmp_path, monkeypatch):
  side_path = tmp_path / "side"
  monkeypatch.setattr(route_desire_live, "SIDE_PATH", str(side_path))
  monkeypatch.setattr(route_guide, "Follower", lambda b: FakeFollower(nav=following(maneuver(30))))
  (tmp_path / "route_bundle.json").write_text(json.dumps(bundle("2099-01-01T00:00:00Z")))
  guide = Guide(True, str(tmp_path / "route_bundle.json"), str(tmp_path / "chosen.json"))
  seen = guide.step(0.0, 0, LAT, LON, NORTH, 20.0, "right", True)
  assert [(r["kind"], r.get("side")) for r in seen] == [("bundle", None), ("state", None), ("route_desire_live", "right")]
  assert json.loads(side_path.read_text())["side"] == "right"
  assert guide.step(0.1, 100_000_000, LAT, LON, NORTH, 20.0, "right", True) == []
  seen = guide.step(0.2, 200_000_000, LAT, LON, NORTH, 20.0, None, True)
  assert [(r["kind"], r["side"]) for r in seen] == [("route_desire_live", None)]
  assert json.loads(side_path.read_text())["side"] is None


def test_off_nothing_is_published(tmp_path, monkeypatch):
  side_path = tmp_path / "side"
  monkeypatch.setattr(route_desire_live, "SIDE_PATH", str(side_path))
  monkeypatch.setattr(route_guide, "Follower", lambda b: FakeFollower(nav=following(maneuver(30))))
  (tmp_path / "route_bundle.json").write_text(json.dumps(bundle("2099-01-01T00:00:00Z")))
  guide = Guide(False, str(tmp_path / "route_bundle.json"), str(tmp_path / "chosen.json"))
  assert [r["kind"] for r in guide.step(0.0, 0, LAT, LON, NORTH, 20.0, "right", True)] == ["bundle", "state"]
  assert not side_path.exists()
