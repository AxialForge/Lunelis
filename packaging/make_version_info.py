"""Writes packaging/version_info.txt (the exe's Properties > Details) from
pyproject.toml - the one place the version lives. Run before PyInstaller."""
import re
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
nums = [int(x) for x in re.findall(r"\d+", version)[:3]] + [0]
nums = (nums + [0, 0, 0, 0])[:4]
(root / "packaging" / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(nums)}, prodvers={tuple(nums)}),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'AxialForge'),
      StringStruct('FileDescription', 'Lunelis photo library'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('ProductName', 'Lunelis'),
      StringStruct('ProductVersion', '{version}'),
      StringStruct('OriginalFilename', 'Lunelis.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
print(version)
