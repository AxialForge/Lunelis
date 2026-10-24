# PyInstaller build of Lunelis: a one-folder app (dist\Lunelis\Lunelis.exe +
# its libraries; on a Mac dist/Lunelis.app, see packaging/build_mac.sh). One-folder rather than one-file: a one-file exe unpacks
# ~150 MB of Qt to a temp folder on EVERY start, and trips antivirus more.
#
#   pyinstaller --noconfirm Lunelis.spec
#   dist\Lunelis\Lunelis.exe --self-test report.txt      (checks the build)
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

datas = [("assets", "assets"), ("src/lunelis/darktable/lunelis.lua", "lunelis/darktable"),
         ("src/lunelis/importing/camera_profiles.json", "lunelis/importing"),
         ("src/lunelis/create/presets.json", "lunelis/create"),
         ("src/lunelis/recognize/scene_labels.json", "lunelis/recognize"),
         ("src/lunelis/geo/places.tsv.gz", "lunelis/geo"), ("src/lunelis/geo/ATTRIBUTION.txt", "lunelis/geo"),
         ("THIRD-PARTY-LICENSES.md", "."), ("LICENSE", ".")] \
    + copy_metadata("lunelis") + collect_data_files("lensfunpy")      # lensfun's lens database
# Each bundled library's own licence texts (its dist-info folder), as THIRD-PARTY-LICENSES.md says.
for _dist in ("PySide6", "PySide6_Essentials", "shiboken6", "Pillow", "pillow_heif", "rawpy", "av", "exifread",
              "piexif", "numpy", "onnxruntime", "lensfunpy", "opencv-python-headless"):
    try:
        datas += copy_metadata(_dist)
    except Exception:                       # a renamed distribution must not break the build
        print(f"no metadata for {_dist}")
binaries = collect_dynamic_libs("rawpy") + collect_dynamic_libs("pillow_heif") + collect_dynamic_libs("av") \
    + collect_dynamic_libs("onnxruntime") + collect_dynamic_libs("lensfunpy")
hiddenimports = collect_submodules("av") + collect_submodules("lunelis") + collect_submodules("onnxruntime") \
    + ["pillow_heif"]

a = Analysis(
    ["packaging/launch.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.Qt3DCore", "PySide6.QtPdf", "PySide6.QtCharts"],
    noarchive=False,
)
MAC = sys.platform == "darwin"
if MAC:
    # opencv-python-headless and lensfunpy each bundle libintl.8.dylib and
    # PyInstaller keeps one of them; lensfun's older copy lacks
    # libintl_bindtextdomain, which OpenCV's libgnutls needs (the self-test's
    # "dlopen ... Symbol not found" on the first Mac build). OpenCV's newer
    # gettext is a superset: every libintl in the bundle is that one.
    import glob
    import importlib.util
    _cv2 = importlib.util.find_spec("cv2").submodule_search_locations[0]
    _intl = (glob.glob(os.path.join(_cv2, ".dylibs", "libintl.8.dylib")) or [None])[0]
    if _intl:
        a.binaries = [(d, _intl if os.path.basename(d) == "libintl.8.dylib" else src, kind)
                      for d, src, kind in a.binaries]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lunelis",
    icon="packaging/mac/Lunelis.icns" if MAC else "assets/icons/lunelis.ico",
    console=False,
    version=None if MAC else "packaging/version_info.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, name="Lunelis")
if MAC:
    from importlib.metadata import version as _version
    _v = _version("lunelis")
    app = BUNDLE(
        coll,
        name="Lunelis.app",
        icon="packaging/mac/Lunelis.icns",
        bundle_identifier="com.axialforge.lunelis",
        version=_v,
        info_plist={
            "CFBundleName": "Lunelis",
            "CFBundleDisplayName": "Lunelis",
            "CFBundleShortVersionString": _v,
            "CFBundleVersion": _v,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.photography",
            "NSHumanReadableCopyright": "MIT licence",
            # Asked by macOS the first time Lunelis reads these places:
            "NSRemovableVolumesUsageDescription": "Lunelis reads photos from memory cards and USB drives.",
            "NSNetworkVolumesUsageDescription": "Lunelis reads photos from folders on your network storage.",
            "NSDesktopFolderUsageDescription": "Lunelis reads photos from folders you add to the library.",
            "NSDocumentsFolderUsageDescription": "Lunelis reads photos from folders you add to the library.",
            "NSDownloadsFolderUsageDescription": "Lunelis reads photos from folders you add to the library.",
        },
    )
