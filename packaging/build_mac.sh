#!/usr/bin/env bash
# Mac build: Lunelis.app (PyInstaller, Lunelis.spec) -> self-test -> Lunelis-vX.Y.Z-macos.dmg
# (+ .sha256). Run on a Mac from the repo root after `pip install . pyinstaller`:
#   bash packaging/build_mac.sh            (version from pyproject.toml)
# Unsigned (no paid Developer ID): PyInstaller signs ad hoc, which Apple
# silicon requires; users open it once with right-click > Open.
set -euo pipefail
cd "$(dirname "$0")/.."

version=$(python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
arch=$(uname -m)                                   # arm64 / x86_64

# Icon: app_icon.png -> every size macOS wants -> Lunelis.icns
mkdir -p packaging/mac
iconset=$(mktemp -d)/Lunelis.iconset
mkdir -p "$iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s assets/icons/app_icon.png --out "$iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) assets/icons/app_icon.png --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o packaging/mac/Lunelis.icns

pyinstaller --noconfirm Lunelis.spec

# The packaged app checks its own libraries, assets, migrations and window.
report=$(mktemp)
if ! dist/Lunelis.app/Contents/MacOS/Lunelis --self-test "$report"; then
  cat "$report"; echo "self-test failed"; exit 1
fi
cat "$report"

# A plain disk image: the app and a link to Applications, to drag onto.
stage=$(mktemp -d)
cp -R dist/Lunelis.app "$stage/"
ln -s /Applications "$stage/Applications"
dmg="Lunelis-v${version}-macos-${arch}.dmg"
rm -f "$dmg"
hdiutil create -volname "Lunelis ${version}" -srcfolder "$stage" -ov -format UDZO "$dmg" >/dev/null
shasum -a 256 "$dmg" | sed 's/ .*//' | tr -d '\n' > "$dmg.sha256"
printf '  %s' "$dmg" >> "$dmg.sha256"
echo "built $dmg"
