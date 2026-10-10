"""
XMP sidecars: which one belongs to a photo, and reading/writing its rating
and colour label without disturbing anything else in it.

The library already holds sidecars from three writers (measured 2026-09-27):
darktable (`name.ARW.xmp`, full edit history), a culling tool
(`name.xmp`, rating + label only) and Adobe Camera Raw (`name.xmp`, develop
settings). Lunelis must round-trip all of them, so edits are SURGICAL TEXT
REPLACEMENTS on the Rating/Label values - never parse-and-reserialize, which
would rename darktable's namespace prefixes and drop the xpacket wrapper.
Every result is checked to still be well-formed XML before it replaces the
file, atomically.

Conventions:
- Reject = xmp:Rating -1 (darktable and Adobe agree). Stars 0-5 otherwise.
- Colour labels are Adobe names (Red/Yellow/Green/Blue/Purple) in xmp:Label;
  darktable's own darktable:colorlabels list is kept in step when present.
- Pick flags have no standard XMP field: they stay in the catalog only.
- Tags are dc:subject (a flat bag: every level of every tag) and
  lr:hierarchicalSubject (a bag of "Places|Ohio|Cleveland" paths) - what
  darktable and Lightroom read. darktable's own automatic tags
  ("darktable|format|arw" and its flat words) are kept when we rewrite the
  bags and never read back as tags.
- An edit is lunelis:EditStack (edit/stack.py's text form) in our own
  namespace. Adobe's crs: develop settings are deliberately NOT written: the
  library holds real Camera Raw sidecars, and overwriting their settings
  with our approximations would destroy someone else's edit.
"""
from __future__ import annotations

import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from lunelis.importers.formats import RAW_EXTS, ext_of

LABELS = ("Red", "Yellow", "Green", "Blue", "Purple")        # darktable index order
NS_XMP = "http://ns.adobe.com/xap/1.0/"
NS_LUNELIS = "https://github.com/AxialForge/Lunelis/ns/1.0/"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_LR = "http://ns.adobe.com/lightroom/1.0/"

NEW_SIDECAR = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Lunelis">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"/>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""


class SidecarError(Exception):
    """The sidecar isn't something we can safely edit (left untouched)."""


@dataclass(frozen=True)
class XmpFields:
    stars: int = 0                  # 0-5
    rejected: bool = False
    label: str | None = None        # one of LABELS
    edit: str | None = None         # lunelis:EditStack, None = unedited
    keywords: tuple = ()            # tag paths, "Places|Ohio"; darktable's own tags excluded
    # False only for a sidecar that was READ and has no xmp:Rating at all: it
    # has no opinion about stars, which is not the same as "0 stars". Left out
    # of comparisons, so two sets of fields are equal when what they'd write is (0.54).
    has_rating: bool = field(default=True, compare=False)


# --- which sidecar ----------------------------------------------------------

def choose_sidecar(filename: str, dir_names_lower: set[str], raw_stems_lower: set[str]) -> str | None:
    """The existing sidecar that belongs to `filename`, or None.

    `name.EXT.xmp` is unambiguous and wins. `name.xmp` is Adobe's convention
    and belongs to the RAW when a RAW with that stem sits in the same folder
    (a RAW+JPG pair shares it), so a JPG only claims it when there's no RAW.
    """
    full = f"{filename}.xmp"
    if full.lower() in dir_names_lower:
        return full
    stem = filename.rsplit(".", 1)[0]
    plain = f"{stem}.xmp"
    if plain.lower() in dir_names_lower:
        if ext_of(filename) in RAW_EXTS or stem.lower() not in raw_stems_lower:
            return plain
    return None


def default_sidecar(filename: str) -> str:
    """Name for a NEW sidecar: darktable's `name.EXT.xmp`, which is what the
    user's editor writes and can't collide between a RAW and its JPG."""
    return f"{filename}.xmp"


# --- reading ----------------------------------------------------------------

