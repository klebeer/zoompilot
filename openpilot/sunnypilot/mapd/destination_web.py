"""
A page served by the comma to pick where to go, opened from a phone on the same network.

Search a place by name, or paste a link shared from Google Maps or Waze. Choosing one hands it to `Keeper`, which
starts the Mapbox navigation. The Mapbox token stays on the comma: the page asks this server, and this server asks
Mapbox.

Every address carries the key in KEY_PATH, made on first start: http://<comma>:PORT/<key>/. Someone on the same
network without the key gets nothing. The page is plain HTTP, so the key is only as private as that network.
"""
import hmac
import json
import os
import secrets
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import requests

from openpilot.common.hardware import PC
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd import destination, mapbox_route

PORT = 8083
KEY_PATH = os.path.expanduser("~/.comma/nav/destination_key") if PC else "/data/nav/destination_key"
STEP_S = 2.0
MAX_BODY = 16384

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Destination</title>
<style>
  body { margin: 0; padding: 16px; background: #0d0d1c; color: #eee; font: 17px -apple-system, system-ui, sans-serif; }
  h1 { font-size: 20px; margin: 4px 0 14px; }
  input { width: 100%; box-sizing: border-box; padding: 13px; border-radius: 12px; border: 1px solid #7a62be;
          background: #16162b; color: #fff; font-size: 17px; }
  button { padding: 12px 16px; border-radius: 12px; border: 0; background: #7a62be; color: #fff; font-size: 16px; }
  button.quiet { background: #2a2a44; }
  ul { list-style: none; margin: 10px 0; padding: 0; }
  li { padding: 12px; border-radius: 12px; background: #16162b; margin-bottom: 8px; }
  li small, #now small { display: block; color: #9aa; margin-top: 3px; }
  #now { padding: 14px; border-radius: 12px; background: #17301f; margin-bottom: 16px; display: none; }
  #now button { margin-top: 10px; }
  #now ol { margin: 8px 0 0; padding-left: 22px; color: #cde; font-size: 15px; }
  #quick { margin-bottom: 12px; }
  #quick button { margin: 0 8px 8px 0; }
  #note { color: #f0b040; min-height: 22px; margin: 8px 0; }
</style></head><body>
<h1>Where to?</h1>
<div id="now"><b id="now-name"></b><small id="now-address"></small><small id="now-route"></small><ol id="now-turns"></ol>
<button class="quiet" id="cancel">Cancel</button> <button class="quiet" id="save-home">Save as home</button>
<button class="quiet" id="save-work">Save as work</button></div>
<div id="quick"></div>
<input id="q" placeholder="Search a place, or paste a Maps or Waze link" autocomplete="off" autocapitalize="off">
<div id="note"></div>
<ul id="results"></ul>
<script>
var q = document.getElementById('q'), results = document.getElementById('results'), note = document.getElementById('note');
var timer = null, asked = 0;
function call(method, path, body) {
  return fetch(path, {method: method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined})
    .then(function (r) { return r.json().then(function (data) { if (!r.ok) { throw new Error(data.error || 'Something went wrong'); } return data; }); });
}
function distance(m) { return m < 1000 ? Math.round(m / 10) * 10 + ' m' : (m / 1000).toFixed(1) + ' km'; }
function quick(label, body) {
  var b = document.createElement('button');
  b.className = 'quiet'; b.textContent = label;
  b.onclick = function () { call('POST', 'api/go', body).then(chosen).catch(fail); };
  document.getElementById('quick').appendChild(b);
}
function show(state) {
  var d = state.destination, r = state.route, f = state.favorites || {recent: []}, now = document.getElementById('now');
  var turns = document.getElementById('now-turns');
  now.style.display = d ? 'block' : 'none';
  turns.innerHTML = '';
  if (d) {
    document.getElementById('now-name').textContent = d.name;
    document.getElementById('now-address').textContent = d.address || '';
    document.getElementById('now-route').textContent = r ? distance(r.distance_m) + ', ' + Math.max(1, Math.round(r.duration_s / 60)) + ' min' : '';
    (r ? r.turns : []).forEach(function (t) {
      var li = document.createElement('li');
      li.textContent = distance(t.after_m) + ': ' + (t.modifier || t.type) + (t.street ? ' - ' + t.street : '');
      turns.appendChild(li);
    });
  }
  document.getElementById('quick').innerHTML = '';
  if (f.home) { quick('Home', {slot: 'home'}); }
  if (f.work) { quick('Work', {slot: 'work'}); }
  f.recent.forEach(function (p, i) { if (!d || p.name !== d.name) { quick(p.name, {recent: i}); } });
}
function save(slot) { return function () { call('POST', 'api/favorite', {slot: slot}).then(show).catch(fail); }; }
function fail(error) { note.textContent = error.message; }
function chosen(state) { note.textContent = ''; results.innerHTML = ''; q.value = ''; show(state); }
function list(places) {
  results.innerHTML = '';
  places.forEach(function (p) {
    var li = document.createElement('li'), small = document.createElement('small');
    li.textContent = p.name; small.textContent = p.address; li.appendChild(small);
    li.onclick = function () { call('POST', 'api/choose', {id: p.id}).then(chosen).catch(fail); };
    results.appendChild(li);
  });
}
q.oninput = function () {
  clearTimeout(timer);
  var text = q.value.trim();
  if (text.indexOf('http') === 0 || /^-?[0-9.]+, *-?[0-9.]+$/.test(text)) {
    note.textContent = 'Reading the link...';
    call('POST', 'api/share', {text: text}).then(chosen).catch(fail);
    return;
  }
  timer = setTimeout(function () {
    var mine = ++asked;
    call('GET', 'api/suggest?q=' + encodeURIComponent(text))
      .then(function (data) { if (mine === asked) { note.textContent = ''; list(data.places); } }).catch(fail);
  }, 350);
};
document.getElementById('save-home').onclick = save('home');
document.getElementById('save-work').onclick = save('work');
document.getElementById('cancel').onclick = function () { call('POST', 'api/clear').then(show).catch(fail); };
call('GET', 'api/state').then(show).catch(fail);
</script></body></html>
"""


def load_key(path: str = KEY_PATH) -> str:
  """The access key, made on first use and readable by this user only."""
  try:
    with open(path) as f:
      key = f.read().strip()
    if len(key) >= 16:
      return key
  except OSError:
    pass
  key = secrets.token_urlsafe(18)
  os.makedirs(os.path.dirname(path), exist_ok=True)
  fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
  with os.fdopen(fd, "w") as f:
    f.write(key + "\n")
  return key


class App:
  """What the page can ask for. One search session at a time: Mapbox bills a search from its first suggestion to
  the place retrieved."""

  def __init__(self, keeper: destination.Keeper, params, session=None, token=mapbox_route.read_token, google_key=destination.read_google_key):
    self.keeper, self.params = keeper, params
    self.session = session or requests.Session()
    self.read_token, self.read_google_key = token, google_key
    self.lock = threading.Lock()
    self.search_session = str(uuid.uuid4())
    # what the last answers offered, by id: the provider to ask for its coordinates, and its name
    self.offered: dict[str, tuple[str, str]] = {}
    self.favorites = destination.Favorites(os.path.join(os.path.dirname(keeper.path), "favorites.json"))
    self.summary: tuple[tuple[float, float], dict | None] | None = None

  def _token(self) -> str:
    token = self.read_token()
    if token is None:
      raise destination.DestinationError("No Mapbox token on this comma yet")
    return token

  def _summary(self, wanted: dict) -> dict | None:
    """The trip to the wanted place, asked once per place: a failed answer is not asked again either."""
    at = (wanted["latitude"], wanted["longitude"])
    if self.summary is None or self.summary[0] != at:
      near, token = destination.last_position(self.params), self.read_token()
      route = None
      if near is not None and token is not None:
        try:
          route = destination.route_summary(self.session, token, near, wanted)
        except destination.DestinationError:
          route = None
      self.summary = (at, route)
    return self.summary[1]

  def state(self) -> dict:
    wanted = self.keeper.wanted()
    return {"destination": wanted, "route": self._summary(wanted) if wanted is not None else None, "favorites": self.favorites.read()}

  def _search(self, query: str) -> list[dict]:
    near, google_key = destination.last_position(self.params), self.read_google_key()
    if google_key is not None:
      provider, places = "google", destination.google_suggest(self.session, google_key, query, self.search_session, near)
    else:
      provider, places = "mapbox", destination.suggest(self.session, self._token(), query, self.search_session, near)
    self.offered = {p["id"]: (provider, p["name"]) for p in places}
    return places

  def suggest(self, query: str) -> dict:
    return {"places": self._search(query)}

  def _go(self, place: dict) -> dict:
    with self.lock:
      self.keeper.set(place)
      self.favorites.visited(self.keeper.wanted())
    return self.state()

  def favorite(self, slot: str) -> dict:
    """Keeps the current destination as home or work."""
    wanted = self.keeper.wanted()
    if wanted is None:
      raise destination.DestinationError("Pick a destination first, then save it")
    with self.lock:
      self.favorites.save(slot, wanted)
    return self.state()

  def go(self, slot: str | None, recent: int | None) -> dict:
    favorites = self.favorites.read()
    if slot in destination.SLOTS and favorites[slot] is not None:
      return self._go(favorites[slot])
    if isinstance(recent, int) and 0 <= recent < len(favorites["recent"]):
      return self._go(favorites["recent"][recent])
    raise destination.DestinationError("That place is not saved")

  def choose(self, place_id: str) -> dict:
    provider, name = self.offered.get(place_id, (None, ""))
    if provider is None:
      raise destination.DestinationError("Search again and pick one of the places listed")
    if provider == "google":
      place = destination.google_retrieve(self.session, self.read_google_key() or "", place_id, self.search_session, name)
    else:
      place = destination.retrieve(self.session, self._token(), place_id, self.search_session)
    self.search_session = str(uuid.uuid4())
    return self._go(place)

  def share(self, text: str) -> dict:
    if not isinstance(text, str) or not text.strip() or len(text) > destination.MAX_TEXT:
      raise destination.DestinationError("That link could not be read")
    text = destination.resolve_short_link(text, self.session)
    place = destination.shared_coordinates(text)
    if place is None:
      named = destination.map_link_query(text)
      if named is None:
        raise destination.DestinationError("That link names no place")
      found = self._search(named)
      if not found:
        raise destination.DestinationError(f"Nothing found for {named}")
      return self.choose(found[0]["id"])
    return self._go(place)

  def clear(self) -> dict:
    with self.lock:
      self.keeper.clear()
    return self.state()


def local_address() -> str | None:
  """This device's address on its network. Nothing is sent: a UDP socket only picks the interface it would use."""
  try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
      s.connect(("10.255.255.255", 1))
      return s.getsockname()[0]
  except OSError:
    return None


def page_address(key_path: str = KEY_PATH) -> str | None:
  """Where the page is, for the QR on the comma's screen. None without a network or before the first start."""
  address = local_address()
  try:
    with open(key_path) as f:
      key = f.read().strip()
  except OSError:
    return None
  return f"http://{address}:{PORT}/{key}/" if address and key else None


def make_handler(app: App, key: str):
  class Handler(BaseHTTPRequestHandler):
    server_version = "comma-destination"

    def log_message(self, *args):  # the default writes every address, key included, to stderr
      pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
      self.send_response(status)
      self.send_header("Content-Type", content_type)
      self.send_header("Content-Length", str(len(body)))
      self.send_header("Cache-Control", "no-store")
      self.send_header("Referrer-Policy", "no-referrer")
      self.end_headers()
      self.wfile.write(body)

    def _json(self, status: int, content: dict) -> None:
      self._send(status, json.dumps(content).encode(), "application/json")

    def _route(self) -> tuple[str, dict] | None:
      """(path after the key, query), or None when the key is wrong or missing."""
      parts = urlsplit(self.path)
      segments = parts.path.split("/", 2)
      if len(segments) < 3 or not hmac.compare_digest(segments[1].encode(), key.encode()):
        return None
      return segments[2], parse_qs(parts.query)

    def _answer(self, call) -> None:
      try:
        self._json(200, call())
      except destination.DestinationError as e:
        self._json(400, {"error": str(e)})
      except Exception:
        cloudlog.exception("destination_web: request failed")
        self._json(500, {"error": "The comma could not do that"})

    def do_GET(self):
      route = self._route()
      if route is None:
        self._send(404, b"", "text/plain")
      elif route[0] == "":
        self._send(200, PAGE.encode(), "text/html; charset=utf-8")
      elif route[0] == "api/state":
        self._answer(app.state)
      elif route[0] == "api/suggest":
        self._answer(lambda: app.suggest((route[1].get("q") or [""])[0]))
      else:
        self._send(404, b"", "text/plain")

    def do_POST(self):
      route = self._route()
      if route is None:
        self._send(404, b"", "text/plain")
        return
      try:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if 0 < length <= MAX_BODY else {}
        if not isinstance(body, dict):
          raise ValueError
      except ValueError:
        self._json(400, {"error": "That request could not be read"})
        return
      if route[0] == "api/choose":
        self._answer(lambda: app.choose(str(body.get("id") or "")))
      elif route[0] == "api/share":
        self._answer(lambda: app.share(body.get("text")))
      elif route[0] == "api/clear":
        self._answer(app.clear)
      elif route[0] == "api/favorite":
        self._answer(lambda: app.favorite(str(body.get("slot") or "")))
      elif route[0] == "api/go":
        self._answer(lambda: app.go(body.get("slot"), body.get("recent")))
      else:
        self._send(404, b"", "text/plain")

  return Handler


def main() -> None:
  params = Params()
  keeper = destination.Keeper(params)
  app = App(keeper, params)
  server = ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(app, load_key()))
  server.daemon_threads = True
  threading.Thread(target=server.serve_forever, daemon=True).start()
  while True:
    try:
      with app.lock:
        keeper.step(not params.get_bool("IsOffroad"))
    except Exception:
      cloudlog.exception("destination_web: step failed")
    time.sleep(STEP_S)


if __name__ == "__main__":
  main()
