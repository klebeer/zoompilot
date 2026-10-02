import json

from openpilot.sunnypilot.mapd.curve_shadow import NavLog, Shadow, load_follower, load_known_curves, route_turns_ahead
from openpilot.sunnypilot.mapd.curve_warning import known_curve_ahead, tight_curve_ahead

# car at the origin heading north; one degree of latitude is about 111 km
LAT, LON = -0.16, -78.50
NORTH = 0.0


def point(north_m, east_m, radius_m):
  return {"latitude": LAT + north_m / 111000.0, "longitude": LON + east_m / 111000.0, "curvature": 1.0 / radius_m}


def test_finds_the_tightest_curve_ahead():
  hit = tight_curve_ahead(LAT, LON, NORTH, [point(60, 0, 30), point(80, 5, 12), point(40, 0, 200)])
  assert hit is not None and round(hit.radius) == 12 and 75 < hit.distance < 85


def test_ignores_curves_behind_too_far_or_gentle():
  assert tight_curve_ahead(LAT, LON, NORTH, [point(-50, 0, 10)]) is None
  assert tight_curve_ahead(LAT, LON, NORTH, [point(150, 0, 10)]) is None
  assert tight_curve_ahead(LAT, LON, NORTH, [point(50, 0, 60)]) is None


def test_shadow_fires_once_per_curve():
  s, curve = Shadow(), [point(70, 0, 12)]
  assert s.step(0.0, True, 35, LAT, LON, NORTH, curve, "Av") is not None
  assert s.step(5.0, True, 35, LAT, LON, NORTH, curve, "Av") is None
  assert s.step(30.0, True, 35, LAT, LON, NORTH, curve, "Av") is None, "same curve inside 60 s"
  assert s.step(70.0, True, 35, LAT, LON, NORTH, curve, "Av") is not None


def test_shadow_waits_20_s_between_curves():
  s = Shadow()
  assert s.step(0.0, True, 35, LAT, LON, NORTH, [point(70, 0, 12)], None) is not None
  assert s.step(10.0, True, 35, LAT, LON, NORTH, [point(90, 40, 10)], None) is None
  assert s.step(25.0, True, 35, LAT, LON, NORTH, [point(90, 40, 10)], None) is not None


def test_shadow_only_while_steering_in_town_speeds():
  curve = [point(70, 0, 12)]
  assert Shadow().step(0.0, False, 35, LAT, LON, NORTH, curve, None) is None
  assert Shadow().step(0.0, True, 5, LAT, LON, NORTH, curve, None) is None
  assert Shadow().step(0.0, True, 80, LAT, LON, NORTH, curve, None) is None


def test_record_carries_the_rule():
  rec = Shadow(known_curves=[]).step(0.0, True, 35, LAT, LON, NORTH, [point(70, 0, 12)], "Stacey Leonor")
  assert rec["road"] == "Stacey Leonor" and rec["radius_m"] == 12.0 and rec["rule"]["min_radius_m"] == 45.0
  assert rec["rules"] == ["map"]


def place(north_m, east_m):
  return {"latitude": LAT + north_m / 111000.0, "longitude": LON + east_m / 111000.0}


def test_known_curve_takes_the_nearest_ahead():
  hit = known_curve_ahead(LAT, LON, NORTH, [place(90, 0), place(40, 0)])
  assert hit is not None and 35 < hit.distance < 45


def test_known_curve_ignores_behind_and_too_far():
  assert known_curve_ahead(LAT, LON, NORTH, [place(-40, 0)]) is None
  assert known_curve_ahead(LAT, LON, NORTH, [place(150, 0)]) is None


def test_learned_curve_fires_where_the_map_says_nothing():
  # a gentle curve the map rule skips, at a place that was hard on earlier drives
  s = Shadow(known_curves=[place(60, 0)])
  rec = s.step(0.0, True, 35, LAT, LON, NORTH, [point(60, 0, 200)], "Oe9a")
  assert rec is not None and rec["rules"] == ["learned"] and rec["radius_m"] == 0.0


def test_both_rules_recorded_and_the_nearer_leads():
  s = Shadow(known_curves=[place(30, 0)])
  rec = s.step(0.0, True, 35, LAT, LON, NORTH, [point(80, 0, 12)], None)
  assert rec["rules"] == ["map", "learned"] and 25 < rec["distance_m"] < 35, "the learned point is nearer"


def test_no_curve_list_behaves_exactly_as_before():
  s = Shadow(known_curves=[])
  assert s.step(0.0, True, 35, LAT, LON, NORTH, [point(60, 0, 200)], None) is None
  assert s.step(0.0, True, 35, LAT, LON, NORTH, [point(70, 0, 12)], None) is not None


def test_missing_curve_file_is_an_empty_list():
  assert load_known_curves("/nonexistent/known_hard_curves.json") == []


def maneuver(distance_m, turn_deg=90.0, kind="turn", north_m=None, route_id="main"):
  at = place(distance_m if north_m is None else north_m, 0)
  return {"route_id": route_id, "index": 3, "type": kind, "modifier": "right" if kind == "turn" else None,
          "distance_m": distance_m, "turn_deg": turn_deg, "lat": at["latitude"], "lon": at["longitude"], "street": "Calle Uno"}


def following(*ahead, state="following"):
  return {"state": state, "route_id": "main", "progress_m": 500.0, "remaining_m": 3000.0, "match_m": 2.0,
          "next": ahead[0] if ahead else None, "ahead": list(ahead)}


