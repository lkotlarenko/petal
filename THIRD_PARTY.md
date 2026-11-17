# Third-party software

Petal's AppImage bundles Python (PSF license), PySide6/Qt and Shiboken (LGPLv3/GPLv3/commercial, depending on component), and the PyInstaller bootloader (GPL with its bootloader exception). Dependency license texts are retained in the bundle. Qt is dynamically linked; the extracted AppDir can be inspected and its Qt libraries replaced with compatible modified versions. Use `./Petal-<version>-x86_64.AppImage --appimage-extract` to extract it.

- [Python license](https://docs.python.org/3/license.html)
- [Qt for Python licensing](https://doc.qt.io/qtforpython-6/licenses.html)
- [Qt LGPL obligations](https://www.qt.io/licensing/open-source-lgpl-obligations)
- [PyInstaller license and exception](https://pyinstaller.org/en/stable/license.html)
- [AppImage runtime](https://github.com/AppImage/type2-runtime)

FFmpeg, FFprobe, libva and Mesa drivers are **system dependencies**, not bundled. Their features and licenses depend on the distribution build. VMAF is optional and uses the system FFmpeg/libvmaf models. [VMAF license](https://github.com/Netflix/vmaf/blob/master/LICENSE).

Petal is licensed under **GPL-3.0-only**, copyright (C) 2026 Petal contributors. See [LICENSE](LICENSE). Source corresponding to every release is available from that release's Git tag at [lkotlarenko/petal](https://github.com/lkotlarenko/petal). Build instructions, packaging scripts and dependency versions are included in the repository.

The bundled Qt/PySide6 source version is 6.12.0; its upstream source is available in the [Qt for Python 6.12.0 source tree](https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=v6.12.0) and [Qt 6.12.0 sources](https://download.qt.io/archive/qt/6.12/6.12.0/single/). Python and PyInstaller source versions are identified by the CI workflow/toolchain; [CPython](https://github.com/python/cpython) and [PyInstaller 6.22.3](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3) provide their source. Included license texts are in `packaging/licenses/` and the extracted image's `usr/share/licenses/petal/`.
