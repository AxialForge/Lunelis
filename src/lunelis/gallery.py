"""
Family gallery: chosen albums as a read-only web page on the home network.

Off until you turn it on. Then a small web server (standard library only)
answers on this PC's home-network address, and:

- **Only the home network:** a request from any address that isn't private
  (10.x, 172.16-31.x, 192.168.x, this PC, IPv6 link-local / unique-local) is
  refused before anything else happens.
- **One key per album:** a shared album gets a long random key in its link
  (`/s/<key>/`); nothing is reachable without one, there's no index of
  albums, and Stop sharing makes the key useless at once.
- **Optional PIN:** stored only as a salted PBKDF2 hash; a right PIN sets a
  cookie (HttpOnly, SameSite=Strict) signed with this PC's own secret. Five
  wrong PINs from one address lock it out for ten minutes, and
  twenty wrong PINs for one album (from any addresses) lock that album's PIN
  for an hour.
- **Only this PC's home address:** the server listens on that one address,
  not on every adapter (a VPN, a virtual machine's network) - 0.50.
- **The PIN cookie lasts 12 hours** (signed with its expiry, 0.50).
- **Originals stream** in 1 MB pieces instead of being read whole into
  memory (a 2 GB video no longer takes 2 GB of RAM) - 0.50.
- **At most 32 connections** at once; more are closed straight away.
- **Rate limit:** each address gets a bucket of requests (burst 120, 20 a
  second); past that, 429.
- **Resized copies:** photos are served as 1600 px JPEGs with their edits
  (cached in the data folder); the original file only when you allowed it for
  that album. Ids are checked against the album, so nothing outside it leaks.
  The cached copies are kept per catalog and per version of the file (0.54),
  so a restored or replaced catalog can't show another photo's copy.
- **Pages aren't kept by the browser** (no-store, 0.54): the PIN page, error
  pages and album pages are asked for again each time, so a link stops
  working as soon as sharing stops. Pictures may be kept for five minutes.
- **No metadata out:** resized copies carry no EXIF (so no GPS).
- Pages send a strict Content-Security-Policy, nosniff and no-referrer.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import ipaddress
import json
import os
import secrets
import sqlite3
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 8735
SIZE = 1600
THUMB = 400
PIN_TRIES = 5
SHARE_PIN_TRIES = 20          # wrong PINs for one album from ALL addresses, per SHARE_LOCKOUT_S
SHARE_LOCKOUT_S = 3600
MAX_CONNECTIONS = 32          # more at once are turned away: slow clients can't pile up threads
LOCKOUT_S = 600
BURST, RATE = 120, 20.0
VIDEO = ("mp4", "mov", "mpeg-ts")


# --- addresses ---------------------------------------------------------------------------------

# Spelled out, not ip.is_private: Python counts documentation and benchmark
# ranges (203.0.113.0/24, 198.18.0.0/15...) as "private" too.
HOME_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
    "::1/128", "fe80::/10", "fc00::/7")]


def is_home(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip.version == n.version and ip in n for n in HOME_NETS)


CHUNK = 1 << 20                  # originals stream in 1 MB pieces
IMAGE_CACHE = "private, max-age=300"     # pictures only; pages are never kept (0.54)
CACHE_KEEP_DAYS = 30             # a resized copy not made again for this long is removed
COOKIE_HOURS = 12                # a right PIN is remembered this long


# Home routers hand out 192.168.x (nearly all of them) or 172.16-31.x; 10.x is
# what a VPN's adapter usually has (NordVPN's is 10.5.0.2), though some home
# networks use it too - so it's the last choice, not left out.
_LAN_ORDER = [ipaddress.ip_network(n) for n in ("192.168.0.0/16", "172.16.0.0/12", "10.0.0.0/8")]


def pick_lan_address(candidates: list[str], routed: str | None = None) -> str:
    """The home-network address among this PC's addresses.

    `routed` is the one Windows would send ordinary traffic from. With a VPN
    on, that is the VPN's adapter (10.x) and phones at home can't reach it:
    a 192.168.x or 172.16-31.x address on another adapter wins over it. Among
    addresses of the same kind the routed one is preferred. 127.0.0.1 when
    there's no home-network address at all (0.54)."""
    seen: list[str] = []
    for a in ([routed] if routed else []) + list(candidates):
        if a and a not in seen:
            seen.append(a)
    for net in _LAN_ORDER:
        for a in seen:
            try:
                if ipaddress.ip_address(a) in net:
                    return a
            except ValueError:
                continue
    return "127.0.0.1"


