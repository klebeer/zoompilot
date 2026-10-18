"""
The followed route of the comma-nav bundle in comma's navigation messages, navRoute and navInstruction.

comma's map renderer (selfdrive/navigation/mapsd.py) draws the frame its nav models take from navRoute and the
GPS, and every drive's log keeps both messages. Publishing ours gives that renderer the route the driver chose,
offline, instead of one from comma's routing service.

Only one process may publish a service: route_guide publishes these only while comma's navd is off
(NavigationEnabled false), and stops when it turns on.
"""

ROUTE_RESEND_S = 10.0         # a reader that starts late (mapsd) gets the route within this, as from navd
INSTRUCTION_PERIOD_S = 1.0    # navInstruction's rate in cereal/services.py
SHOW_FULL_M = 150.0           # the banner is shown in full from this close to the maneuver
PASSED_M = 10.0               # a maneuver this far behind the car is no longer listed


def followed_route(nav: dict | None, follower) -> tuple[str, list] | None:
  """(route_id, shape as [lat, lon] pairs) of the route the follower follows, the chosen one while the car is on
  it. None when nothing is followed."""
  if nav is None or follower is None or nav["state"] != "following":
    return None
  route = next((t.route for t in follower.trackers if t.route.route_id == nav["route_id"]), None)
  return (nav["route_id"], route.shape) if route is not None else None


def instruction(nav: dict | None, follower) -> dict | None:
  """navInstruction's fields for the followed route, or None when nothing is followed."""
  route = followed_route(nav, follower)
  if route is None:
    return None
  maneuvers = next(t.route.maneuvers for t in follower.trackers if t.route.route_id == route[0])
  along = nav["progress_m"]
  ahead = [m for m in maneuvers if m["distance_m"] - along >= -PASSED_M and m["type"] != "depart"]
  fields = {
    "distanceRemaining": float(nav["remaining_m"]),
    "allManeuvers": [{"distance": float(m["distance_m"] - along), "type": m["type"], "modifier": m["modifier"] or ""}
                     for m in ahead],
  }
  nxt = next((m for m in ahead if m["distance_m"] - along >= 0.0), None)
  if nxt is not None:
    distance = float(nxt["distance_m"] - along)
    fields.update({
      "maneuverPrimaryText": nxt["street"] or "",
      "maneuverType": nxt["type"],
      "maneuverModifier": nxt["modifier"] or "",
      "maneuverDistance": distance,
      "showFull": distance <= SHOW_FULL_M,
    })
  return fields


class NavPublisher:
  """Sends navRoute when the followed route changes and every ROUTE_RESEND_S, and navInstruction every
  INSTRUCTION_PERIOD_S, invalid while nothing is followed. `new_message(service, valid)` and `pm` are messaging's."""

  def __init__(self, pm, new_message):
    self.pm, self.new_message = pm, new_message
    self.route_id: str | None = None
    self.route_sent_at = -1e9
    self.instruction_sent_at = -1e9

  def step(self, now: float, nav: dict | None, follower) -> None:
    route = followed_route(nav, follower)
    route_id = route[0] if route is not None else None
    if route_id != self.route_id or (route_id is not None and now - self.route_sent_at >= ROUTE_RESEND_S):
      msg = self.new_message("navRoute", valid=True)
      msg.navRoute.coordinates = [{"latitude": lat, "longitude": lon} for lat, lon in route[1]] if route else []
      self.pm.send("navRoute", msg)
      self.route_id, self.route_sent_at = route_id, now
    if now - self.instruction_sent_at >= INSTRUCTION_PERIOD_S:
      fields = instruction(nav, follower)
      msg = self.new_message("navInstruction", valid=fields is not None)
      for key, value in (fields or {}).items():
        setattr(msg.navInstruction, key, value)
      self.pm.send("navInstruction", msg)
      self.instruction_sent_at = now
