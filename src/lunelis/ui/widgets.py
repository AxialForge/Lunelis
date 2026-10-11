"""Small shared widget helpers."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidget


def row_toggles(lw: QListWidget) -> None:
    """A tick-box list where clicking anywhere on a row (or Space/Enter) ticks
    or unticks it - not just the 16-pixel box. Items keep their check state
    but aren't Qt-"user checkable", so one click is one toggle."""
    def flip(item) -> None:
        if item is None or item.data(Qt.ItemDataRole.CheckStateRole) is None:
            return
        on = item.checkState() == Qt.CheckState.Checked
        item.setCheckState(Qt.CheckState.Unchecked if on else Qt.CheckState.Checked)

    lw.itemClicked.connect(flip)
    lw.itemActivated.connect(flip)
    lw.setCursor(Qt.CursorShape.PointingHandCursor)


def plain(e: BaseException) -> str:
    """An error in words for a dialog. Lunelis's own errors already are; the
    system's ("[WinError 32] The process cannot access the file...") get a
    sentence, with the file, and the details stay in the log."""
    import errno
    import logging
    import sqlite3
    if isinstance(e, sqlite3.OperationalError) and "locked" in str(e):
        return "The catalog was busy with something else for too long. Try again in a moment."
    if not isinstance(e, OSError) or type(e).__module__ not in ("builtins",):
        return str(e)
    logging.getLogger("lunelis.errors").info("shown as plain words: %r", e)
    where = f" ({e.filename})" if getattr(e, "filename", None) else ""
    win = getattr(e, "winerror", None)
    if isinstance(e, FileNotFoundError) or e.errno == errno.ENOENT:
        return f"It isn't there any more{where}."
    if isinstance(e, PermissionError) or win in (5, 32, 33):
        return (f"Windows wouldn't let Lunelis use it{where} - another program may have it open, "
                "or it's read-only.")
    if win in (53, 64, 67, 121, 1231) or e.errno in (errno.EHOSTUNREACH, errno.ETIMEDOUT):
        return f"The network drive didn't answer{where}. Is the NAS awake and connected?"
    if win == 112 or e.errno == errno.ENOSPC:
        return f"The drive is full{where}."
    if win in (21, 1005):
        return f"The drive isn't ready{where} - was it removed?"
    return f"{e.strerror or e}{where}."


def sharp(pm, edge: int, widget):
    """`pm` scaled to fit `edge` x `edge` on screen, with the screen's pixels:
    on a 150 % display a 72-pixel thumbnail is 108 real pixels, not 72 blown up."""
    from PySide6.QtCore import Qt
    ratio = max(widget.devicePixelRatioF() if widget is not None else 1.0, 1.0)
    out = pm.scaled(round(edge * ratio), round(edge * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(ratio)
    return out


class ElidedLabel(QLabel):
    """A one-line label that shortens long text in the middle (a long folder path)
    instead of making the window wider; the whole text is its tooltip."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full = ""
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full)
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle,
                                                      max(40, self.width())))


# --- one look for dialogs and form headings (0.55) --------------------------------------------

def make_primary(button, text: str | None = None):
    """Make `button` the dialog's one filled button, named after what it does
    ("Export", not "OK") and pressed by Enter. OK and Cancel used to be two
    identical outlines (0.55). Works for a QDialogButtonBox button too: the
    "Primary" rule in theme.py goes by the object name."""
    if text:
        button.setText(text)
    button.setObjectName("Primary")
    button.setAutoDefault(True)
    button.setDefault(True)
    return button


def dialog_heading(layout, text: str) -> QLabel:
    """A small dialog's bold first line, with 16 px of air above it - the text
    used to start at the window's top edge with nothing saying what the
    dialog is for (0.55)."""
    left, top, right, bottom = layout.getContentsMargins()
    layout.setContentsMargins(max(left, 16), max(top, 16), max(right, 16), max(bottom, 14))
    head = QLabel(text, objectName="DialogHeading")
    head.setWordWrap(True)
    head.setIndent(0)           # a label with style-sheet padding is set in by half a letter otherwise
    layout.addWidget(head)
    return head


def section_label(text: str) -> QLabel:
    """A heading inside a card ("NEW JOBS START WITH"): small, uppercase, with
    space above (theme.py, #CardSection) so it doesn't read as one more field
    label. The words as written stay as its accessible name (0.55)."""
    lab = QLabel(text.upper(), objectName="CardSection")
    lab.setIndent(0)            # in line with the field labels (see dialog_heading)
    lab.setAccessibleName(text)
    return lab


def tidy_form(form) -> None:
    """A QFormLayout with one left and one right edge for every field, and each
    label centred on its field (0.55). Call it after the last addRow(): fields
    all grow to the same right edge, a row of several controls loses the
    margin that set it in from the others, and a label is made as tall as its
    field so its text sits in the middle of it - it used to hang about 8 px
    high beside a 34 px field."""
    from PySide6.QtWidgets import QFormLayout, QWidget
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(10)
    for row in range(form.rowCount()):
        label = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        field = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
        holder = field.widget() if field is not None else None
        if holder is None:
            continue
        if holder.layout() is not None:
            holder.layout().setContentsMargins(0, 0, 0, 0)
        # The app stylesheet sets the fields' real height; it's only applied
        # once a widget is polished, which otherwise happens at show().
        for w in (holder, *holder.findChildren(QWidget)):
            w.ensurePolished()
        lab = label.widget() if label is not None else None
        if isinstance(lab, QLabel) and lab.text():
            lab.setMinimumHeight(holder.sizeHint().height())
            lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
