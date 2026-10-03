# PyInstaller build of Lunelis: a one-folder app (dist\Lunelis\Lunelis.exe +
# its libraries). One-folder rather than one-file: a one-file exe unpacks
# ~150 MB of Qt to a temp folder on EVERY start, and trips antivirus more.
#
#   pyinstaller --noconfirm Lunelis.spec
#   dist\Lunelis\Lunelis.exe --self-test report.txt      (checks the build)
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

datas = [("assets", "assets"), ("src/lunelis/darktable/lunelis.lua", "lunelis/darktable"),
         ("src/lunelis/importing/camera_profiles.json", "lunelis/importing")] \
    + copy_metadata("lunelis") + collect_data_files("lensfunpy")      # lensfun's lens database
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
              "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lunelis",
    icon="assets/icons/lunelis.ico",
    console=False,
    version="packaging/version_info.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, name="Lunelis")
