"""The smart album editor: a name, all / any, and a row per rule."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from lunelis.albums import smart


class RuleRow(QWidget):
    def __init__(self, rule: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.field = QComboBox()
        for key, f in smart.FIELDS.items():
            self.field.addItem(f.title, key)
        self.op = QComboBox()
        self.num = QDoubleSpinBox(minimum=0, maximum=10_000_000, decimals=2)
        self.text = QLineEdit()
        self.choice = QComboBox()
        self.remove = QPushButton("Remove")
        self.remove.setFlat(True)
        for w in (self.field, self.op, self.num, self.text, self.choice):
            h.addWidget(w)
        h.addWidget(self.remove)
        h.setStretch(3, 1)
        self.field.currentIndexChanged.connect(self._field_changed)
        if rule:
            self.field.setCurrentIndex(max(0, self.field.findData(rule.get("field"))))
        self._field_changed()
        if rule:
            self.op.setCurrentIndex(max(0, self.op.findText(rule.get("op", ""))))
            self._set_value(rule.get("value"))

    def _field_changed(self) -> None:
        f = smart.FIELDS[self.field.currentData()]
        self.op.clear()
        self.op.addItems(list(smart.OPS[f.kind]))
        self.num.setVisible(f.kind == "num")
        self.choice.setVisible(f.kind == "enum")
        self.text.setVisible(f.kind in ("text", "date", "tag", "search"))
        self.text.setPlaceholderText({"date": "2026-06-19", "tag": "Trips > Vegas", "search": "sunset beach",
                                      "text": "e.g. 24-70"}.get(f.kind, ""))
        if f.kind == "enum":
            self.choice.clear()
            self.choice.addItems(list(f.choices))
        if f.kind == "num":
            self.num.setDecimals(0 if self.field.currentData() in ("stars", "iso") else 2)
            self.num.setMaximum(5 if self.field.currentData() == "stars" else 10_000_000)

    def _set_value(self, v) -> None:
        f = smart.FIELDS[self.field.currentData()]
        if f.kind == "num":
            self.num.setValue(float(v or 0))
        elif f.kind == "enum":
            self.choice.setCurrentIndex(max(0, self.choice.findText(str(v))))
        else:
            self.text.setText(str(v or ""))

    def rule(self) -> dict:
        f = smart.FIELDS[self.field.currentData()]
        value = (self.num.value() if f.kind == "num" else self.choice.currentText() if f.kind == "enum"
                 else self.text.text().strip())
        return {"field": self.field.currentData(), "op": self.op.currentText(), "value": value}


class SmartAlbumDialog(QDialog):
    """Make or change a smart album. `result_value` = (name, rules dict)."""

    def __init__(self, name: str = "", rules: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Smart album")
        self.setMinimumWidth(640)
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Name:"))
        self.name = QLineEdit(name, placeholderText="e.g. Low light keepers")
        top.addWidget(self.name, 1)
        v.addLayout(top)
        match = QHBoxLayout()
        match.addWidget(QLabel("Photos that match"))
        self.match = QComboBox()
        self.match.addItem("all of these", "all")
        self.match.addItem("any of these", "any")
        match.addWidget(self.match)
        match.addStretch(1)
        v.addLayout(match)
        self.rows_box = QVBoxLayout()
        v.addLayout(self.rows_box)
        add = QPushButton("Add a rule", clicked=lambda: self.add_row())
        v.addWidget(add)
        help_ = QLabel("A smart album keeps itself up to date: a photo that starts to match shows up by itself.",
                       objectName="Help")
        help_.setWordWrap(True)
        v.addWidget(help_)
        self.error = QLabel(objectName="Error")
        self.error.setWordWrap(True)
        v.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self.rows: list[RuleRow] = []
        rules = rules or {"match": "all", "rules": [{"field": "stars", "op": ">=", "value": 4}]}
        self.match.setCurrentIndex(max(0, self.match.findData(rules.get("match", "all"))))
        for r in rules.get("rules", []):
            self.add_row(r)
        self.result_value = None

    def add_row(self, rule: dict | None = None) -> RuleRow:
        row = RuleRow(rule)
        row.remove.clicked.connect(lambda: self._remove(row))
        self.rows.append(row)
        self.rows_box.addWidget(row)
        return row

    def _remove(self, row: RuleRow) -> None:
        if len(self.rows) > 1:
            self.rows.remove(row)
            row.deleteLater()

    def value(self) -> dict:
        return {"match": self.match.currentData(), "rules": [r.rule() for r in self.rows]}

    def _accept(self) -> None:
        rules = self.value()
        try:
            smart.check(rules)
            if not self.name.text().strip():
                raise smart.RuleError("Give the smart album a name.")
        except smart.RuleError as e:
            self.error.setText(str(e))
            return
        self.result_value = (self.name.text().strip(), rules)
        self.accept()
