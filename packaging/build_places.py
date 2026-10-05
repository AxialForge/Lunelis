"""
Build src/lunelis/geo/places.tsv.gz - the built-in place list for place tags -
from three GeoNames files (CC BY 4.0, https://www.geonames.org):

    https://download.geonames.org/export/dump/cities15000.zip   (cities of 15,000+ people)
    https://download.geonames.org/export/dump/admin1CodesASCII.txt  (states, regions)
    https://download.geonames.org/export/dump/countryInfo.txt   (country names)

    python packaging/build_places.py <folder with those three files>

Each line: latitude, longitude, city, region, country, population
(tab-separated, UTF-8), coordinates rounded to 4 decimals (~10 m). Parts of a
city (PPLX) and historical, abandoned or destroyed places are left out, so a
photo in Rome is tagged Rome. Only run when refreshing the list;
the result is committed so builds and tests never need the internet.
"""
from __future__ import annotations

import gzip
import io
import sys
import zipfile
from pathlib import Path

SKIP = {"PPLX", "PPLH", "PPLQ", "PPLW"}      # parts of a city ("Esquilino" in Rome); historical / abandoned
OUT = Path(__file__).resolve().parents[1] / "src" / "lunelis" / "geo" / "places.tsv.gz"


def main(src: Path) -> None:
    countries = {}
    for line in (src / "countryInfo.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            cols = line.split("\t")
            countries[cols[0]] = cols[4]
    regions = {}
    for line in (src / "admin1CodesASCII.txt").read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if len(cols) >= 2:
            regions[cols[0]] = cols[1]
    with zipfile.ZipFile(src / "cities15000.zip") as z:
        text = z.read("cities15000.txt").decode("utf-8")
    rows = []
    for line in text.splitlines():
        c = line.split("\t")
        if len(c) < 15:
            continue
        name, lat, lon, cc, a1 = c[1], float(c[4]), float(c[5]), c[8], c[10]
        if c[7] in SKIP:
            continue
        pop = int(c[14] or 0)
        country = countries.get(cc)
        if not country:
            continue
        region = regions.get(f"{cc}.{a1}", "")
        if region == name:
            region = ""                                  # Singapore, Singapore - say it once
        rows.append(f"{lat:.4f}\t{lon:.4f}\t{name}\t{region}\t{country}\t{pop}")
    rows.sort()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as g:
        g.write(("\n".join(rows) + "\n").encode("utf-8"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(buf.getvalue())
    print(f"{len(rows):,} places -> {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
