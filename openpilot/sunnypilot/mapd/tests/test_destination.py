import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from openpilot.sunnypilot.mapd import destination, destination_web


class FakeParams:
  def __init__(self):
    self.values = {}

  def get(self, key):
    return self.values.get(key)

  def get_bool(self, key):
    return bool(self.values.get(key))

  def put(self, key, value):
    self.values[key] = value

  def put_bool(self, key, value):
    self.values[key] = value

  def remove(self, key):
    self.values.pop(key, None)


class FakeResponse:
  def __init__(self, status=200, content=None, headers=None):
    self.status_code, self.content, self.headers = status, content, headers or {}

  def json(self):
    return self.content

  def __enter__(self):
    return self

  def __exit__(self, *args):
    return False


class FakeSession:
  def __init__(self, answers):
    self.answers, self.calls = answers, []

  def get(self, url, **kwargs):
    self.calls.append((url, kwargs))
    return next(answer for prefix, answer in self.answers if url.startswith(prefix))

  def request(self, method, url, **kwargs):
    return self.get(url, method=method, **kwargs)


PLACE = {"name": "Quicentro", "address": "Av. Naciones Unidas", "latitude": -0.1763, "longitude": -78.4797}


@pytest.fixture
def keeper(tmp_path):
  return destination.Keeper(FakeParams(), str(tmp_path / "nav" / "destination.json"))


@pytest.mark.parametrize("text,expected", [
  ("https://www.google.com/maps/place/Quicentro/@-0.17,-78.48,17z/data=!3d-0.1763!4d-78.4797", ("Quicentro", -0.1763, -78.4797)),
  ("https://maps.google.com/?q=-0.2100,-78.4900", ("Shared destination", -0.21, -78.49)),
  ("geo:-0.18,-78.47", ("Shared destination", -0.18, -78.47)),
  ("-0.1050, -78.4700", ("Shared destination", -0.105, -78.47)),
])
def test_coordinates_come_out_of_shared_links(text, expected):
  place = destination.shared_coordinates(text)
  assert (place["name"], place["latitude"], place["longitude"]) == expected


def test_the_place_pin_wins_over_where_the_map_was_looking():
  place = destination.shared_coordinates("https://www.google.com/maps/place/X/@10.0,20.0,17z/data=!3d-0.5!4d-78.5")
  assert (place["latitude"], place["longitude"]) == (-0.5, -78.5)


def test_text_without_coordinates_is_not_a_place():
  assert destination.shared_coordinates("https://www.google.com/maps/place/Quicentro+Shopping") is None
  assert destination.shared_coordinates("geo:0,0?q=Quicentro") is None
  assert destination.map_link_query("https://www.google.com/maps/place/Quicentro+Shopping") == "Quicentro Shopping"
  assert destination.map_link_query("https://example.com/maps/place/Elsewhere") is None


def test_a_waze_link_carries_a_geohash():
  latitude, longitude = destination.waze_geohash("6rbj0")
  assert abs(latitude - (-0.2)) < 0.5 and abs(longitude - (-78.5)) < 0.5


def test_short_links_are_followed_only_through_google():
  full = "https://www.google.com/maps/place/X/data=!3d-0.5!4d-78.5"
  session = FakeSession([("https://maps.app.goo.gl/", FakeResponse(302, headers={"Location": full})), (full, FakeResponse(200))])
  assert destination.resolve_short_link("look https://maps.app.goo.gl/abc", session) == f"look {full}"
  elsewhere = FakeSession([("https://maps.app.goo.gl/", FakeResponse(302, headers={"Location": "https://evil.example/x"}))])
  assert destination.resolve_short_link("https://maps.app.goo.gl/abc", elsewhere) == "https://maps.app.goo.gl/abc"
  assert [url for url, _ in elsewhere.calls] == ["https://maps.app.goo.gl/abc"]


def test_suggest_and_retrieve_speak_search_box():
  session = FakeSession([
    (f"{destination.SEARCH_HOST}/suggest", FakeResponse(content={"suggestions": [{"mapbox_id": "abc123", "name": "Quicentro", "full_address": "Quito"}]})),
    (f"{destination.SEARCH_HOST}/retrieve/abc123", FakeResponse(content={"features": [
      {"geometry": {"coordinates": [-78.4797, -0.1763]}, "properties": {"name": "Quicentro", "full_address": "Quito"}}]})),
  ])
  assert destination.suggest(session, "token", "quicentro", "s1", (-0.2, -78.5)) == [{"id": "abc123", "name": "Quicentro", "address": "Quito"}]
  assert session.calls[0][1]["params"]["proximity"] == "-78.5,-0.2"
  place = destination.retrieve(session, "token", "abc123", "s1")
  assert (place["latitude"], place["longitude"]) == (-0.1763, -78.4797)
  assert destination.suggest(session, "token", "q", "s1", None) == []
  with pytest.raises(destination.DestinationError):
    destination.retrieve(session, "token", "../etc", "s1")


