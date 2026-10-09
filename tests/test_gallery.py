"""0.31 family gallery: albums on the home network only, one key each,
optional PIN, rate-limited, resized copies without location, originals only
when allowed, and off means nothing listening."""
import time
import os
import urllib.error
import urllib.parse
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from lunelis import gallery  # noqa: E402


def test_home_addresses():
    for a in ("192.168.1.20", "10.0.0.5", "172.20.1.1", "127.0.0.1", "::1", "fe80::1", "fd12::3", "::ffff:192.168.1.9"):
        assert gallery.is_home(a), a
    for a in ("8.8.8.8", "203.0.113.9", "2001:4860:4860::8888", "::ffff:8.8.8.8", "not an address"):
        assert not gallery.is_home(a), a


def test_pins_are_only_stored_hashed():
    h = gallery._pin_hash("2468")
    assert "2468" not in h and gallery._pin_ok("2468", h) and not gallery._pin_ok("1357", h)
    assert gallery._pin_hash("2468") != h                         # salted


@pytest.fixture
def served(tmp_path):
    import piexif
    from lunelis.albums import model as albums
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.scan import add_root, scan_root
    root = tmp_path / "Photos"
    root.mkdir()
    exif = piexif.dump({"GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N",
                                piexif.GPSIFD.GPSLatitude: ((41, 1), (30, 1), (0, 1))}})
    for i in range(3):
        Image.fromarray(np.full((300, 400, 3), 60 * i + 40, np.uint8)).save(root / f"P{i}.jpg", exif=exif)
    db = tmp_path / "cat.db"
    conn = open_catalog(db)
    scan_root(conn, add_root(conn, root))
    ids = [r[0] for r in conn.execute("SELECT id FROM files ORDER BY filename")]
    aid = albums.create(conn, "Grandma's birthday", ids[:2])
    other = albums.create(conn, "Private", ids[2:])
    g = gallery.Gallery(db, tmp_path / "gcache", port=0, host="127.0.0.1")
    g.start()
    yield conn, ids, aid, other, g
    g.stop()
    conn.close()


def get(g, path, data=None, cookie=None):
    req = urllib.request.Request(f"http://127.0.0.1:{g.port}{path}", data=data)
    if cookie:
        req.add_header("Cookie", cookie)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        r = opener.open(req, timeout=10)
        return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def test_a_shared_album_is_seen_and_nothing_else(served):
    conn, ids, aid, other, g = served
    token = gallery.share(conn, aid)
    code, body, headers = get(g, f"/s/{token}/")
    assert code == 200 and b"Grandma&#x27;s birthday" in body and b"2 photos" in body
    assert "default-src 'none'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff"
    code, img, h = get(g, f"/s/{token}/img/{ids[0]}.jpg")
    assert code == 200 and h["Content-Type"] == "image/jpeg"
    from io import BytesIO
    with Image.open(BytesIO(img)) as im:
        assert max(im.size) <= gallery.SIZE and "exif" not in im.info          # no GPS leaves
    assert get(g, f"/s/{token}/img/{ids[2]}.jpg")[0] == 404                      # not in this album
    assert get(g, "/s/not-a-real-key/")[0] == 404
    assert get(g, "/")[0] == 404 and get(g, "/s/")[0] == 404                    # no index of albums
    assert get(g, f"/s/{token}/original/{ids[0]}.jpg")[0] == 403                # originals not allowed
    assert get(g, f"/s/{token}/tv")[0] == 200


def test_originals_only_when_allowed(served):
    conn, ids, aid, other, g = served
    token = gallery.share(conn, aid, originals=True)
    code, data, h = get(g, f"/s/{token}/original/{ids[0]}.jpg")
    assert code == 200 and data[:2] == b"\xff\xd8" and "attachment" in h["Content-Disposition"]


