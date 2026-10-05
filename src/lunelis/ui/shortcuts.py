"""
The shortcut sheet (press ?): every keyboard shortcut, the current screen's first.

Menu shortcuts are read from the window's own actions, so the sheet can't
drift from the menus; keys that aren't menu commands (grid navigation, the
photo view, the Edit panel) are listed here - the one place they're written
down for the user.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

KEYS: dict[str, list[tuple[str, str]]] = {
    "Culling (Photo > Cull full screen, Ctrl+K)": [
        ("← →", "Previous / next photo (in a compare, the group moves along)"),
        ("P / X / U", "Pick / reject / unflag"),
        ("0-5 / 6-9", "Stars / colour label"),
        ("A", "Auto-advance on or off"),
        ("C", "Compare: 2, 3, 4 photos side by side, then back to 1"),
        ("Tab", "In a compare: the next photo is the one the keys act on"),
        ("Z, wheel, drag", "Zoom and move - every photo in the compare together"),
        ("Ctrl+Z / Ctrl+Y", "Undo / redo the last star, label or flag"),
        ("?", "These keys"),
        ("Esc", "Back to the library"),
    ],
    "Video": [
        ("Space or K", "Play / pause"),
        ("J / L", "5 seconds back / on"),
        ("I / O", "Mark where a trimmed copy starts / ends"),
        ("Left / Right", "Previous / next photo or video"),
    ],
    "Library": [
        ("Enter", "Open the selected photo"),
        ("Ctrl+A / Esc", "Select all / select none"),
        ("Space", "This photo in or out of the selection"),
        ("Menu key / Shift+F10", "The Photo menu for the selection"),
        ("Ctrl+Z / Ctrl+Shift+Z", "Undo / redo stars, tags, albums, archive, stacks, edits"),
        ("Ctrl+click, Shift+click", "Add a photo to the selection / select a range"),
        ("Arrows, Page Up/Down, Home/End", "Move through the grid (with Shift: extend the selection)"),
        ("Ctrl+mouse wheel", "Change the thumbnail size"),
        ("S", "Open or close a burst stack"),
    ],
    "Photo view": [
        ("Left / Right, Space", "Previous / next photo"),
        ("Tilt the mouse wheel", "Previous / next photo"),
        ("Home / End", "First / last photo"),
        ("Z, double-click", "Fit to the window / 100 %"),
        ("Mouse wheel", "Zoom around the pointer (Settings > General can make it change photo)"),
        ("Middle-button drag", "Pan the photo"),
        ("E", "Open or close the Edit panel"),
        ("F", "Show or hide the faces and their names (click a face to name or correct it; Ctrl+drag adds one)"),
        ("Esc, Backspace", "Back to the library"),
    ],
    "Edit panel": [
        ("\\ (backslash)", "Show the original"),
        ("Ctrl+Z / Ctrl+Shift+Z, Ctrl+Y", "Undo / redo"),
        ("R", "Start or finish cropping (Enter or Esc finishes too)"),
        ("O", "Show or hide the selected mask"),
        ("Alt while painting", "Erase a brush mask"),
        ("Double-click a slider", "Put it back to 0"),
        ("Esc", "Deselect the mask, then leave editing"),
    ],
    "Everywhere": [
        ("?", "This sheet"),
        ("Ctrl+B", "Sidebar: icons only"),
        ("F5", "Rescan every folder"),
    ],
}


def menu_shortcuts(window) -> list[tuple[str, str]]:
    seen, out = set(), []
    for a in window.findChildren(QAction):
        keys = a.shortcut().toString()
        if keys and keys not in seen:
            seen.add(keys)
            text = a.text().replace("&&", "\0").replace("&", "").replace("\0", "&").rstrip("…").strip()
            out.append((keys, text))
    return sorted(out, key=lambda r: (len(r[0]) > 1, r[0]))


class ShortcutSheet(QDialog):
    def __init__(self, window, screen: str = "Library") -> None:
        super().__init__(window)
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(640, 640)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"On this screen: <b>{screen}</b>. Menu commands work everywhere."))
        groups = ("Library", "Photo view", "Video", "Edit panel", "Culling (Photo > Cull full screen, Ctrl+K)")
        order = [g for g in groups if g == screen or g.startswith(screen + " ")] + \
            [g for g in groups if not (g == screen or g.startswith(screen + " "))]
        rows: list[tuple[str, str, str]] = []
        for group in order:
            rows += [(group, k, d) for k, d in KEYS.get(group, ())]
        rows += [("Menus", k, d) for k, d in menu_shortcuts(window)]
        rows += [("Everywhere", k, d) for k, d in KEYS["Everywhere"]]
        self.table = QTableWidget(len(rows), 3)
        self.table.setHorizontalHeaderLabels(["Where", "Keys", "What they do"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for i, row in enumerate(rows):
            for c, text in enumerate(row):
                it = QTableWidgetItem(text)
                if c == 0 and text == screen:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(i, c, it)
        v.addWidget(self.table, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        v.addWidget(box)
        self.rows = rows
