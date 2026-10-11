"""
What Lunelis recognised in the photo on screen (0.57): a panel to the left of
the photo with the people, the pets and animals and the scene suggestions
found in it, each answered where it is - yes / no on a suggested name, a name
for an unnamed face, a face or an animal added by hand.

And the dialog that asks for a name (NameDialog): the face itself, a box to
type in that narrows the list of people, and the answers that aren't a name
(a stranger, an animal, not a face) as buttons instead of a second menu.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from lunelis.recognize import faces
from lunelis.ui.widgets import dialog_heading, make_primary, section_label

CROP = 44          # a face in the panel
WIDTH = 280        # the panel


def crop_pixmap(face_id: int | None, edge: int) -> QPixmap | None:
    """The saved crop of a face, square, or None when there isn't one yet."""
    if face_id is None:
        return None
    try:
        p = faces.crop_path(face_id)
        if not p.exists():
            return None
        pm = QPixmap(str(p))
    except OSError:
        return None
    if pm.isNull():
        return None
    return pm.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation).copy(0, 0, edge, edge)


class NameDialog(QDialog):
    """Who is this? `choice` after exec(): "name" (with `name`), "stranger",
    "animal", "not_a_face" or None (cancelled). `kind`: faces.PERSON or faces.PET."""

    def __init__(self, conn, face_id: int | None = None, kind: str = faces.PERSON, suggested: str | None = None,
                 title: str | None = None, others: bool = True, parent=None) -> None:
        super().__init__(parent)
        pet = kind == faces.PET
        self.setWindowTitle(title or ("Name this pet" if pet else "Name this face"))
        self.setMinimumWidth(420)
        self.choice: str | None = None
        self.name = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(14)
        pm = crop_pixmap(face_id, 96)
        if pm is not None:
            pic = QLabel()
            pic.setPixmap(pm)
            pic.setFixedSize(96, 96)
            pic.setObjectName("FaceCrop")
            top.addWidget(pic, 0, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(6)
        dialog_heading(right, "Which pet is this?" if pet else "Who is this?")
        right.addWidget(QLabel("Type a name, or pick one below. A new name makes a new "
                               + ("pet." if pet else "person."), objectName="Help", wordWrap=True))
        self.box = QLineEdit(placeholderText="Name")
        self.box.setClearButtonEnabled(True)
        right.addWidget(self.box)
        top.addLayout(right, 1)
        v.addLayout(top)
        self.list = QListWidget()
        self.list.setIconSize(QSize(32, 32))
        self.list.setMinimumHeight(180)
        self.list.setUniformItemSizes(True)
        v.addWidget(self.list, 1)
        self._people = faces.people(conn, kind)
        # The suggested person first, then whoever has the most photos.
        self._people.sort(key=lambda p: (p.name != suggested, -p.photos, p.name.casefold()))
        self._fill("")
        self.new_hint = QLabel(objectName="Help")
        v.addWidget(self.new_hint)
        row = QHBoxLayout()
        alt = QHBoxLayout()
        alt.setSpacing(6)
        if others:
            alt.addWidget(QLabel("Or:", objectName="Help"))
        if others and not pet:
            for text, choice, tip in (
                    ("Stranger", "stranger", "Someone you don't know: the photo is tagged People > Unknown"),
                    ("It's an animal", "animal", "Not a person - a pet or another animal"),
                    ("Not a face", "not_a_face", "Lunelis was wrong: set this box aside")):
                b = QPushButton(text, clicked=lambda _=False, c=choice: self._done(c))
                b.setToolTip(tip)
                b.setAutoDefault(False)
                b.setObjectName("RowButton")
                alt.addWidget(b)
        elif others:
            b = QPushButton("An animal with no name", clicked=lambda: self._done("animal"))
            b.setAutoDefault(False)
            b.setObjectName("RowButton")
            alt.addWidget(b)
        alt.addStretch(1)
        if others:
            v.addLayout(alt)
        row.addStretch(1)
        cancel = QPushButton("Cancel", clicked=self.reject)
        cancel.setAutoDefault(False)
        self.ok_b = QPushButton("Name")
        make_primary(self.ok_b, "Name")
        self.ok_b.setDefault(True)
        self.ok_b.clicked.connect(self._accept_name)
        row.addWidget(cancel)
        row.addWidget(self.ok_b)
        v.addLayout(row)
        self.box.textChanged.connect(self._typed)
        self.list.itemClicked.connect(lambda it: self.box.setText(it.text()))
        self.list.itemDoubleClicked.connect(lambda it: (self.box.setText(it.text()), self._accept_name()))
        self.box.installEventFilter(self)
        self._prefilled = suggested or ""
        if suggested:
            self.box.setText(suggested)
            self.box.selectAll()
        self._typed(self.box.text())
        self.box.setFocus()

    def _fill(self, needle: str) -> None:
        self.list.clear()
        n = needle.casefold().strip()
        for p in self._people:
            if n and n not in p.name.casefold():
                continue
            it = QListWidgetItem(p.name)
            pm = crop_pixmap(p.cover_face_id, 32)
            if pm is not None:
                it.setIcon(QIcon(pm))
            it.setToolTip(f"{p.photos:,} photo{'s' if p.photos != 1 else ''}")
            self.list.addItem(it)

    def _typed(self, text: str) -> None:
        # The suggested name is only an offer: everyone stays listed until you type.
        self._fill("" if text == getattr(self, "_prefilled", "") else text)
        name = text.strip()
        known = {p.name.casefold() for p in self._people}
        exact = name.casefold() in known
        if self.list.count() and name:
            hit = next((i for i in range(self.list.count())
                        if self.list.item(i).text().casefold() == name.casefold()), 0)
            self.list.setCurrentRow(hit)
        self.ok_b.setEnabled(bool(name))
        self.ok_b.setText("Name" if exact or not name else f"Add “{name}”")
        self.new_hint.setText("" if exact or not name else
                              "Press Enter for a new name, or Down to pick one from the list."
                              if self.list.count() else "Nobody by that name yet - Enter adds them.")

    def eventFilter(self, obj, e) -> bool:
        # Down / Up from the box walk the list, so a name is picked without the mouse.
        if obj is self.box and e.type() == e.Type.KeyPress and e.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) \
                and self.list.count():
            step = 1 if e.key() == Qt.Key.Key_Down else -1
            row = max(0, min(self.list.count() - 1, self.list.currentRow() + step))
            self.list.setCurrentRow(row)
            self.box.blockSignals(True)
            self.box.setText(self.list.item(row).text())
            self.box.blockSignals(False)
            self.ok_b.setText("Name")
            self.ok_b.setEnabled(True)
            self.new_hint.setText("")
            return True
        return super().eventFilter(obj, e)

    def _accept_name(self) -> None:
        name = self.box.text().strip()
        if not name:
            return
        if name.casefold() == "unknown":
            self.new_hint.setText("“Unknown” is kept for strangers - press Stranger instead.")
            return
        self.name = name
        self._done("name")

    def _done(self, choice: str) -> None:
        self.choice = choice
        self.accept()


