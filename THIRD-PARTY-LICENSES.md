# Third-party software in Lunelis

Lunelis itself is MIT-licensed (see `LICENSE`). The Windows build bundles the
libraries below, each under its own licence. Their full licence texts ship
with the build, in each package's `*.dist-info` folder inside the Lunelis
program folder (`_internal`), and at the links given here. Where a licence
asks for the source code, it is at the link given.

Because the bundled FFmpeg and pillow-heif builds include GPL-2.0 components (x264 and
x265), the Windows build as a whole is distributed under the GPL-2.0-or-later. Lunelis's own
source code stays MIT-licensed, and the source of each GPL component is at the link below.

This list is for information. It is not legal advice.

## Libraries in the program

| Component | Used for | Licence | Source |
|---|---|---|---|
| Qt 6 and PySide6 / Shiboken6 | The whole interface | LGPL-3.0 (Qt and PySide6 are also offered under GPL and commercial terms) | https://code.qt.io, https://pyside.org |
| Python 3.13 | Runs Lunelis | PSF License | https://www.python.org |
| Pillow | Reading and writing images | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| pillow-heif with libheif, libde265 and x265 | HEIC / HEIF photos | pillow-heif BSD-3-Clause; its Windows wheel as a whole GPL-2.0 because of x265; libheif and libde265 LGPL-3.0 | https://github.com/bigcat88/pillow_heif, https://github.com/strukturag/libheif, https://github.com/strukturag/libde265, https://bitbucket.org/multicoreware/x265_git |
| rawpy with LibRaw | RAW photos | rawpy MIT; LibRaw LGPL-2.1 or CDDL-1.0 | https://github.com/letmaik/rawpy, https://www.libraw.org |
| PyAV with FFmpeg | Videos | PyAV BSD-3-Clause; the bundled FFmpeg build includes x264 and x265, which makes it GPL-2.0-or-later | https://github.com/PyAV-Org/PyAV, https://ffmpeg.org |
| exifread | Reading EXIF | BSD-3-Clause | https://github.com/ianare/exif-py |
| piexif | Writing EXIF into exports | MIT | https://github.com/hMatoba/Piexif |
| NumPy | Image maths | BSD-3-Clause and others (see its licence file) | https://numpy.org |
| ONNX Runtime | Running the optional AI models | MIT | https://onnxruntime.ai |
| OpenCV (opencv-python-headless) | Face detection and recognition | Apache-2.0 (bundled parts: see `cv2/LICENSE-3RD-PARTY.txt`) | https://github.com/opencv/opencv-python |
| lensfunpy with lensfun | Lens corrections | lensfunpy MIT; lensfun library LGPL-3.0; lens database CC BY-SA 3.0 | https://github.com/letmaik/lensfunpy, https://lensfun.github.io |

## Data in the program

| Data | Used for | Licence | Source |
|---|---|---|---|
| GeoNames cities15000, admin1 and country info | Place names for photo locations | CC BY 4.0 (see `lunelis/geo/ATTRIBUTION.txt`) | https://www.geonames.org |

## Optional downloads (only when you choose them)

| Model | Used for | Licence | Source |
|---|---|---|---|
| OpenAI CLIP ViT-B/32 (ONNX by Xenova) | Scene tags, Find similar | MIT | https://huggingface.co/Xenova/clip-vit-base-patch32 |
| YuNet (OpenCV Zoo) | Finding faces | MIT | https://github.com/opencv/opencv_zoo |
| SFace (OpenCV Zoo) | Recognising faces | Apache-2.0 | https://github.com/opencv/opencv_zoo |
| Silueta (rembg) | Subject masks | MIT (rembg) | https://github.com/danielgatis/rembg |
| skyseg | Sky masks | Not stated by its publisher | https://huggingface.co/JianyuanWang/skyseg |

## Online services (only when used)

- **GitHub** (`api.github.com`, `github.com`): the daily update check and
  downloads.
- **OpenStreetMap** tiles, when the online map is turned on: map data
  © OpenStreetMap contributors, ODbL. https://www.openstreetmap.org/copyright
