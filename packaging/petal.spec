from pathlib import Path

root = Path(SPECPATH).parent
# The hardware evidence signature hashes these sources in frozen builds too.
datas = [(str(p), 'petal') for pattern in ('*.py', '*.svg') for p in (root / 'petal').glob(pattern)]
a = Analysis([str(root / 'packaging/entry.py')], pathex=[str(root)], datas=datas,
             hiddenimports=['petal._child'], excludes=['tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtQml'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='petal', console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='petal')