def test_a_pin_guards_the_album(served):
    conn, ids, aid, other, g = served
    token = gallery.share(conn, aid, pin="2468")
    code, body, _ = get(g, f"/s/{token}/")
    assert code == 200 and b"Enter the PIN" in body and b"/img/" not in body
    assert get(g, f"/s/{token}/img/{ids[0]}.jpg")[0] == 200 and b"PIN" in get(g, f"/s/{token}/img/{ids[0]}.jpg")[1]
    code, body, _ = get(g, f"/s/{token}/pin", data=b"pin=1111")
    assert code == 401 and b"Wrong PIN" in body
    code, _, h = get(g, f"/s/{token}/pin", data=b"pin=2468")
    assert code == 303
    cookie = h["Set-Cookie"].split(";")[0]
    assert "HttpOnly" in h["Set-Cookie"] and "SameSite=Strict" in h["Set-Cookie"]
    code, body, _ = get(g, f"/s/{token}/", cookie=cookie)
    assert code == 200 and b"/img/" in body
    assert b"/img/" not in get(g, f"/s/{token}/", cookie="lg1=forged")[1]


def test_wrong_pins_lock_an_address_out(served):
    conn, ids, aid, other, g = served
    token = gallery.share(conn, aid, pin="2468")
    for _ in range(gallery.PIN_TRIES):
        get(g, f"/s/{token}/pin", data=b"pin=0000")
    code, body, _ = get(g, f"/s/{token}/pin", data=b"pin=2468")
    assert code == 429 and b"ten minutes" in body


def test_the_rate_limit_holds(served, monkeypatch):
    conn, ids, aid, other, g = served
    monkeypatch.setattr(gallery, "BURST", 5)
    monkeypatch.setattr(gallery, "RATE", 0.01)
    g.limiter = gallery._Limiter()
    token = gallery.share(conn, aid)
    codes = [get(g, f"/s/{token}/")[0] for _ in range(8)]
    assert codes[:5] == [200] * 5 and 429 in codes[5:]


def test_addresses_outside_the_home_are_refused(served, monkeypatch):
    conn, ids, aid, other, g = served
    token = gallery.share(conn, aid)
    monkeypatch.setattr(gallery, "is_home", lambda addr: False)
    code, body, _ = get(g, f"/s/{token}/")
    assert code == 403 and b"Only the home network" in body


def test_stop_sharing_and_turning_off(served):
    conn, ids, aid, other, g = served
    token = gallery.share(conn, aid)
    assert get(g, f"/s/{token}/")[0] == 200
    gallery.stop_sharing(conn, aid)
    assert get(g, f"/s/{token}/")[0] == 404                       # the old key is useless
    assert gallery.share(conn, aid) != token                     # sharing again makes a new key
    port = g.port
    g.stop()
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3)


def test_the_qr_code_reads_back():
    import cv2
    png = gallery.qr_png("http://192.168.1.20:8735/s/abc123/")
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert cv2.QRCodeDetector().detectAndDecode(img)[0] == "http://192.168.1.20:8735/s/abc123/"


def test_the_share_dialog(served, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.share_dialog import ShareDialog
    conn, ids, aid, other, g = served
    d = ShareDialog(conn, aid, "Grandma's birthday", lambda: (g.port, True))
    assert d.share_b.text() == "Share" and not d.stop_b.isEnabled()
    d.pin.setText("12")
    d.do_share()
    assert "at least 4" in d.note.text() and not gallery.shares(conn)
    d.pin.setText("2468")
    d.do_share()
    [(a, name, token, has_pin, originals)] = gallery.shares(conn)
    assert a == aid and has_pin and not originals and d.link.text().endswith(f"/s/{token}/")
    assert d.qr.pixmap() is not None and "PIN required" in d.note.text()
    d.do_stop()
    assert gallery.shares(conn) == [] and d.link.text() == ""


def test_updating_a_share_keeps_its_pin_unless_removed(served):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from lunelis.ui.share_dialog import ShareDialog
    conn, ids, aid, other, g = served
    gallery.share(conn, aid, pin="2468")
    d = ShareDialog(conn, aid, "Album", lambda: (g.port, True))
    d.originals.setChecked(True)
    d.do_share()                                               # empty PIN field: keep the PIN
    [(_a, _n, _t, has_pin, originals)] = gallery.shares(conn)
    assert has_pin and originals
    d.remove_pin.setChecked(True)
    d.do_share()
    assert not gallery.shares(conn)[0][3]


def test_parallel_guesses_cant_slip_past_the_lockout(served):
    import threading
    conn, ids, aid, other, g = served
    token = gallery.share(conn, aid, pin="2468")
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(get(g, f"/s/{token}/pin", data=b"pin=0000")[0]))
               for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes.count(401) <= gallery.PIN_TRIES and codes.count(429) >= 20 - gallery.PIN_TRIES


