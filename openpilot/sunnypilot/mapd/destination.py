"""
Where to go, for the Mapbox navigation: a place searched by name, or one shared as a Google Maps or Waze link.

Places are searched with Google Places when the driver's key is in GOOGLE_KEY_PATH, since Mapbox knows streets but
few businesses in some countries, and with Mapbox otherwise. The route is Mapbox's either way: a search only gives
the coordinates.

`Keeper` holds the destination across the car's off and on. comma clears NavDestination every time the car turns
off, so the wanted place is kept in DESTINATION_PATH and put back when the car starts. NavigationEnabled is on only
while a destination is wanted: without one, the comma-nav route of the phone is followed as before.

The link rules are StarPilot's (starpilot/navigation/share.py, MIT, firestar5683 and StarPilot contributors).
"""
import json
import os
import re
from urllib.parse import parse_qs, unquote_plus, urljoin, urlsplit

import requests

from openpilot.common.hardware import PC
from openpilot.sunnypilot.mapd.route_choice import read_json, write_json

DESTINATION_PATH = os.path.expanduser("~/.comma/nav/destination.json") if PC else "/data/nav/destination.json"
FAVORITES_PATH = os.path.expanduser("~/.comma/nav/favorites.json") if PC else "/data/nav/favorites.json"
DIRECTIONS_HOST = "https://api.mapbox.com/directions/v5/mapbox/driving-traffic"
SLOTS = ("home", "work")
RECENTS = 6
SUMMARY_STEPS = 4
# two places this close are the same one in the list of recent ones
SAME_PLACE_DEG = 0.0003
GOOGLE_KEY_PATH = os.path.expanduser("~/.comma/nav/google_places_key") if PC else "/data/nav/google_places_key"
SEARCH_HOST = "https://api.mapbox.com/search/searchbox/v1"
GOOGLE_HOST = "https://places.googleapis.com/v1"
# results near the car first, without ruling out the rest of the country
GOOGLE_BIAS_RADIUS_M = 50000.0
# only fields of the cheapest Place Details tier: the name comes from the suggestion
GOOGLE_FIELDS = "location,formattedAddress"
TIMEOUT_S = (3.05, 6.0)
MAX_TEXT = 8192
SUGGESTIONS = 8
# NavDestination has to stay gone this many steps of a drive to count as arrived: the car turning off clears it
# too, a moment before the drive is seen to end
GONE_STEPS = 3

NUMBER = r'(-?\d+(?:\.\d+)?)'
# destination coordinates before Google's viewport (@lat,lng), which is only where the map was looking
COORDINATES = (re.compile(rf'!3d{NUMBER}!4d{NUMBER}'), re.compile(rf'[?&:](?:q|query|daddr|destination|ll|coordinate)={NUMBER},\s*{NUMBER}'),
               re.compile(rf'geo:{NUMBER},\s*{NUMBER}'), re.compile(rf'to=ll\.{NUMBER},\s*{NUMBER}'), re.compile(rf'@{NUMBER},\s*{NUMBER}'))
WAZE_SHORT = re.compile(r'https://(?:www\.)?waze\.com/ul/h([0-9b-hjkmnp-z]{4,12})(?![0-9a-z])', re.I)
GEOHASH = '0123456789bcdefghjkmnpqrstuvwxyz'
QUERY_KEYS = ('q', 'query', 'daddr', 'destination')
LINK = re.compile(r'https://[^\s<>"]+')
MAP_HOST = re.compile(r'(?:[a-z0-9-]+\.)*google\.com|(?:maps\.app\.)?goo\.gl|maps\.apple(?:\.com)?|(?:[a-z0-9-]+\.)?waze\.com')
SHORT_MAP_LINK = re.compile(r'https://(?:maps\.app\.goo\.gl|goo\.gl/maps)/[^\s<>"]+')
GOOGLE_MAP_HOST = re.compile(r'(?:[a-z0-9-]+\.)*google\.com|(?:maps\.app\.)?goo\.gl')
REDIRECTS = 4


class DestinationError(Exception):
  """Something the person at the page can act on. Its text is shown to them."""


def place(label: str, latitude: float, longitude: float, address: str | None = None) -> dict:
  name, _, rest = label.partition(',')
  return {"name": name.strip()[:256] or "Shared destination", "latitude": latitude, "longitude": longitude,
          "address": address or rest.strip() or None}