def ask_name(parent, conn, face_id: int | None = None, kind: str = faces.PERSON, suggested: str | None = None,
             title: str | None = None, others: bool = True) -> tuple[str | None, str]:
    """Show the dialog: (choice, name). The one place a name is asked for, so
    tests can answer it."""
    d = NameDialog(conn, face_id, kind, suggested, title, others, parent)
    if not d.exec() or d.choice is None:
        return None, ""
    return d.choice, d.name


def _small(text: str, tip: str, slot, name: str | None = None) -> QToolButton:
    b = QToolButton(text=text, toolTip=tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setAutoRaise(True)
    b.setFixedSize(26, 26)
    if name:
        b.setObjectName(name)
    b.clicked.connect(slot)
    return b


class RecognitionPanel(QWidget):
    """The left-hand panel of the photo view. The view calls show_photo();
    the panel answers through the catalog and emits `changed`."""
    changed = Signal()                      # a face or a scene tag changed: redraw the boxes, the Info panel
    add_face = Signal(str)                  # "person" | "pet": the next drag on the photo draws its box
    hover = Signal(object)                  # a face id (or None): the view highlights its box
    show_person = Signal(int)

    def __init__(self, conn, parent=None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.file_id: int | None = None
        self.setObjectName("RecognitionPanel")
        self.setFixedWidth(WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.body = QWidget()
        self.v = QVBoxLayout(self.body)
        self.v.setContentsMargins(10, 12, 8, 12)
        self.v.setSpacing(6)
        scroll.setWidget(self.body)
        outer.addWidget(scroll)
        self.status = QLabel(objectName="Help", wordWrap=True)

    # --- filling ---------------------------------------------------------------------------

    def _clear(self) -> None:
        while self.v.count():
            it = self.v.takeAt(0)
            w = it.widget()
            if w is not None and w is not self.status:
                # Hidden, then deleted by the event loop. (Not setParent(None): that
                # makes each row a window for a moment, and the photo view lost
                # the keyboard when those windows went.)
                w.hide()
                w.deleteLater()

    def show_photo(self, file_id: int | None, video: bool = False) -> None:
        self.file_id = file_id
        self.refresh(video)

    def refresh(self, video: bool = False) -> None:
        self._clear()
        self.rows: dict[int, QWidget] = {}
        if self.file_id is None or video:
            self.v.addWidget(QLabel("Faces and scenes are found in photos, not videos." if video else "",
                                    objectName="Help", wordWrap=True))
            self.v.addStretch(1)
            return
        found = faces.faces_of(self.conn, self.file_id, with_strangers=True)
        people = [f for f in found if not f.animal]
        animals = [f for f in found if f.animal]
        self.v.addWidget(section_label("People"))
        if not people:
            self.v.addWidget(QLabel("No faces found in this photo.", objectName="Help", wordWrap=True))
        for f in people:
            self.v.addWidget(self._face_row(f))
        self.v.addWidget(self._action("Add a face by hand…", "Drag a box around a face Lunelis missed",
                                      lambda: self.add_face.emit("person")))
        self.v.addSpacing(8)
        self.v.addWidget(section_label("Pets and animals"))
        if not animals:
            self.v.addWidget(QLabel("None found.", objectName="Help", wordWrap=True))
        for f in animals:
            self.v.addWidget(self._face_row(f))
        self.v.addWidget(self._action("Add a pet or animal by hand…", "Drag a box around it",
                                      lambda: self.add_face.emit("pet")))
        self.v.addSpacing(8)
        self.v.addWidget(section_label("Scene suggestions"))
        from lunelis.recognize import scenes
        waiting = scenes.suggestions_of(self.conn, self.file_id)
        if not waiting:
            self.v.addWidget(QLabel("Nothing waiting for an answer.", objectName="Help", wordWrap=True))
        for tag, conf in waiting:
            self.v.addWidget(self._scene_row(tag, conf))
        self.v.addSpacing(8)
        self.v.addWidget(self._action("Look for faces again", "Run face detection on this photo again; named "
                                      "and hand-drawn faces stay", self._look_again))
        self.v.addWidget(self.status)
        self.v.addStretch(1)
        # The panel never holds the keyboard: Left / Right still change photo
        # after a click here, and a row rebuilt under the focus can't pass it
        # on to the next button. Everything here is also in the Faces menu.
        from PySide6.QtWidgets import QAbstractButton
        for b in self.findChildren(QAbstractButton):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _action(self, text: str, tip: str, slot) -> QPushButton:
        b = QPushButton(text, clicked=slot)
        b.setToolTip(tip)
        b.setObjectName("PanelAction")
        return b

    def _face_row(self, f) -> QWidget:
        w = QFrame(objectName="RecognitionRow")
        w.setProperty("face", f.id)
        h = QHBoxLayout(w)
        h.setContentsMargins(4, 4, 2, 4)
        h.setSpacing(5)
        pic = QLabel()
        pic.setFixedSize(CROP, CROP)
        pm = crop_pixmap(f.id, CROP)
        if pm is not None:
            pic.setPixmap(pm)
        else:
            pic.setObjectName("FaceCropEmpty")
        h.addWidget(pic)
        text = QVBoxLayout()
        text.setSpacing(0)
        if f.stranger:
            title, sub = "Stranger", "Someone you don't know"
        elif f.name:
            title, sub = f.name, "Pet" if f.pet else "Named"
        elif f.animal:
            title, sub = "Animal", "No name"
        elif f.suggested:
            pct = f" · {round((f.suggestion or 0) * 100)} %" if f.suggestion else ""
            title, sub = f"{f.suggested}?", "Suggested" + pct
        else:
            title, sub = "Unnamed", "Who is this?"
        from lunelis.ui.widgets import ElidedLabel
        name = ElidedLabel(title)
        name.setObjectName("RowTitle")
        name.setToolTip(title)
        text.addWidget(name)
        text.addWidget(QLabel(sub, objectName="Help"))
        h.addLayout(text, 1)
        if f.suggested and not f.name and not f.stranger and not f.animal:
            h.addWidget(_small("✓", f"Yes, this is {f.suggested}", lambda _=False, x=f: self._yes(x), "Yes"))
            h.addWidget(_small("✕", f"No, this isn't {f.suggested}", lambda _=False, x=f: self._no(x), "No"))
        elif not f.name and not f.stranger:
            b = QPushButton("Name…", clicked=lambda _=False, x=f: self.name_face(x))
            b.setObjectName("RowButton")
            h.addWidget(b)
        more = _small("⋯", "More", lambda: None)
        more.clicked.disconnect()
        more.clicked.connect(lambda _=False, x=f, b=more: self._menu(x, b))
        h.addWidget(more)
        w.enterEvent = lambda e, i=f.id: self.hover.emit(i)
        w.leaveEvent = lambda e: self.hover.emit(None)
        self.rows[f.id] = w
        return w

    def _scene_row(self, tag: str, conf: float) -> QWidget:
        w = QFrame(objectName="RecognitionRow")
        h = QHBoxLayout(w)
        h.setContentsMargins(4, 2, 2, 2)
        h.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(0)
        leaf = tag.split(">")[-1].split("|")[-1].strip()
        name = QLabel(leaf, objectName="RowTitle")
        name.setToolTip(tag)
        text.addWidget(name)
        text.addWidget(QLabel(f"{round(conf * 100)} % sure", objectName="Help"))
        h.addLayout(text, 1)
        h.addWidget(_small("✓", f"Yes: tag this photo {leaf}", lambda _=False, t=tag: self._scene(t, True), "Yes"))
        h.addWidget(_small("✕", "No: not in this photo", lambda _=False, t=tag: self._scene(t, False), "No"))
        return w

    # --- answering -------------------------------------------------------------------------

    def _did(self, text: str = "") -> None:
        self.status.setText(text)
        self.refresh()
        self.changed.emit()

    def _yes(self, f) -> None:
        faces.confirm(self.conn, [f.id], f.suggested_id)
        self._did(f"Named {f.suggested}.")

    def _no(self, f) -> None:
        faces.reject(self.conn, [f.id])
        self._did(f"Not {f.suggested} - it won't be suggested for this face again.")

    def _scene(self, tag: str, yes: bool) -> None:
        from lunelis.recognize import scenes
        (scenes.accept if yes else scenes.reject)(self.conn, tag, [self.file_id])
        self._did("")

    def name_face(self, f, kind: str | None = None) -> None:
        """Ask who (or which pet) a face is, and store the answer."""
        kind = kind or (faces.PET if f.animal else faces.PERSON)
        choice, name = ask_name(self, self.conn, f.id, kind, f.suggested if kind == faces.PERSON else None)
        if choice is None:
            return

        class d:                                   # noqa: N801 - the answer, read below
            pass
        d.choice, d.name = choice, name
        try:
            if d.choice == "name":
                (faces.name_pet if kind == faces.PET else faces.name_faces)(self.conn, [f.id], d.name)
                self._did(f"Named {d.name}.")
            elif d.choice == "stranger":
                faces.mark_strangers(self.conn, [f.id])
                self._did("Marked as a stranger.")
            elif d.choice == "animal":
                faces.mark_animal(self.conn, [f.id])
                self._did("Marked as an animal.")
            elif d.choice == "not_a_face":
                faces.ignore(self.conn, [f.id])
                self._did("Set aside: not a face.")
        except ValueError as e:                    # a person's name given to a pet, or the other way round
            self.status.setText(str(e))

    def _menu(self, f, button) -> None:
        m = QMenu(self)
        if f.name or f.stranger or f.animal:
            m.addAction("Change the name…", lambda: self.name_face(f))
        if not f.animal:
            m.addAction("It's a pet or animal…", lambda: self.name_face(f, faces.PET))
            if not f.stranger:
                m.addAction("Stranger", lambda: (faces.mark_strangers(self.conn, [f.id]), self._did("")))
        else:
            m.addAction("It's a person…", lambda: self.name_face(f, faces.PERSON))
        if f.name and f.person_id is not None:
            m.addAction(f"All photos of {f.name}…", lambda: self.show_person.emit(f.person_id))
            m.addAction("Not " + f.name, lambda: (faces.reject(self.conn, [f.id]), self._did("")))
        m.addSeparator()
        m.addAction("Not a face", lambda: (faces.ignore(self.conn, [f.id]), self._did("Set aside: not a face.")))
        m.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _look_again(self) -> None:
        if self.file_id is None:
            return
        try:
            n = faces.look_again(self.conn, self.file_id)
        except RuntimeError as e:
            self.status.setText(str(e))
            return
        self._did(f"{n} face{'s' if n != 1 else ''} found." if n else "No faces found - add one by hand.")
