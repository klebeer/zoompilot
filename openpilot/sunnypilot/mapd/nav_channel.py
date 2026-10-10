"""
The comma's side of comma-nav's device channel (comma-nav ADR 0002, contracts/device-channel.asyncapi.yaml).

Keeps one WebSocket open to the server named in SERVER_PATH, authenticated with a JWT signed by this comma's own
key (ES256, an hour at most), and applies what the server announces: a bundle is fetched over HTTPS from that same
server, checked against its size and hash, and written to BUNDLE_PATH; a choice is written to CHOICE_PATH, or
removed. After applying anything, and on every connect, it reports what it holds. It reconnects with backoff.

Runs onroad and offroad. A bundle or a choice that arrives during a drive is used at once: route_guide takes either
file again when it changes. Without SERVER_PATH it does nothing and checks again every minute.
"""
import hashlib
import json
import os
import random
import time
from datetime import UTC, datetime

import jwt
import requests
import websocket

from openpilot.common.hardware import PC
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd.route_choice import BUNDLE_PATH, CHOICE_PATH, read_json, write_json

SERVER_PATH = os.path.expanduser("~/.comma/nav_server") if PC else "/data/zoompilot_nav_server"
KEY_PATH = os.path.expanduser("~/.comma/id_ecdsa") if PC else "/persist/comma/id_ecdsa"
TOKEN_S = 3600
RECV_TIMEOUT_S = 90       # the server pings every 20 s; this long without a frame means the link is gone
FETCH_TIMEOUT_S = 30
MAX_BACKOFF_S = 60


def server() -> str | None:
  try:
    with open(SERVER_PATH) as f:
      url = f.read().strip().rstrip("/")
  except OSError:
    return None
  return url if url.startswith("https://") else None


def token(dongle_id: str, key: str, now: float | None = None) -> str:
  now = int(time.time() if now is None else now)
  return jwt.encode({"identity": dongle_id, "nbf": now, "iat": now, "exp": now + TOKEN_S}, key, algorithm="ES256")


def held_state(offroad: bool, applied: str | None = None) -> dict:
  """What the comma holds now, as the channel reports it."""
  bundle = read_json(BUNDLE_PATH)
  state = {"type": "state", "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "offroad": offroad,
           "bundle_id": bundle.get("bundle_id") if bundle else None, "choice": read_json(CHOICE_PATH)}
  if applied:
    state["applied"] = applied
  return state


def apply_bundle(message: dict, base: str, auth: dict, get=requests.get) -> None:
  """Fetch, check and write the announced bundle. The JWT goes only to the server the channel is open to."""
  if not message["url"].startswith(base + "/"):
    raise ValueError(f"bundle url outside {base}")
  r = get(message["url"], headers=auth, timeout=FETCH_TIMEOUT_S)
  r.raise_for_status()
  content = r.content
  if len(content) != message["bytes"] or hashlib.sha256(content).hexdigest() != message["sha256"]:
    raise ValueError("bundle does not match its announcement")
  bundle = json.loads(content)
  if bundle.get("bundle_id") != message["bundle_id"]:
    raise ValueError("bundle id does not match its announcement")
  os.makedirs(os.path.dirname(BUNDLE_PATH), exist_ok=True)
  write_json(bundle, BUNDLE_PATH)


def apply_choice(message: dict) -> None:
  choice = message.get("choice")
  if choice is None:
    try:
      os.remove(CHOICE_PATH)
    except FileNotFoundError:
      pass
    return
  os.makedirs(os.path.dirname(CHOICE_PATH), exist_ok=True)
  write_json(choice, CHOICE_PATH)


def session(base: str, dongle_id: str, key: str, params: Params) -> None:
  """One connection, until it drops."""
  bearer = {"Authorization": f"Bearer {token(dongle_id, key)}"}
  ws = websocket.create_connection("wss://" + base.removeprefix("https://") + "/v1/device", timeout=RECV_TIMEOUT_S,
                                   header=[f"{k}: {v}" for k, v in bearer.items()])
  try:
    ws.send(json.dumps(held_state(params.get_bool("IsOffroad"))))
    while True:
      message = json.loads(ws.recv())
      applied = message.get("id")
      try:
        if message.get("type") == "bundle":
          apply_bundle(message, base, {"Authorization": f"Bearer {token(dongle_id, key)}"})
        elif message.get("type") == "choice":
          apply_choice(message)
        else:
          continue
      except (requests.RequestException, ValueError, KeyError, OSError):
        cloudlog.exception("nav_channel: could not apply %s", message.get("type"))
      ws.send(json.dumps(held_state(params.get_bool("IsOffroad"), applied)))
  finally:
    ws.close()


def main() -> None:
  params = Params()
  failures = 0
  while True:
    base = server()
    if base is None:
      time.sleep(60)
      continue
    try:
      dongle_id = params.get("DongleId")
      dongle_id = dongle_id.decode() if isinstance(dongle_id, bytes) else dongle_id
      with open(KEY_PATH) as f:
        key = f.read()
      session(base, dongle_id, key, params)
      failures = 0
    except Exception:
      cloudlog.exception("nav_channel: connection lost")
      failures += 1
    time.sleep(min(MAX_BACKOFF_S, 2 ** min(failures, 6)) * random.uniform(0.5, 1.0))


if __name__ == "__main__":
  main()
