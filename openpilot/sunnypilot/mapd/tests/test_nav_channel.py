import hashlib
import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from openpilot.sunnypilot.mapd import nav_channel, route_choice

BASE = "https://nav.example"


@pytest.fixture
def files(tmp_path, monkeypatch):
  monkeypatch.setattr(nav_channel, "BUNDLE_PATH", str(tmp_path / "nav" / "route_bundle.json"))
  monkeypatch.setattr(nav_channel, "CHOICE_PATH", str(tmp_path / "nav" / "chosen.json"))
  monkeypatch.setattr(nav_channel, "SERVER_PATH", str(tmp_path / "server"))
  return tmp_path


class Fetched:
  def __init__(self, content: bytes):
    self.content = content

  def raise_for_status(self):
    pass


def announce(content: bytes, **override):
  bundle_id = json.loads(content)["bundle_id"]
  return {"type": "bundle", "id": "m-0001", "bundle_id": bundle_id, "url": f"{BASE}/v1/device/bundle", "bytes": len(content),
          "sha256": hashlib.sha256(content).hexdigest(), **override}


def test_the_token_is_es256_for_this_comma_and_lives_an_hour_at_most():
  key = ec.generate_private_key(ec.SECP256R1())
  private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
  claims = jwt.decode(nav_channel.token("f5a7000000000000", private, now=1_800_000_000), key.public_key(), algorithms=["ES256"],
                      options={"verify_exp": False, "verify_nbf": False, "verify_iat": False})
  assert claims["identity"] == "f5a7000000000000"
  assert claims["exp"] - claims["iat"] == nav_channel.TOKEN_S <= 3600


def test_an_announced_bundle_is_fetched_checked_and_written(files):
  content = json.dumps({"schema": 1, "bundle_id": "25ac5087c04f1f50", "routes": []}).encode()
  asked = []
  nav_channel.apply_bundle(announce(content), BASE, {"Authorization": "Bearer t"}, get=lambda url, **kw: asked.append((url, kw)) or Fetched(content))
  assert asked[0][0] == f"{BASE}/v1/device/bundle" and asked[0][1]["headers"] == {"Authorization": "Bearer t"}
  assert route_choice.read_json(nav_channel.BUNDLE_PATH)["bundle_id"] == "25ac5087c04f1f50"
  assert nav_channel.held_state(True, "m-0001") | {"at": None} == {"type": "state", "at": None, "offroad": True, "bundle_id": "25ac5087c04f1f50",
                                                                   "choice": None, "applied": "m-0001"}


@pytest.mark.parametrize("override", [{"url": "https://elsewhere.example/v1/device/bundle"}, {"sha256": "0" * 64}, {"bytes": 1},
                                      {"bundle_id": "ffffffffffffffff"}])
def test_a_bundle_that_is_not_the_announced_one_is_not_written(files, override):
  content = json.dumps({"schema": 1, "bundle_id": "25ac5087c04f1f50", "routes": []}).encode()
  with pytest.raises(ValueError):
    nav_channel.apply_bundle(announce(content, **override), BASE, {}, get=lambda url, **kw: Fetched(content))
  assert route_choice.read_json(nav_channel.BUNDLE_PATH) is None


def test_a_choice_is_written_and_cleared(files):
  choice = {"schema": 1, "bundle_id": "25ac5087c04f1f50", "route_id": "t1-1", "chosen_by": "app", "chosen_at": "2026-10-09T18:00:00Z",
            "expires_at": "2026-10-10T18:00:00Z"}
  nav_channel.apply_choice({"type": "choice", "id": "m-0002", "choice": choice})
  assert route_choice.read_json(nav_channel.CHOICE_PATH) == choice
  nav_channel.apply_choice({"type": "choice", "id": "m-0003", "choice": None})
  assert route_choice.read_json(nav_channel.CHOICE_PATH) is None


def test_it_stays_off_without_a_https_server(files):
  assert nav_channel.server() is None
  (files / "server").write_text("http://nav.example\n")
  assert nav_channel.server() is None
  (files / "server").write_text("https://nav.example/\n")
  assert nav_channel.server() == "https://nav.example"
