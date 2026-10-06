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
- **At most 32 connections** at once; more are closed straight away.
- **Rate limit:** each address gets a bucket of requests (burst 120, 20 a
  second); past that, 429.
- **Resized copies:** photos are served as 1600 px JPEGs with their edits
  (cached in the data folder); the original file only when you allowed it for
  that album. Ids are checked against the album, so nothing outside it leaks.
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


def lan_address() -> str:
    """This PC's address on the home network (the one a phone would use)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))           # no packet is sent: this only picks the route
        addr = s.getsockname()[0]
    except OSError:
        addr = "127.0.0.1"
    finally:
        s.close()
    return addr if is_home(addr) else "127.0.0.1"


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

        def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8", extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                             "script-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

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
            sh = app.share_for(parts[1])
            if sh is None:
                self._deny(404, "This link isn't shared any more.")
                return None
            return addr, sh, parts[2:]

        def _cookie_ok(self, sh) -> bool:
            if not sh["pin_hash"]:
                return True
            c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            m = c.get(f"lg{sh['id']}")
            return bool(m) and hmac.compare_digest(m.value, app.sign(sh["token"] + sh["pin_hash"]))

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
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
                if fid not in app.ids(sh["album_id"]):
                    return self._deny(404, "Not in this album.")
                if rest[0] == "original":
                    if not sh["originals"]:
                        return self._deny(403, "Originals aren't shared for this album.")
                    path = app.original(fid)
                    with open(path, "rb") as f:
                        data = f.read()
                    from urllib.parse import quote
                    name = os.path.basename(path)
                    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
                    return self._send(200, data, "application/octet-stream",
                                      {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                                              f"filename*=UTF-8''{quote(name)}"})
                data = app.resized(fid, THUMB if rest[0] == "thumb" else SIZE)
                return self._send(200, data, "image/jpeg")
            self._deny(404, "Nothing here.")

        def do_POST(self):
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
            ck = f"lg{sh['id']}={app.sign(sh['token'] + sh['pin_hash'])}; Path=/s/{sh['token']}/; HttpOnly; SameSite=Strict"
            self._send(303, b"", extra={"Location": f"/s/{sh['token']}/", "Set-Cookie": ck})

        def _pin_form(self, sh, wrong: bool = False):
            body = (f"<form method='post' action='/s/{sh['token']}/pin'><h1>{html.escape(sh['name'])}</h1>"
                    f"<p>{'Wrong PIN. ' if wrong else ''}Enter the PIN to see these photos.</p>"
                    "<input name='pin' type='password' inputmode='numeric' autofocus> <button>Open</button></form>")
            self._send(401 if wrong else 200, PAGE.format(title=html.escape(sh["name"]), body=body).encode())

        def _album(self, sh):
            ids = app.ids(sh["album_id"])
            t = sh["token"]
            tiles = "".join(f"<a href='/s/{t}/img/{f}.jpg'><img loading='lazy' src='/s/{t}/thumb/{f}.jpg' alt=''></a>"
                            for f in ids)
            body = (f"<header><h1>{html.escape(sh['name'])}</h1><span>{len(ids)} photos</span>"
                    f"<a href='/s/{t}/tv'>Slideshow</a></header><div class='grid'>{tiles}</div>")
            self._send(200, PAGE.format(title=html.escape(sh["name"]), body=body).encode())

        def _tv(self, sh):
            ids = app.ids(sh["album_id"])
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

    def __init__(self, db_path: Path, cache: Path, port: int = DEFAULT_PORT, host: str = "0.0.0.0") -> None:
        self.db_path, self.cache, self.port, self.host = db_path, cache, port, host
        self.limiter = _Limiter()
        self.secret = secrets.token_bytes(32)              # cookies don't outlive a restart of the gallery
        self.pin_lock = threading.Lock()
        self.render_slots = threading.Semaphore(2)         # big RAWs: at most two being made at once
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def _conn(self):
        from lunelis.catalog.schema import open_catalog
        return open_catalog(self.db_path)

    def sign(self, token: str) -> str:
        return hmac.new(self.secret, token.encode(), hashlib.sha256).hexdigest()

    def share_for(self, token: str) -> dict | None:
        if not token or len(token) > 64:
            return None
        conn = self._conn()
        try:
            row = conn.execute("SELECT s.id, s.album_id, s.token, s.pin_hash, s.originals, a.name FROM shares s"
                               " JOIN albums a ON a.id = s.album_id WHERE s.token = ? AND s.revoked_at IS NULL",
                               (token,)).fetchone()
        finally:
            conn.close()
        if row is None or not hmac.compare_digest(row[2], token):
            return None
        return {"id": row[0], "album_id": row[1], "token": row[2], "pin_hash": row[3], "originals": bool(row[4]),
                "name": row[5]}

    def ids(self, album_id: int) -> list[int]:
        conn = self._conn()
        try:
            return [r[0] for r in conn.execute(
                "SELECT f.id FROM album_files af JOIN files f ON f.id = af.file_id WHERE af.album_id = ?"
                " AND f.missing_since IS NULL AND f.quarantined_at IS NULL"
                f" AND COALESCE(f.format, '') NOT IN ({','.join('?' * len(VIDEO))}) ORDER BY af.position",
                (album_id, *VIDEO))]
        finally:
            conn.close()

    def original(self, fid: int) -> str:
        conn = self._conn()
        try:
            root, rel = conn.execute("SELECT r.path, f.rel_path FROM files f JOIN roots r ON r.id = f.root_id"
                                     " WHERE f.id = ?", (fid,)).fetchone()
        finally:
            conn.close()
        return os.path.join(root, *rel.split("/"))

    def resized(self, fid: int, edge: int) -> bytes:
        conn = self._conn()
        try:
            rev = conn.execute("SELECT COALESCE((SELECT rev FROM edits WHERE file_id = ?), 0)", (fid,)).fetchone()[0]
            p = self.cache / f"{fid}_{edge}_{rev}.jpg"
            if not p.exists():
                from lunelis.edit.export import rendered
                with self.render_slots:
                    if p.exists():
                        return p.read_bytes()
                    img = rendered(conn, fid, edge)
                self.cache.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp")
                img.save(tmp, "JPEG", quality=85)             # no exif: nothing (GPS) leaves with the picture
                os.replace(tmp, p)
            return p.read_bytes()
        finally:
            conn.close()

    def start(self) -> None:
        if self.httpd is not None:
            return
        self.httpd = _CappedServer((self.host, self.port), _handler(self))
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
