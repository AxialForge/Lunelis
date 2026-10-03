"""
Capture every page, tab, dialog and menu of Lunelis, with the position of
every control, for the user manual - no clicking by hand.

    .venv/Scripts/python docs/_tools/demo_library.py  <work>
    .venv/Scripts/python docs/_tools/capture.py       <work> <out dir> [graphite|midnight]

Runs Qt offscreen (exactly 100 % scale, no windows appear) on the FAKE demo
library in <work>. Writes, into <out dir>:
    screenshots/NN_<screen>_clean.png        the screen as it is
    screenshots/NN_<screen>_annotated.png    numbered callouts + leader lines
    capture_<theme>.json                     screens, controls, callout numbers
(`build_inventory.py` turns capture_graphite.json into ui_inventory.json.)

Modal dialogs, message boxes, input prompts and file pickers are opened
WITHOUT blocking (their exec() is replaced for the capture), so each can be
photographed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

WORK = Path(sys.argv[1])
OUT = Path(sys.argv[2])
THEME = sys.argv[3] if len(sys.argv) > 3 else "graphite"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
# Every run starts from the demo exactly as demo_library.py left it: the
# capture adds a mask and so on, and none of that may leak into the next run.
# (The pristine copy sits beside it, so shown paths stay <work>\data.)
import shutil  # noqa: E402
_PRISTINE = WORK / "data.pristine"
if not _PRISTINE.exists():
    shutil.copytree(WORK / "data", _PRISTINE)
shutil.rmtree(WORK / "data")
shutil.copytree(_PRISTINE, WORK / "data")
os.environ["LUNELIS_DATA_DIR"] = str(WORK / "data")
# No real user name may reach a screenshot: home-folder defaults (export and
# merge folders, darktable's folder, the log path) point at a made-up user.
os.environ["USERPROFILE"] = "C:\\Users\\Demo"
os.environ["APPDATA"] = "C:\\Users\\Demo\\AppData\\Roaming"
os.environ["LOCALAPPDATA"] = "C:\\Users\\Demo\\AppData\\Local"

from PySide6.QtCore import QPoint, QRect, Qt, QThreadPool  # noqa: E402
from PySide6.QtGui import QAction, QFont  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton, QAbstractSlider, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QInputDialog, QLabel, QLineEdit, QListWidget, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QScrollBar, QSlider, QSpinBox, QTabBar,
    QTableView, QToolButton, QTreeWidget, QWidget,
)

app = QApplication([])
app.setFont(QFont("Segoe UI", 9))

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "lunelis"

# --- non-blocking dialogs -------------------------------------------------------------------
opened: list = []


def _exec(self, *a, **k):
    opened.append(self)
    self.show()
    return 0


QDialog.exec = _exec
QMessageBox.exec = _exec


def _box(icon):
    def show(parent, title, text, *a, **k):
        b = QMessageBox(icon, title, text, parent=parent)
        if icon == QMessageBox.Icon.Question:
            b.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        opened.append(b)
        b.show()
        return QMessageBox.StandardButton.No
    return show


QMessageBox.question = staticmethod(_box(QMessageBox.Icon.Question))
QMessageBox.information = staticmethod(_box(QMessageBox.Icon.Information))
QMessageBox.warning = staticmethod(_box(QMessageBox.Icon.Warning))
QMessageBox.about = staticmethod(_box(QMessageBox.Icon.NoIcon))


def _input(parent, title, label, *a, **k):
    d = QInputDialog(parent)
    d.setWindowTitle(title)
    d.setLabelText(label)
    opened.append(d)
    d.show()
    return "", False


QInputDialog.getText = staticmethod(_input)
QInputDialog.getItem = staticmethod(lambda parent, title, label, items, *a, **k: (
    _input(parent, title, label)[0], False))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))


def pump(seconds: float = 0.6) -> None:
    t = time.time()
    while time.time() - t < seconds:
        app.processEvents()
        QThreadPool.globalInstance().waitForDone(15)


# --- source lookup ----------------------------------------------------------------------------
_SOURCE = [(p, p.read_text(encoding="utf-8").splitlines()) for p in sorted((SRC / "ui").glob("*.py"))] + \
          [(p, p.read_text(encoding="utf-8").splitlines()) for p in sorted(SRC.glob("*/*.py")) if p.parent.name != "ui"]


SCREEN_FILE, SCREEN_CLASS, SCREEN_ID = "", "", ""


def _rel(path: Path, i: int) -> str:
    return f"{path.relative_to(REPO).as_posix()}:{i}"


def defined_at(*texts: str, prefer: str = "") -> str:
    """file:line of the string literal holding one of these texts. An exact
    literal beats a prefix, and the screen's own file (prefer) beats the rest."""
    order = sorted(_SOURCE, key=lambda pl: pl[0].relative_to(REPO).as_posix() != prefer)
    for exact in (True, False):
        for text in texts:
            t = (text or "").replace("&&", "&").strip()
            if not t or (len(t) < 2 and not exact):
                continue
            t = t.split("\n")[0][:60]
            q = r"""["']"""
            variants = [(v, q + re.escape(v) + (q if exact else ""))
                        for v in (t, t.replace("&", "&&"), t.rstrip("…"), t.split("  ")[0])]
            if not exact:     # text built by an f-string: its fixed start, or its fixed end
                head, tail = re.split(r"[\d(]", t)[0].strip(), t.split(" - ")[-1]
                variants += [(head, q + re.escape(head)), (tail, re.escape(tail) + q)]
            for variant, pattern in variants:
                if len(variant) < (1 if exact else 4):
                    continue
                pat = re.compile(pattern)
                for path, lines in order:
                    for i, line in enumerate(lines, 1):
                        if pat.search(line):
                            return _rel(path, i)
    return "UNVERIFIED"


