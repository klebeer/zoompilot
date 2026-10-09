from openpilot.cereal import messaging
from openpilot.selfdrive.ui.mici.widgets.button import BigButton
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.mapd import route_choice
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets.scroller import NavScroller


def parked() -> bool:
  return not ui_state.started or ui_state.sm["carState"].standstill


def describe(route: dict, n: int) -> tuple[str, str]:
  """A route as a button shows it: the name the driver gave it, else its number in the list; then its streets."""
  return route.get("name") or f"{tr('route')} {n}", "" if route.get("trip") == route["label"] else route_choice.streets(route).lower()


class RoutesLayoutMici(NavScroller):
  """Which route this trip takes: by where to go, or from the saved trips the car was sent. Usable only with the car stopped."""

  def __init__(self):
    super().__init__()
    self._bundle: dict | None = None
    self._sm = messaging.SubMaster(["liveLocationKalman"])
    self._here: tuple[float, float] | None = None
    self._build()

  def show_event(self):
    self._build()
    super().show_event()

  def _update_state(self):
    super()._update_state()
    if not ui_state.started:
      self._here = None
      return
    self._sm.update(0)
    loc = self._sm["liveLocationKalman"]
    if self._sm.updated["liveLocationKalman"] and loc.positionGeodetic.valid:
      self._here = (loc.positionGeodetic.value[0], loc.positionGeodetic.value[1])

  def _place_label(self, name: str | None) -> str:
    return next((p["label"] for p in (self._bundle or {}).get("places", []) if p["name"] == name), name or "")

  def _build(self):
    self._bundle = route_choice.load_bundle()
    trips, chosen, places = route_choice.trips(self._bundle), route_choice.chosen_route(self._bundle), route_choice.places(self._bundle)
    items = []
    if chosen is not None:
      clear_btn = BigButton(tr("clear route"), value=(chosen.get("trip") or f"→ {self._place_label(chosen.get('to_place'))}").lower())
      clear_btn.set_click_callback(self._clear)
      items.append(clear_btn)
    if places:
      go_btn = BigButton(tr("go to"), value=", ".join(p["label"] for p in places).lower())
      go_btn.set_click_callback(lambda: self._push([self._place_button(p) for p in places]))
      items.append(go_btn)
    for name, routes in trips:
      picked = next((describe(r, n)[0] for n, r in enumerate(routes, 1) if chosen is not None and r["route_id"] == chosen["route_id"]), None)
      btn = BigButton(name.lower(), value=picked or f"{len(routes)} {tr('routes')}")
      btn.set_click_callback(lambda routes=routes: self._push(self._route_buttons(routes)))
      items.append(btn)
    if not trips and not places:
      items.append(BigButton(tr("no saved trips"), value=tr("send them from comma-nav")))
    for item in items:
      item.set_enabled(parked)
    self._scroller._items.clear()
    self._scroller.add_widgets(items)

  def _place_button(self, place: dict) -> BigButton:
    btn = BigButton(place["label"].lower())
    btn.set_click_callback(lambda name=place["name"]: self._go_to(name))
    return btn

  def _go_to(self, place: str):
    routes = route_choice.routes_to(self._bundle, place, self._here)
    if len(routes) == 1:
      return self._choose(routes[0]["route_id"])
    origins = {r.get("from_place") for r in routes}
    self._push(self._route_buttons(routes, None if len(origins) == 1 else self._place_label))

  def _route_buttons(self, routes: list[dict], origin_of=None) -> list[BigButton]:
    """One button per route; `origin_of` names where each starts, for routes that start at different places."""
    btns = []
    for n, route in enumerate(routes, 1):
      text, streets = describe(route, n)
      btn = BigButton(text, value=f"{tr('from')} {origin_of(route.get('from_place')).lower()}" if origin_of else streets)
      btn.set_click_callback(lambda route_id=route["route_id"]: self._choose(route_id))
      btns.append(btn)
    return btns

  def _push(self, btns: list[BigButton]):
    for btn in btns:
      btn.set_enabled(parked)
    scroller = NavScroller()
    scroller._scroller.add_widgets(btns)
    gui_app.push_widget(scroller)

  def _choose(self, route_id: str):
    if self._bundle is not None:
      route_choice.choose(self._bundle, route_id)
    self._back_to_main()

  def _clear(self):
    route_choice.clear()
    self._back_to_main()

  def _back_to_main(self):
    self._build()
    gui_app.pop_widgets_to(self)