def waze_geohash(code: str) -> tuple[float, float]:
  latitude, longitude, even = [-90.0, 90.0], [-180.0, 180.0], True
  for char in code.lower():
    bits = GEOHASH.index(char)
    for shift in range(4, -1, -1):
      span = longitude if even else latitude
      middle = (span[0] + span[1]) / 2
      span[0 if bits >> shift & 1 else 1] = middle
      even = not even
  return round((latitude[0] + latitude[1]) / 2, 6), round((longitude[0] + longitude[1]) / 2, 6)


def shared_coordinates(text: str) -> dict | None:
  """The place a shared link or a pair of coordinates points at, or None when the text carries no coordinates."""
  waze = WAZE_SHORT.search(text)
  if waze:
    return place("Shared destination", *waze_geohash(waze[1]))
  decoded = unquote_plus(text.strip())
  bare = re.fullmatch(rf'{NUMBER},\s*{NUMBER}', decoded)
  for match in ([bare] if bare else []) + [pattern.search(decoded) for pattern in COORDINATES]:
    if match is None:
      continue
    latitude, longitude = float(match[1]), float(match[2])
    if abs(latitude) > 90 or abs(longitude) > 180:
      continue
    # geo:0,0?q=an address is a search, not a destination in the ocean
    if match[0].startswith('geo:0,0') and re.search(r'[?&:]q=', decoded):
      continue
    label = (re.search(r'\(([^()]{1,120})\)', decoded) or re.search(r'/place/([^/?#&\n]+)', decoded) or
             re.search(r'[?&](?:name|q)=([^&#\n]*[^\d\s,.&#-][^&#\n]*)', decoded))
    return place(label[1] if label else "Shared destination", latitude, longitude)
  return None


def map_link_query(text: str) -> str | None:
  """The place a Google or Apple Maps link names, for links without coordinates."""
  for link in LINK.findall(text):
    parts = urlsplit(link)
    if not MAP_HOST.fullmatch(parts.hostname or ''):
      continue
    named = re.search(r'/place/([^/]+)', parts.path)
    values = parse_qs(parts.query)
    query = unquote_plus(named[1]) if named else next((values[key][0] for key in QUERY_KEYS if key in values), '')
    query = ' '.join(query.split())
    if query:
      return query[:256]
  return None


def resolve_short_link(text: str, session) -> str:
  """A Google Maps short link followed to the full one, which carries the coordinates. Only Google's own hosts
  are followed, so a pasted link cannot send the comma anywhere else."""
  match = SHORT_MAP_LINK.search(text)
  if match is None:
    return text
  current = match.group(0)
  for _ in range(REDIRECTS):
    try:
      with session.get(current, allow_redirects=False, stream=True, timeout=(2.0, 2.0)) as response:
        location = response.headers.get('Location') if 300 <= response.status_code < 400 else None
    except requests.RequestException:
      break
    if not location:
      break
    following = urljoin(current, location)
    parts = urlsplit(following)
    if parts.scheme != 'https' or not GOOGLE_MAP_HOST.fullmatch(parts.hostname or '') or len(following) > MAX_TEXT:
      break
    current = following
  return text.replace(match.group(0), current)


def _mapbox(session, path: str, query: dict) -> dict:
  try:
    response = session.get(f"{SEARCH_HOST}/{path}", params=query, timeout=TIMEOUT_S)
  except requests.RequestException:
    raise DestinationError("The map service could not be reached") from None
  if response.status_code in (401, 403):
    raise DestinationError("The map service refused the Mapbox token")
  if response.status_code == 429:
    raise DestinationError("The Mapbox allowance is used up for now")
  if response.status_code != 200:
    raise DestinationError(f"The map service answered {response.status_code}")
  try:
    return response.json()
  except ValueError:
    raise DestinationError("The map service sent an unreadable answer") from None


def suggest(session, token: str, query: str, search_session: str, near: tuple[float, float] | None, language: str = "es") -> list[dict]:
  """Places matching what was typed, nearest first when the car's position is known: [{id, name, address}]."""
  query = ' '.join(query.split())[:200]
  if len(query) < 2:
    return []
  request = {"q": query, "access_token": token, "session_token": search_session, "limit": SUGGESTIONS, "language": language}
  if near is not None:
    request["proximity"] = f"{near[1]},{near[0]}"
  data = _mapbox(session, "suggest", request)
  return [{"id": s["mapbox_id"], "name": s.get("name") or "", "address": s.get("full_address") or s.get("place_formatted") or ""}
          for s in data.get("suggestions") or [] if isinstance(s.get("mapbox_id"), str)]