def attr_of(w: QWidget) -> tuple[str, str] | None:
    """(attribute name, owning class) when an ancestor keeps w as self.<name>."""
    p = w.parentWidget()
    while p is not None:
        for k, v in vars(p).items() if hasattr(p, "__dict__") else ():
            if v is w and not k.startswith("__"):
                return k, type(p).__name__
        p = p.parentWidget()
    return None


def attr_line(attr: str, owner: str) -> str:
    """file:line where class owner assigns self.<attr>."""
    where = class_at(owner)
    if where == "UNVERIFIED":
        return where
    file, start = where.rsplit(":", 1)
    for path, lines in _SOURCE:
        if path.relative_to(REPO).as_posix() == file:
            pat = re.compile(rf"self\.{re.escape(attr)}\s*(:[^=]+)?=")
            for i in range(int(start), len(lines) + 1):
                if i > int(start) and lines[i - 1].startswith("class "):
                    break
                if pat.search(lines[i - 1]):
                    return _rel(path, i)
    return "UNVERIFIED"


def class_at(cls_name: str) -> str:
    for path, lines in _SOURCE:
        for i, line in enumerate(lines, 1):
            if line.startswith(f"class {cls_name}("):
                return f"{path.relative_to(REPO).as_posix()}:{i}"
    return "UNVERIFIED"


# --- controls --------------------------------------------------------------------------------
CUSTOM = {"PhotoGrid": "Photo grid", "PhotoCanvas": "Photo", "EditCanvas": "Photo (live edit)",
          "Filmstrip": "Filmstrip", "CurveCanvas": "Tone curve graph", "TimelineScrubber": "Timeline scrubber",
          "AlbumTile": "Album tile", "NewAlbumTile": "New album tile"}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("&&", "\0").replace("&", "").replace("\0", "&")).strip()


def _row_label(w: QWidget) -> str:
    """The QLabel to the left of w in the same row (settings-style forms)."""
    parent = w.parentWidget()
    if parent is None:
        return ""
    g = w.geometry()
    best, best_dx = "", 10 ** 6
    for lab in parent.findChildren(QLabel):
        if lab.parentWidget() is not parent or not lab.isVisibleTo(parent) or not lab.text():
            continue
        lg = lab.geometry()
        if lg.right() <= g.left() + 4 and abs(lg.center().y() - g.center().y()) < max(18, g.height()):
            dx = g.left() - lg.right()
            if dx < best_dx:
                best, best_dx = lab.text(), dx
    return _clean(re.sub("<[^>]+>", "", best))


def _caption(w: QWidget) -> str:
    """The heading label just above w (lists under a caption)."""
    parent = w.parentWidget()
    if parent is None:
        return ""
    g = w.geometry()
    best, best_dy = "", 60
    for lab in parent.findChildren(QLabel):
        if lab.parentWidget() is not parent or not lab.isVisibleTo(parent) or not lab.text():
            continue
        lg = lab.geometry()
        dy = g.top() - lg.bottom()
        if 0 <= dy < best_dy and lg.left() < g.right() and lg.right() > g.left():
            best, best_dy = lab.text(), dy
    text = _clean(re.sub("<[^>]+>", "", best))
    return text.capitalize() if text.isupper() else text


def _from_attr(w: QWidget) -> str:
    a = attr_of(w)
    return a[0].strip("_").replace("_", " ").capitalize() if a else ""


def _tip_name(w: QWidget) -> str:
    """A short name from the tooltip, for buttons that show only a symbol."""
    tip = _clean(w.toolTip().split("\n")[0])
    return re.split(r" \(| - ", tip)[0] if tip else ""