_DESC = re.compile(r"<rdf:Description\b[^>]*?/?>", re.S)


def _attr(tag: str, name: str) -> re.Match | None:
    return re.search(rf'(\s){re.escape(name)}=(["\'])(.*?)\2', tag, re.S)


def _elem(text: str, name: str) -> re.Match | None:
    return re.search(rf"<{re.escape(name)}>(.*?)</{re.escape(name)}>", text, re.S)


def _find_attr(text: str, name: str) -> tuple[re.Match, re.Match] | None:
    """(Description tag match, attribute match) for `name` on ANY
    rdf:Description - Adobe tools sometimes split properties across several."""
    for m in _DESC.finditer(text):
        a = _attr(m.group(0), name)
        if a:
            return m, a
    return None


def parse_fields(text: str) -> XmpFields:
    if not _DESC.search(text):
        return XmpFields()

    def value(name: str) -> str | None:
        found = _find_attr(text, name)
        if found:
            return found[1].group(3).strip()
        e = _elem(text, name)
        return e.group(1).strip() if e else None

    stars, rejected = 0, False
    raw = value("xmp:Rating")
    try:
        r = int(float(raw)) if raw is not None else 0
    except ValueError:
        r = 0
    if r < 0:
        rejected = True
    else:
        stars = min(5, r)

    label = None
    lab = value("xmp:Label")
    if lab:
        label = next((L for L in LABELS if L.lower() == lab.lower()), None)
    if label is None:
        dt = _elem(text, "darktable:colorlabels")
        if dt:
            idx = re.findall(r"<rdf:li>\s*(\d)\s*</rdf:li>", dt.group(1))
            if idx and int(idx[0]) < len(LABELS):
                label = LABELS[int(idx[0])]
    edit = value("lunelis:EditStack")
    return XmpFields(stars, rejected, label, _unescape(edit) if edit else None, _keywords(text),
                     has_rating=raw is not None)


def _bag_items(text: str, name: str) -> list[str] | None:
    e = _elem(text, name)
    if e is None:
        return None
    return [_unescape(v.strip()) for v in re.findall(r"<rdf:li[^>]*>(.*?)</rdf:li>", e.group(1), re.S)]


def _darktable_words(paths: list[str]) -> set[str]:
    return {w.lower() for p in paths if p.lower().startswith("darktable|") for w in p.split("|")}


def _keywords(text: str) -> tuple:
    hier = _bag_items(text, "lr:hierarchicalSubject") or []
    flat = _bag_items(text, "dc:subject") or []
    dt = _darktable_words(hier)
    out: list[str] = []
    for p in hier:
        if not p.lower().startswith("darktable|") and p.lower() not in {o.lower() for o in out}:
            out.append(p)
    covered = {w.lower() for p in out for w in p.split("|")}
    for w in flat:                                   # flat-only keywords (other tools)
        if w.lower() not in covered and w.lower() not in dt and w.lower() not in {o.lower() for o in out}:
            out.append(w)
    return tuple(sorted(out, key=str.lower))


def _escape(v: str) -> str:
    return v.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _unescape(v: str) -> str:
    return v.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def read_sidecar(path: str) -> XmpFields:
    with open(path, "rb") as fh:
        return parse_fields(fh.read().decode("utf-8", "replace"))


# --- writing ----------------------------------------------------------------

