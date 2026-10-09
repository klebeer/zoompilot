import json

from openpilot.sunnypilot.mapd.route_choice import chosen_route, choose, clear, load_bundle, trips


def route(route_id, trip=None):
  r = {"route_id": route_id, "label": route_id, "length_m": 100.0, "shape": [[-0.22, -78.51], [-0.219, -78.51]], "maneuvers": []}
  return {**r, "trip": trip} if trip else r


BUNDLE = {"schema": 1, "bundle_id": "ab" * 8, "expires_at": "2999-01-01T00:00:00Z",
          "routes": [route("t1-1", "Work to home"), route("t1-2", "Work to home"), route("t2-1", "Home to work"), route("d00000001-aaa-p1")]}


def test_only_a_bundle_the_device_should_follow_is_loaded(tmp_path):
  path = tmp_path / "route_bundle.json"
  assert load_bundle(str(path)) is None
  path.write_text(json.dumps({**BUNDLE, "expires_at": "2000-01-01T00:00:00Z"}))
  assert load_bundle(str(path)) is None, "expired"
  path.write_text("[]")
  assert load_bundle(str(path)) is None
  path.write_text(json.dumps(BUNDLE))
  assert load_bundle(str(path))["bundle_id"] == "ab" * 8


def test_the_saved_trips_are_offered_with_their_routes_and_past_drives_are_not():
  assert [(name, [r["route_id"] for r in routes]) for name, routes in trips(BUNDLE)] == \
         [("Work to home", ["t1-1", "t1-2"]), ("Home to work", ["t2-1"])]
  assert trips(None) == []


def test_a_route_chosen_on_the_device_holds_until_arrived_or_cleared(tmp_path):
  file = tmp_path / "chosen.json"
  path = str(file)
  assert chosen_route(BUNDLE, path) is None
  choose(BUNDLE, "t1-2", path)
  choice = json.loads(file.read_text())
  assert (choice["route_id"], choice["chosen_by"], choice["bundle_id"]) == ("t1-2", "device", "ab" * 8)
  assert chosen_route(BUNDLE, path)["route_id"] == "t1-2"
  assert chosen_route({**BUNDLE, "bundle_id": "cd" * 8}, path) is None, "a new bundle came"
  file.write_text(json.dumps({**choice, "arrived_at": "2026-10-03T12:00:00Z"}))
  assert chosen_route(BUNDLE, path) is None, "arrived"
  choose(BUNDLE, "t2-1", path)
  clear(path)
  clear(path)
  assert chosen_route(BUNDLE, path) is None


def go(route_id, start, frm, to, name=None):
  r = {**route(route_id), "shape": [start, [-0.2, -78.5]], "from_place": frm, "to_place": to}
  return {**r, "name": name} if name else r


PLACES = [{"name": "work", "label": "Work", "lat": -0.21, "lon": -78.49}, {"name": "home", "label": "Home", "lat": -0.2, "lon": -78.5},
          {"name": "gym", "label": "Gym", "lat": -0.19, "lon": -78.48}]


def test_go_to_offers_the_places_routes_reach_and_the_routes_that_start_near_the_car():
  from openpilot.sunnypilot.mapd.route_choice import places, routes_to, streets
  near_work, near_work_too, from_gym = [-0.21, -78.49], [-0.2101, -78.4901], [-0.19, -78.48]
  bundle = {**BUNDLE, "places": PLACES, "routes": BUNDLE["routes"] + [go("go-work-home-1", near_work, "work", "home"),
            go("go-gym-home-1", from_gym, "gym", "home"), go("go-work-home-2", near_work_too, "work", "home", "by the park")]}
  assert [p["name"] for p in places(bundle)] == ["home"], "nothing goes to work or the gym"
  assert [r["route_id"] for r in routes_to(bundle, "home")] == ["go-work-home-1", "go-gym-home-1", "go-work-home-2"]
  assert [r["route_id"] for r in routes_to(bundle, "home", (-0.2102, -78.4902))] == ["go-work-home-2", "go-work-home-1"], "nearest first"
  assert len(routes_to(bundle, "home", (-0.3, -78.6))) == 3, "far from every start: all of them"
  assert places(None) == [] and routes_to(None, "home") == []
  assert streets(route("t1-1", "Work to home") | {"label": "Work to home: Av. Uno"}) == "Av. Uno" and streets(route("x")) == "x"