# Names the capture can't read off the screen (inline sentence fragments).
NAME_OVERRIDES = {
    "From your folder names, and from photos taken more than": "Gap between shoots (hours)",
    "apart, with at least": "Smallest event (photos)",
    "from": "Quiet hours start",
    "to": "Quiet hours end",
}


# Per screen: a label the capture reads from the wrong row.
SCREEN_NAME_OVERRIDES = {
    ("import", "Destination"): "Folders",
    ("migrate", r"{YYYY}\{M}-{D}-{YYYY}[ {import_name}]"): "Folders",
}


def kind_and_name(w: QWidget) -> tuple[str, str] | None:
    k = _kind_and_name(w)
    if k is None:
        return None
    kind, name = k
    name = NAME_OVERRIDES.get(name, name)
    if kind in ("Text box", "List", "Text area", "Number box") and name in ("", kind, "PathField"):
        name = _from_attr(w) or name
    return kind, name


def _kind_and_name(w: QWidget) -> tuple[str, str] | None:
    cls = type(w).__name__
    if cls in CUSTOM:
        name = CUSTOM[cls]
        if cls == "AlbumTile":
            name = f"Album tile: {getattr(w, 'album', None) and w.album.name}"
        return CUSTOM[cls] if cls not in ("AlbumTile",) else "Album tile", name
    if cls == "ParamSlider":
        return "Slider with number box", _clean(w.name.text())
    if isinstance(w, QCheckBox):
        return "Check box", _clean(w.text()) or _row_label(w)
    if isinstance(w, QRadioButton):
        return "Option button", _clean(w.text())
    if isinstance(w, QAbstractButton):
        text = _clean(w.text())
        if isinstance(w, QToolButton) and w.menu() is not None:
            return "Menu button", text.rstrip(" ▾") or _clean(w.toolTip())
        if text[:1] in "▾▸" and text[1:].strip():
            title = text[1:].strip()
            return "Section header", title.capitalize() if title.isupper() else title
        if not re.search(r"[A-Za-z0-9]", text) or text == _clean(w.toolTip()):
            return "Button", _tip_name(w) or text or w.objectName() or cls
        return "Button", text or w.objectName() or cls
    if isinstance(w, QComboBox):
        return "Drop-down list", _row_label(w) or _clean(w.currentText())
    if isinstance(w, QLineEdit):
        kind = "Search box" if "search" in (w.placeholderText() or "").lower() or w.objectName() == "Search" \
            else "Text box"
        return kind, _clean(w.placeholderText()) or _row_label(w) or _caption(w) or "Text box"
    if isinstance(w, QAbstractSpinBox):
        return "Number box", _row_label(w) or "Number box"
    if isinstance(w, QSlider):
        return "Slider", _clean(w.toolTip()) or _row_label(w) or "Slider"
    if isinstance(w, QTreeWidget):
        return "Tree", "Tree: " + ", ".join(w.headerItem().text(i) for i in range(w.columnCount()))
    if isinstance(w, QTableView):
        model = w.model()
        heads = [str(model.headerData(i, Qt.Orientation.Horizontal)) for i in range(model.columnCount())] if model else []
        return "Table", "Table: " + ", ".join(h for h in heads if h and h != "None")
    if isinstance(w, QListWidget):
        return "List", _caption(w) or _row_label(w) or "List"
    if isinstance(w, QPlainTextEdit):
        return "Text area", "Text area"
    return None


def _num(v: float) -> float | int:
    return int(v) if float(v).is_integer() else round(v, 2)


def limits(w: QWidget) -> dict:
    """What the control accepts, and the value it showed on the demo library."""
    if type(w).__name__ == "ParamSlider":
        n = w.number
        return {"limits": f"{_num(n.minimum())} to {_num(n.maximum())}, step {_num(n.singleStep())}",
                "shown": _num(n.value())}
    if isinstance(w, QAbstractSpinBox) and hasattr(w, "minimum"):
        suffix = w.suffix().strip() if hasattr(w, "suffix") else ""
        return {"limits": f"{_num(w.minimum())} to {_num(w.maximum())} {suffix}".strip(), "shown": _num(w.value())}
    if isinstance(w, QSlider):
        return {"limits": f"{w.minimum()} to {w.maximum()}", "shown": w.value()}
    if isinstance(w, QComboBox):
        return {"limits": "Choices: " + "; ".join(_clean(w.itemText(i)) for i in range(w.count())),
                "shown": _clean(w.currentText())}
    if isinstance(w, QCheckBox):
        return {"shown": "ticked" if w.isChecked() else "not ticked"}
    if isinstance(w, QLineEdit):
        return {"shown": w.text()}
    return {}


