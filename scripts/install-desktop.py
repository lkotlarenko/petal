#!/usr/bin/env python3
"""Install a local desktop entry referencing this checkout; no system writes."""
import argparse
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--target", type=Path, help="Alternative applications directory (for review)")
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
if not base.is_absolute():
    base = Path.home() / ".local/share"
directory = args.target or base / "applications"
directory.mkdir(parents=True, exist_ok=True)
launcher = str(root / "launch.sh")
for before, after in (("\\", "\\\\"), ('"', '\\"'), ("`", "\\`"), ("$", "\\$"), ("%", "%%")):
    launcher = launcher.replace(before, after)
entry = directory / "petal.desktop"
entry.write_text(f'''[Desktop Entry]
Type=Application
Name=Petal
Comment=Careful GPU batch video compression
Exec="{launcher}"
Icon={root / 'petal/petal.svg'}
Terminal=false
Categories=AudioVideo;Video;
StartupNotify=true
StartupWMClass=petal
''', encoding="utf-8")
print(f"Installed {entry}. Keep this checkout at {root}.")
