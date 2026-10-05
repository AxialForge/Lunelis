"""
The People page (sidebar > Photos > People): everyone Lunelis has found,
and the place to put its mistakes right.

- People: each named person with how many photos they're in and how many
  faces wait for a yes. Open one to see all their faces: confirm the
  suggested ones, take out wrong ones ("Not Ann" / Move to... / Not a face),
  rename, merge (rename to an existing name) or forget the person.
- To confirm: every "Ann?" suggestion, person by person.
- Unnamed: groups of alike faces nobody has named yet - name a whole group
  at once, or pull out faces that don't belong.
- Strangers & not faces: people you don't know (their photos are tagged
  People|Unknown - for crowds and public places) and faces marked "not a
  face"; bring one back if that was wrong.

A named face tags its photo People|<name>; taking it out removes the tag
again (recognize/faces.py keeps the two in step). Double-click a face to
open its photo.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from lunelis.recognize import faces
from lunelis.ui.background import Background, unless_closed

FACE = 112
LIMIT = 600                 # faces shown in one list; the rest after the first are dealt with


def _face_item(f: faces.Face, text: str | None = None) -> QListWidgetItem:
    label = text if text is not None else (f.name or (f"{f.suggested}?" if f.suggested else ""))
    it = QListWidgetItem(label)
    it.setData(Qt.ItemDataRole.UserRole, f.id)
    it.setData(Qt.ItemDataRole.UserRole + 1, f.file_id)
    it.setSizeHint(QSize(FACE + 18, FACE + 34))
    it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    return it


class FaceList(QListWidget):
    """A grid of face crops (loaded a few at a time, so long lists open at once)."""
    open_photo = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(FACE, FACE))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setWordWrap(True)
        self.setSpacing(4)
        self.itemDoubleClicked.connect(lambda it: self.open_photo.emit(it.data(Qt.ItemDataRole.UserRole + 1)))
        self._pending: list[QListWidgetItem] = []
        self._timer = QTimer(self, interval=0, timeout=self._load)

    def fill(self, items: list[QListWidgetItem]) -> None:
        self.clear()
        self._pending = []
        for it in items:
            self.addItem(it)
            self._pending.append(it)
        self._timer.start()

    def _load(self) -> None:
        for _ in range(30):
            if not self._pending:
                self._timer.stop()
                return
            it = self._pending.pop(0)
            try:
                fid = it.data(Qt.ItemDataRole.UserRole)
            except RuntimeError:                       # the list was refilled meanwhile
                continue
            pix = QPixmap(str(faces.crop_path(fid)))
            if pix.isNull():
                pix = QPixmap(FACE, FACE)
                pix.fill(QColor(128, 128, 128, 60))
            it.setIcon(QIcon(pix.scaled(FACE, FACE, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)))

    def chosen(self) -> list[int]:
        return [it.data(Qt.ItemDataRole.UserRole) for it in self.selectedItems()]

    def all_ids(self) -> list[int]:
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]


class PeopleView(QWidget):
    show_ids = Signal(list, str)        # a person's photos in the library
    open_photo = Signal(int)
    find_faces = Signal()               # start the faces job
    open_settings = Signal()
    changed = Signal()                  # names / tags changed (the Tags page and filters refresh)

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.bg = Background(self, conn)
        self.person: int | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 16)
        head = QHBoxLayout()
        title = QLabel("People", objectName="PageTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.find_b = QPushButton("Find faces in the library…", clicked=self.find_faces.emit)
        self.settings_b = QPushButton("Turn on faces…", objectName="Primary", clicked=self.open_settings.emit)
        head.addWidget(self.find_b)
        head.addWidget(self.settings_b)
        v.addLayout(head)
        self.summary = QLabel(objectName="Help")
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        v.addWidget(self.tabs, 1)

        # People: a grid of people, or one person's faces.
        self.people_stack = QStackedWidget()
        self.people_list = QListWidget()
        self.people_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.people_list.setIconSize(QSize(FACE, FACE))
        self.people_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.people_list.setMovement(QListWidget.Movement.Static)
        self.people_list.setWordWrap(True)
        self.people_list.setSpacing(6)
        self.people_list.itemActivated.connect(lambda it: self.open_person(it.data(Qt.ItemDataRole.UserRole)))
        self.people_list.itemDoubleClicked.connect(lambda it: self.open_person(it.data(Qt.ItemDataRole.UserRole)))
        self.people_stack.addWidget(self.people_list)
        self.people_stack.addWidget(self._person_page())
        self.tabs.addTab(self.people_stack, "People")
        self.tabs.addTab(self._confirm_page(), "To confirm")
        self.tabs.addTab(self._groups_page(), "Unnamed")
        self.tabs.addTab(self._ignored_page(), "Strangers & not faces")

    # --- pages ---------------------------------------------------------------------------------

    def _person_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        top = QHBoxLayout()
        top.addWidget(QPushButton("‹ Everyone", clicked=self.back_to_people))
        self.person_name = QLabel(objectName="SectionTitle")
        top.addWidget(self.person_name)
        top.addStretch(1)
        top.addWidget(QPushButton("Show photos", objectName="Primary", clicked=self._show_person_photos))
        top.addWidget(QPushButton("Rename…", clicked=self._rename))
        top.addWidget(QPushButton("Forget this person…", clicked=self._forget))
        v.addLayout(top)
        self.person_waiting_label = QLabel(objectName="SubTitle")
        v.addWidget(self.person_waiting_label)
        self.person_waiting = FaceList()
        self.person_waiting.setMaximumHeight(FACE + 60)
        self.person_waiting.open_photo.connect(self.open_photo)
        v.addWidget(self.person_waiting)
        row = QHBoxLayout()
        row.addWidget(QPushButton("Yes, it's them", objectName="Primary",
                                  clicked=lambda: self._confirm(self.person_waiting.chosen())))
        row.addWidget(QPushButton("Yes to all", clicked=lambda: self._confirm(self.person_waiting.all_ids())))
        row.addWidget(QPushButton("No", clicked=lambda: self._reject(self.person_waiting.chosen())))
        row.addStretch(1)
        v.addLayout(row)
        self.person_faces_label = QLabel(objectName="SubTitle")
        v.addWidget(self.person_faces_label)
        self.person_faces = FaceList()
        self.person_faces.open_photo.connect(self.open_photo)
        self.person_faces.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.person_faces.customContextMenuRequested.connect(lambda _: self._named_menu())
        v.addWidget(self.person_faces, 1)
        row = QHBoxLayout()
        self.not_b = QPushButton("Not this person", clicked=lambda: self._reject(self.person_faces.chosen()))
        row.addWidget(self.not_b)
        row.addWidget(QPushButton("Move to…", clicked=lambda: self._move(self.person_faces.chosen())))
        row.addWidget(QPushButton("Stranger", clicked=lambda: self._stranger(self.person_faces.chosen())))
        row.addWidget(QPushButton("Not a face", clicked=lambda: self._ignore(self.person_faces.chosen())))
        row.addWidget(QPushButton("Use as cover", clicked=self._cover))
        row.addStretch(1)
        row.addWidget(QLabel("Select faces, then choose. Double-click opens the photo.", objectName="Help"))
        v.addLayout(row)
        return w

    def _confirm_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.addWidget(QLabel("Faces Lunelis thinks it knows. Say yes and the photo gets the person's tag; say no "
                           "and that person isn't suggested for the face again.", objectName="Help", wordWrap=True))
        split = QSplitter()
        self.confirm_people = QListWidget()
        self.confirm_people.setMinimumWidth(200)
        self.confirm_people.currentItemChanged.connect(lambda *_: self._load_confirm_faces())
        split.addWidget(self.confirm_people)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 0, 0, 0)
        self.confirm_faces = FaceList()
        self.confirm_faces.open_photo.connect(self.open_photo)
        rv.addWidget(self.confirm_faces, 1)
        row = QHBoxLayout()
        row.addWidget(QPushButton("Yes", objectName="Primary",
                                  clicked=lambda: self._confirm(self.confirm_faces.chosen(), self._confirm_pid())))
        row.addWidget(QPushButton("Yes to all shown",
                                  clicked=lambda: self._confirm(self.confirm_faces.all_ids(), self._confirm_pid())))
        row.addWidget(QPushButton("No", clicked=lambda: self._reject(self.confirm_faces.chosen())))
        row.addWidget(QPushButton("Someone else…", clicked=lambda: self._move(self.confirm_faces.chosen())))
        row.addWidget(QPushButton("Stranger", clicked=lambda: self._stranger(self.confirm_faces.chosen())))
        row.addWidget(QPushButton("Not a face", clicked=lambda: self._ignore(self.confirm_faces.chosen())))
        row.addStretch(1)
        rv.addLayout(row)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        v.addWidget(split, 1)
        return w

    def _groups_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.addWidget(QLabel("Faces that look alike, not named yet. Name a group and every face in it is named at "
                           "once; take out the ones that don't belong first.", objectName="Help", wordWrap=True))
        split = QSplitter()
        self.group_list = QListWidget()
        self.group_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.group_list.setIconSize(QSize(72, 72))
        self.group_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.group_list.setMovement(QListWidget.Movement.Static)
        self.group_list.setMinimumWidth(240)
        self.group_list.currentItemChanged.connect(lambda *_: self._load_group_faces())
        split.addWidget(self.group_list)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 0, 0, 0)
        self.group_faces = FaceList()
        self.group_faces.open_photo.connect(self.open_photo)
        rv.addWidget(self.group_faces, 1)
        row = QHBoxLayout()
        row.addWidget(QPushButton("Name this person…", objectName="Primary", clicked=self._name_group))
        row.addWidget(QPushButton("Name selected…", clicked=lambda: self._move(self.group_faces.chosen())))
        row.addWidget(QPushButton("Not in this group", clicked=self._ungroup))
        row.addWidget(QPushButton("Strangers", clicked=self._group_strangers))
        row.addWidget(QPushButton("Not a face", clicked=lambda: self._ignore(self.group_faces.chosen())))
        row.addStretch(1)
        rv.addLayout(row)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        v.addWidget(split, 1)
        return w

    def _ignored_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.addWidget(QLabel("Strangers - people you don't know, in a crowd or a public place - put a "
                           "People > Unknown tag on their photos. Faces marked \"not a face\" are left out of "
                           "everything. Neither is suggested or grouped.", objectName="Help", wordWrap=True))
        self.ignored = FaceList()
        self.ignored.open_photo.connect(self.open_photo)
        v.addWidget(self.ignored, 1)
        row = QHBoxLayout()
        row.addWidget(QPushButton("Bring back", clicked=lambda: self._unignore(self.ignored.chosen())))
        row.addWidget(QPushButton("Stranger", clicked=lambda: self._stranger(self.ignored.chosen())))
        row.addWidget(QPushButton("Not a face", clicked=lambda: self._ignore(self.ignored.chosen())))
        row.addWidget(QPushButton("Name…", clicked=lambda: self._move(self.ignored.chosen())))
        row.addStretch(1)
        v.addLayout(row)
        return w

    # --- loading --------------------------------------------------------------------------

    def refresh(self) -> None:
        self.bg.run("counts", lambda c: (faces.counts(c), faces.available()), self._show_counts)
        tab = self.tabs.currentIndex()
        if tab == 0:
            if self.person is not None and self.people_stack.currentIndex() == 1:
                self._load_person()
            else:
                self.bg.run("people", lambda c: faces.people(c), self._show_people)
        elif tab == 1:
            self._load_confirm_people()
        elif tab == 2:
            self.bg.run("groups", lambda c: faces.groups(c, 2), self._show_groups)
        else:
            self.ignored.fill([_face_item(f, "Stranger" if f.stranger else "Not a face")
                               for f in faces.ignored_faces(self.conn, LIMIT)])

    @unless_closed
    def _show_counts(self, result) -> None:
        c, have = result
        self.settings_b.setVisible(not have)
        self.find_b.setVisible(have)
        if not have and not c["faces"]:
            self.summary.setText("Faces are off. Turn them on to download two small models (about 39 MB) that "
                                 "find and recognise faces on this PC - nothing is sent anywhere.")
            return
        self.summary.setText(f"{c['people']:,} people · {c['named']:,} named faces · {c['waiting']:,} waiting for "
                             f"a yes · {c['faces']:,} faces in {c['scanned']:,} photos looked at")
        self.tabs.setTabText(1, f"To confirm ({c['waiting']:,})" if c["waiting"] else "To confirm")

    @unless_closed
    def _show_people(self, people) -> None:
        self.people_list.clear()
        for p in people:
            text = f"{p.name}\n{p.photos:,} photo{'s' if p.photos != 1 else ''}"
            if p.waiting:
                text += f" · {p.waiting:,}?"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            it.setSizeHint(QSize(FACE + 30, FACE + 50))
            it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            pix = QPixmap(str(faces.crop_path(p.cover_face_id))) if p.cover_face_id else QPixmap()
            if not pix.isNull():
                it.setIcon(QIcon(pix.scaled(FACE, FACE, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)))
            self.people_list.addItem(it)
        if not people:
            it = QListWidgetItem("Nobody named yet - open Unnamed to name the faces Lunelis has grouped.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.people_list.addItem(it)

    def open_person(self, pid) -> None:
        if pid is None:
            return
        self.person = pid
        self.tabs.setCurrentIndex(0)
        self.people_stack.setCurrentIndex(1)
        self._load_person()

    def back_to_people(self) -> None:
        self.person = None
        self.people_stack.setCurrentIndex(0)
        self.refresh()

    def _load_person(self) -> None:
        pid = self.person
        name = faces.name_of(self.conn, pid) if pid is not None else None
        if name is None:
            self.back_to_people()
            return
        self.person_name.setText(name)
        self.not_b.setText(f"Not {name}")
        waiting = faces.suggested_for(self.conn, pid, LIMIT)
        named = faces.faces_of_person(self.conn, pid, LIMIT)
        self.person_waiting_label.setText(f"Is this {name}? ({len(waiting):,})" if waiting else
                                          "No faces waiting for a yes")
        self.person_waiting.setVisible(bool(waiting))
        self.person_waiting.fill([_face_item(f, f"{(f.suggestion or 0) * 100:.0f} %") for f in waiting])
        self.person_faces_label.setText(f"{name}'s faces ({len(named):,})")
        self.person_faces.fill([_face_item(f, "") for f in named])

    def _load_confirm_people(self) -> None:
        keep = self._confirm_pid()
        self.confirm_people.blockSignals(True)
        self.confirm_people.clear()
        for p in faces.people(self.conn):
            if not p.waiting:
                continue
            it = QListWidgetItem(f"{p.name}   ({p.waiting:,})")
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            self.confirm_people.addItem(it)
            if p.id == keep:
                self.confirm_people.setCurrentItem(it)
        self.confirm_people.blockSignals(False)
        if self.confirm_people.currentItem() is None and self.confirm_people.count():
            self.confirm_people.setCurrentRow(0)
        self._load_confirm_faces()

    def _confirm_pid(self) -> int | None:
        it = self.confirm_people.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _load_confirm_faces(self) -> None:
        pid = self._confirm_pid()
        self.confirm_faces.fill([] if pid is None else [
            _face_item(f, f"{f.suggested}? {(f.suggestion or 0) * 100:.0f} %")
            for f in faces.suggested_for(self.conn, pid, LIMIT)])

    @unless_closed
    def _show_groups(self, groups) -> None:
        keep = self._group()
        self.group_list.blockSignals(True)
        self.group_list.clear()
        for cluster, n, first in groups:
            it = QListWidgetItem(f"{n:,} faces")
            it.setData(Qt.ItemDataRole.UserRole, cluster)
            it.setSizeHint(QSize(96, 104))
            pix = QPixmap(str(faces.crop_path(first)))
            if not pix.isNull():
                it.setIcon(QIcon(pix.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)))
            self.group_list.addItem(it)
            if cluster == keep:
                self.group_list.setCurrentItem(it)
        self.group_list.blockSignals(False)
        self.tabs.setTabText(2, f"Unnamed ({len(groups):,})" if groups else "Unnamed")
        if self.group_list.currentItem() is None and self.group_list.count():
            self.group_list.setCurrentRow(0)
        self._load_group_faces()

    def _group(self) -> int | None:
        it = self.group_list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _load_group_faces(self) -> None:
        c = self._group()
        self.group_faces.fill([] if c is None else [_face_item(f, "") for f in faces.faces_in_group(self.conn, c, LIMIT)])

    # --- actions ----------------------------------------------------------------------------

    def ask_name(self, title: str = "Name", current: str = "") -> str | None:
        names = [p.name for p in faces.people(self.conn)]
        name, ok = QInputDialog.getItem(self, title, "Who is this? Pick someone or type a new name:",
                                        names if names else [""], names.index(current) if current in names else 0,
                                        True)
        name = (name or "").strip()
        if ok and name.casefold() == "unknown":
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Strangers", "\"Unknown\" is kept for strangers: choose Stranger "
                                    "instead, and the photo is tagged People > Unknown.")
            return None
        return name if ok and name else None

    def _done(self, message: str | None = None) -> None:
        self.changed.emit()
        self.refresh()
        if message:
            self.summary.setText(message)

    def _confirm(self, ids: list[int], pid: int | None = None) -> None:
        pid = pid if pid is not None else self.person
        if not ids or pid is None:
            return
        faces.confirm(self.conn, ids, pid)
        self._done()

    def _reject(self, ids: list[int]) -> None:
        if ids:
            faces.reject(self.conn, ids)
            self._done()

    def _ignore(self, ids: list[int]) -> None:
        if ids:
            faces.ignore(self.conn, ids)
            self._done()

    def _stranger(self, ids: list[int]) -> None:
        if ids:
            faces.mark_strangers(self.conn, ids)
            self._done()

    def _group_strangers(self) -> None:
        """The whole group (or the selected faces) are people you don't know."""
        ids = self.group_faces.chosen() or self.group_faces.all_ids()
        if ids:
            faces.mark_strangers(self.conn, ids)
            self._done(f"{len(ids):,} faces marked as strangers - their photos are tagged People > Unknown.")

    def _unignore(self, ids: list[int]) -> None:
        if ids:
            faces.ignore(self.conn, ids, ignored=False)
            self._done()

    def _move(self, ids: list[int]) -> None:
        if not ids:
            return
        name = self.ask_name("Who is this?")
        if name:
            faces.name_faces(self.conn, ids, name)
            self._done()

    def _name_group(self) -> None:
        c = self._group()
        if c is None:
            return
        name = self.ask_name("Name this person")
        if name:
            faces.name_group(self.conn, c, name)
            self._done(f"Named {name}.")

    def _ungroup(self) -> None:
        ids = self.group_faces.chosen()
        if ids:
            faces.ungroup(self.conn, ids)
            self._done()

    def _cover(self) -> None:
        ids = self.person_faces.chosen()
        if ids and self.person is not None:
            self.conn.execute("UPDATE people SET cover_face_id = ? WHERE id = ?", (ids[0], self.person))
            self.conn.commit()
            self.summary.setText("Cover face changed.")

    def _named_menu(self) -> None:
        ids = self.person_faces.chosen()
        if not ids:
            return
        m = QMenu(self)
        m.addAction(self.not_b.text(), lambda: self._reject(ids))
        m.addAction("Move to…", lambda: self._move(ids))
        m.addAction("Stranger", lambda: self._stranger(ids))
        m.addAction("Not a face", lambda: self._ignore(ids))
        m.addAction("Use as cover", self._cover)
        m.exec(self.cursor().pos())

    def _show_person_photos(self) -> None:
        if self.person is not None:
            self.show_ids.emit(faces.photos_of(self.conn, self.person), faces.name_of(self.conn, self.person) or "")

    def _rename(self) -> None:
        if self.person is None:
            return
        old = faces.name_of(self.conn, self.person)
        new, ok = QInputDialog.getText(self, "Rename", "New name (an existing name merges the two):", text=old)
        if ok and new.strip() and new.strip() != old:
            self.person = faces.rename_person(self.conn, self.person, new)
            self._done()

    def _forget(self) -> None:
        if self.person is None:
            return
        name = faces.name_of(self.conn, self.person)
        if QMessageBox.question(self, "Forget this person",
                                f"Forget {name}? Their faces become unnamed again and the People|{name} tag is "
                                "removed from their photos. The photos themselves don't change.") \
                != QMessageBox.StandardButton.Yes:
            return
        faces.delete_person(self.conn, self.person)
        self.back_to_people()
        self.changed.emit()
