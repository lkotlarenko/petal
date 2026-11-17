#!/usr/bin/env python3
"""Exercise the release's CLI, subprocess trampoline, resources and native Qt UI."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

image = Path(os.environ["PETAL_APPIMAGE"]).resolve()
with tempfile.TemporaryDirectory(prefix="petal-package-test-") as folder:
    root = Path(folder)
    subprocess.run(
        [str(image), "--appimage-extract"],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        timeout=60,
    )
    launcher = root / "squashfs-root/AppRun"
    appdir = root / "squashfs-root"
    assert (appdir / "petal.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (appdir / ".DirIcon").resolve() == (appdir / "petal.png")
    for size in (32, 48, 64, 128, 256, 512):
        assert (
            appdir / f"usr/share/icons/hicolor/{size}x{size}/apps/petal.png"
        ).is_file()
    env = dict(
        os.environ,
        QT_QPA_PLATFORM="offscreen",
        XDG_CONFIG_HOME=str(root / "config"),
        XDG_STATE_HOME=str(root / "state"),
        XDG_CACHE_HOME=str(root / "cache"),
        XDG_DATA_HOME=str(root / "data"),
        APPIMAGE=str(image),
    )

    def run(*args):
        result = subprocess.run(
            [str(launcher), *args],
            env=env,
            text=True,
            capture_output=True,
            check=True,
            timeout=60,
        )
        if "Fontconfig warning:" in result.stderr:
            raise RuntimeError(result.stderr)
        return result.stdout

    print(run("--version").strip())
    source = root / "source"
    source.mkdir()
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10",
            "-t",
            "1",
            "-c:v",
            "libx264",
            str(source / "demo.mkv"),
        ],
        check=True,
        timeout=30,
    )
    jobs = json.loads(run("scan", str(source), str(root / "output")))
    assert len(jobs) == 1 and jobs[0]["duration"] > 0, jobs
    run("--smoke-test")
    run("--install-desktop")
    assert (root / "data/applications/petal.desktop").is_file()
    assert (root / "data/icons/hicolor/256x256/apps/petal.png").is_file()
    # The runtime also needs to work without a FUSE mount.
    subprocess.run(
        [str(image), "--version"],
        env=dict(env, APPIMAGE_EXTRACT_AND_RUN="1"),
        check=True,
        timeout=60,
    )
print("Packaged CLI, media discovery, Qt UI/icon and extract-and-run passed.")
