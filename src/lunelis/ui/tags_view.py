"""
The Tags page (sidebar > Photos > Tags): every tag with how many photos
carry it (including the tags inside it). Double-click a tag to see its
photos. Right-click to rename, merge, add a tag inside, or delete -
renaming onto an existing tag merges the two.

Two views (0.52): Cards - the groups (People, Pets, Scene, Places, your own
tags) on the left, each tag as a picture card on the right (a person's or a
pet's face, else the newest photo carrying it) - and List, the tree.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from lunelis.tags import model as tags
from lunelis.ui.background import Background
from lunelis.ui.widgets import plain


def _counts(conn):
    """Every tag, its photo count (a parent counts the distinct photos under it
    too), and how many photos have any tag. On a worker: see refresh."""
    rows = [tuple(r) for r in tags.all_tags(conn)]
    totals = {}
    for name, _n in rows:
        cond, params = tags.filter_sql(name)
        totals[name] = conn.execute(
            f"SELECT COUNT(*) FROM files f WHERE {cond} AND {tags.LIVE}", params).fetchone()[0]
    n_photos = conn.execute(f"SELECT COUNT(DISTINCT ft.file_id) FROM file_tags ft JOIN files f"
                            f" ON f.id = ft.file_id WHERE {tags.LIVE}").fetchone()[0]
    return rows, totals, n_photos, _covers(conn, [n for n, _ in rows])


CARD = 120                 # a tag card's picture, px


def _covers(conn, names: list[str]) -> dict[str, str]:
    """A picture per tag (0.52): the face of the person or pet a People| /
    Pets| tag names, else the newest photo carrying the tag."""
    from lunelis import paths
    from lunelis.recognize import faces
    out: dict[str, str] = {}
    for name in names:
        root, _, leaf = name.partition(tags.SEP)
        if leaf and root in (faces.ROOT, faces.PETS) and tags.SEP not in leaf:
            row = conn.execute("SELECT cover_face_id FROM people WHERE name = ? COLLATE NOCASE AND kind = ?",
                               (leaf, faces.PET if root == faces.PETS else faces.PERSON)).fetchone()
            if row and row[0] is not None and faces.crop_path(row[0]).exists():
                out[name] = str(faces.crop_path(row[0]))
                continue
        cond, params = tags.filter_sql(name)
        row = conn.execute(f"SELECT f.thumbnail_path FROM files f LEFT JOIN exif e ON e.file_id = f.id"
                           f" WHERE {cond} AND {tags.LIVE} AND f.thumbnail_path IS NOT NULL"
                           f" ORDER BY e.captured_at DESC LIMIT 1", params).fetchone()
        if row:
            out[name] = str(paths.THUMBNAIL_CACHE / row[0])
    return out


def _square(path: str | None, size: int) -> QIcon:
    pix = QPixmap(path) if path else QPixmap()
    if pix.isNull():
        return QIcon()
    pix = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation)
    x, y = (pix.width() - size) // 2, (pix.height() - size) // 2
    return QIcon(pix.copy(x, y, size, size))


class TagsView(QWidget):
    open_tag = Signal(str)

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.bg = Background(self, conn)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 16, 24, 16)
        top = QHBoxLayout()
        title = QLabel("Tags", objectName="SectionTitle")
        top.addWidget(title)
        self.summary = QLabel(objectName="Count")
        top.addWidget(self.summary, 1)
        self.search = QLineEdit(placeholderText="Find a tag…")
        self.search.setMaximumWidth(260)
        self.search.textChanged.connect(self._filter)
        top.addWidget(self.search)
        self.view_group = QButtonGroup(self)
        for i, label in enumerate(("Cards", "List")):
            b = QPushButton(label, checkable=True, objectName="Segment")
            self.view_group.addButton(b, i)
            top.addWidget(b)
        self.view_group.button(0).setChecked(True)
        self.view_group.idClicked.connect(lambda i: self.views.setCurrentIndex(i))
        self.new_b = QPushButton("New tag…", clicked=lambda: self.new_tag(None))
        top.addWidget(self.new_b)
        v.addLayout(top)
        hint = QLabel("Double-click a tag to see its photos. Tag photos in the library with Ctrl+T, or in the "
                      "photo view's Info panel. Right-click a tag to rename, merge or delete it.", objectName="Hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Tag", "Photos"])
        self.tree.setColumnWidth(0, 420)
        self.tree.itemActivated.connect(lambda item, _c: self.open_tag.emit(item.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        # Cards: the groups, and a picture card per tag.
        cards = QWidget()
        ch = QHBoxLayout(cards)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(12)
        self.groups = QListWidget(objectName="TagGroups")
        self.groups.setFixedWidth(200)
        self.groups.currentItemChanged.connect(lambda *_: self._fill_cards())
        ch.addWidget(self.groups)
        self.cards = QListWidget(objectName="TagCards")
        self.cards.setViewMode(QListWidget.ViewMode.IconMode)
        self.cards.setIconSize(QSize(CARD, CARD))
        self.cards.setGridSize(QSize(CARD + 40, CARD + 58))
        self.cards.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.cards.setMovement(QListWidget.Movement.Static)
        self.cards.setWordWrap(True)
        self.cards.setUniformItemSizes(True)
        self.cards.setSpacing(4)
        self.cards.itemActivated.connect(lambda it: self.open_tag.emit(it.data(Qt.ItemDataRole.UserRole)))
        self.cards.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cards.customContextMenuRequested.connect(self._card_menu)
        ch.addWidget(self.cards, 1)
        self.views = QStackedWidget()
        self.views.addWidget(cards)
        self.views.addWidget(self.tree)
        self._rows, self._totals, self._covers = [], {}, {}
        from PySide6.QtWidgets import QTabWidget
        from lunelis.ui.scene_review import SceneReview
        self.tabs = QTabWidget()
        from lunelis.ui.empty_state import EmptyStack
        self.tree_box = EmptyStack(self.views)
        self.tabs.addTab(self.tree_box, "Your tags")
        self.review = SceneReview(conn)
        self.review.changed.connect(self.refresh)
        self.tabs.addTab(self.review, "Scene suggestions")
        self.tabs.currentChanged.connect(lambda i: self.review.refresh() if i == 1 else None)
        v.addWidget(self.tabs, 1)

    def show_suggestions(self) -> None:
        self.tabs.setCurrentWidget(self.review)
        self.review.refresh()

    def refresh(self) -> None:
        # A count per tag over the whole library: on a worker.
        self.bg.run("tags", _counts, self._show,
                    error=lambda e: self.summary.setText(f"Couldn't count the tags: {e}"))

    def _show(self, counted) -> None:
        rows, totals, n_photos, *rest = counted
        self._rows, self._totals, self._covers = rows, totals, (rest[0] if rest else {})
        self._icons = {}                               # card pictures, kept until the tags are counted again
        self._fill_groups()
        direct = dict(rows)
        self.tree.clear()
        items: dict[str, QTreeWidgetItem] = {}
        for name, _n in rows:
            parent = name.rsplit(tags.SEP, 1)[0] if tags.SEP in name else None
            item = QTreeWidgetItem([tags.leaf(name), f"{totals[name]:,}"])
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight)
            (items[parent].addChild(item) if parent in items else self.tree.addTopLevelItem(item))
            items[name] = item
        self.tree.expandToDepth(0)
        self.tree_box.empty(None if direct else
                            "No tags yet.\n\nSelect photos in the library and press T (or Photo > Tags) to tag them. "
                            "Scene suggestions - beach, dog, sunset - are on the Suggestions tab once scene tagging "
                            "is on (Settings > Library).")
        self.summary.setText(f"{len(direct):,} tag{'s' if len(direct) != 1 else ''} on {n_photos:,} "
                             f"photo{'s' if n_photos != 1 else ''}" if direct else "No tags yet")
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        q = text.strip().lower()

        def walk(item: QTreeWidgetItem) -> bool:
            shown = any([walk(item.child(i)) for i in range(item.childCount())])
            me = not q or q in item.data(0, Qt.ItemDataRole.UserRole).lower()
            item.setHidden(not (me or shown))
            if q and shown:
                item.setExpanded(True)
            return me or shown
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        self._fill_cards()

    # --- cards -------------------------------------------------------------------------------

    GROUP_ORDER = ("People", "Pets", "Scene", "Places")
    ALL, YOURS = "\x00all", "\x00yours"

    def _fill_groups(self) -> None:
        names = [n for n, _ in self._rows]
        parents = {n.split(tags.SEP, 1)[0] for n in names if tags.SEP in n}
        keep = self.groups.currentItem().data(Qt.ItemDataRole.UserRole) if self.groups.currentItem() else self.ALL
        self.groups.blockSignals(True)
        self.groups.clear()
        roots = sorted(parents, key=lambda r: (self.GROUP_ORDER.index(r) if r in self.GROUP_ORDER else 99, r.lower()))
        loose = [n for n in names if tags.SEP not in n and n not in parents]
        entries = [(self.ALL, "All tags", len(names))] + [
            (r, r, sum(1 for n in names if n.startswith(r + tags.SEP))) for r in roots]
        if loose:
            entries.append((self.YOURS, "Your own tags", len(loose)))
        for key, label, n in entries:
            it = QListWidgetItem(f"{label}   {n:,}")
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.groups.addItem(it)
        self.groups.blockSignals(False)
        match = [i for i in range(self.groups.count()) if self.groups.item(i).data(Qt.ItemDataRole.UserRole) == keep]
        self.groups.setCurrentRow(match[0] if match else 0)

    def _card_names(self) -> list[str]:
        it = self.groups.currentItem()
        key = it.data(Qt.ItemDataRole.UserRole) if it else self.ALL
        names = [n for n, _ in self._rows]
        parents = {n.rsplit(tags.SEP, 1)[0] for n in names if tags.SEP in n}
        if key == self.ALL:
            out = [n for n in names if n not in parents]              # every tag that holds photos itself
        elif key == self.YOURS:
            out = [n for n in names if tags.SEP not in n and n not in parents]
        else:
            out = [n for n in names if n.startswith(key + tags.SEP)]
        q = self.search.text().strip().lower()
        return [n for n in out if not q or q in n.lower()]

    def _fill_cards(self) -> None:
        if not hasattr(self, "cards"):
            return
        self.cards.clear()
        it = self.groups.currentItem()
        key = it.data(Qt.ItemDataRole.UserRole) if it else self.ALL
        for name in self._card_names():
            shown = name.split(tags.SEP, 1)[1] if key not in (self.ALL, self.YOURS) and tags.SEP in name else name
            n = self._totals.get(name, 0)
            icons = self.__dict__.setdefault("_icons", {})
            if name not in icons:                      # one decode per tag, not one per keystroke in Find (0.53)
                icons[name] = _square(self._covers.get(name), CARD)
            card = QListWidgetItem(icons[name],
                                   f"{shown.replace(tags.SEP, ' › ')}\n{n:,} photo{'s' if n != 1 else ''}")
            card.setData(Qt.ItemDataRole.UserRole, name)
            card.setToolTip(name.replace(tags.SEP, " > "))
            card.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.cards.addItem(card)

    def _card_menu(self, pos) -> None:
        item = self.cards.itemAt(pos)
        if item is not None:
            self._menu_for(item.data(Qt.ItemDataRole.UserRole), self.cards.viewport().mapToGlobal(pos))

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        self._menu_for(item.data(0, Qt.ItemDataRole.UserRole), self.tree.viewport().mapToGlobal(pos))

    def _menu_for(self, name: str, at) -> None:
        m = QMenu(self)
        m.addAction("Show its photos", lambda: self.open_tag.emit(name))
        m.addSeparator()
        m.addAction("Rename…", lambda: self.rename(name))
        m.addAction("Merge into…", lambda: self.merge(name))
        m.addAction("New tag inside…", lambda: self.new_tag(name))
        m.addSeparator()
        m.addAction("Delete…", lambda: self.delete(name))
        m.exec(at)

    def _managed(self, name: str) -> bool:
        """People|… and Places|… tags are kept in step with the People page and
        the photos' locations: changing them here would put the two out of step."""
        root = name.split(tags.SEP, 1)[0]
        where = {"People": "Rename, merge or forget the person on the People page - the tags follow.",
                 "Pets": "Rename or forget the pet on the People page's Pets tab - the tags follow.",
                 "Places": "Place tags follow each photo's location - change a pin on the Map instead."}.get(root)
        if where:
            QMessageBox.information(self, "Tags", f"'{name.replace(tags.SEP, ' > ')}' is kept by Lunelis. {where}")
        return bool(where)

    def rename(self, name: str) -> None:
        if self._managed(name):
            return
        new, ok = QInputDialog.getText(self, "Rename tag", "New name (use > to nest):",
                                       text=name.replace(tags.SEP, " > "))
        if not ok:
            return
        target = tags.SEP.join(p.strip() for p in new.replace(" > ", tags.SEP).replace(">", tags.SEP).split(tags.SEP))
        if target.lower() != name.lower() and target.lower() in {n.lower() for n in tags.names(self.conn)} and \
                QMessageBox.question(self, "Merge tags?",
                                     f"'{new}' already exists - renaming merges the two, which can't be undone. "
                                     "Go ahead?") != QMessageBox.StandardButton.Yes:
            return
        try:
            tags.rename(self.conn, name, new)
        except ValueError as e:
            QMessageBox.warning(self, "Rename tag", plain(e))
        self.refresh()

    def merge(self, name: str) -> None:
        if self._managed(name):
            return
        others = [n for n in tags.names(self.conn) if n != name and not n.startswith(name + tags.SEP)]
        if not others:
            return
        into, ok = QInputDialog.getItem(self, "Merge tag", f"Merge '{tags.leaf(name)}' into:",
                                        [o.replace(tags.SEP, " > ") for o in others], 0, False)
        if ok and into:
            if QMessageBox.question(
                    self, "Merge tags?",
                    f"Every photo tagged '{tags.leaf(name)}' gets '{into}' instead, and '{tags.leaf(name)}' "
                    "goes away (sidecars too). This can't be undone.") != QMessageBox.StandardButton.Yes:
                return
            tags.merge(self.conn, name, into)
            self.refresh()

    def new_tag(self, parent: str | None) -> None:
        text, ok = QInputDialog.getText(self, "New tag", "Name:" if parent is None
                                        else f"Name (inside {tags.leaf(parent)}):")
        if ok and tags.normalize(text):
            tags.tag_id(self.conn, (parent + tags.SEP if parent else "") + tags.normalize(text))
            self.conn.commit()
            self.refresh()

    def delete(self, name: str) -> None:
        if self._managed(name):
            return
        cond, params = tags.filter_sql(name)
        n = self.conn.execute(f"SELECT COUNT(*) FROM files f WHERE {cond}", params).fetchone()[0]
        answer = QMessageBox.question(
            self, "Delete tag",
            f"Delete the tag '{tags.leaf(name)}' and every tag inside it?"
            + (f"\n\n{n:,} photo{'s' if n != 1 else ''} lose it. The photos themselves aren't touched." if n else ""))
        if answer == QMessageBox.StandardButton.Yes:
            tags.delete(self.conn, name)
            self.refresh()