def retrieve(session, token: str, mapbox_id: str, search_session: str) -> dict:
  """A suggestion's coordinates. Ends the search session Mapbox bills by."""
  if not re.fullmatch(r'[A-Za-z0-9_\-=]{1,512}', mapbox_id):
    raise DestinationError("That place could not be read")
  data = _mapbox(session, f"retrieve/{mapbox_id}", {"access_token": token, "session_token": search_session})
  try:
    feature = data["features"][0]
    longitude, latitude = feature["geometry"]["coordinates"][:2]
    properties = feature.get("properties") or {}
    return place(properties.get("name") or "Destination", float(latitude), float(longitude),
                 properties.get("full_address") or properties.get("place_formatted"))
  except (KeyError, IndexError, TypeError, ValueError):
    raise DestinationError("The map service sent a place without coordinates") from None


def read_google_key(path: str = GOOGLE_KEY_PATH) -> str | None:
  key = os.environ.get("GOOGLE_PLACES_KEY")
  if key:
    return key
  try:
    with open(path) as f:
      return f.read().strip() or None
  except OSError:
    return None


def _google(session, method: str, path: str, key: str, **kwargs) -> dict:
  try:
    response = session.request(method, f"{GOOGLE_HOST}/{path}", headers={"X-Goog-Api-Key": key, **kwargs.pop("headers", {})},
                               timeout=TIMEOUT_S, **kwargs)
  except requests.RequestException:
    raise DestinationError("The place search could not be reached") from None
  if response.status_code in (400, 401, 403):
    raise DestinationError("Google refused the Places key, or the Places API (New) is not enabled for it")
  if response.status_code == 429:
    raise DestinationError("The Google Places allowance is used up for now")
  if response.status_code != 200:
    raise DestinationError(f"The place search answered {response.status_code}")
  try:
    return response.json()
  except ValueError:
    raise DestinationError("The place search sent an unreadable answer") from None


def google_suggest(session, key: str, query: str, search_session: str, near: tuple[float, float] | None, language: str = "es") -> list[dict]:
  """Places matching what was typed, from Google Places autocomplete: [{id, name, address}]."""
  query = ' '.join(query.split())[:200]
  if len(query) < 2:
    return []
  body: dict = {"input": query, "sessionToken": search_session, "languageCode": language}
  if near is not None:
    body["locationBias"] = {"circle": {"center": {"latitude": near[0], "longitude": near[1]}, "radius": GOOGLE_BIAS_RADIUS_M}}
  data = _google(session, "POST", "places:autocomplete", key, json=body)
  out = []
  for suggestion in data.get("suggestions") or []:
    prediction = suggestion.get("placePrediction") or {}
    if not isinstance(prediction.get("placeId"), str):
      continue
    structured = prediction.get("structuredFormat") or {}
    out.append({"id": prediction["placeId"],
                "name": (structured.get("mainText") or {}).get("text") or (prediction.get("text") or {}).get("text") or "",
                "address": (structured.get("secondaryText") or {}).get("text") or ""})
  return out[:SUGGESTIONS]


def google_retrieve(session, key: str, place_id: str, search_session: str, name: str) -> dict:
  """A suggestion's coordinates. Ends the search session Google bills by."""
  if not re.fullmatch(r'[A-Za-z0-9_\-]{1,512}', place_id):
    raise DestinationError("That place could not be read")
  data = _google(session, "GET", f"places/{place_id}", key, params={"sessionToken": search_session},
                 headers={"X-Goog-FieldMask": GOOGLE_FIELDS})
  try:
    return place(name or "Destination", float(data["location"]["latitude"]), float(data["location"]["longitude"]), data.get("formattedAddress"))
  except (KeyError, TypeError, ValueError):
    raise DestinationError("The place search sent a place without coordinates") from None


