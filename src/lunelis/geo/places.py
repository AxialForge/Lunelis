"""
Places: a place tag for every photo with a location, worked out on this PC.

The built-in list (places.tsv.gz, ~34,000 towns and cities of 15,000+
people from GeoNames, CC BY 4.0 - see ATTRIBUTION.txt) is searched for the
nearest place; nothing is looked up online. A photo gets

    Places|Italy|Lazio|Rome          a named place within CITY_KM
    Places|Italy|Unknown             in a country, but no town nearby (a trail, a beach)
    Places|Unknown                   far from anywhere on the list (at sea)
    Places|No location               no location at all - only with Settings
                                     places_tag_no_location on (off by default)

A location is the photo's own GPS, or a pin you dropped on the Map
(`locations`, which wins - it's what you said). `photo_places` remembers the
tag Lunelis gave and from which coordinates, so a moved pin swaps the tag
and a place tag you removed by hand isn't put back until the location changes.
"""
from __future__ import annotations

import gzip
import math
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from lunelis.tags import model as tags

ROOT = "Places"
UNKNOWN = "Unknown"
NO_LOCATION = "No location"
DATA = Path(__file__).with_name("places.tsv.gz")
CITY_KM = 40.0
SIZE_KM = 5.0
COUNTRY_KM = 250.0
EARTH_KM = 6371.0
LIVE = "f.missing_since IS NULL AND f.excluded = 0 AND f.quarantined_at IS NULL"


@dataclass(frozen=True)
class Place:
    country: str | None
    region: str | None
    city: str | None
    km: float

    @property
    def tag(self) -> str:
        parts = [ROOT]
        if self.country is None:
            parts.append(UNKNOWN)
        else:
            parts.append(self.country)
            if self.city is None:
                parts.append(UNKNOWN)
            else:
                if self.region:
                    parts.append(self.region)
                parts.append(self.city)
        return tags.SEP.join(_clean(p) for p in parts)

    @property
    def label(self) -> str:
        """For people: "Rome, Lazio, Italy" / "Somewhere in Italy" / "Unknown place"."""
        if self.country is None:
            return "Unknown place"
        if self.city is None:
            return f"Somewhere in {self.country}"
        return ", ".join(p for p in (self.city, self.region, self.country) if p)


def _clean(text: str) -> str:
    return " ".join(text.replace(tags.SEP, " ").split())


@lru_cache(maxsize=1)
def _table():
    lats, lons, names, pops = [], [], [], []
    with gzip.open(DATA, "rt", encoding="utf-8") as fh:
        for line in fh:
            la, lo, city, region, country, pop = line.rstrip("\n").split("\t")
            lats.append(float(la))
            lons.append(float(lo))
            names.append((city, region or None, country))
            pops.append(int(pop or 0))
    la, lo = np.radians(np.array(lats)), np.radians(np.array(lons))
    xyz = np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=1).astype(np.float64)
    # A city ten times bigger may be SIZE_KM further away and still be the one named.
    bonus = SIZE_KM * np.log10(np.maximum(np.array(pops, dtype=np.float64), 15000.0) / 15000.0)
    return xyz, names, bonus


def _xyz(lat: float, lon: float) -> np.ndarray:
    la, lo = math.radians(lat), math.radians(lon)
    return np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])


def lookup(lat: float, lon: float) -> Place:
    return lookup_many([(lat, lon)])[0]


def lookup_many(points: list[tuple[float, float]]) -> list[Place]:
    if not points:
        return []
    xyz, names, bonus = _table()
    out = []
    for start in range(0, len(points), 512):
        chunk = points[start:start + 512]
        q = np.stack([_xyz(a, b) for a, b in chunk])
        km_all = np.arccos(np.clip(q @ xyz.T, -1.0, 1.0)) * EARTH_KM
        for row in range(len(chunk)):
            d = km_all[row]
            near = np.flatnonzero(d <= CITY_KM)
            # Nearby places compete on distance less a bonus for size: the city, not its suburb.
            i = int(near[np.argmin(d[near] - bonus[near])]) if len(near) else int(np.argmin(d))
            km = float(d[i])
            city, region, country = names[i]
            if km <= CITY_KM:
                out.append(Place(country, region, city, km))
            elif km <= COUNTRY_KM:
                out.append(Place(country, None, None, km))
            else:
                out.append(Place(None, None, None, km))
    return out


