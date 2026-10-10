import json

from openpilot.sunnypilot.models import fetcher


def bundle(ref, name="NNMV2FT", index=1000):
  return {"short_name": name, "display_name": f"{name} model", "is_20hz": True, "is_big": False, "ref": ref, "environment": "local",
          "runner": "tinygrad", "index": index, "minimum_selector_version": "19", "generation": "12",
          "overrides": {"folder": "Local Models", "lat": ".1", "long": ".3"},
          "models": [{"type": "chunked", "artifact": {"file_name": f"{name.lower()}.pkl", "download_uri": {"url": "", "sha256": ""},
                                                      "chunks": [{"file_name": f"{name.lower()}.pkl.chunk01of01", "sha256": "ab" * 32}]}}]}


def use_catalog(tmp_path, monkeypatch, content):
  from openpilot.common.hardware import hw
  monkeypatch.setattr(hw.Paths, "model_root", staticmethod(lambda: str(tmp_path)))
  path = tmp_path / "catalog.json"
  if content is not None:
    path.write_text(content if isinstance(content, str) else json.dumps(content))
  monkeypatch.setattr(fetcher, "LOCAL_CATALOG_PATH", str(path))


def test_local_bundles_are_listed_for_the_small_model_only(tmp_path, monkeypatch):
  use_catalog(tmp_path, monkeypatch, {"bundles": [bundle("local-1")]})
  local = fetcher.local_bundles("qcom")
  assert [(b.ref, b.displayName) for b in local] == [("local-1", "NNMV2FT model")]
  assert {o.key: o.value for o in local[0].overrides}["lat"] == ".1"
  assert fetcher.local_bundles("chestnut") == []
  assert (tmp_path / "nnmv2ft.pkl.chunkmanifest").read_text() == "1", "the manifest modeld reads is written as for remote bundles"


def test_no_catalog_or_a_broken_one_lists_nothing(tmp_path, monkeypatch):
  use_catalog(tmp_path, monkeypatch, None)
  assert fetcher.local_bundles("qcom") == []
  use_catalog(tmp_path, monkeypatch, "{not json")
  assert fetcher.local_bundles("qcom") == []


def test_local_bundles_follow_the_remote_ones_without_repeating_a_ref(tmp_path, monkeypatch):
  use_catalog(tmp_path, monkeypatch, {"bundles": [bundle("remote-1", name="DUP"), bundle("local-1")]})
  remote = fetcher.ModelParser.parse_models({"bundles": [bundle("remote-1", name="REMOTE", index=1)]})
  assert [b.ref for b in fetcher.with_local("qcom", remote)] == ["remote-1", "local-1"]
  assert [b.ref for b in fetcher.with_local("chestnut", remote)] == ["remote-1"]
