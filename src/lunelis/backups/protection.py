"""
Is a photo protected? - from what the backups hold (backup_files).

A photo is **protected** when at least one backup set holds a copy of it
with no problem recorded (a verify pass that found the copy bad sets
`problem`). The copy is "current" when it was made from the file as it is
now (same mtime); an older copy still protects the photo as it was.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

OK_COPY = "b.problem IS NULL"
PROTECTED_SQL = f"EXISTS (SELECT 1 FROM backup_files b WHERE b.file_id = f.id AND {OK_COPY})"


@dataclass
class Protection:
    copies: int = 0                  # good copies, one per backup set
    current: int = 0                 # of those, made from the file as it is now
    damaged: int = 0                 # copies a verify pass found bad
    checked: str | None = None       # the latest verify of a good copy
    sets: tuple[str, ...] = ()

    @property
    def protected(self) -> bool:
        return self.copies > 0

    def text(self) -> str:
        if not self.copies:
            return "Not backed up" + (f" - {self.damaged} backup copy is damaged" if self.damaged else "")
        out = f"Backed up · {self.copies} cop{'y' if self.copies == 1 else 'ies'} ({', '.join(self.sets)})"
        if self.current < self.copies:
            out += " · changed since the last backup"
        if self.checked:
            try:
                out += f" · checked {datetime.fromisoformat(self.checked):%b %d, %Y}".replace(" 0", " ")
            except ValueError:
                pass
        if self.damaged:
            out += f" · {self.damaged} damaged cop{'y' if self.damaged == 1 else 'ies'}"
        return out


def of(conn: sqlite3.Connection, file_id: int) -> Protection:
    rows = conn.execute(
        "SELECT s.name, b.problem, b.verified_at, b.mtime = f.mtime FROM backup_files b"
        " JOIN backup_sets s ON s.id = b.set_id JOIN files f ON f.id = b.file_id WHERE b.file_id = ?"
        " ORDER BY s.name", (file_id,)).fetchall()
    p = Protection()
    good = [r for r in rows if r[1] is None]
    p.copies, p.damaged = len(good), len(rows) - len(good)
    p.current = sum(1 for r in good if r[3])
    p.sets = tuple(r[0] for r in good)
    checks = [r[2] for r in good if r[2]]
    p.checked = max(checks) if checks else None
    return p


def unprotected_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM files f JOIN roots r ON r.id = f.root_id WHERE r.enabled = 1"
        " AND f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"
        f" AND NOT {PROTECTED_SQL}").fetchone()[0]