def test_route_turn_fires_without_any_map_data():
  rec = Shadow(known_curves=[]).step(0.0, True, 35, LAT, LON, NORTH, None, "Av. Central", following(maneuver(80)))
  assert rec["rules"] == ["route"] and rec["distance_m"] == 80.0 and rec["radius_m"] == 0.0
  assert rec["route"] == {"route_id": "main", "type": "turn", "modifier": "right", "turn_deg": 90.0,
                          "street": "Calle Uno", "distance_m": 80}
  assert rec["rule"]["route_turn_deg"] == 45.0


def test_route_rule_wants_a_followed_route_a_real_turn_and_the_lookahead():
  assert route_turns_ahead(None) == []
  assert route_turns_ahead(following(maneuver(80), state="off_route")) == []
  assert route_turns_ahead(following(maneuver(150))) == []
  assert route_turns_ahead(following(maneuver(80, turn_deg=20.0))) == []
  assert len(route_turns_ahead(following(maneuver(80, turn_deg=-60.0)))) == 1
  assert len(route_turns_ahead(following(maneuver(80, turn_deg=15.0, kind="roundabout")))) == 1


def test_route_rule_skips_a_gentle_maneuver_for_the_turn_behind_it():
  nav = following(maneuver(40, turn_deg=10.0), maneuver(90, turn_deg=-88.0))
  assert [n["distance_m"] for n in route_turns_ahead(nav)] == [90]


def test_route_rule_keeps_the_gates_of_the_other_rules():
  nav = following(maneuver(80))
  assert Shadow(known_curves=[]).step(0.0, False, 35, LAT, LON, NORTH, None, None, nav) is None
  assert Shadow(known_curves=[]).step(0.0, True, 5, LAT, LON, NORTH, None, None, nav) is None
  s = Shadow(known_curves=[])
  assert s.step(0.0, True, 35, LAT, LON, NORTH, None, None, nav) is not None
  assert s.step(10.0, True, 35, LAT, LON, NORTH, None, None, nav) is None, "one warning every 20 s"


def test_three_rules_recorded_and_the_nearest_leads():
  s = Shadow(known_curves=[place(60, 0)])
  rec = s.step(0.0, True, 35, LAT, LON, NORTH, [point(80, 0, 12)], None, following(maneuver(30)))
  assert rec["rules"] == ["map", "learned", "route"] and rec["distance_m"] == 30.0
  s = Shadow(known_curves=[place(40, 0)])
  rec = s.step(0.0, True, 35, LAT, LON, NORTH, [point(80, 0, 12)], None, following(maneuver(90)))
  assert rec["rules"] == ["map", "learned", "route"] and 35 < rec["distance_m"] < 45 and rec["route"]["distance_m"] == 90


def test_without_a_route_the_record_is_as_before():
  rec = Shadow(known_curves=[]).step(0.0, True, 35, LAT, LON, NORTH, [point(70, 0, 12)], None, following(state="searching"))
  assert rec["rules"] == ["map"] and "route" not in rec
  assert Shadow(known_curves=[place(60, 0)]).step(0.0, True, 35, LAT, LON, NORTH, None, None) is None, "learned still waits for mapd"


def bundle(expires_at):
  shape = [[LAT, LON], [LAT + 0.003, LON]]
  ends = [{"shape_index": i, "distance_m": d, "type": t, "modifier": None, "bearing_before": None, "bearing_after": None,
           "turn_deg": 0.0, "street": "", "exit": None} for i, d, t in ((0, 0.0, "depart"), (1, 333.6, "arrive"))]
  return {"schema": 1, "bundle_id": "0123456789abcdef", "expires_at": expires_at,
          "routes": [{"route_id": "north", "label": "north", "length_m": 333.6, "shape": shape, "maneuvers": ends}]}


def test_follower_loads_only_from_a_bundle_the_device_should_follow(tmp_path):
  path = tmp_path / "route_bundle.json"
  assert load_follower(str(path)) is None, "no bundle"
  path.write_text("{not json")
  assert load_follower(str(path)) is None, "unreadable"
  path.write_text(json.dumps(bundle("2020-01-01T00:00:00Z")))
  assert load_follower(str(path)) is None, "expired"
  path.write_text(json.dumps({**bundle("2099-01-01T00:00:00Z"), "schema": 2}))
  assert load_follower(str(path)) is None, "unknown version"
  path.write_text(json.dumps(bundle("2099-01-01T00:00:00Z")))
  follower = load_follower(str(path))
  states = [follower.update(LAT + k * 0.00005, LON, NORTH)["state"] for k in range(20)]
  assert states[0] == "searching" and states[-1] == "following"


def test_nav_log_records_each_state_change_and_each_turn_once():
  log = NavLog()
  first = log.records(0.0, following(state="searching"), LAT, LON, 20.0, True)
  assert [r["kind"] for r in first] == ["state"] and first[0]["state"] == "searching"
  assert log.records(1.0, following(state="searching"), LAT, LON, 20.0, True) == []
  seen = log.records(2.0, following(maneuver(90)), LAT, LON, 30.0, False)
  assert [r["kind"] for r in seen] == ["state", "turn"]
  assert seen[1]["street"] == "Calle Uno" and seen[1]["distance_m"] == 90 and seen[1]["lat_active"] is False
  assert log.records(3.0, following(maneuver(70, north_m=90)), LAT, LON, 30.0, True) == [], "the same turn, nearer"
  again = log.records(200.0, following(maneuver(90)), LAT, LON, 30.0, True)
  assert [r["kind"] for r in again] == ["turn"], "another pass by the same turn"


def test_nav_log_records_the_turns_of_every_route_the_car_is_on():
  log = NavLog()
  seen = log.records(0.0, following(maneuver(60), maneuver(95, turn_deg=-70.0, route_id="alt")), LAT, LON, 30.0, True)
  assert [(r["kind"], r.get("route_id")) for r in seen] == [("state", "main"), ("turn", "main"), ("turn", "alt")]