def _skip(w: QWidget, target: QWidget) -> bool:
    if isinstance(w, QScrollBar):
        return True
    p = w.parentWidget()
    while p is not None and p is not target:
        if isinstance(p, (QComboBox, QAbstractSpinBox, QTabBar, QLineEdit)) or type(p).__name__ == "ParamSlider":
            return True
        if type(p).__name__ in CUSTOM and type(w).__name__ not in CUSTOM:
            return True
        p = p.parentWidget()
    return False


def _in_chrome(w: QWidget) -> bool:
    """Part of the sidebar or status bar, which is documented once (main window)."""
    p = w
    while p is not None:
        if p.objectName() == "Sidebar" or p is win.statusBar():
            return True
        p = p.parentWidget()
    return False


def controls(target: QWidget, chrome: bool = True) -> list[dict]:
    out = []
    trect = target.rect()
    for w in target.findChildren(QWidget):
        if not w.isVisibleTo(target) or _skip(w, target) or (not chrome and _in_chrome(w)):
            continue
        if isinstance(w, QTabBar):
            for i in range(w.count()):
                r = w.tabRect(i)
                tl = w.mapTo(target, r.topLeft())
                rect = QRect(tl, r.size()).intersected(trect)
                if rect.width() > 4 and rect.height() > 4:
                    out.append({"type": "Tab", "name": _clean(w.tabText(i)), "tooltip": "",
                                "rect": [rect.x(), rect.y(), rect.width(), rect.height()],
                                "defined_at": defined_at(w.tabText(i), prefer=SCREEN_FILE)})
            continue
        kn = kind_and_name(w)
        if kn is None:
            continue
        tl = w.mapTo(target, QPoint(0, 0))
        rect = QRect(tl, w.size()).intersected(trect)
        # On a whole window, also skip controls a scroll area clips away. (Full-length
        # grabs of scrolled content draw everything, so there it doesn't apply.)
        seen = w.visibleRegion().boundingRect() if target.isWindow() else rect
        if rect.width() < 6 or rect.height() < 6 or rect.width() * rect.height() < 0.5 * w.width() * w.height() \
                or seen.width() * seen.height() < 0.5 * w.width() * w.height():
            continue
        kind, name = kn
        text = ""
        if isinstance(w, QAbstractButton):
            text = w.text()
        elif isinstance(w, QLineEdit):
            text = w.placeholderText()
        cls = type(w).__name__
        extra = {}
        attr = attr_of(w)
        where = attr_line(*attr) if attr else "UNVERIFIED"
        if where == "UNVERIFIED":
            if cls in CUSTOM:
                where = class_at(cls)
            elif cls == "ParamSlider":
                where = defined_at(w.name.text(), prefer=SCREEN_FILE)
            else:
                where = defined_at(text, w.toolTip(), name, prefer=SCREEN_FILE)
        name = SCREEN_NAME_OVERRIDES.get((SCREEN_ID, name), name)
        in_box = isinstance(w.parentWidget(), QDialogButtonBox)
        if (in_box or where == "UNVERIFIED") and _clean(text) in ("OK", "Cancel", "Close", "Yes", "No"):
            where = class_at(SCREEN_CLASS)
            extra["note"] = "Standard Qt dialog button, made by the dialog class named here"
        out.append({"type": kind, "name": name or kind, "tooltip": _clean(w.toolTip()),
                    "rect": [rect.x(), rect.y(), rect.width(), rect.height()], "defined_at": where,
                    "class": cls, "enabled": w.isEnabled(), **limits(w), **extra})
    # Reading order: top to bottom, left to right; one entry per name.
    out.sort(key=lambda c: (c["rect"][1] // 12, c["rect"][0]))
    seen, uniq = set(), []
    for c in out:
        key = (c["type"], c["name"])
        if key in seen and c["type"] not in ("Album tile",):
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


def menu_controls(menu: QMenu) -> list[dict]:
    out = []
    for a in menu.actions():
        if a.isSeparator() or not a.isVisible():
            continue
        r = menu.actionGeometry(a)
        name = _clean(a.text())
        where = defined_at(a.text(), a.text()[:1], prefer="src/lunelis/ui/main_window.py")
        if set(name) == {"★"}:
            name = f"{len(name)} star" + ("s" if len(name) > 1 else "")
        out.append({"type": "Submenu" if a.menu() else "Menu command", "name": name,
                    "shortcut": a.shortcut().toString(), "tooltip": "",
                    "rect": [r.x(), r.y(), r.width(), r.height()], "defined_at": where})
    return out


# --- annotation ------------------------------------------------------------------------------
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ACCENT = (226, 74, 51)
MARGIN = 80
R = 13                  # callout bubble radius: readable once the page scales the shot to ~55 %
GAP = 2 * R + 3


def layout(ctrls: list[dict], W: int, H: int) -> list[dict]:
    """Choose each callout's side and height, and number them: the left margin
    top to bottom, then the right margin top to bottom - so the numbers read
    in order and leader lines don't cross."""
    cap = max(1, int((H - 2 * R) // GAP) + 1)
    sides = {"left": [], "right": []}
    for c in sorted(ctrls, key=lambda c: abs(c["rect"][0] + c["rect"][2] / 2 - W / 2), reverse=True):
        cx = c["rect"][0] + c["rect"][2] / 2
        side = "left" if cx < W / 2 else "right"
        if len(sides[side]) >= cap:
            side = "right" if side == "left" else "left"
        sides[side].append(c)
    n = 0
    for side in ("left", "right"):
        group = sorted(sides[side], key=lambda c: (c["rect"][1] + c["rect"][3] / 2, c["rect"][0]))
        k = len(group)
        gap = GAP + 3 if k < 2 else min(GAP + 3, (H - 2 * R) / (k - 1))
        ys = [c["rect"][1] + c["rect"][3] / 2 for c in group]
        for i in range(k):                                  # push down to keep the gap...
            ys[i] = max(ys[i], R + 1, ys[i - 1] + gap if i else R + 1)
        for i in range(k - 1, -1, -1):                      # ...and back up from the bottom
            ys[i] = min(ys[i], H - R - 1 if i == k - 1 else ys[i + 1] - gap)
        for c, y in zip(group, ys):
            n += 1
            c["n"], c["_side"], c["_y"] = n, side, round(y, 1)
    ctrls.sort(key=lambda c: c["n"])
    return ctrls


def annotate(clean: Path, ctrls: list[dict], dest: Path) -> None:
    img = Image.open(clean).convert("RGB")
    W, H = img.size
    canvas = Image.new("RGB", (W + 2 * MARGIN, H), (255, 255, 255))
    canvas.paste(img, (MARGIN, 0))
    d = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 15)
    for c in ctrls:
        x, y, w, h = c["rect"]
        d.rectangle([MARGIN + x, y, MARGIN + x + w - 1, y + h - 1], outline=ACCENT, width=2)
    for c in ctrls:
        x, y, w, h = c["rect"]
        side, by = c.pop("_side"), c.pop("_y")
        cy = y + h / 2
        left = side == "left"
        bx = MARGIN / 2 if left else W + MARGIN * 1.5
        ex = MARGIN + (x if left else x + w)             # leader line to the control's near edge
        d.line([(bx + (R if left else -R), by), (ex, cy)], fill=ACCENT, width=2)
        d.ellipse([ex - 3, cy - 3, ex + 3, cy + 3], fill=ACCENT)
        d.ellipse([bx - R, by - R, bx + R, by + R], fill=ACCENT, outline=(255, 255, 255), width=2)
        label = str(c["n"])
        tw = d.textlength(label, font=font)
        d.text((bx - tw / 2, by - 10), label, fill=(255, 255, 255), font=font)
    canvas.save(dest)


# --- the screens ------------------------------------------------------------------------------
from lunelis.ui import main_window as mw  # noqa: E402

win = mw.MainWindow()
win.apply_theme(THEME)
win.resize(1440, 900)
win.show()
pump(5)
conn = win.conn


def ids(where: str) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM files WHERE rel_path LIKE ? AND quarantined_at IS NULL"
                                       " ORDER BY filename", (where + "%",))]


def page(name: str):
    def go():
        win.open_page(name)
        pump(1.5)
        return win
    return go


def settings_tab(tab: str):
    def go():
        win.open_page("Settings")
        win.settings_page.show_tab(tab)
        pump(0.6)
        scroll = win.settings_page.tabs.currentWidget()
        return scroll.widget() if isinstance(scroll, QScrollArea) else scroll
    return go


def library():
    win.set_filter(mw.Filter())
    win.open_page("Library")
    pump(2)
    return win


def search():
    library()
    win.search.setText("summer")
    win._search_changed()
    pump(1.5)
    return win


def photo_view():
    library()
    win.search.clear()
    win._search_changed()
    fid = ids("2025/08-19 Mountain Trip")[1]
    win.open_detail(fid)
    pump(3)
    return win


def photo_zoomed():
    photo_view()
    c = win.detail.canvas
    c.zoom_to(1.0, c.rect().center())
    pump(3)
    return win


def edit_panel():
    photo_view()
    win.detail.set_editing(True)
    pump(3)
    return win


def edit_panel_full():
    edit_panel()
    panel = win.detail.develop
    for s in panel.sections.values():
        s.set_open(True)
    pump(0.8)
    return panel.widget()


def edit_mask():
    edit_panel()
    win.detail.edit.add_mask("radial")
    win.detail.develop.mask_sliders["exposure"].set_value(0.6, emit=True)
    # Show the mask's own controls: fold every other section, unfold Masks.
    for title, section in win.detail.develop.sections.items():
        section.set_open(title.lower() == "masks")
    pump(2)
    return win


def edit_crop():
    edit_panel()
    win.detail.edit.set_crop_mode(True)
    pump(2)
    return win


def dupes_tab(i: int):
    def go():
        win.open_page("Duplicates")
        win.dupes.tabs.setCurrentIndex(i)
        pump(1.5)
        return win
    return go


def dialog(fn):
    """Run fn (which opens a dialog without blocking) and return that dialog."""
    def go():
        library()
        before = len(opened)
        result = fn()
        pump(1)
        if isinstance(result, QWidget):
            return result
        return opened[before] if len(opened) > before else win
    return go


def select(fids):
    win.grid.selected = set(fids)
    win.grid.current = win.index.position(fids[0])
    win.grid.viewport().update()


def export_dialog():
    from lunelis.ui.export_dialog import ExportDialog
    d = ExportDialog(conn, 3, win)
    d.show()
    return d


def merge_dialog():
    from lunelis.ui.merge_dialog import MergeDialog
    d = MergeDialog(conn, "hdr", 3, "DSC00001.JPG", win)
    d.show()
    return d


def pano_dialog():
    from lunelis.ui.merge_dialog import MergeDialog
    d = MergeDialog(conn, "panorama", 4, "DSC00020.JPG", win)
    d.show()
    return d


def tag_dialog():
    from lunelis.ui.tag_editor import TagDialog
    d = TagDialog(conn, ids("2024/06-14 Lakeside Weekend")[:3], win)
    d.show()
    return d


def jobs_panel():
    win.show_jobs()
    pump(0.5)
    return win._jobs_dialog


def scope_dialog():
    from lunelis.ui.jobs import ScopeDialog
    d = ScopeDialog(conn, "Find duplicates", win)
    d.show()
    return d


def new_backup():
    from lunelis.ui.backups_view import NewBackupDialog
    d = NewBackupDialog(conn, win)
    d.show()
    return d


def confirm_empty():
    from lunelis.dupes import manage
    from lunelis.ui.quarantine_view import ConfirmEmpty
    d = ConfirmEmpty(manage.entries(conn), win)
    d.show()
    return d


def report_problem():
    win.report_problem()


def about():
    win.about()


def new_album():
    select(ids("2024/06-14 Lakeside Weekend")[:2])
    win.new_album()


def hover_card():
    library()
    g = win.grid
    fid = ids("2025/08-19 Mountain Trip")[1]
    pos = win.index.position(fid)
    g._show_card(pos) if hasattr(g, "_show_card") else None
    pump(1)
    return win


def menu(title: str, sub: str | None = None):
    def go():
        library()
        act = next(a for a in win.menuBar().actions() if _clean(a.text()) == title)
        m = act.menu()
        if sub:
            m = next(a.menu() for a in m.actions() if a.menu() and _clean(a.text()) == sub)
        m.popup(QPoint(0, 0))
        pump(0.4)
        return m
    return go


SCREENS = [
    ("main_window", "Main window (library)", None, library, "MainWindow"),
    ("search", "Searching the library", "main_window", search, "MainWindow"),
    ("photo_view", "Photo view with the Info panel", "main_window", photo_view, "DetailView"),
    ("photo_zoom", "Photo view zoomed to 100 %", "photo_view", photo_zoomed, "PhotoCanvas"),
    ("edit_panel", "Edit panel", "photo_view", edit_panel, "DevelopPanel"),
    ("edit_panel_full", "Edit panel - every section", "edit_panel", edit_panel_full, "DevelopPanel"),
    ("edit_mask", "Editing a mask", "edit_panel", edit_mask, "MaskTool"),
    ("edit_crop", "Cropping", "edit_panel", edit_crop, "EditCanvas"),
    ("albums", "Albums page", "main_window", page("Albums"), "AlbumsView"),
    ("tags", "Tags page", "main_window", page("Tags"), "TagsView"),
    ("events", "Event suggestions", "albums", page("Events"), "EventsView"),
    ("import", "Import page", "main_window", page("Import"), "ImportView"),
    ("migrate", "Migrate page", "main_window", page("Migrate"), "MigrateView"),
    ("dupes_exact", "Duplicates - Exact copies", "main_window", dupes_tab(0), "DuplicatesView"),
    ("dupes_near", "Duplicates - Near-duplicates", "main_window", dupes_tab(1), "NearView"),
    ("damaged", "Damaged files page", "main_window", page("Damaged files"), "DamagedView"),
    ("backups", "Backups page", "main_window", page("Backups"), "BackupsView"),
    ("quarantine", "Quarantine page", "main_window", page("Quarantine"), "QuarantineView"),
    ("settings", "Settings page", "main_window", settings_tab("General") and page("Settings"), "SettingsView"),
] + [(f"settings_{t.lower().replace(' & ', '_').replace(' ', '_')}", f"Settings - {t} tab", "main_window",
      settings_tab(t), "SettingsView") for t in
     ("General", "Appearance", "Library", "Import", "Edit", "Ratings & sidecars", "Duplicates & jobs",
      "Backups", "darktable", "Updates", "Advanced")] + [
    ("dialog_export", "Export dialog", "main_window", dialog(export_dialog), "ExportDialog"),
    ("dialog_merge_hdr", "Merge to HDR dialog", "main_window", dialog(merge_dialog), "MergeDialog"),
    ("dialog_merge_pano", "Merge to panorama dialog", "main_window", dialog(pano_dialog), "MergeDialog"),
    ("dialog_tags", "Tags dialog (Ctrl+T)", "main_window", dialog(tag_dialog), "TagDialog"),
    ("dialog_jobs", "Jobs panel", "main_window", dialog(jobs_panel), "JobsDialog"),
    ("dialog_find_duplicates", "Find duplicates (where and when)", "main_window", dialog(scope_dialog), "ScopeDialog"),
    ("dialog_new_backup", "New backup dialog", "backups", dialog(new_backup), "NewBackupDialog"),
    ("dialog_confirm_empty", "Empty quarantine confirmation", "quarantine", dialog(confirm_empty), "ConfirmEmpty"),
    ("dialog_report_problem", "Report a problem", "main_window", dialog(report_problem), "MainWindow"),
    ("dialog_about", "About Lunelis", "main_window", dialog(about), "MainWindow"),
    ("dialog_new_album", "New album from selection", "main_window", dialog(new_album), "MainWindow"),
    ("menu_library", "Library menu", "main_window", menu("Library"), "MainWindow"),
    ("menu_photo", "Photo menu", "main_window", menu("Photo"), "MainWindow"),
    ("menu_help", "Help menu", "main_window", menu("Help"), "MainWindow"),
] + [(f"menu_photo_{s.lower()}", f"Photo menu - {s}", "menu_photo", menu("Photo", s), "MainWindow")
     for s in ("Rating", "Label", "Event", "Album", "Tags", "Merge", "Edit", "Stack", "Flag")]


# --- named parts of the window ---------------------------------------------------------------
# The manual names the areas of each screen ("Sidebar", "Photo bar", "Info panel"...)
# and says which area every control is in.

PAGE_NAMES = {"AlbumsView": "Albums page", "TagsView": "Tags page", "EventsView": "Event suggestions page",
              "ImportView": "Import page", "MigrateView": "Migrate page", "DuplicatesView": "Duplicates page",
              "DamagedView": "Damaged files page", "BackupsView": "Backups page",
              "QuarantineView": "Quarantine page"}
# Grabs of one widget (not the whole window): every control is in this part.
WHOLE_PART = {"edit_panel_full": "Edit panel"}


def _inside(w: QWidget, ancestor: QWidget) -> bool:
    p = w.parentWidget()
    while p is not None:
        if p is ancestor:
            return True
        p = p.parentWidget()
    return False


def find_parts(target: QWidget) -> list[dict]:
    if target is not win:
        return []
    found: list[dict] = []
    visible = [w for w in win.findChildren(QWidget) if w.isVisibleTo(win)]

    def first(pred):
        return next((w for w in visible if pred(w)), None)

    def add(name: str, w: QWidget | None) -> None:
        if w is None or not w.isVisibleTo(win):
            return
        r = QRect(w.mapTo(win, QPoint(0, 0)), w.size()).intersected(win.rect())
        if r.width() > 8 and r.height() > 8:
            found.append({"name": name, "rect": [r.x(), r.y(), r.width(), r.height()]})

    add("Menu bar", win.menuBar())
    add("Sidebar", first(lambda w: w.objectName() == "Sidebar"))
    detail = first(lambda w: type(w).__name__ == "DetailView")
    page = win.pages.currentWidget()
    if detail is not None and (page is detail or _inside(detail, page) or _inside(page, detail)):
        add("Photo bar", first(lambda w: w.objectName() == "Toolbar" and _inside(w, detail)))
        add("Photo", first(lambda w: type(w).__name__ in ("EditCanvas", "PhotoCanvas")))
        add("Filmstrip", first(lambda w: type(w).__name__ == "Filmstrip"))
        add("Info panel", first(lambda w: type(w).__name__ == "InfoPanel"))
        add("Edit panel", first(lambda w: type(w).__name__ == "DevelopPanel"))
    elif page is win.grid:
        add("Top bar", first(lambda w: w.objectName() == "Toolbar"))
        add("Filter bar", first(lambda w: w.objectName() == "FilterBar"))
        add("Photo grid", win.grid)
        add("Timeline", first(lambda w: type(w).__name__ == "TimelineScrubber"))
    else:
        cls = type(page).__name__
        tabs = getattr(page, "tabs", None)
        if cls == "SettingsView":
            add("Tab strip", tabs.tabBar())
            add("Settings tab", tabs.currentWidget())
        else:
            if tabs is not None and hasattr(tabs, "tabBar"):
                add("Tab strip", tabs.tabBar())
            add(PAGE_NAMES.get(cls, "Page"), page)
    add("Status bar", win.statusBar())
    for i, p in enumerate(found):
        p["letter"] = chr(ord("A") + i)
    return found


def where(c: dict, parts: list[dict], sid: str, screen_name: str) -> str:
    if sid in WHOLE_PART:
        return WHOLE_PART[sid]
    if not parts:
        if sid.startswith("settings_"):
            return "Settings tab"
        return screen_name
    x, y, w, h = c["rect"]
    cx, cy = x + w / 2, y + h / 2
    inside = [p for p in parts if p["rect"][0] <= cx <= p["rect"][0] + p["rect"][2]
              and p["rect"][1] <= cy <= p["rect"][1] + p["rect"][3]]
    return min(inside, key=lambda p: p["rect"][2] * p["rect"][3])["name"] if inside else "-"


def draw_parts(clean: Path, parts: list[dict], dest: Path) -> None:
    img = Image.open(clean).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 20)
    for p in sorted(parts, key=lambda p: -p["rect"][2] * p["rect"][3]):     # big first, small on top
        x, y, w, h = p["rect"]
        d.rectangle([x + 2, y + 2, x + w - 3, y + h - 3], outline=ACCENT + (255,), width=3, fill=ACCENT + (24,))
    for p in parts:
        x, y, w, h = p["rect"]
        cx, cy, r = x + w / 2, y + h / 2, 16
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT + (255,), outline=(255, 255, 255, 255), width=2)
        tw = d.textlength(p["letter"], font=font)
        d.text((cx - tw / 2, cy - 14), p["letter"], fill=(255, 255, 255, 255), font=font)
    Image.alpha_composite(img, overlay).convert("RGB").save(dest)