# --- locations ----------------------------------------------------------------------------------

def location_of(conn: sqlite3.Connection, file_id: int) -> tuple[float, float, str] | None:
    """(lat, lon, "pin" | "gps") or None."""
    row = conn.execute("SELECT lat, lon FROM locations WHERE file_id = ?", (file_id,)).fetchone()
    if row:
        return row[0], row[1], "pin"
    row = conn.execute("SELECT gps_lat, gps_lon FROM exif WHERE file_id = ? AND gps_lat IS NOT NULL"
                       " AND gps_lon IS NOT NULL AND NOT (gps_lat = 0 AND gps_lon = 0)", (file_id,)).fetchone()
    return (row[0], row[1], "gps") if row else None


LOCATED_SQL = (
    "SELECT f.id, COALESCE(l.lat, e.gps_lat), COALESCE(l.lon, e.gps_lon) FROM files f"
    " JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
    " LEFT JOIN locations l ON l.file_id = f.id"
    f" WHERE r.enabled = 1 AND {LIVE} AND (l.file_id IS NOT NULL OR (e.gps_lat IS NOT NULL"
    " AND e.gps_lon IS NOT NULL AND NOT (e.gps_lat = 0 AND e.gps_lon = 0)))")


def located(conn: sqlite3.Connection) -> list[tuple[int, float, float]]:
    """Every photo with a location (a pin wins over the photo's GPS), for the Map."""
    return [tuple(r) for r in conn.execute(LOCATED_SQL)]


def without_location(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT f.id FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
        " LEFT JOIN locations l ON l.file_id = f.id"
        f" WHERE r.enabled = 1 AND {LIVE} AND l.file_id IS NULL AND (e.gps_lat IS NULL OR e.gps_lon IS NULL"
        " OR (e.gps_lat = 0 AND e.gps_lon = 0)) ORDER BY f.id")]


