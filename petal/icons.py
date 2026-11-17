"""Raster desktop icons and explicit user-level AppImage integration."""

import os
from pathlib import Path


def render_icons(directory):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(Path(__file__).with_name("petal.svg")))
    if not renderer.isValid():
        raise RuntimeError("Invalid Petal SVG")
    for size in (32, 48, 64, 128, 256, 512):
        target = directory / str(size) / "petal.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        if not image.save(str(target)):
            raise RuntimeError("Could not render application icon")
    app.processEvents()


def install_desktop(data_home=None, appimage=None):
    """Only called by an explicit application action or --install-desktop."""
    base = Path(
        data_home or os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))
    )
    if not base.is_absolute():
        raise ValueError("Desktop data directory must be absolute")
    image = Path(appimage or os.environ.get("APPIMAGE", "")).expanduser()
    if not image.is_absolute() or not image.is_file():
        raise ValueError(
            "Run the AppImage from its permanent location before adding it to applications"
        )
    if any(c in str(base) + str(image) for c in "\r\n"):
        raise ValueError("Desktop paths must not contain line breaks")
    applications = base / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    icon = base / "icons/hicolor/256x256/apps/petal.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    from PySide6.QtGui import QIcon

    if (
        not QIcon(str(Path(__file__).with_name("petal.png")))
        .pixmap(256, 256)
        .save(str(icon))
    ):
        raise RuntimeError("Could not install desktop icon")
    executable = str(image)
    for before, after in [
        ("\\", "\\\\"),
        ('"', '\\"'),
        ("`", "\\`"),
        ("$", "\\$"),
        ("%", "%%"),
    ]:
        executable = executable.replace(before, after)
    entry = applications / "petal.desktop"
    text = f'[Desktop Entry]\nType=Application\nName=Petal\nComment=Careful GPU video compression\nExec="{executable}"\nIcon={icon}\nTerminal=false\nCategories=AudioVideo;Video;\nStartupWMClass=petal\nStartupNotify=true\n'
    temporary = entry.with_suffix(".desktop.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(entry)
    return entry
