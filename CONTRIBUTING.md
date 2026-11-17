# Contributing to Petal

Use Python 3.11+ and system FFmpeg/FFprobe with libx264, libx265, libaom-av1 and FFV1 for integration tests. Install PySide6 to include native offscreen Qt tests. The packaging toolchain uses Python 3.12 in CI.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r packaging/requirements.txt
PETAL_PYTHON=.venv/bin/python ./scripts/test.sh
```

`PETAL_TEST_GPU=1` enables the actual-device test; alternatively run `./scripts/test.sh --gpu`. A missing GPU is an explicit skip in normal CI. Missing libvmaf skips VMAF integration tests. Inspect skips before claiming coverage.

Keep changes focused. Describe the visible behavior, tests run and remaining limitations in your pull request. Follow existing Python formatting and use argument arrays for subprocesses. Keep FFmpeg work off the Qt thread. Do not weaken verification, no-clobber publication, source preservation or cancellation to make an encode succeed. See [design](docs/DESIGN.md) and [safety reference](docs/REFERENCE.md).

Tests create synthetic media in temporary directories. Do not commit personal media, source filenames, local settings, tokens or captured user logs. `scripts/screenshots.py` renders the real interface with explicit demonstration data and isolated XDG paths.

```bash
python3 scripts/screenshots.py
python3 -m compileall -q petal scripts tests
bash -n launch.sh scripts/test.sh scripts/build-appimage.sh
```

Use regression tests for meaningful failure modes. GPU fixtures must never become a production CPU-encoding fallback. The build toolchain pins direct dependencies; transitive packages and upstream AppImage runtime are not fully locked, so builds are not claimed byte-for-byte reproducible.

Petal uses GPL-3.0-only. Contributions are provided under the same license. See [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY.md).

For library/report changes, run the native GUI tests and the history/relocation suites. File moves must retain no-clobber behavior, full-byte verification, directory durability and source-hold recovery; do not replace this protocol with `shutil.move` or a cross-device rename fallback. Screenshots must continue to use isolated demonstration data. See [UI QA](docs/UI_QA.md) and [organization protocol](docs/ORGANIZING.md).