def main() -> None:
    shots = OUT / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    result = {"theme": THEME, "window": [1440, 900], "screens": [], "shortcuts": []}
    global SCREEN_FILE, SCREEN_CLASS, SCREEN_ID
    for n, (sid, name, parent, go, cls) in enumerate(SCREENS, 1):
        SCREEN_CLASS, SCREEN_ID = cls, sid
        SCREEN_FILE = class_at(cls).rsplit(":", 1)[0]
        try:
            target = go()
        except Exception as e:                           # recorded, never fatal: the manual lists it
            result["screens"].append({"id": sid, "name": name, "error": f"{type(e).__name__}: {e}"})
            print("FAILED", sid, e)
            continue
        stem = f"{n:02d}_{sid}" + ("" if THEME == "graphite" else f"_{THEME}")
        clean = shots / f"{stem}_clean.png"
        target.grab().save(str(clean))
        if isinstance(target, QMenu):
            ctrls = menu_controls(target)
        else:
            ctrls = controls(target, chrome=(sid == "main_window"))
        layout(ctrls, target.width(), target.height())
        annotate(clean, ctrls, shots / f"{stem}_annotated.png")
        parts = find_parts(target)
        for c in ctrls:
            c["where"] = where(c, parts, sid, name)
        if parts:
            draw_parts(clean, parts, shots / f"{stem}_parts.png")
        result["screens"].append({"id": sid, "number": n, "name": name, "parent": parent,
                                  "defined_at": class_at(cls), "class": cls,
                                  "clean": clean.name, "annotated": f"{stem}_annotated.png",
                                  "size": [target.width(), target.height()], "controls": ctrls,
                                  "parts": parts, "parts_image": f"{stem}_parts.png" if parts else None})
        print(f"{n:02d} {sid}: {len(ctrls)} controls")
        if isinstance(target, QMenu):
            target.close()
        for d in list(opened):
            d.close()
        opened.clear()
    for a in win.findChildren(QAction):
        if a.shortcut().toString():
            result["shortcuts"].append({"keys": a.shortcut().toString(), "command": _clean(a.text()),
                                        "defined_at": defined_at(a.text(), a.text()[:1],
                                                                prefer="src/lunelis/ui/main_window.py")})
    (OUT / f"capture_{THEME}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    win._quitting = True
    win.close()


main()