def _set_attr(text: str, name: str, value: str | None) -> str:
    """Set/replace/remove attribute `name` on the first rdf:Description, or its
    element form if that's how this file stores it."""
    found = _find_attr(text, name)
    if found:
        m, a = found
        tag = m.group(0)
        if value is None:
            new_tag = tag[: a.start()] + tag[a.end():]
        else:
            new_tag = tag[: a.start(3)] + value + tag[a.end(3):]
        return text[: m.start()] + new_tag + text[m.end():]
    e = _elem(text, name)
    if e:
        if value is None:
            # drop the element and the whitespace/newline before it
            start = e.start()
            while start > 0 and text[start - 1] in " \t":
                start -= 1
            if start > 0 and text[start - 1] == "\n":
                start -= 1
            return text[:start] + text[e.end():]
        return text[: e.start(1)] + value + text[e.end(1):]
    if value is None:
        return text
    # Insert as an attribute on the Description that declares the xmp
    # namespace (else the first), matching the indentation of existing ones.
    if "xmlns:xmp=" not in text:
        raise SidecarError("xmp namespace not declared")
    descs = list(_DESC.finditer(text))
    if not descs:
        raise SidecarError("no rdf:Description")
    m = next((d for d in descs if "xmlns:xmp=" in d.group(0)), descs[0])
    tag = m.group(0)
    close = len(tag) - (2 if tag.endswith("/>") else 1)
    indent = re.search(r"\n([ \t]+)\S+=", tag)
    sep = "\n" + indent.group(1) if indent else " "
    new_tag = tag[:close].rstrip() + f'{sep}{name}="{value}"' + tag[close:]
    return text[: m.start()] + new_tag + text[m.end():]


def _has_rating(text: str) -> bool:
    return _find_attr(text, "xmp:Rating") is not None or _elem(text, "xmp:Rating") is not None


def _ensure_xmp_ns(text: str) -> str:
    if "xmlns:xmp=" in text:
        return text
    m = _DESC.search(text)
    if not m:
        raise SidecarError("no rdf:Description")
    tag = m.group(0)
    new_tag = tag.replace("<rdf:Description", f'<rdf:Description xmlns:xmp="{NS_XMP}"', 1)
    return text[: m.start()] + new_tag + text[m.end():]


def _set_bag(text: str, name: str, items: list[str], ns_prefix: str, ns_uri: str) -> str:
    """Replace (or add, or with no items remove) a bag element."""
    e = _elem(text, name)
    if items:
        lis = "".join(f"\n     <rdf:li>{_escape(i)}</rdf:li>" for i in items)
        block = f"<{name}>\n    <rdf:Bag>{lis}\n    </rdf:Bag>\n   </{name}>"
    if e is not None:
        if not items:
            start = e.start()
            while start > 0 and text[start - 1] in " \t":
                start -= 1
            if start > 0 and text[start - 1] == "\n":
                start -= 1
            return text[:start] + text[e.end():]
        return text[:e.start()] + block + text[e.end():]
    if not items:
        return text
    descs = list(_DESC.finditer(text))
    if not descs:
        raise SidecarError("no rdf:Description")
    m = next((d for d in descs if "xmlns:xmp=" in d.group(0)), descs[0])
    tag = m.group(0)
    if f"xmlns:{ns_prefix}=" not in text:
        tag = tag.replace("<rdf:Description", f'<rdf:Description xmlns:{ns_prefix}="{ns_uri}"', 1)
    if tag.endswith("/>"):
        # A self-closing Description has no room for elements: open it up.
        new = tag[:-2].rstrip() + ">\n   " + block + "\n  </rdf:Description>"
        return text[:m.start()] + new + text[m.end():]
    close = text.find("</rdf:Description>", m.end())
    if close < 0:
        raise SidecarError("rdf:Description isn't closed")
    body_end = close
    while body_end > m.end() and text[body_end - 1] in " \t":
        body_end -= 1
    return text[:m.start()] + tag + text[m.end():body_end] + "   " + block + "\n  " + text[close:]


def set_keywords(text: str, keywords) -> str:
    """Write tags as dc:subject + lr:hierarchicalSubject, keeping darktable's
    own automatic tags."""
    hier_now = _bag_items(text, "lr:hierarchicalSubject") or []
    keep_hier = [p for p in hier_now if p.lower().startswith("darktable|")]
    dt = _darktable_words(keep_hier)
    keep_flat = [w for w in (_bag_items(text, "dc:subject") or []) if w.lower() in dt]
    paths = list(keywords)
    flat: list[str] = []
    for p in paths:
        for w in p.split("|"):
            if w.lower() not in {f.lower() for f in flat}:
                flat.append(w)
    text = _set_bag(text, "lr:hierarchicalSubject", keep_hier + paths, "lr", NS_LR)
    return _set_bag(text, "dc:subject", keep_flat + [w for w in flat if w.lower() not in dt], "dc", NS_DC)


