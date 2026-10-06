"""
`Lunelis.exe --self-test <report file>`: checks that a packaged build has
everything it needs - the bundled libraries (LibRaw, HEIF, FFmpeg), the
assets, the catalog migrations, the window - without touching the real
library: it runs against a throwaway data folder. CI runs it on every build
before the build is published; exit code 0 = all good.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path


def run(report: str | None, samples: list[str] | None = None) -> int:
    """samples: real photos/videos to render too (read only - e.g. a camera's RAW)."""
    tmp = tempfile.mkdtemp(prefix="lunelis-selftest-")
    os.environ["LUNELIS_DATA_DIR"] = os.path.join(tmp, "data")
    from lunelis import paths
    paths.reload()
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[bool, str]] = []

    def check(name, fn):
        try:
            detail = fn()
            results.append((True, f"{name}{': ' + str(detail) if detail else ''}"))
        except Exception as e:                       # noqa: BLE001 - reported, not raised
            results.append((False, f"{name}: {type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"))

    check("version", paths.version)
    def assets():
        missing = [n for n in ("app_icon.png", "check.svg", "lunelis.ico") if not (paths.ASSETS / "icons" / n).exists()]
        if missing:
            raise FileNotFoundError(f"{missing} not in {paths.ASSETS}")
        return str(paths.ASSETS)
    check("assets", assets)

    def darktable_script():
        from lunelis.darktable import bridge
        text = bridge.LUA.read_text(encoding="utf-8")
        if "@EXCHANGE@" not in text:
            raise ValueError("unexpected script contents")
        return str(bridge.LUA)
    check("darktable plugin script", darktable_script)

    def libraw():
        import rawpy
        return f"LibRaw {rawpy.libraw_version}"
    check("RAW decoding (rawpy/LibRaw)", libraw)

    def jpeg_pipeline():
        from PIL import Image
        from lunelis.raw.thumbnails import render
        p = Path(tmp) / "t.jpg"
        Image.effect_noise((64, 48), 40).convert("RGB").save(p, "JPEG")
        return f"{render(str(p)).size}"
    check("JPEG thumbnail pipeline", jpeg_pipeline)

    def heif():
        import pillow_heif
        from PIL import Image
        pillow_heif.register_heif_opener()
        p = Path(tmp) / "t.heic"
        Image.effect_noise((64, 48), 40).convert("RGB").save(p, "HEIF")
        return f"{Image.open(p).size}, libheif {pillow_heif.libheif_version()}"
    check("HEIC (pillow-heif)", heif)

    def video():
        import av
        p = Path(tmp) / "t.mp4"
        with av.open(str(p), "w") as out:
            s = out.add_stream("mpeg4", rate=10)
            s.width, s.height, s.pix_fmt = 64, 48, "yuv420p"
            from PIL import Image
            for _ in range(3):
                frame = av.VideoFrame.from_image(Image.effect_noise((64, 48), 40).convert("RGB"))
                for pkt in s.encode(frame):
                    out.mux(pkt)
            for pkt in s.encode():
                out.mux(pkt)
        from lunelis.importers.video import poster_frame, probe
        with open(p, "rb") as fh:
            info = probe(fh)
        with open(p, "rb") as fh:
            img = poster_frame(fh, 64)
        return f"PyAV {av.__version__}, {img.size}, {info.get('duration_s')}"
    check("Video (PyAV/FFmpeg)", video)

    def playback():
        # The photo view's player needs Qt Multimedia and its FFmpeg plugin in
        # the build; with the plugin missing no format can be decoded.
        from PySide6.QtMultimedia import QMediaFormat
        formats = QMediaFormat().supportedFileFormats(QMediaFormat.ConversionMode.Decode)
        if not formats:
            raise RuntimeError("Qt Multimedia has no backend (multimedia plugin missing)")
        from PySide6.QtMultimediaWidgets import QVideoWidget  # noqa: F401
        return f"{len(formats)} formats"
    check("Video playback (Qt Multimedia)", playback)

    for sample in samples or []:
        def render_sample(sample=sample):
            from lunelis.raw.thumbnails import render
            return f"{render(sample).size}"
        check(f"Sample {os.path.basename(sample)}", render_sample)

    def catalog():
        from lunelis.catalog.schema import MIGRATIONS, open_catalog
        conn = open_catalog(paths.DEFAULT_CATALOG_PATH)
        v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        conn.close()
        if v != MIGRATIONS[-1][0]:
            raise RuntimeError(f"schema {v}, expected {MIGRATIONS[-1][0]}")
        return f"schema v{v}"
    check("Catalog + migrations", catalog)

    def editing():
        # The edit engine (numpy) and export (piexif, sRGB profile) - a
        # module left out of the build only shows up here.
        import piexif
        from PIL import Image
        from lunelis.edit import pipeline
        from lunelis.edit.export import _srgb_icc
        from lunelis.edit.stack import Stack, loads
        a = pipeline.to_array(Image.effect_noise((64, 48), 40).convert("RGB"))
        s = loads("v=1;f=Vivid@50;exposure=0.5;shadows=20;sharpen=30;rotate=90")
        out = pipeline.apply_tiled(a, s, {"vibrance": 35}, rows=16)
        exif = piexif.dump({"0th": {piexif.ImageIFD.Software: b"Lunelis"}})
        return f"{out.shape[1]}x{out.shape[0]}, exif {len(exif)} B, icc {len(_srgb_icc())} B, {Stack() == loads('')}"
    check("Editing + export", editing)

    def onnx():
        # AI masks: the runtime must load (the models themselves download on first use).
        import onnxruntime
        providers = onnxruntime.get_available_providers()
        if "CPUExecutionProvider" not in providers:
            raise RuntimeError(f"no CPU provider: {providers}")
        return f"onnxruntime {onnxruntime.__version__}"
    check("AI masks runtime (onnxruntime)", onnx)

    def lenses():
        # The lens database ships as package data: a build that left it out finds nothing.
        from lunelis.edit import lens
        name = lens.profile_name(lens.LensInfo("SONY", "ILCE-7RM5", "FE 24-105mm F4 G OSS", 24.0, 4.0))
        if not name:
            raise RuntimeError("the lens database has no Sony FE 24-105mm")
        import cv2
        return f"{name}; OpenCV {cv2.__version__}"
    check("Lens profiles (lensfun) + OpenCV", lenses)

    def face_runtime():
        # The face models are downloaded later; the build must carry OpenCV's face classes to run them.
        import cv2
        for name in ("FaceDetectorYN", "FaceRecognizerSF"):
            if not hasattr(cv2, name):
                raise RuntimeError(f"OpenCV has no {name}")
        return "FaceDetectorYN, FaceRecognizerSF"
    check("Faces runtime (OpenCV)", face_runtime)

    def place_list():
        # The place list ships as package data (geo/places.tsv.gz): a build that left it out names nothing.
        from lunelis.geo import places
        tag = places.lookup(41.9028, 12.4964).tag
        if not tag.endswith("|Rome"):
            raise RuntimeError(f"Rome came out as {tag}")
        return tag
    check("Place list (GeoNames)", place_list)

    def licence_notices():
        # About > Third-party licences opens this; the build ships it beside the libraries.
        from lunelis import paths
        path = paths.PROJECT_ROOT / "THIRD-PARTY-LICENSES.md"
        if not path.exists():
            raise RuntimeError(f"{path} is missing")
        return f"{path.stat().st_size:,} bytes"
    check("Third-party licence notices", licence_notices)

    def worker_thumbnails():
        # Pages that read thumbnails on a worker thread (On this day, Library status) decode with Pillow.
        import tempfile
        from PIL import Image
        from lunelis.ui.thumbcache import load_image
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.jpg"
            Image.new("RGB", (64, 48), (200, 30, 30)).save(p)
            img = load_image(p, 24)
        if img.isNull() or img.height() != 24:
            raise RuntimeError("couldn't read a thumbnail")
        return f"{img.width()}x{img.height()}"
    check("Thumbnails on worker threads", worker_thumbnails)

    def window():
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([sys.argv[0]])
        from lunelis.ui.main_window import MainWindow
        w = MainWindow()
        title = w.windowTitle()
        w._quitting = True
        w.close()
        app.processEvents()
        return title
    check("Main window", window)

    ok = all(r[0] for r in results)
    text = "\n".join(("ok   " if good else "FAIL ") + line for good, line in results)
    text += f"\n\n{'PASS' if ok else 'FAIL'}\n"
    if report:
        Path(report).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if ok else 1
