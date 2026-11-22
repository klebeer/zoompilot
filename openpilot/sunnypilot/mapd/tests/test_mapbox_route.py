import os

from openpilot.sunnypilot.mapd import mapbox_route
from openpilot.sunnypilot.mapd.route_choice import load_bundle, read_json
from openpilot.sunnypilot.mapd.route_follower import Follower, choice_holds, usable


def step(coordinates, kind, modifier=None, before=0, after=0, name="", distance=100.0, banners=()):
  maneuver = {"type": kind, "bearing_before": before, "bearing_after": after}
  if modifier is not None:
    maneuver["modifier"] = modifier
  return {"geometry": {"coordinates": coordinates}, "maneuver": maneuver, "name": name, "distance": distance, "duration": 10.0,
          "bannerInstructions": list(banners)}


# north along one street, a right turn onto another, then the arrival. Mapbox gives longitude first.
STEPS = [
  step([[-78.5, -0.2000], [-78.5, -0.1991]], "depart", name="Avenida Amazonas"),
  step([[-78.5, -0.1991], [-78.4991, -0.1991]], "turn", "right", before=0, after=90, name="Avenida Naciones Unidas"),
  step([[-78.4991, -0.1991], [-78.4991, -0.1991]], "arrive", distance=0.0),
]


def test_lanes_follow_the_banner_left_to_right():
  banner = {"primary": {"text": "x"}, "sub": {"components": [
    {"type": "lane", "directions": ["left"], "active": False},
    {"type": "lane", "directions": ["straight", "slight right"], "active": True, "active_direction": "slight right"},
    {"type": "text", "text": "ignored"},
  ]}}
  assert mapbox_route.banner_lanes(banner) == [
    {"directions": ["left"], "active": False, "activeDirection": "left"},
    {"directions": ["straight", "slightRight"], "active": True, "activeDirection": "slightRight"},
  ]
  assert mapbox_route.banner_lanes({"primary": {"text": "x"}}) == []


def test_maneuvers_ahead_start_at_the_end_of_the_current_step():
  ahead = mapbox_route.maneuvers_ahead(STEPS, 0, 40.0)
  assert [(m["type"], m["modifier"], m["distance"]) for m in ahead] == [("turn", "right", 40.0), ("arrive", "", 140.0)]
  assert mapbox_route.maneuvers_ahead(STEPS, 2, 0.0) == []


def test_route_puts_each_maneuver_where_its_step_begins():
  route = mapbox_route.to_route(STEPS, "mapbox")
  assert route["shape"] == [[-0.2000, -78.5], [-0.1991, -78.5], [-0.1991, -78.4991]]
  turn = route["maneuvers"][1]
  assert (turn["shape_index"], turn["type"], turn["modifier"], turn["turn_deg"], turn["street"]) == (1, "turn", "right", 90.0, "Avenida Naciones Unidas")
  assert abs(turn["distance_m"] - 100.1) < 0.5
  # the depart has no road behind it: its bearings say nothing about a turn
  assert route["maneuvers"][0]["turn_deg"] == 0.0
  assert abs(route["length_m"] - 200.2) < 1.0


def test_the_follower_takes_the_bundle_with_its_route_chosen():
  bundle, choice = mapbox_route.to_bundle(STEPS)
  assert usable(bundle) and choice_holds(choice, bundle)
  follower = Follower(bundle)
  follower.choose(choice)
  nav = None
  for lat in (-0.2000, -0.1998, -0.1996, -0.1994):
    nav = follower.update(lat, -78.5, 0.0)
  assert nav["state"] == "following" and nav["route_id"] == "mapbox"
  assert nav["next"]["type"] == "turn" and nav["next"]["turn_deg"] == 90.0


def test_another_route_is_another_bundle():
  other = [STEPS[0], step([[-78.5, -0.1991], [-78.5009, -0.1991]], "turn", "left", before=0, after=270), STEPS[2]]
  assert mapbox_route.to_bundle(STEPS)[0]["bundle_id"] != mapbox_route.to_bundle(other)[0]["bundle_id"]


def test_publish_and_withdraw(tmp_path):
  bundle_path, choice_path = str(tmp_path / "nav" / "bundle.json"), str(tmp_path / "nav" / "chosen.json")
  mapbox_route.publish(STEPS, bundle_path, choice_path)
  bundle = load_bundle(bundle_path)
  assert bundle is not None and choice_holds(read_json(choice_path), bundle)
  mapbox_route.withdraw(bundle_path, choice_path)
  assert not os.path.exists(bundle_path) and not os.path.exists(choice_path)
  mapbox_route.withdraw(bundle_path, choice_path)


def test_token_comes_from_the_environment_or_the_file(tmp_path, monkeypatch):
  path = tmp_path / "token"
  monkeypatch.delenv("MAPBOX_TOKEN", raising=False)
  assert mapbox_route.read_token(str(path)) is None
  path.write_text("from-file\n")
  assert mapbox_route.read_token(str(path)) == "from-file"
  monkeypatch.setenv("MAPBOX_TOKEN", "from-env")
  assert mapbox_route.read_token(str(path)) == "from-env"
