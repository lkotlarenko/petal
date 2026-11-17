# Technical reference

## Commands

Run the AppImage with these arguments, or use `target/release/petal` from a source build:

```text
petal --help
petal --version
petal --install-desktop
petal scan SOURCE OUTPUT
petal compress SOURCE OUTPUT
petal doctor
petal sample VIDEO SAMPLE_FOLDER
petal quality ORIGINAL COMPRESSED
petal verify ORIGINAL COMPRESSED
```

Options include `--codec av1_vaapi|hevc_vaapi`, `--profile "High Quality"|Balanced|"Maximum Savings"`, `--resolution 480p|720p|1080p|1440p|2160p|original`, `--device /dev/dri/renderD128`, `--audio copy|opus`, `--min-savings 5`, `--stream-policy compatible|skip|omit`, `--sampled-verification` and `--no-auto-quality`.

`scan` prints job JSON; diagnostic events go to standard error. `quality` prints the sampled report. `verify` checks an existing pair. `doctor` forces a real selected-format graphics check. `sample` writes unique, non-overwriting profile clips and logs their paths. Internal packaging checks use `self-test`, `--write-icons`, `--smoke-test` and read-only demonstration mode `--demo`.

## Local data

| Location | Content |
| --- | --- |
| `$XDG_STATE_HOME/petal/native-v1.json` | Settings, Jobs/Finished and latest report |
| `$XDG_STATE_HOME/petal/native-proofs/` | Completion records and verification hashes |
| `$XDG_STATE_HOME/petal/native-moves-*.json` | Move recovery journals |
| `$XDG_STATE_HOME/petal/native-active-*.json` | Registered encoding partials |
| `$XDG_STATE_HOME/petal/native-hardware.json` | Successful graphics test evidence |
| `$XDG_CACHE_HOME/petal/native-probes.json` | Disposable discovery cache |
| `$XDG_DATA_HOME/applications/petal.desktop` | Explicitly installed menu entry |
| `$XDG_DATA_HOME/icons/hicolor/…/apps/petal.png` | Explicitly installed icon |

Unset XDG variables use the standard home locations: `~/.local/state`, `~/.cache` and `~/.local/share`. Keep state/journals for recovery. Clearing the discovery cache only costs another scan. Legacy settings/history are imported if native state does not yet exist and are left untouched.

## Limits

Linux x86_64 and AMD VA-API are the release target. HDR, interlaced and unsupported pixel/stream combinations may be skipped. The destination must support local locking, directory synchronization and no-replacement hard links/renames. Network/unusual filesystems may reject these operations safely. Neither output creation nor organization overwrites existing files.

The AppImage is built on Ubuntu 24.04 and needs compatible host system libraries. FFmpeg, drivers, GIO and file-picker portal are host dependencies; no encoder/runtime is downloaded when processing media. A package checksum checks the downloaded file, not the safety of arbitrary media or the health of your storage.
