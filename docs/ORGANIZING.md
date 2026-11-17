# Organizing originals

Compression keeps originals. The batch report offers separate actions after processing is idle. Each move requires confirmation; Trash has a specific warning.

## Report and lists

The report shows compressed, skipped, failed and unfinished counts, plus the bytes saved by successful smaller copies. Moving an unchanged original is not counted as compression savings. Clicking a result count filters Finished. The latest report and Jobs/Finished lists survive restart.

Removing a row removes it from the list only. Returning a result to Jobs does not delete either file or rewrite historical totals. Right-click a row to open files, show a folder, copy a path, inspect details/quality, requeue or organize eligible originals.

## Skipped and failed files

**Move skipped originals** sends `Videos/series/clip.mp4` to `Output/skipped/series/clip.mp4`. Failed files use `Output/failed/…`. Relative source folders and mixed-input namespaces are retained. Existing destination files are never replaced. The context menu can move selected eligible rows instead of the whole report.

On the **same filesystem**, Petal uses an atomic rename with no replacement. It does not copy/reread the entire video. The inode and file bytes remain the same. Source identity checks, a durable journal and directory synchronization protect the operation.

**Between filesystems**, Petal copies into a private file, calculates a full source checksum, syncs the copy and verifies its contents. It keeps the destination without replacement, holds the original under a private name, rechecks source/destination hashes and identities, then removes the held source. A changed file, read error, collision or cancellation retains/restores the original. Basic timestamps are preserved; ownership, ACLs and extended attributes are not guaranteed across filesystems.

These checks handle ordinary failures and concurrent changes; they cannot guarantee against every hardware/filesystem failure. Avoid editing files during organization and keep backups of irreplaceable media.

## Successfully compressed originals

**Move compressed originals to Trash** is on the report. Its warning explains that smaller copies are kept and originals go to desktop Trash. Only successful results with unchanged originals and outputs are eligible. Petal checks the successful record, requires full verification and compares full-file source/output SHA-256 hashes before asking GIO to trash the original.

If the result is old or was only sample-verified, Petal requires a successful full verification before eligibility is upgraded. Missing, modified or unverifiable files keep their originals. If GIO/Trash fails, the original is retained and the error is reported. Petal never substitutes permanent deletion and never empties Trash. Space is not actually reclaimed while originals remain in Trash.

## Recovery

Keep the state directory and journals. Native library/proof/move state lives under `$XDG_STATE_HOME/petal` (normally `~/.local/state/petal`). Move journals are `native-moves-<hash>.json`; active-output journals are `native-active-<uuid>.json`.

On scanning or moving the same output, registered recovery records are examined under the output lock. A held original is restored without replacing a newly created file. Already completed moves can be recognized from destination identity/hash. Recovery never automatically deletes an original. A verified copy can remain beside a retained source after cancellation; retry rechecks it before finishing.

Only exact registered temporary paths are cleanup candidates. Unknown similarly named files are left alone. If recovery reports a collision or changed file, retain both paths and inspect the log. Do not delete `.petal-*` source holds or journals just to clear the UI.

## Older collections

The Rust app can import 0.4 settings/history and recognize unchanged completion records. Older versions that did not save skipped/error labels cannot reconstruct them reliably. Explicitly select remaining Jobs and choose **Skip selected** to organize them without encoding. Petal does not invent an error classification from the absence of an output.