def test_a_refused_token_is_said_plainly():
  session = FakeSession([(destination.SEARCH_HOST, FakeResponse(401))])
  with pytest.raises(destination.DestinationError, match="token"):
    destination.suggest(session, "bad", "quicentro", "s1", None)


def test_a_destination_set_with_the_car_off_survives_the_start(keeper):
  keeper.set(PLACE)
  assert keeper.params.get_bool("NavigationEnabled") and keeper.params.get("NavDestination")["latitude"] == PLACE["latitude"]
  keeper.params.remove("NavDestination")  # comma clears it when the car turns off
  keeper.step(False)
  keeper.step(True)
  assert keeper.params.get("NavDestination")["longitude"] == PLACE["longitude"]


def test_arriving_ends_the_destination(keeper):
  keeper.set(PLACE)
  keeper.step(True)
  keeper.params.remove("NavDestination")  # navd reached it
  for _ in range(destination.GONE_STEPS):
    assert keeper.wanted() is not None
    keeper.step(True)
  assert keeper.wanted() is None and not keeper.params.get_bool("NavigationEnabled")


def test_the_car_turning_off_is_not_an_arrival(keeper):
  keeper.set(PLACE)
  keeper.step(True)
  keeper.params.remove("NavDestination")
  keeper.step(True)   # the param is cleared a moment before the drive is seen to end
  keeper.step(False)
  keeper.step(False)
  assert keeper.wanted() is not None
  keeper.step(True)
  assert keeper.params.get("NavDestination") is not None


def test_clear_turns_the_navigation_off(keeper):
  keeper.set(PLACE)
  keeper.clear()
  assert keeper.wanted() is None and keeper.params.get("NavDestination") is None and not keeper.params.get_bool("NavigationEnabled")
  with pytest.raises(destination.DestinationError):
    keeper.set({"latitude": 91, "longitude": 0})


@pytest.fixture
def served(keeper):
  session = FakeSession([
    (f"{destination.SEARCH_HOST}/suggest", FakeResponse(content={"suggestions": [{"mapbox_id": "abc123", "name": "Quicentro"}]})),
    (f"{destination.SEARCH_HOST}/retrieve/abc123", FakeResponse(content={"features": [
      {"geometry": {"coordinates": [-78.4797, -0.1763]}, "properties": {"name": "Quicentro"}}]})),
  ])
  app = destination_web.App(keeper, keeper.params, session=session, token=lambda: "token", google_key=lambda: None)
  server = ThreadingHTTPServer(("127.0.0.1", 0), destination_web.make_handler(app, "k" * 24))
  threading.Thread(target=server.serve_forever, daemon=True).start()
  yield f"http://127.0.0.1:{server.server_port}", keeper
  server.shutdown()
  server.server_close()


def request(url, body=None):
  data = None if body is None else json.dumps(body).encode()
  with urllib.request.urlopen(urllib.request.Request(url, data=data, method="GET" if body is None else "POST"), timeout=5) as response:
    return response.status, response.read()


def test_nothing_is_served_without_the_key(served):
  base, _ = served
  for path in ("/", "/wrong-key/", "/wrong-key/api/state", "/" + "k" * 23 + "/"):
    with pytest.raises(urllib.error.HTTPError) as error:
      request(base + path)
    assert error.value.code == 404
  with pytest.raises(urllib.error.HTTPError):
    request(base + "/wrong-key/api/clear", {})


def test_the_page_searches_chooses_and_cancels(served):
  base, keeper = served
  root = f"{base}/{'k' * 24}"
  status, page = request(root + "/")
  assert status == 200 and b"Where to?" in page and b"token" not in page.lower().replace(b"session_token", b"")
  assert json.loads(request(root + "/api/suggest?q=quicentro")[1])["places"][0]["id"] == "abc123"
  assert json.loads(request(root + "/api/choose", {"id": "abc123"})[1])["destination"]["name"] == "Quicentro"
  assert keeper.params.get("NavDestination")["latitude"] == -0.1763
  assert json.loads(request(root + "/api/share", {"text": "geo:-0.18,-78.47"})[1])["destination"]["latitude"] == -0.18
  assert json.loads(request(root + "/api/clear", {})[1])["destination"] is None
  with pytest.raises(urllib.error.HTTPError) as error:
    request(root + "/api/share", {"text": "nothing here"})
  assert error.value.code == 400 and b"no place" in error.value.read()


def test_the_key_is_made_once_and_kept_private(tmp_path):
  path = tmp_path / "nav" / "key"
  key = destination_web.load_key(str(path))
  assert len(key) >= 16 and destination_web.load_key(str(path)) == key
  assert path.stat().st_mode & 0o077 == 0


