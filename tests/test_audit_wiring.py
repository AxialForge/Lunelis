"""0.37.1 (audit LRA-003/055): the installer writes each model under its own
name, and worker signals never go to lambdas."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_model_choices_follow_the_page_order():
    iss = (ROOT / "packaging" / "installer" / "lunelis.iss").read_text(encoding="utf-8")
    labels = re.findall(r"ModelsPage\.Add\('(\w+)", iss)
    words = {"Scene": "scene", "Faces": "faces", "Subject": "subject", "Sky": "sky"}
    page = [words[w] for w in labels]
    written = dict(re.findall(r"ModelsPage\.Values\[(\d)\].*?'\"(\w+)\"'", iss))
    assert [written[str(i)] for i in range(len(page))] == page


def test_worker_signals_are_not_connected_to_lambdas():
    # A lambda receiver runs on the emitting thread in PySide6: a dialog or a
    # label touched there can crash or hang the app.
    src = (ROOT / "src" / "lunelis" / "ui" / "main_window.py").read_text(encoding="utf-8")
    bad = re.findall(r"self\.(?:_worker|_autopilot|_xmp_worker)\.\w+\.connect\(\s*lambda", src)
    assert bad == []


def test_a_job_that_raises_is_marked_failed_and_the_runner_carries_on(tmp_path, monkeypatch):
    # audit LRA-058
    import threading
    import time
    from lunelis import paths
    from lunelis.catalog.schema import open_catalog
    from lunelis.importers.scan import add_root, scan_root
    from lunelis.jobs import engine
    from lunelis.ui import jobs as jobs_ui

    db = tmp_path / "cat.db"
    monkeypatch.setattr(paths, "DEFAULT_CATALOG_PATH", db)
    conn = open_catalog(db)
    lib = tmp_path / "L"
    lib.mkdir()
    (lib / "a.jpg").write_bytes(b"x" * 1000)
    rid = add_root(conn, lib)
    scan_root(conn, rid)
    bad = engine.create_job(conn, "verify", "bad", [(rid, None)])
    good = engine.create_job(conn, "verify", "good", [(rid, None)])
    conn.close()
    real = engine.KINDS["verify"]

    def kind(c, root_id, folder, **kw):
        if c.execute("SELECT state FROM jobs WHERE id = ?", (bad,)).fetchone()[0] == "running":
            raise RuntimeError("boom")
        return real(c, root_id, folder, **kw)
    monkeypatch.setitem(engine.KINDS, "verify", kind)
    runner = jobs_ui.JobRunner()
    t = threading.Thread(target=runner.run)
    t.start()
    end = time.monotonic() + 20
    states = {}
    while time.monotonic() < end:
        c = open_catalog(db)
        states = dict(c.execute("SELECT id, state FROM jobs").fetchall())
        c.close()
        if states.get(bad) not in ("queued", "running") and states.get(good) not in ("queued", "running"):
            break
        time.sleep(0.2)
    runner.quit()
    t.join(10)
    assert states[bad] == "failed" and states[good] == "done"