def test_changing_the_pin_logs_old_visitors_out(served):
    conn, ids, aid, other, g = served
    token = gallery.share(conn, aid, pin="2468")
    cookie = get(g, f"/s/{token}/pin", data=b"pin=2468")[2]["Set-Cookie"].split(";")[0]
    assert b"/img/" in get(g, f"/s/{token}/", cookie=cookie)[1]
    gallery.share(conn, aid, pin="1357")
    assert b"/img/" not in get(g, f"/s/{token}/", cookie=cookie)[1]


def test_a_bad_content_length_is_harmless(served):
    import socket
    conn, ids, aid, other, g = served
    token = gallery.share(conn, aid, pin="2468")
    s = socket.create_connection(("127.0.0.1", g.port), timeout=10)
    crlf = chr(13) + chr(10)
    s.sendall(crlf.join([f"POST /s/{token}/pin HTTP/1.1", "Host: x", "Content-Length: -5", "", ""]).encode())
    assert b"401" in s.recv(200).split(crlf.encode())[0]
    s.close()


def test_originals_with_any_name(served, tmp_path):
    conn, ids, aid, other, g = served
    root = conn.execute("SELECT path FROM roots").fetchone()[0]
    import os
    os.rename(os.path.join(root, "P0.jpg"), os.path.join(root, "Grand-mère 日本.jpg"))
    conn.execute("UPDATE files SET rel_path = 'Grand-mère 日本.jpg', filename = 'Grand-mère 日本.jpg' WHERE id = ?", (ids[0],))
    conn.commit()
    token = gallery.share(conn, aid, originals=True)
    code, data, h = get(g, f"/s/{token}/original/{ids[0]}.jpg")
    assert code == 200 and "filename*=UTF-8''Grand-m%C3%A8re" in h["Content-Disposition"]


def test_an_albums_pin_has_its_own_budget_across_addresses():
    # audit LRA-020: many LAN addresses can't each get five guesses for ever
    from lunelis import gallery
    lim = gallery._Limiter()
    for _ in range(gallery.SHARE_PIN_TRIES):
        assert lim.share_attempt("tok")
        lim.share_failed("tok")
    assert not lim.share_attempt("tok")
    assert lim.share_attempt("other")


def test_the_server_caps_connections(tmp_path, monkeypatch):
    # audit LRA-019
    import socket
    import time
    from lunelis import gallery
    monkeypatch.setattr(gallery, "MAX_CONNECTIONS", 2)
    g = gallery.Gallery(tmp_path / "none.db", tmp_path, port=0, host="127.0.0.1")
    g.start()
    try:
        held = [socket.create_connection(("127.0.0.1", g.port)) for _ in range(2)]
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", g.port))
        extra.settimeout(3)
        assert extra.recv(10) == b""                       # closed at once, not left waiting
        for s in held + [extra]:
            s.close()
    finally:
        g.stop()


def test_0_50_cookie_expires_originals_stream_and_one_address(served, monkeypatch):
    """0.50: the PIN cookie lasts 12 hours, originals stream instead of being read
    whole, and the server listens on the home address only."""
    import inspect
    conn, ids, aid, other, g = served
    token = gallery.share(conn, aid, pin="2468", originals=True)
    st, _, h = get(g, f"/s/{token}/pin", data=b"pin=2468")
    assert f"Max-Age={gallery.COOKIE_HOURS * 3600}" in h["Set-Cookie"]
    cookie = h["Set-Cookie"].split(";")[0]
    assert get(g, f"/s/{token}/", cookie=cookie)[0] == 200
    real = time.time()
    monkeypatch.setattr(gallery.time, "time", lambda: real + gallery.COOKIE_HOURS * 3600 + 60)
    st, body, _ = get(g, f"/s/{token}/", cookie=cookie)
    assert st == 200 and b"PIN" in body                               # expired: the PIN form again
    monkeypatch.setattr(gallery.time, "time", lambda: real)
    st, body, h = get(g, f"/s/{token}/original/{ids[0]}", cookie=cookie)
    assert st == 200 and int(h["Content-Length"]) == len(body) > 0
    assert "f.read()" not in inspect.getsource(gallery._handler)      # nothing read whole
    assert gallery.Gallery(gallery.Path("x"), gallery.Path("y")).host is None   # bound at start() to the home address
