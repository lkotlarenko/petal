Petal is now a native Rust app. Python and Qt are not included or required.

Download the AppImage and checksum, make the AppImage executable, and open it. Videos stay on your computer. Your existing Petal settings and recorded results can be imported automatically.

New: the report can move successfully compressed **originals** to desktop Trash, after a warning and fresh checks. The smaller compressed copies are kept. Same-filesystem moves of skipped/failed files now use atomic moves instead of full copies.

FFmpeg/FFprobe and graphics drivers remain system requirements. Desktop Trash uses `gio` (GLib). See the README for setup and the comparison report for measured size and timing results.
