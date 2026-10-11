"""
"Review your shoot": what the import autopilot did (importing/autopilot.py),
one row per stage, each with its answer:

- done stages (best frames, scene tags, the event, the album): **Keep** is
  simply leaving it; **Undo** takes it away again.
- waiting stages (edits in your style, the highlight reel): **Apply** /
  **Make it**, or **Skip**. Nothing of these happens without the click.
- **Show the photos** shows the shoot in the library; **Done reviewing**
  closes the run (waiting stages are skipped).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from lunelis.importing import autopilot as ap
from lunelis.ui.background import Background

MARK = {"done": "✔", "applied": "✔", "made": "✔", "waiting": "•", "skipped": "–", "off": "–",
        "undone": "↺", "failed": "!", "pending": "…"}


class AutopilotView(QWidget):
    show_ids = Signal(list, str)
    open_page = Signal(str)
    reviewed = Signal()

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.bg = Background(self, conn)
        self.run_id: int | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        from lunelis.ui.page_header import page_header
        head, h = page_header("Review your shoot")
        self.info = QLabel(objectName="Count")
        h.insertWidget(head.LEFT, self.info)
        self.show_b = QPushButton("Show the photos", clicked=self._show_photos)
        self.done_b = QPushButton("Done reviewing", objectName="Primary", clicked=self._finish)
        h.addWidget(self.show_b)
        h.addWidget(self.done_b)
        outer.addWidget(head)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        body = QWidget()
        self.rows = QVBoxLayout(body)
        self.rows.setContentsMargins(24, 16, 24, 24)
        self.rows.setSpacing(10)
        self.rows.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.buttons: dict[str, dict[str, QPushButton]] = {}

    def load(self, run_id: int | None = None) -> None:
        if run_id is None:
            waiting = ap.to_review(self.conn)
            run_id = waiting[0] if waiting else None
        self.run_id = run_id
        self._draw()

    def _draw(self) -> None:
        while self.rows.count() > 1:
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.buttons = {}
        r = ap.get(self.conn, self.run_id) if self.run_id else None
        self.show_b.setEnabled(bool(r and r["file_ids"]))
        self.done_b.setEnabled(bool(r and r["state"] == "review"))
        self.rows.setStretch(self.rows.count() - 1, 0 if r is None else 1)    # an empty page's line: mid-page
        if r is None:
            self.info.setText("")
            note = QLabel("No shoot waiting for review.\n\nTick Autopilot on the Import page and the next import "
                          "is sorted out for you to look over here.", objectName="EmptyNote", wordWrap=True)
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)        # centred, like every empty page (0.55)
            note.setContentsMargins(48, 24, 48, 24)
            self.rows.insertWidget(0, note, 1)
            return
        n = len(r["file_ids"])
        self.info.setText(f"{n:,} photo{'s' if n != 1 else ''} imported {r['created_at'][:10]}"
                          + (" · reviewed" if r["state"] == "done" else ""))
        for i, stage in enumerate(ap.STAGES):
            st = r["stages"][stage]
            card = QFrame(objectName="Card")
            row = QHBoxLayout(card)
            row.setContentsMargins(16, 10, 16, 10)
            mark = QLabel(MARK.get(st["status"], "·"))
            mark.setFixedWidth(18)
            row.addWidget(mark)
            text = QVBoxLayout()
            text.addWidget(QLabel(ap.TITLES[stage], objectName="SectionTitle"))
            summary = "Turned off" if st["status"] == "off" else st["summary"]
            lab = QLabel(objectName="Help", wordWrap=True)
            made = st["data"].get("path") if st["status"] == "made" else None
            if made:
                # The reel is a new file: a way to it, not just its name.
                from html import escape
                lab.setText(f'{escape(summary)} <a href="open">Play it</a> · <a href="folder">Open the folder</a>')
                lab.linkActivated.connect(lambda href, path=made: self._open(path, href == "folder"))
            else:
                lab.setTextFormat(Qt.TextFormat.PlainText)
                lab.setText(summary)
            text.addWidget(lab)
            row.addLayout(text, 1)
            b = {}
            if r["state"] == "review":
                if st["status"] == "done":
                    b["undo"] = QPushButton("Undo", clicked=lambda _=False, s=stage: self._undo(s))
                elif st["status"] == "waiting":
                    label = "Apply" if stage == "edits" else "Make it"
                    # Plain: "Done reviewing" is the page's one filled button (0.55: three were).
                    b["go"] = QPushButton(label, clicked=lambda _=False, s=stage: self._go(s))
                    b["skip"] = QPushButton("Skip", clicked=lambda _=False, s=stage: self._skip(s))
            for w in b.values():
                row.addWidget(w)
            self.buttons[stage] = b
            self.rows.insertWidget(i, card)

    def _undo(self, stage: str) -> None:
        ap.undo(self.conn, self.run_id, stage)
        self._draw()

    def _skip(self, stage: str) -> None:
        ap.skip(self.conn, self.run_id, stage)
        self._draw()

    def _go(self, stage: str) -> None:
        if stage == "edits":
            ap.apply_edits(self.conn, self.run_id)
            self.reviewed.emit()                      # thumbnails to remake: the window reloads
            self._draw()
            return
        from lunelis.create import engine
        folder = engine.output_dir(self.conn)
        self.buttons[stage]["go"].setEnabled(False)
        self.buttons[stage]["go"].setText("Making…")
        rid = self.run_id
        self.bg.run("reel", lambda conn: ap.make_reel(conn, rid, folder), lambda _p: self._draw(),
                    error=lambda e: (self.info.setText(f"Couldn't make the reel: {e}"), self._draw()))

    @staticmethod
    def _open(path: str, folder: bool) -> None:
        import os
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path) if folder else path))

    def _show_photos(self) -> None:
        r = ap.get(self.conn, self.run_id) if self.run_id else None
        if r and r["file_ids"]:
            self.show_ids.emit(list(r["file_ids"]), "This shoot")

    def _finish(self) -> None:
        if self.run_id:
            ap.finish(self.conn, self.run_id)
            self.reviewed.emit()
            self.load()
