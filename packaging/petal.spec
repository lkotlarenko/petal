from pathlib import Path

root = Path(SPECPATH).parent
# The hardware evidence signature hashes these sources in frozen builds too.
datas = [(str(p), 'petal') for pattern in ('*.py', '*.svg', '*.png') for p in (root / 'petal').glob(pattern)]
a = Analysis([str(root / 'packaging/entry.py')], pathex=[str(root)], datas=datas,
             hiddenimports=['petal._child'], excludes=['tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtQml'])
# Fontconfig reads the host's configuration and cache formats. An older bundled
# copy cannot interpret newer Arch/CachyOS configuration; use the host ABI.
a.binaries = [entry for entry in a.binaries if not Path(entry[0]).name.startswith('libfontconfig.so')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='petal', console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='petal')
