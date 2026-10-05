"""
Albums > (album) > Share on the home network...: the family gallery for one
album (gallery.py) - its link and QR code for a phone on the same Wi-Fi, an
optional PIN, whether the originals can be downloaded, and Stop sharing.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from lunelis import gallery


class ShareDialog(QDialog):
    def __init__(self, conn, album_id: int, name: str, server, parent=None) -> None:
        """server: a callable that starts the gallery if needed and returns (port, running)."""
        super().__init__(parent)
        self.conn, self.album_id, self.server = conn, album_id, server
        self.setWindowTitle(f"Share \"{name}\"")
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        intro = QLabel("Anyone on your home network with the link (or the QR code) can see this album's photos - "
                       "resized, with your edits, without their location. Nothing is reachable from outside "
                       "the home network.", wordWrap=True, objectName="Help")
        v.addWidget(intro)
        form = QFormLayout()
        self.pin = QLineEdit(placeholderText="No PIN")
        self.pin.setEchoMode(QLineEdit.EchoMode.Password)
        self.pin.setMaxLength(12)
        form.addRow("PIN (optional)", self.pin)
        self.remove_pin = QCheckBox("Remove the PIN")
        form.addRow("", self.remove_pin)
        self.originals = QCheckBox("Allow downloading the original files")
        form.addRow("", self.originals)
        v.addLayout(form)
        self.link = QLineEdit(readOnly=True)
        row = QHBoxLayout()
        row.addWidget(self.link, 1)
        self.copy_b = QPushButton("Copy", clicked=lambda: QGuiApplication.clipboard().setText(self.link.text()))
        row.addWidget(self.copy_b)
        v.addLayout(row)
        self.qr = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.qr)
        self.note = QLabel(objectName="Help", wordWrap=True)
        v.addWidget(self.note)
        buttons = QDialogButtonBox()
        self.share_b = buttons.addButton("Share", QDialogButtonBox.ButtonRole.AcceptRole)
        self.stop_b = buttons.addButton("Stop sharing", QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self.share_b.clicked.connect(self.do_share)
        self.stop_b.clicked.connect(self.do_stop)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self._show()

    def _current(self):
        return next((s for s in gallery.shares(self.conn) if s[0] == self.album_id), None)

    def _show(self) -> None:
        cur = self._current()
        self.stop_b.setEnabled(cur is not None)
        self.share_b.setText("Update" if cur else "Share")
        if cur is None:
            self.link.setText("")
            self.qr.clear()
            self.note.setText("Not shared.")
            return
        _aid, _name, token, has_pin, originals = cur
        self.originals.setChecked(originals)
        self.remove_pin.setVisible(has_pin)
        self.remove_pin.setChecked(False)
        self.pin.setPlaceholderText("Leave empty to keep the PIN" if has_pin else "No PIN")
        port, running = self.server()
        url = gallery.link(token, port)
        self.link.setText(url)
        pm = QPixmap()
        pm.loadFromData(gallery.qr_png(url, 5))
        self.qr.setPixmap(pm)
        self.note.setText(("PIN required. " if has_pin else "No PIN. ")
                          + ("The gallery is on." if running else
                             f"The gallery couldn't start on port {port} - another program may be using it.")
                          + " Windows may ask once whether to allow Lunelis on private networks: allow it.")

    def do_share(self) -> None:
        pin = self.pin.text().strip() or None
        if pin and not pin.isdigit():
            self.note.setText("Use numbers only for the PIN (4-12 digits).")
            return
        if pin and len(pin) < 4:
            self.note.setText("A PIN needs at least 4 digits.")
            return
        if pin is None:
            # Empty keeps a PIN that's there; only the tick box takes it away.
            pin = None if self.remove_pin.isChecked() else gallery.KEEP
        gallery.share(self.conn, self.album_id, pin, self.originals.isChecked())
        self.pin.clear()
        self._show()

    def do_stop(self) -> None:
        gallery.stop_sharing(self.conn, self.album_id)
        self._show()
