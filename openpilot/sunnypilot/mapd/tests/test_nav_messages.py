import pytest

from openpilot.cereal import log
from openpilot.sunnypilot.mapd.nav_messages import ROUTE_RESEND_S, NavPublisher, followed_route, instruction
from openpilot.sunnypilot.mapd.route_follower import Follower

# 300 m north from the origin, then 300 m east; one degree of latitude is about 111 km
LAT, LON = -0.16, -78.50
DLAT = 300 / 111195.0
DLON = 300 / (111195.0 * 0.99999610)


def bundle():
  shape = [[LAT, LON], [LAT + DLAT, LON], [LAT + DLAT, LON + DLON]]
  maneuvers = [
    {"shape_index": 0, "distance_m": 0.0, "type": "depart", "modifier": None, "turn_deg": 0.0, "street": "Calle Uno"},
    {"shape_index": 1, "distance_m": 300.0, "type": "turn", "modifier": "right", "turn_deg": 90.0, "street": "Calle Dos"},
    {"shape_index": 2, "distance_m": 600.0, "type": "arrive", "modifier": None, "turn_deg": 0.0, "street": "Calle Dos"},
  ]
  return {"schema": 1, "bundle_id": "0123456789abcdef", "expires_at": "2099-01-01T00:00:00Z",
          "routes": [{"route_id": "north-east", "label": "", "length_m": 600.0, "shape": shape, "maneuvers": maneuvers}]}


def drive_north(follower, metres):
  nav = None
  for m in range(0, metres + 1, 2):
    nav = follower.update(LAT + m / 111195.0, LON, 0.0)
  return nav


class FakePM:
  def __init__(self):
    self.sent = []

  def send(self, service, msg):
    self.sent.append((service, msg))


def new_message(service, valid=True):
  msg = log.Event.new_message()
  msg.init(service)
  msg.valid = valid
  return msg


def test_nothing_is_followed_before_the_route_locks():
  follower = Follower(bundle())
  nav = drive_north(follower, 20)
  assert nav["state"] != "following"
  assert followed_route(nav, follower) is None and instruction(nav, follower) is None


def test_the_followed_route_and_its_next_maneuver():
  follower = Follower(bundle())
  nav = drive_north(follower, 100)
  route_id, shape = followed_route(nav, follower)
  assert route_id == "north-east" and len(shape) == 3
  fields = instruction(nav, follower)
  assert fields["maneuverType"] == "turn" and fields["maneuverModifier"] == "right"
  assert fields["maneuverPrimaryText"] == "Calle Dos"
  assert fields["maneuverDistance"] == pytest.approx(200.0, abs=3.0)
  assert fields["showFull"] is False
  assert fields["distanceRemaining"] == pytest.approx(500.0, abs=3.0)
  assert [(m["type"], round(m["distance"])) for m in fields["allManeuvers"]] == [("turn", 200), ("arrive", 500)]


def test_the_banner_shows_in_full_near_the_maneuver():
  follower = Follower(bundle())
  assert instruction(drive_north(follower, 200), follower)["showFull"] is True


def test_route_on_change_and_every_resend_period_instruction_every_second():
  follower = Follower(bundle())
  pm = FakePM()
  pub = NavPublisher(pm, new_message)
  nav = drive_north(follower, 100)
  pub.step(0.0, nav, follower)
  assert [s for s, _ in pm.sent] == ["navRoute", "navInstruction"]
  route = pm.sent[0][1].navRoute.coordinates
  # Float32 in the schema, as from comma's navd: about 1 m at this longitude
  assert len(route) == 3 and route[2].longitude == pytest.approx(LON + DLON, abs=1e-5)
  assert pm.sent[1][1].valid and pm.sent[1][1].navInstruction.maneuverModifier == "right"
  pm.sent.clear()
  pub.step(0.5, nav, follower)
  assert pm.sent == [], "nothing new within the instruction period"
  pub.step(1.0, nav, follower)
  assert [s for s, _ in pm.sent] == ["navInstruction"]
  pm.sent.clear()
  pub.step(ROUTE_RESEND_S, nav, follower)
  assert [s for s, _ in pm.sent] == ["navRoute", "navInstruction"], "the route goes out again for a late reader"


def test_when_following_stops_the_route_clears_and_the_instruction_turns_invalid():
  follower = Follower(bundle())
  pm = FakePM()
  pub = NavPublisher(pm, new_message)
  pub.step(0.0, drive_north(follower, 100), follower)
  pm.sent.clear()
  pub.step(1.0, None, None)
  assert [s for s, _ in pm.sent] == ["navRoute", "navInstruction"]
  assert len(pm.sent[0][1].navRoute.coordinates) == 0
  assert not pm.sent[1][1].valid
