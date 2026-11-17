# Third-party software

Petal is GPL-3.0-only; see [LICENSE](LICENSE). The app is written in Rust. Its AppImage does not contain Python, PySide6 or Qt.

The native interface uses eframe/egui with its OpenGL renderer and bundled fonts. File pickers use rfd and the desktop portal. Serialization, checksums and filesystem traversal use serde/serde_json, sha2, libc, uuid, walkdir and image. Exact versions and transitive dependencies are recorded in [Cargo.lock](Cargo.lock).

The AppImage includes dependency notices and license texts under `usr/share/licenses/petal/`. The collection includes lockfile dependencies for other platforms and build tools; inclusion in that notice directory does not mean all of them are linked into the Linux executable. Dependency source archives are identified in each notice. Rust's standard library uses its MIT/Apache licensing; see the [Rust license](https://github.com/rust-lang/rust/tree/stable/LICENSES).

FFmpeg, FFprobe, the graphics driver, desktop portal, OpenGL/window-system libraries, `gio` and `xdg-open` are provided by the Linux system. They are not bundled. Their features and licenses depend on the distribution. VMAF uses the installed FFmpeg/libvmaf model; no model download or online analysis is performed.

Source for each Petal release is available from its GitHub release tag. Build scripts, locked Rust dependencies and packaging instructions are included in the repository. The older Python implementation remains in the Git history and in the 0.4.0 release, rather than in the maintained source tree.
