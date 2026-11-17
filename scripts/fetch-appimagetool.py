#!/usr/bin/env python3
"""Download the official tool and verify GitHub's published SHA-256 digest."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

metadata = json.loads(subprocess.check_output([
    'gh', 'api', 'repos/AppImage/appimagetool/releases/latest'], text=True))
asset = next(a for a in metadata['assets'] if a['name'] == 'appimagetool-x86_64.AppImage')
digest = asset.get('digest', '')
if not digest.startswith('sha256:'):
    raise SystemExit('Upstream has no SHA-256 digest; refusing an unchecked tool')
root = Path(__file__).resolve().parent.parent / 'build/tools'
root.mkdir(parents=True, exist_ok=True)
path = root / 'appimagetool'
with urllib.request.urlopen(asset['browser_download_url'], timeout=60) as response:
    data = response.read()
if hashlib.sha256(data).hexdigest() != digest.removeprefix('sha256:'):
    raise SystemExit('AppImage tool checksum mismatch')
path.write_bytes(data)
path.chmod(0o755)
print(f"Verified {metadata['tag_name']}: {digest}")