def set_location(conn: sqlite3.Connection, file_ids, lat: float, lon: float) -> int:
    """Pin photos to a spot (their files aren't changed) and retag them."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("That isn't a place on Earth.")
    ids = list(file_ids)
    conn.executemany("INSERT INTO locations (file_id, lat, lon, set_at) VALUES (?, ?, ?, datetime('now'))"
                     " ON CONFLICT(file_id) DO UPDATE SET lat = excluded.lat, lon = excluded.lon,"
                     " set_at = excluded.set_at", [(f, round(lat, 6), round(lon, 6)) for f in ids])
    conn.commit()
    tag_files(conn, ids)
    return len(ids)


def clear_location(conn: sqlite3.Connection, file_ids) -> int:
    """Take the pins away (the photo's own GPS, if any, counts again)."""
    ids = list(file_ids)
    before = conn.total_changes
    conn.executemany("DELETE FROM locations WHERE file_id = ?", [(f,) for f in ids])
    n = conn.total_changes - before
    conn.commit()
    tag_files(conn, ids)
    return n


def pinned(conn: sqlite3.Connection, file_ids) -> set[int]:
    ids = list(file_ids)
    out: set[int] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        out |= {r[0] for r in conn.execute(
            f"SELECT file_id FROM locations WHERE file_id IN ({','.join('?' * len(chunk))})", chunk)}
    return out


# --- place tags --------------------------------------------------------------------------------------

def _want(conn: sqlite3.Connection, file_ids: list[int], no_location: bool) -> dict[int, tuple]:
    """{file id: (tag or None, lat, lon)} - the place tag each photo should have now."""
    coords: dict[int, tuple[float, float]] = {}
    for i in range(0, len(file_ids), 500):
        chunk = file_ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for fid, la, lo in conn.execute(
                f"SELECT f.id, COALESCE(l.lat, e.gps_lat), COALESCE(l.lon, e.gps_lon) FROM files f"
                f" LEFT JOIN exif e ON e.file_id = f.id LEFT JOIN locations l ON l.file_id = f.id"
                f" WHERE f.id IN ({q})", chunk):
            if la is not None and lo is not None and not (la == 0 and lo == 0):
                coords[fid] = (la, lo)
    places = dict(zip(coords, lookup_many(list(coords.values()))))
    out = {}
    for fid in file_ids:
        if fid in coords:
            out[fid] = (places[fid].tag, *coords[fid])
        else:
            out[fid] = (tags.SEP.join((ROOT, NO_LOCATION)) if no_location else None, None, None)
    return out


def tag_files(conn: sqlite3.Connection, file_ids, should_cancel=None) -> int:
    """Bring these photos' place tags up to date. Returns how many changed."""
    from lunelis.settings import Settings
    no_location = bool(Settings(conn).get("places_tag_no_location"))
    ids = list(file_ids)
    changed = 0
    for i in range(0, len(ids), 500):
        if should_cancel and should_cancel():
            break
        chunk = ids[i:i + 500]
        want = _want(conn, chunk, no_location)
        q = ",".join("?" * len(chunk))
        had = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            f"SELECT file_id, place, lat, lon FROM photo_places WHERE file_id IN ({q})", chunk)}
        add: dict[str, list[int]] = {}
        for fid in chunk:
            tag, la, lo = want[fid]
            old = had.get(fid)
            if old is not None and old[0] == tag and old[1] == la and old[2] == lo:
                continue
            if old is not None and old[0] and old[0] != tag:
                tags.remove(conn, [fid], old[0], commit=False)
            if tag is None:
                conn.execute("DELETE FROM photo_places WHERE file_id = ?", (fid,))
            else:
                if old is None or old[0] != tag:
                    add.setdefault(tag, []).append(fid)
                conn.execute("INSERT INTO photo_places (file_id, place, lat, lon) VALUES (?, ?, ?, ?)"
                             " ON CONFLICT(file_id) DO UPDATE SET place = excluded.place, lat = excluded.lat,"
                             " lon = excluded.lon", (fid, tag, la, lo))
            changed += 1
        for tag, fids in add.items():
            tags.add(conn, fids, [tag], commit=False)
        conn.commit()
    return changed


def pending(conn: sqlite3.Connection) -> list[int]:
    """Photos whose place tag may be out of date: never done, or their location changed."""
    from lunelis.settings import Settings
    no_location = bool(Settings(conn).get("places_tag_no_location"))
    sql = (
        "SELECT f.id FROM files f JOIN roots r ON r.id = f.root_id LEFT JOIN exif e ON e.file_id = f.id"
        " LEFT JOIN locations l ON l.file_id = f.id LEFT JOIN photo_places pp ON pp.file_id = f.id"
        f" WHERE r.enabled = 1 AND {LIVE} AND ("
        "   (COALESCE(l.lat, e.gps_lat) IS NOT NULL AND NOT (COALESCE(l.lat, e.gps_lat) = 0"
        "     AND COALESCE(l.lon, e.gps_lon) = 0)"
        "     AND (pp.file_id IS NULL OR pp.lat IS NOT COALESCE(l.lat, e.gps_lat)"
        "          OR pp.lon IS NOT COALESCE(l.lon, e.gps_lon)))"
        "   OR (pp.file_id IS NOT NULL AND pp.lat IS NOT NULL AND COALESCE(l.lat, e.gps_lat) IS NULL)")
    if no_location:
        sql += ("   OR (pp.file_id IS NULL AND (COALESCE(l.lat, e.gps_lat) IS NULL"
                "       OR (COALESCE(l.lat, e.gps_lat) = 0 AND COALESCE(l.lon, e.gps_lon) = 0)))")
    else:
        sql += "   OR (pp.file_id IS NOT NULL AND pp.lat IS NULL)"
    return [r[0] for r in conn.execute(sql + ") ORDER BY f.id")]


def tag_pending(conn: sqlite3.Connection, should_cancel=None) -> int:
    return tag_files(conn, pending(conn), should_cancel)
