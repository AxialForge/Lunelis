"""
Settings > Edit > Lens profiles: add or remove lensfun profiles for lenses
the built-in database doesn't know, and a guide to where to find them.
Added files are copies in <data folder>/lens profiles; removing one sends
that copy to the Recycle Bin.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QFile, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTextBrowser, QVBoxLayout,
)

from lunelis.edit import lens

GUIDE = """
<h3>Lens profiles - where to find them</h3>
<p>Lunelis corrects distortion, colour fringing and vignetting with <b>lensfun</b>, a free database of
over 1,300 lenses that is built in. Add a profile only when <i>Develop &gt; Lens &gt; Use the lens
profile</i> says there's none for your lens.</p>
<h4>1. Look for your lens</h4>
<ul>
<li><b>The lens list</b> - check whether lensfun knows it: lensfun.github.io/lenslist</li>
<li><b>The version 1 download</b> - every profile, in the format Lunelis reads:
lensfun.github.io/db/version_1.tar.bz2 (unpack it with Unpacker or 7-Zip). One file per maker
and kind: <code>slr-sony.xml</code>, <code>mil-sony.xml</code>, <code>compact-canon.xml</code>...</li>
<li><b>lensfun on GitHub</b> - github.com/lensfun/lensfun, folder <code>data/db</code>: the newest
profiles, but in version 2 (see below).</li>
</ul>
<h4>2. The format</h4>
<ul>
<li>An <b>.xml</b> file starting <code>&lt;lensdatabase version="1"&gt;</code> (or with no version).
Files that say <code>version="2"</code> can't be read - take the same maker's file from the
version 1 download.</li>
<li>Each lens needs: <code>&lt;maker&gt;</code>, <code>&lt;model&gt;</code> (as your camera writes
it - see Info &gt; Lens), <code>&lt;mount&gt;</code>, <code>&lt;cropfactor&gt;</code>, and a
<code>&lt;calibration&gt;</code> with <code>distortion</code>, <code>tca</code> and/or
<code>vignetting</code> lines per focal length (and per aperture for vignetting).</li>
<li>The camera body has to be known to lensfun too (almost all are).</li>
</ul>
<h4>3. No profile anywhere?</h4>
<ul>
<li>Adobe profiles (<code>.lcp</code>, from Adobe's free Lens Profile Creator) can be converted with
lensfun's <code>lensfun-convert-lcp</code> tool, then added here.</li>
<li>Or make one: lensfun's calibration tutorial (lensfun.github.io/calibration) uses Hugin, free.</li>
<li>Or use the manual sliders in Develop &gt; Lens.</li>
</ul>
<h4>4. Add it</h4>
<p>Click <b>Add profile...</b> and pick the .xml file. Lunelis checks it, copies it into its own
folder and lists the lenses it found. Then open a photo from that lens: Develop &gt; Lens shows the
profile's name when it matches.</p>
"""


class GuideDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Where to find lens profiles")
        self.resize(640, 620)
        v = QVBoxLayout(self)
        t = QTextBrowser()
        t.setHtml(GUIDE)
        v.addWidget(t, 1)
        b = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        b.rejected.connect(self.reject)
        v.addWidget(b)


def add_profile(path: str) -> tuple[list[str], str | None]:
    """Check one file and copy it in: (lens names, why it wasn't added or None)."""
    names, why = lens.check_profile(path)
    if why:
        return [], why
    d = lens.profile_dir()
    d.mkdir(parents=True, exist_ok=True)
    target = d / Path(path).name
    if target.exists():
        return [], f"A profile called {target.name} is already added - remove it first, or rename the new file."
    shutil.copy2(path, target)
    lens.reload()
    return names, None


def build(card_maker, parent):
    """The card, made with SettingsView._card."""
    card, v = card_maker("Lens profiles",
                         "lensfun's database of 1,300+ lenses is built in. Add a profile here for a lens it "
                         "doesn't know - Develop > Lens > Use the lens profile then finds it.")
    box = QListWidget()
    box.setMinimumHeight(90)
    v.addWidget(box)
    status = QLabel(objectName="Help", wordWrap=True)
    v.addWidget(status)
    row = QHBoxLayout()

    def refresh() -> None:
        box.clear()
        files = lens.user_profiles()
        for p in files:
            names, why = lens.check_profile(p)
            more = f" and {len(names) - 4} more" if len(names) > 4 else ""
            it = QListWidgetItem(f"{p.name} - " + (why if why else ", ".join(names[:4]) + more))
            it.setData(Qt.ItemDataRole.UserRole, str(p))
            box.addItem(it)
        status.setText(f"{len(files)} profile file{'s' if len(files) != 1 else ''} added." if files
                       else "No profiles added - only the built-in database is used.")

    def add() -> None:
        path, _ = QFileDialog.getOpenFileName(parent, "Add a lens profile", "", "lensfun profile (*.xml)")
        if not path:
            return
        names, why = add_profile(path)
        if why:
            QMessageBox.warning(parent, "Lens profiles", f"This file wasn't added: {why}")
            return
        refresh()
        status.setText(f"Added {len(names)} lens{'es' if len(names) != 1 else ''}: {', '.join(names[:6])}")

    def remove() -> None:
        it = box.currentItem()
        if it is None:
            return
        name = Path(it.data(Qt.ItemDataRole.UserRole)).name
        if QMessageBox.question(parent, "Lens profiles", f"Remove {name}? Lunelis's copy goes to the Recycle "
                                "Bin; the file you added it from isn't touched.") != QMessageBox.StandardButton.Yes:
            return
        QFile.moveToTrash(it.data(Qt.ItemDataRole.UserRole))
        lens.reload()
        refresh()

    def open_folder() -> None:
        d = lens.profile_dir()
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    for text, fn in (("Add profile...", add), ("Remove", remove), ("Open folder", open_folder),
                     ("Where to find profiles...", lambda: GuideDialog(parent).exec())):
        row.addWidget(QPushButton(text, clicked=fn))
    row.addStretch(1)
    v.addLayout(row)
    refresh()
    return card