def _routed_address() -> str | None:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))           # no packet is sent: this only picks the route
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _own_addresses() -> list[str]:
    """Every IPv4 address of this PC's adapters."""
    import socket
    try:
        return list(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        return []


def lan_address() -> str:
    """This PC's address on the home network (the one a phone would use)."""
    return pick_lan_address(_own_addresses(), _routed_address())


# --- shares ------------------------------------------------------------------------------------

def _pin_hash(pin: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000)
    return salt.hex() + ":" + digest.hex()


def _pin_ok(pin: str, stored: str) -> bool:
    salt, _ = stored.split(":", 1)
    return hmac.compare_digest(_pin_hash(pin, bytes.fromhex(salt)), stored)


KEEP = object()          # share(pin=KEEP): leave the PIN as it is


def share(conn: sqlite3.Connection, album_id: int, pin=KEEP, originals: bool = False) -> str:
    """Share an album (or change it); returns its key. pin: digits for a new PIN,
    None to remove it, KEEP (the default) to leave it as it is."""
    row = conn.execute("SELECT token, pin_hash FROM shares WHERE album_id = ? AND revoked_at IS NULL",
                       (album_id,)).fetchone()
    if pin is KEEP:
        pin_hash = row[1] if row else None
    else:
        pin_hash = _pin_hash(pin) if pin else None
    if row:
        conn.execute("UPDATE shares SET pin_hash = ?, originals = ? WHERE token = ?", (pin_hash, int(originals), row[0]))
        conn.commit()
        return row[0]
    token = secrets.token_urlsafe(18)
    conn.execute("INSERT INTO shares (album_id, token, pin_hash, originals) VALUES (?, ?, ?, ?)",
                 (album_id, token, pin_hash, int(originals)))
    conn.commit()
    return token


def stop_sharing(conn: sqlite3.Connection, album_id: int) -> None:
    conn.execute("UPDATE shares SET revoked_at = datetime('now') WHERE album_id = ? AND revoked_at IS NULL", (album_id,))
    conn.commit()


def shares(conn: sqlite3.Connection) -> list[tuple[int, str, str, bool, bool]]:
    """(album id, album name, key, has PIN, originals allowed) of every live share."""
    return [(a, n, t, bool(p), bool(o)) for a, n, t, p, o in conn.execute(
        "SELECT s.album_id, a.name, s.token, s.pin_hash, s.originals FROM shares s JOIN albums a ON a.id = s.album_id"
        " WHERE s.revoked_at IS NULL ORDER BY a.name")]


def link(token: str, port: int, host: str | None = None) -> str:
    return f"http://{host or lan_address()}:{port}/s/{token}/"


def qr_png(text: str, scale: int = 8) -> bytes:
    """A QR code of `text` as a PNG (OpenCV's encoder)."""
    import cv2
    import numpy as np
    m = cv2.QRCodeEncoder.create().encode(text)
    m = np.pad(m, 4, constant_values=255)
    big = cv2.resize(m, (m.shape[1] * scale, m.shape[0] * scale), interpolation=cv2.INTER_NEAREST)
    ok, buf = cv2.imencode(".png", big)
    return buf.tobytes()


# --- the server --------------------------------------------------------------------------------

class _Limiter:
    def __init__(self) -> None:
        self.buckets: dict[str, tuple[float, float]] = {}
        self.fails: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def allow(self, addr: str) -> bool:
        now = time.monotonic()
        with self.lock:
            tokens, at = self.buckets.get(addr, (BURST, now))
            tokens = min(BURST, tokens + (now - at) * RATE)
            if tokens < 1:
                self.buckets[addr] = (tokens, now)
                return False
            self.buckets[addr] = (tokens - 1, now)
            return True

    def attempt(self, addr: str) -> float | None:
        """Count a PIN attempt *before* it's checked (so parallel guesses can't
        all slip past the lockout); None when the address is locked out.
        The returned stamp is handed back to succeeded()."""
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.fails.get(addr, []) if now - t < LOCKOUT_S]
            if len(recent) >= PIN_TRIES:
                self.fails[addr] = recent
                return None
            recent.append(now)
            self.fails[addr] = recent
            return now

    def succeeded(self, addr: str, stamp: float) -> None:
        with self.lock:
            tries = self.fails.get(addr, [])
            if stamp in tries:
                tries.remove(stamp)

    def share_attempt(self, token: str) -> bool:
        """The album's own budget, whatever address the guesses come from (a
        LAN has plenty): False once SHARE_PIN_TRIES wrong PINs in the hour."""
        now = time.monotonic()
        key = "share:" + token
        with self.lock:
            recent = [t for t in self.fails.get(key, []) if now - t < SHARE_LOCKOUT_S]
            self.fails[key] = recent
            return len(recent) < SHARE_PIN_TRIES

    def share_failed(self, token: str) -> None:
        with self.lock:
            self.fails.setdefault("share:" + token, []).append(time.monotonic())


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{margin:0;font:16px system-ui,sans-serif;background:#131317;color:#ececf1}}
header{{padding:16px 20px;display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}}h1{{font-size:1.3rem;margin:0}}
a{{color:#8b8cf2}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:6px;padding:0 6px 20px}}
.grid img{{width:100%;aspect-ratio:1;object-fit:cover;display:block;border-radius:4px}}
form{{padding:40px 20px}}input{{font:inherit;padding:8px;border-radius:6px;border:1px solid #444;background:#1a1a1f;color:inherit}}
button{{font:inherit;padding:8px 14px;border-radius:6px;border:0;background:#5b5bd6;color:#fff}}
.tv{{position:fixed;inset:0;background:#000}}.tv img{{width:100%;height:100%;object-fit:contain}}</style></head>
<body>{body}</body></html>"""


def _handler(app: "Gallery"):
    class H(BaseHTTPRequestHandler):
        server_version = "Lunelis"
        sys_version = ""
        timeout = 15                                          # an idle or trickling connection is dropped

        def log_message(self, *a):                          # the app log, not stderr
            pass

        conn = None                                           # this request's one catalog connection

        def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8",
                  extra: dict | None = None, cache: bool = False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            # Only pictures may be kept. A page (the PIN form, an error, the
            # album) kept for five minutes still opened after sharing was
            # stopped or the PIN changed (0.54).
            self.send_header("Cache-Control", IMAGE_CACHE if cache else "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                             "script-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _send_file(self, path: str, ctype: str, extra: dict | None = None):
            """An original, streamed in 1 MB pieces (0.50: it was read whole into memory)."""
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", IMAGE_CACHE)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def _deny(self, code: int, text: str):
            self._send(code, PAGE.format(title="Lunelis", body=f"<form><p>{html.escape(text)}</p></form>").encode())

        def _gate(self):
            addr = self.client_address[0]
            if not is_home(addr):
                self._deny(403, "Only the home network can see this.")
                return None
            if not app.limiter.allow(addr):
                self._deny(429, "Too many requests - wait a moment.")
                return None
            parts = urlparse(self.path).path.strip("/").split("/")
            if len(parts) < 2 or parts[0] != "s":
                self._deny(404, "Nothing here.")
                return None
            # One connection for the whole request: the share, the album
            # check and the picture each opened (and upgrade-checked) the
            # catalog again (0.54). Closed in do_GET / do_POST.
            self.conn = app._conn()
            sh = app.share_for(parts[1], self.conn)
            if sh is None:
                self._deny(404, "This link isn't shared any more.")
                return None
            return addr, sh, parts[2:]

        def _cookie_ok(self, sh) -> bool:
            if not sh["pin_hash"]:
                return True
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            m = c.get(f"lg{sh['id']}")
            if not m or "." not in m.value:
                return False
            exp, sig = m.value.split(".", 1)
            if not exp.isdigit() or int(exp) < time.time():
                return False                               # expired: the PIN again (0.50)
            return hmac.compare_digest(sig, app.sign(sh["token"] + sh["pin_hash"] + exp))

        def _done(self):
            if self.conn is not None:
                self.conn.close()
                self.conn = None

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            try:
                self._get()
            finally:
                self._done()

        def do_POST(self):
            try:
                self._post()
            finally:
                self._done()

        def _get(self):
            g = self._gate()
            if g is None:
                return
            addr, sh, rest = g
            if not self._cookie_ok(sh):
                return self._pin_form(sh)
            if not rest or rest == [""]:
                return self._album(sh)
            if rest[0] == "tv":
                return self._tv(sh)
            if rest[0] in ("img", "thumb", "original") and len(rest) == 2 and rest[1].split(".")[0].isdigit():
                fid = int(rest[1].split(".")[0])
                if not app.in_album(sh["album_id"], fid, self.conn):   # one row, not the album's list (0.54)
                    return self._deny(404, "Not in this album.")
                if rest[0] == "original":
                    if not sh["originals"]:
                        return self._deny(403, "Originals aren't shared for this album.")
                    path = app.original(fid, self.conn)
                    from urllib.parse import quote
                    name = os.path.basename(path)
                    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
                    return self._send_file(path, "application/octet-stream",
                                           {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                                                   f"filename*=UTF-8''{quote(name)}"})
                data = app.resized(fid, THUMB if rest[0] == "thumb" else SIZE, self.conn)
                return self._send(200, data, "image/jpeg", cache=True)
            self._deny(404, "Nothing here.")

        def _post(self):
            g = self._gate()
            if g is None:
                return
            addr, sh, rest = g
            if rest[:1] != ["pin"] or not sh["pin_hash"]:
                return self._deny(404, "Nothing here.")
            if not app.limiter.share_attempt(sh["token"]):
                return self._deny(429, "Too many wrong PINs for this album - try again in an hour.")
            stamp = app.limiter.attempt(addr)
            if stamp is None:
                return self._deny(429, "Too many wrong PINs - try again in ten minutes.")
            try:
                n = max(0, min(int(self.headers.get("Content-Length") or 0), 1024))
            except ValueError:
                n = 0
            pin = parse_qs(self.rfile.read(n).decode("utf-8", "replace")).get("pin", [""])[0]
            with app.pin_lock:                         # one PBKDF2 at a time: guessing can't load the PC
                ok = _pin_ok(pin, sh["pin_hash"])
            if not ok:
                app.limiter.share_failed(sh["token"])
                return self._pin_form(sh, wrong=True)
            app.limiter.succeeded(addr, stamp)
            exp = str(int(time.time()) + COOKIE_HOURS * 3600)
            ck = (f"lg{sh['id']}={exp}.{app.sign(sh['token'] + sh['pin_hash'] + exp)}; Path=/s/{sh['token']}/; "
                  f"Max-Age={COOKIE_HOURS * 3600}; HttpOnly; SameSite=Strict")
            self._send(303, b"", extra={"Location": f"/s/{sh['token']}/", "Set-Cookie": ck})

        def _pin_form(self, sh, wrong: bool = False):
            body = (f"<form method='post' action='/s/{sh['token']}/pin'><h1>{html.escape(sh['name'])}</h1>"
                    f"<p>{'Wrong PIN. ' if wrong else ''}Enter the PIN to see these photos.</p>"
                    "<input name='pin' type='password' inputmode='numeric' autofocus> <button>Open</button></form>")
            self._send(401 if wrong else 200, PAGE.format(title=html.escape(sh["name"]), body=body).encode())

        def _album(self, sh):
            ids = app.ids(sh["album_id"], self.conn)
            t = sh["token"]
            tiles = "".join(f"<a href='/s/{t}/img/{f}.jpg'><img loading='lazy' src='/s/{t}/thumb/{f}.jpg' alt=''></a>"
                            for f in ids)
            body = (f"<header><h1>{html.escape(sh['name'])}</h1><span>{len(ids)} photos</span>"
                    f"<a href='/s/{t}/tv'>Slideshow</a></header><div class='grid'>{tiles}</div>")
            self._send(200, PAGE.format(title=html.escape(sh["name"]), body=body).encode())

        def _tv(self, sh):
            ids = app.ids(sh["album_id"], self.conn)
            urls = json.dumps([f"/s/{sh['token']}/img/{f}.jpg" for f in ids])
            body = ("<div class='tv'><img id='p' alt=''></div><script>"
                    f"var u={urls},i=0,p=document.getElementById('p');"
                    "function n(){if(!u.length)return;p.src=u[i%u.length];i++;}n();setInterval(n,6000);"
                    "document.onclick=function(){document.documentElement.requestFullscreen&&"
                    "document.documentElement.requestFullscreen();};</script>")
            self._send(200, PAGE.format(title=html.escape(sh["name"]), body=body).encode())
    return H


class _CappedServer(ThreadingHTTPServer):
    """At most MAX_CONNECTIONS handled at once; the rest are closed at once."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class Gallery:
    """The running server. One catalog connection per request (thread-safe)."""

    def __init__(self, db_path: Path, cache: Path, port: int = DEFAULT_PORT, host: str | None = None) -> None:
        # Only the home-network address, not every adapter (0.50); chosen at start().
        self.db_path, self.cache, self.port, self.host = db_path, cache, port, host
        self.limiter = _Limiter()
        self.secret = secrets.token_bytes(32)              # cookies don't outlive a restart of the gallery
        self.pin_lock = threading.Lock()
        self.render_slots = threading.Semaphore(2)         # big RAWs: at most two being made at once
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self._catalog_id: str | None = None                # which catalog the cached copies belong to
        self._tidy_lock = threading.Lock()                 # ...asked once, and the old copies cleared then

    def _conn(self):
        from lunelis.catalog.schema import open_catalog
        return open_catalog(self.db_path)

    def _with(self, conn, fn):
        """Run fn(connection): the request's own, or one opened just for this."""
        if conn is not None:
            return fn(conn)
        conn = self._conn()
        try:
            return fn(conn)
        finally:
            conn.close()

    def sign(self, token: str) -> str:
        return hmac.new(self.secret, token.encode(), hashlib.sha256).hexdigest()

    def share_for(self, token: str, conn=None) -> dict | None:
        if not token or len(token) > 64:
            return None
        row = self._with(conn, lambda c: c.execute(
            "SELECT s.id, s.album_id, s.token, s.pin_hash, s.originals, a.name FROM shares s"
            " JOIN albums a ON a.id = s.album_id WHERE s.token = ? AND s.revoked_at IS NULL",
            (token,)).fetchone())
        if row is None or not hmac.compare_digest(row[2], token):
            return None
        return {"id": row[0], "album_id": row[1], "token": row[2], "pin_hash": row[3], "originals": bool(row[4]),
                "name": row[5]}

    _SHOWN = ("FROM album_files af JOIN files f ON f.id = af.file_id WHERE af.album_id = ?"
              " AND f.missing_since IS NULL AND f.quarantined_at IS NULL"
              f" AND COALESCE(f.format, '') NOT IN ({','.join('?' * len(VIDEO))})")

    def ids(self, album_id: int, conn=None) -> list[int]:
        return self._with(conn, lambda c: [r[0] for r in c.execute(
            f"SELECT f.id {self._SHOWN} ORDER BY af.position", (album_id, *VIDEO))])

    def in_album(self, album_id: int, fid: int, conn=None) -> bool:
        """Is this photo one the album's page shows? (The same rule as ids().)"""
        return self._with(conn, lambda c: c.execute(
            f"SELECT 1 {self._SHOWN} AND af.file_id = ? LIMIT 1", (album_id, *VIDEO, fid)).fetchone() is not None)

    def original(self, fid: int, conn=None) -> str:
        root, rel = self._with(conn, lambda c: c.execute(
            "SELECT r.path, f.rel_path FROM files f JOIN roots r ON r.id = f.root_id WHERE f.id = ?",
            (fid,)).fetchone())
        return os.path.join(root, *rel.split("/"))

    def cache_path(self, conn, fid: int, edge: int) -> Path:
        """Where this photo's resized copy is kept.

        By photo number alone (as it was) a restored or replaced catalog -
        which numbers its photos from 1 again - was served another photo's
        copy. The folder is now this catalog's own id, and the name carries
        the edit's revision and the file's size and date, so a replaced file
        gets a new copy too (0.54)."""
        with self._tidy_lock:
            if self._catalog_id is None:
                from lunelis.catalog.cachecheck import catalog_id
                self._catalog_id = catalog_id(conn)
                self.tidy_cache(self._catalog_id)           # once per start of the gallery
        size, mtime, rev = conn.execute(
            "SELECT f.size_bytes, f.mtime, COALESCE((SELECT rev FROM edits WHERE file_id = f.id), 0)"
            " FROM files f WHERE f.id = ?", (fid,)).fetchone()
        sig = hashlib.sha256(f"{size}:{mtime}".encode()).hexdigest()[:10]
        return self.cache / self._catalog_id / f"{fid}_{edge}_{rev}_{sig}.jpg"

    def tidy_cache(self, keep: str) -> None:
        """Remove resized copies that can't be used again: other catalogs'
        folders, the loose files of before 0.54, and copies older than
        CACHE_KEEP_DAYS. Lunelis's own cache, never your photos. Best effort."""
        import re
        import shutil
        old = time.time() - CACHE_KEEP_DAYS * 86400
        try:
            entries = list(os.scandir(self.cache))
        except OSError:
            return
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    # Only folders named like a catalog id and files named like
                    # a resized copy are ever touched, whatever folder this is.
                    if not re.fullmatch(r"[0-9a-f]{32}", e.name):
                        continue
                    if e.name != keep:
                        shutil.rmtree(e.path, ignore_errors=True)
                        continue
                    for f in os.scandir(e.path):
                        if (f.is_file(follow_symlinks=False) and f.stat().st_mtime < old
                                and re.fullmatch(r"\d+_\d+_\d+_[0-9a-f]+\.jpg", f.name)):
                            os.remove(f.path)
                elif re.fullmatch(r"\d+_\d+_\d+\.(jpg|tmp)", e.name):
                    os.remove(e.path)
            except OSError:
                pass

    def resized(self, fid: int, edge: int, conn=None) -> bytes:
        return self._with(conn, lambda c: self._resized(c, fid, edge))

    def _resized(self, conn, fid: int, edge: int) -> bytes:
        p = self.cache_path(conn, fid, edge)
        try:
            return p.read_bytes()
        except FileNotFoundError:
            pass
        from lunelis.edit.export import rendered
        from lunelis.raw.thumbnails import save_jpeg_atomic
        with self.render_slots:
            if p.exists():
                return p.read_bytes()
            img = rendered(conn, fid, edge)
        # Its own temporary name: two requests for one picture shared a
        # single .tmp (0.54). No exif: nothing (GPS) leaves with the picture.
        save_jpeg_atomic(img, p, 85)
        for stale in p.parent.glob(f"{fid}_{edge}_*.jpg"):      # this photo's copies of earlier edits
            if stale != p:
                try:
                    stale.unlink()
                except OSError:
                    pass
        return p.read_bytes()

    def start(self) -> None:
        if self.httpd is not None:
            return
        self.httpd = _CappedServer((self.host or lan_address(), self.port), _handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="lunelis-gallery", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None
        self.thread = None

    @property
    def running(self) -> bool:
        return self.httpd is not None