def route_summary(session, token: str, origin: tuple[float, float], destination: dict, language: str = "es") -> dict:
  """How far and how long the trip is now, and its first turns, for the page. One Directions request."""
  coordinates = f"{origin[1]},{origin[0]};{destination['longitude']},{destination['latitude']}"
  try:
    response = session.get(f"{DIRECTIONS_HOST}/{coordinates}", timeout=TIMEOUT_S,
                           params={"access_token": token, "overview": "false", "steps": "true", "language": language})
  except requests.RequestException:
    raise DestinationError("The route could not be asked for") from None
  if response.status_code != 200:
    raise DestinationError(f"The route service answered {response.status_code}")
  try:
    leg = response.json()["routes"][0]["legs"][0]
    turns = [{"type": s["maneuver"].get("type") or "", "modifier": s["maneuver"].get("modifier") or "", "street": s.get("name") or "",
              "after_m": round(sum(before["distance"] for before in leg["steps"][:i]))}
             for i, s in enumerate(leg["steps"]) if s["maneuver"].get("type") not in ("depart", "arrive")]
    return {"distance_m": round(leg["distance"]), "duration_s": round(leg["duration"]), "turns": turns[:SUMMARY_STEPS]}
  except (KeyError, IndexError, TypeError, ValueError):
    raise DestinationError("No route to that place") from None


class Favorites:
  """Home, work and the last places chosen, on disk."""

  def __init__(self, path: str = FAVORITES_PATH):
    self.path = path

  def read(self) -> dict:
    value = read_json(self.path) or {}
    return {**{slot: value.get(slot) if isinstance(value.get(slot), dict) else None for slot in SLOTS},
            "recent": [p for p in value.get("recent") or [] if isinstance(p, dict)][:RECENTS]}

  def _write(self, value: dict) -> None:
    os.makedirs(os.path.dirname(self.path), exist_ok=True)
    write_json(value, self.path)

  def save(self, slot: str, destination: dict) -> None:
    if slot not in SLOTS:
      raise DestinationError("There is no such favorite")
    self._write({**self.read(), slot: destination})

  def visited(self, destination: dict) -> None:
    """Newest first, each place once."""
    value = self.read()
    others = [p for p in value["recent"] if abs(p["latitude"] - destination["latitude"]) > SAME_PLACE_DEG or
              abs(p["longitude"] - destination["longitude"]) > SAME_PLACE_DEG]
    self._write({**value, "recent": [destination] + others[:RECENTS - 1]})


def last_position(params) -> tuple[float, float] | None:
  """(latitude, longitude) the car was last seen at, to search near it."""
  try:
    value = params.get("LastGPSPositionLLK")
    value = json.loads(value) if isinstance(value, (str, bytes)) else value
    return float(value["latitude"]), float(value["longitude"])
  except (TypeError, ValueError, KeyError):
    return None


class Keeper:
  """The wanted destination, on disk, and its two params.

  `step` runs every couple of seconds. A destination set while the car is off goes into NavDestination when the car
  starts, and again after each restart. When NavDestination disappears during a drive, navd has reached the place
  or someone cancelled it: the destination is no longer wanted."""

  def __init__(self, params, path: str = DESTINATION_PATH):
    self.params, self.path = params, path
    self.onroad = False
    self.applied = False
    self.missing = 0

  def wanted(self) -> dict | None:
    value = read_json(self.path)
    try:
      return value if value is not None and abs(float(value["latitude"])) <= 90 and abs(float(value["longitude"])) <= 180 else None
    except (KeyError, TypeError, ValueError):
      return None

  def set(self, destination: dict) -> None:
    latitude, longitude = float(destination["latitude"]), float(destination["longitude"])
    if not (abs(latitude) <= 90 and abs(longitude) <= 180):
      raise DestinationError("Those coordinates are not on the map")
    os.makedirs(os.path.dirname(self.path), exist_ok=True)
    write_json({"name": str(destination.get("name") or "Destination")[:256], "address": destination.get("address"),
                "latitude": latitude, "longitude": longitude}, self.path)
    self.params.put_bool("NavigationEnabled", True)
    self._apply()

  def clear(self) -> None:
    try:
      os.remove(self.path)
    except OSError:
      pass
    self.applied = False
    self.params.remove("NavDestination")
    self.params.put_bool("NavigationEnabled", False)

  def _apply(self) -> None:
    wanted = self.wanted()
    if wanted is not None:
      self.params.put("NavDestination", {"latitude": wanted["latitude"], "longitude": wanted["longitude"], "place_name": wanted["name"]})
      self.applied = True

  def step(self, onroad: bool) -> None:
    started, self.onroad = onroad and not self.onroad, onroad
    if self.wanted() is None:
      self.applied = False
      return
    present = self.params.get("NavDestination") is not None
    self.missing = 0 if present or not onroad else self.missing + 1
    if started or not self.applied:
      self._apply()
    elif self.missing >= GONE_STEPS:
      self.clear()
    elif not onroad:
      # comma clears NavDestination when the car turns off: put it back at the next start
      self.applied = present
