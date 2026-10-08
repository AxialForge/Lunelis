"""
The Tags page (sidebar > Photos > Tags): every tag as a tree with how many
photos carry it (including the tags inside it). Double-click a tag to see
its photos. Right-click (or the buttons) to rename, merge, add a tag
inside, or delete - renaming onto an existing tag merges the two.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
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
    return rows, totals, n_photos


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
        from PySide6.QtWidgets import QTabWidget
        from lunelis.ui.scene_review import SceneReview
        self.tabs = QTabWidget()
        from lunelis.ui.empty_state import EmptyStack
        self.tree_box = EmptyStack(self.tree)
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
        rows, totals, n_photos = counted
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

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        name = item.data(0, Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        m.addAction("Show its photos", lambda: self.open_tag.emit(name))
        m.addSeparator()
        m.addAction("Rename…", lambda: self.rename(name))
        m.addAction("Merge into…", lambda: self.merge(name))
        m.addAction("New tag inside…", lambda: self.new_tag(name))
        m.addSeparator()
        m.addAction("Delete…", lambda: self.delete(name))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _managed(self, name: str) -> bool:
        """People|… and Places|… tags are kept in step with the People page and
        the photos' locations: changing them here would put the two out of step."""
        root = name.split(tags.SEP, 1)[0]
        where = {"People": "Rename, merge or forget the person on the People page - the tags follow.",
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