GOOGLE = [
  (f"{destination.GOOGLE_HOST}/places:autocomplete", FakeResponse(content={"suggestions": [
    {"placePrediction": {"placeId": "ChIJ_quicentro", "structuredFormat": {"mainText": {"text": "Quicentro Shopping"},
                                                                         "secondaryText": {"text": "Quito, Ecuador"}}}},
    {"queryPrediction": {"text": {"text": "not a place"}}}]})),
  (f"{destination.GOOGLE_HOST}/places/ChIJ_quicentro", FakeResponse(content={"location": {"latitude": -0.1763, "longitude": -78.4797},
                                                                           "formattedAddress": "Av. Naciones Unidas, Quito"})),
]


def test_google_places_gives_the_places_and_their_coordinates():
  session = FakeSession(GOOGLE)
  places = destination.google_suggest(session, "key", "quicentro", "s1", (-0.2, -78.5))
  assert places == [{"id": "ChIJ_quicentro", "name": "Quicentro Shopping", "address": "Quito, Ecuador"}]
  url, sent = session.calls[0]
  assert sent["headers"]["X-Goog-Api-Key"] == "key" and "key" not in url
  assert sent["json"]["locationBias"]["circle"]["center"] == {"latitude": -0.2, "longitude": -78.5}
  place = destination.google_retrieve(session, "key", "ChIJ_quicentro", "s1", "Quicentro Shopping")
  assert (place["name"], place["latitude"], place["address"]) == ("Quicentro Shopping", -0.1763, "Av. Naciones Unidas, Quito")
  assert session.calls[1][1]["headers"]["X-Goog-FieldMask"] == destination.GOOGLE_FIELDS


def test_with_a_google_key_the_search_is_googles(keeper):
  app = destination_web.App(keeper, keeper.params, session=FakeSession(GOOGLE), token=lambda: None, google_key=lambda: "key")
  assert app.suggest("quicentro")["places"][0]["id"] == "ChIJ_quicentro"
  assert app.choose("ChIJ_quicentro")["destination"]["name"] == "Quicentro Shopping"
  with pytest.raises(destination.DestinationError, match="Search again"):
    app.choose("ChIJ_never_offered")


DIRECTIONS = (destination.DIRECTIONS_HOST, FakeResponse(content={"routes": [{"legs": [{"distance": 5200.4, "duration": 780.2, "steps": [
  {"maneuver": {"type": "depart"}, "name": "A", "distance": 300.0},
  {"maneuver": {"type": "turn", "modifier": "right"}, "name": "Avenida B", "distance": 4900.4},
  {"maneuver": {"type": "arrive"}, "name": "", "distance": 0.0}]}]}]}))


def test_the_route_summary_gives_the_trip_and_its_turns():
  summary = destination.route_summary(FakeSession([DIRECTIONS]), "token", (-0.2, -78.5), PLACE)
  assert summary == {"distance_m": 5200, "duration_s": 780,
                     "turns": [{"type": "turn", "modifier": "right", "street": "Avenida B", "after_m": 300}]}
  with pytest.raises(destination.DestinationError):
    destination.route_summary(FakeSession([(destination.DIRECTIONS_HOST, FakeResponse(content={"routes": []}))]), "token", (-0.2, -78.5), PLACE)


def test_the_summary_is_asked_once_per_destination(keeper):
  keeper.params.put("LastGPSPositionLLK", '{"latitude": -0.2, "longitude": -78.5}')
  session = FakeSession([DIRECTIONS])
  app = destination_web.App(keeper, keeper.params, session=session, token=lambda: "token", google_key=lambda: None)
  assert app.share("geo:-0.18,-78.47")["route"]["distance_m"] == 5200
  app.state()
  app.state()
  assert len(session.calls) == 1


def test_favorites_and_recent_places(keeper):
  app = destination_web.App(keeper, keeper.params, session=FakeSession([]), token=lambda: None, google_key=lambda: None)
  with pytest.raises(destination.DestinationError):
    app.favorite("home")
  app.share("geo:-0.18,-78.47")
  assert app.favorite("home")["favorites"]["home"]["latitude"] == -0.18
  app.share("geo:-0.30,-78.55")
  app.share("geo:-0.18,-78.47")
  assert [p["latitude"] for p in app.state()["favorites"]["recent"]] == [-0.18, -0.30]
  app.clear()
  assert app.go("home", None)["destination"]["latitude"] == -0.18
  assert app.go(None, 1)["destination"]["latitude"] == -0.30
  with pytest.raises(destination.DestinationError):
    app.go("work", None)
  with pytest.raises(destination.DestinationError):
    app.favorite("elsewhere")
