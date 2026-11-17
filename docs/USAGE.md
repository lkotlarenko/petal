# Using Petal

Petal makes smaller copies of local videos. Nothing is uploaded, and compression keeps originals in place.

## Your first batch

1. Choose a source folder and a separate output folder. Neither folder may contain the other. You can also use **Add videos…** or drop local files/folders into the window.
2. Click **Find videos**. Check the filenames and any explanations in the list.
3. Start with **Balanced** and your preferred **Picture size**. Small videos are not enlarged.
4. Click **Compress**. Petal checks graphics support, compresses each video and checks the result before keeping it.
5. Review **Batch report**. Watch a few copies before organizing originals.

**Jobs** shows waiting and interrupted work. **Finished** shows compressed, skipped and failed results. Search and filter the list, use **Select all shown**, or right-click a row for file actions. **Remove from list** never deletes media. **Return to Jobs** queues a result again; existing output files are still protected against replacement.

**Pause after current** lets current work drain. **Stop safely** cancels pending work and active subprocesses. Closing during work waits for shutdown and cleanup. Restarting restores lists and marks interrupted work ready to resume.

## Settings

- **Video format:** AV1 or H.265 using the graphics encoder. If AV1 fails its real output tests, compression may select H.265 after checking it and showing a notice.
- **Quality:** High Quality, Balanced or Maximum Savings. Try a few samples before making a large change.
- **Picture size:** 480p, 720p, 1080p, 1440p, 4K or Original. These are ceilings, not upscaling requests.
- **Thorough verification:** decodes the whole output. Turning it off checks decoded sections plus full track timing. It gives less corruption coverage.
- **Minimum saving:** keeps only worthwhile smaller copies; default 5% includes audio and container overhead.
- **Audio:** retain the original or use Opus.
- **Extra tracks:** keep/convert compatible tracks, keep exactly or skip, or explicitly omit unsupported tracks.
- **Picture checks:** score scenes from up to the first three eligible new successes. Scores do not replace integrity checks.
- **Compare samples:** select one video, then choose a sample folder outside its source folder. Three named profile samples are saved for visual comparison; paths appear in the detailed log. Samples do not predict whole-file savings.

For file moves and the warning-confirmed Trash action, see [Organizing originals](ORGANIZING.md). For scene scores, see [Picture checks](QUALITY.md).

## If something needs attention

**No graphics support:** check your render-device permissions and system VA-API driver. Select the correct device in Settings and run **Check graphics support**. Do not run Petal as root.

**Frame size needs attention:** the decoder and container disagree. Petal rejects a new output with that mismatch. Keep the original; use a format that passes the graphics check. This catches the observed 1920×1082 versus 1920×1080 AV1 output.

**Skipped:** inspect its explanation. Unsupported formats/HDR, existing destinations and insufficient savings are common reasons. Skipped originals stay in place.

**Needs attention:** inspect the row details and log. Disk, track/timing, decoder or permission errors keep the original. Existing outputs are never overwritten.

**No scores:** your FFmpeg may lack libvmaf or the required model, or the clip may be too short. A missing score does not invalidate a successfully verified copy.

**App will not open:** launch the AppImage from a terminal. Check system OpenGL/window libraries and file-picker portal. X11 needs the `libxkbcommon-x11` runtime (`libxkbcommon-x11-0` on Ubuntu). If FUSE is unavailable, use `APPIMAGE_EXTRACT_AND_RUN=1 ./Petal-<version>-x86_64.AppImage`.

**Interrupted move:** reopen/scan the same output with the same state directory. Keep journal and hold files until recovery completes. See the recovery guide before manually moving them.
