"""The address of the destination page as a QR code, to open it from a phone on the comma's network."""
import raylib_nb as rl

from openpilot.common.qrcode import make_texture
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.mapd.destination_web import page_address
from openpilot.system.ui.lib.application import FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets.nav_widget import NavWidget


class DestinationQrDialog(NavWidget):
  def __init__(self):
    super().__init__()
    self._address: str | None = None
    self._texture: rl.Texture | None = None
    self._label = UnifiedLabel("", font_size=40, font_weight=FontWeight.BOLD, text_color=rl.Color(255, 255, 255, int(255 * 0.9)), line_height=0.9)

  def show_event(self):
    super().show_event()
    address = page_address()
    if address != self._address:
      self._address = address
      self._unload()
      if address is not None:
        try:
          self._texture = make_texture(address, inverted=True)
        except Exception as e:
          cloudlog.warning(f"destination QR failed: {e}")
    self._label.set_text(tr("scan to pick where to go") if self._texture else tr("connect the comma to a network first"))

  def _render(self, rect: rl.Rectangle):
    label_x = self._rect.x + 24
    if self._texture:
      scale = self._rect.height / self._texture.height
      rl.draw_texture_ex(self._texture, rl.Vector2(round(self._rect.x + 8), round(self._rect.y)), 0.0, scale, rl.WHITE)
      label_x = self._rect.x + 8 + self._rect.height + 24
    self._label.set_max_width(int(self._rect.x + self._rect.width - label_x))
    self._label.set_position(label_x, self._rect.y + 16)
    self._label.render()

  def _unload(self):
    if self._texture and self._texture.id != 0:
      rl.unload_texture(self._texture)
    self._texture = None

  def __del__(self):
    self._unload()
