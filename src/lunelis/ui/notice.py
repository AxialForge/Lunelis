"""
A result message that stays put (0.49): "Accepted 12", "Placed 40 photos" were
overwritten a moment later by the page's own reload of its counts.

    self.note = Notice()
    self.note.say("Placed 40 photos at Rome")      # after an action
    label.setText(self.note.prefix() + counts)     # wherever the counts are written
"""
from __future__ import annotations

import time

HOLD_S = 6.0


class Notice:
    def __init__(self) -> None:
        self.text, self.until = "", 0.0

    def say(self, text: str, hold_s: float = HOLD_S) -> str:
        self.text, self.until = text, time.monotonic() + hold_s
        return text

    def current(self) -> str:
        return self.text if time.monotonic() < self.until else ""

    def prefix(self) -> str:
        t = self.current()
        return f"{t}  ·  " if t else ""
