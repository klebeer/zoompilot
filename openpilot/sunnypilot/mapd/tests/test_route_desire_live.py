from openpilot.sunnypilot.mapd import route_desire_live as live
from openpilot.sunnypilot.mapd.route_desire_live import LATCH_MAX_KPH, LATCH_S, REFRESH_NS, STALE_NS, Latch, Publisher, Reader

S = 1_000_000_000


class TestLatch:
  def test_holds_while_the_blinker_stays_on_that_side(self):
    latch = Latch()
    assert latch.update(0.0, "left", "left", 20) == "left"
    # past the corner the route's next turn is another one: the turn still holds
    assert latch.update(2.0, None, "left", 15) == "left"
    assert latch.update(3.0, None, None, 15) is None

  def test_nothing_without_the_route(self):
    assert Latch().update(0.0, None, "right", 20) is None

  def test_ends_after_latch_s_or_above_its_speed(self):
    latch = Latch()
    latch.update(0.0, "right", "right", 20)
    assert latch.update(LATCH_S + 0.1, None, "right", 20) is None
    latch.update(20.0, "right", "right", 20)
    assert latch.update(21.0, None, "right", LATCH_MAX_KPH + 1) is None

  def test_blinker_to_the_other_side_ends_it(self):
    latch = Latch()
    latch.update(0.0, "left", "left", 20)
    assert latch.update(1.0, None, "right", 20) is None


class TestPublishAndRead:
  def test_fresh_side_is_read_and_goes_stale(self, tmp_path, monkeypatch):
    monkeypatch.setattr(live, "SIDE_PATH", str(tmp_path / "side"))
    pub, reader = Publisher(), Reader()
    assert pub.step("left", 10 * S)
    assert reader.get(10 * S + 100_000_000) == "left"
    assert Reader().get(10 * S + STALE_NS + 1) is None

  def test_rewrites_only_on_change_or_refresh(self, tmp_path, monkeypatch):
    monkeypatch.setattr(live, "SIDE_PATH", str(tmp_path / "side"))
    pub = Publisher()
    assert not pub.step(None, 0)
    assert pub.step("right", S)
    assert not pub.step("right", S + REFRESH_NS - 1)
    assert pub.step("right", S + REFRESH_NS)
    assert pub.step(None, 2 * S)
    assert Reader().get(2 * S) is None

  def test_missing_or_broken_file_reads_as_none(self, tmp_path, monkeypatch):
    monkeypatch.setattr(live, "SIDE_PATH", str(tmp_path / "side"))
    assert Reader().get(S) is None
    (tmp_path / "side").write_text("{not json")
    assert Reader().get(S) is None

  def test_off_unless_enabled(self, tmp_path, monkeypatch):
    monkeypatch.setattr(live, "ENABLE_PATH", str(tmp_path / "on"))
    assert not live.enabled()
    (tmp_path / "on").touch()
    assert live.enabled()
