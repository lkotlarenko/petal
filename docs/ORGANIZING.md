# Reports, lists and original-file organization

Compression still leaves every original in place. Organization is a separate operation, available only while compression is idle, and runs only after you confirm a move.

## Report and lists

A report opens when a batch completes or stops safely. **Batch report** reopens it later, including after a restart. Saved space counts verified compressed files; moving an unchanged original is not counted as compression savings. Counts include successful, skipped and failed results, with unfinished files reported separately. Clicking a count opens the matching Finished filter and clears any older search.

**Jobs** contains waiting/interrupted work. **Finished** contains compressed, skipped and failed results. Search both lists by filename, folder, status or explanation. Add files through **Add videos**, choose a source folder or drop local folders/files. Select rows and use **Remove selected**, or the right-click menu, to remove list entries without deleting media. Removing a row does not rewrite the historical batch report.

Right-click rows to open the original or compressed file, show the original folder, copy its path, inspect details, return selected files to Jobs, move eligible originals or remove list entries. A relocated original opens from its new location. Returned results remain unchanged on disk; the engine still refuses to overwrite an existing compressed destination. Right-click headers to show optional columns, including source codec.

Results are the latest stored entry for each original path, rather than an unlimited archive of every encoding attempt. A report is a separate snapshot and does not change when you queue a file again. Interrupted encoding/verifying rows restore as unfinished work. Source/output changes still require a fresh scan where the queue no longer matches the selected folders.

## Move skipped or failed originals

1. Open the report after processing. Inspect skipped/error explanations if needed.
2. Choose **Move skipped originals** or **Move failed originals**. The confirmation gives the exact destination root and number of originals.
3. Leave Petal open while it copies and verifies. Progress names each stage. **Stop moving safely** cancels remaining work and retains/restores the current source before removal.
4. Review Finished for moved locations or a move issue. A collision or changed file leaves the original in place. The context menu can organize selected rows instead of the whole report.

For source `Videos/series/clip.mp4`, the skipped original goes to `Output/skipped/series/clip.mp4`, retaining its bytes and extension. Folder-drop namespaces remain part of the relative path. Failed originals use `Output/failed/…`. Existing destinations, including symlinks, are never replaced. Mixed selections from different output roots must be organized separately. Completed originals are not eligible for these move actions.

## Transfer protocol

The mover holds the output lock so it cannot overlap another Petal operation on that output. It opens source/output directories without traversing symlink components, checks available source identity against the job, and rejects path traversal and inputs inside the output tree.

It copies to a private, journaled file on the destination filesystem, handles short writes, preserves basic mode and timestamps, syncs the copy, rereads it and compares full SHA-256 checksums against the source. Publication uses a no-replacement hard link in the output directory. The copy and directory entry are made durable before source removal is considered.

The source is briefly renamed to a unique hold name in its own directory. Its identity and full checksum are checked again, and the published copy is reread immediately before removal. Changed source/copy identities, read errors or cancellation restore/retain the original. Source removal and both directory states are synced; the journal records completion. This also supports moves between filesystems: only the verified copy crosses devices, while each rename/link stays on its own filesystem.

These checks detect ordinary copy errors and concurrent changes; no software can guarantee against all failing hardware, hostile concurrent modifications or filesystems that misreport durability. Do not edit files during relocation. This verifies byte integrity, not whether an already-failed original was a playable video. ACLs, ownership and extended attributes are not guaranteed to transfer between filesystems. Keep backups of irreplaceable media.

## Recovery

The XDG state directory contains `history.json` and `relocations-<output-hash>.json`, alongside existing verification state. Move journals name exact temporary/hold files and checksum proofs. Do not delete them to clear a list.

On startup for a configured output, on scanning that output and before another move, Petal examines its journal. A held original is restored to its original name without replacing a newly created file. If source removal completed before its final journal update, the durable destination is verified and recognized. Recovery never automatically deletes an original. A verified published copy may remain alongside a retained source after cancellation; retrying the move rechecks the journaled copy before finishing.

An interrupted destination copy may use `.petal-<uuid>.move-copy`; a held original uses `.petal-<uuid>.move-source`. Do not remove a source hold manually. If recovery encounters a changed file, a new file at the original path, unavailable directories or an unreadable journal, it retains files and reports the issue. Inspect the log and preserve both paths until you have compared them.

## Collections processed before 0.4

Scan the same source/output folders with the same state directory. Unchanged recorded completed outputs appear in Finished; this displays their recorded result, rather than creating a fresh integrity proof. Returning a file to Jobs uses the engine's current checks before reusing any output.

Older versions did not persist skipped/error results. Petal therefore cannot reliably reconstruct those labels from a missing output. Remaining originals stay in Jobs. To organize them without another encode, explicitly select the desired rows and **Skip selected**, then use **Batch report → Move skipped originals**. Files known to have errored earlier can be inspected through their old logs; Petal will not fabricate an error classification.