def _ensure_lunelis_ns(text: str) -> str:
    # Declared on the Description that carries xmp: (where _set_attr inserts).
    if "xmlns:lunelis=" in text:
        return text
    descs = list(_DESC.finditer(text))
    if not descs:
        raise SidecarError("no rdf:Description")
    m = next((d for d in descs if "xmlns:xmp=" in d.group(0)), descs[0])
    tag = m.group(0).replace("<rdf:Description", f'<rdf:Description xmlns:lunelis="{NS_LUNELIS}"', 1)
    return text[: m.start()] + tag + text[m.end():]


def _set_darktable_labels(text: str, label: str | None) -> str:
    e = _elem(text, "darktable:colorlabels")
    if not e:
        return text                               # not a darktable file: leave it alone
    inner = e.group(1)
    seq = re.search(r"<rdf:Seq\s*/>|<rdf:Seq>.*?</rdf:Seq>", inner, re.S)
    if label is None:
        new_seq = "<rdf:Seq/>"
    else:
        new_seq = f"<rdf:Seq>\n     <rdf:li>{LABELS.index(label)}</rdf:li>\n    </rdf:Seq>"
    new_inner = inner[: seq.start()] + new_seq + inner[seq.end():] if seq else new_seq
    return text[: e.start(1)] + new_inner + text[e.end(1):]


def apply_fields(text: str, fields: XmpFields) -> str:
    """Only fields whose value differs are touched: an unchanged label never
    rewrites darktable's label list (which can hold several labels - we read
    only the first), and a no-op write leaves the text identical."""
    cur = parse_fields(text) if _DESC.search(text) else XmpFields()
    out = text
    if (cur.stars, cur.rejected) != (fields.stars, fields.rejected) or not _has_rating(text):
        out = _ensure_xmp_ns(out)
        out = _set_attr(out, "xmp:Rating", "-1" if fields.rejected else str(fields.stars))
    if cur.label != fields.label:
        out = _ensure_xmp_ns(out)
        out = _set_attr(out, "xmp:Label", fields.label)
        out = _set_darktable_labels(out, fields.label)
    if tuple(sorted(cur.keywords, key=str.lower)) != tuple(sorted(fields.keywords, key=str.lower)):
        out = _ensure_xmp_ns(out)
        out = set_keywords(out, sorted(fields.keywords, key=str.lower))
    if cur.edit != fields.edit:
        out = _ensure_xmp_ns(out)
        if fields.edit is not None:
            out = _ensure_lunelis_ns(out)
        out = _set_attr(out, "lunelis:EditStack", _escape(fields.edit) if fields.edit else None)
    try:
        ET.fromstring(re.sub(r"<\?xpacket[^>]*\?>", "", out).strip())
    except ET.ParseError as e:
        raise SidecarError(f"edit would leave invalid XML: {e}") from e
    return out


def write_sidecar(path: str, fields: XmpFields) -> None:
    """Create or update `path` so it carries `fields`; everything else in the
    file is preserved byte for byte. Atomic: a crash leaves the old file."""
    if os.path.exists(path):
        with open(path, "rb") as fh:
            raw = fh.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise SidecarError("not UTF-8") from e
    else:
        text = NEW_SIDECAR
    new = apply_fields(text, fields)
    if os.path.exists(path) and new == text:
        return
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".lunelis-", suffix=".xmp.tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new.encode("utf-8"))
            # On the disk before it takes the old file's place: without this a
            # power cut just after the rename could leave an empty .xmp where
            # a full one had been (0.54).
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
