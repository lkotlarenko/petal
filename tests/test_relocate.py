"""Original-file relocation: byte integrity, interruption and no-clobber tests."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from petal.models import Job, Status
from petal.relocate import Relocator
from petal.safety import fingerprint, output_lock


class PowerLoss(BaseException):
    pass


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "output"
        self.state = self.root / "state"
        path = self.source / "nested" / "猫.mp4"
        path.parent.mkdir()
        path.write_bytes(os.urandom(200000))
        self.original = path.read_bytes()
        self.job = Job(
            path,
            Path("nested/猫.mp4"),
            self.output / "nested/猫.mkv",
            size=len(self.original),
            status=Status.SKIPPED,
            source_key=fingerprint(path),
            output_root=self.output,
        )
        self.worker = Relocator(self.output, self.state)

    def test_verified_move_preserves_bytes_name_mtime_and_is_idempotent(self):
        stamp = self.job.source.stat().st_mtime_ns
        result = self.worker.move([self.job], "skipped")
        self.assertTrue(result[0][1], result)
        self.assertFalse(self.job.source.exists())
        target = self.output / "skipped/nested/猫.mp4"
        self.assertEqual(target.read_bytes(), self.original)
        self.assertEqual(target.stat().st_mtime_ns, stamp)
        self.assertEqual(self.job.relocated_to, target)
        self.assertEqual(self.worker.move([self.job], "skipped"), [])
        self.assertFalse(list(self.output.rglob("*.move-copy")))

    def test_collision_never_replaces_destination_or_removes_original(self):
        target = self.output / "skipped" / self.job.relative
        target.parent.mkdir(parents=True)
        target.write_bytes(b"existing")
        result = self.worker.move([self.job], "skipped")
        self.assertFalse(result[0][1])
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(self.job.source.read_bytes(), self.original)

    def test_changed_original_symlinks_and_traversal_are_refused(self):
        self.job.source.write_bytes(b"changed")
        self.assertFalse(self.worker.move([self.job], "skipped")[0][1])
        self.assertEqual(self.job.source.read_bytes(), b"changed")
        self.job.source_key = ""
        self.job.relative = Path("../escape.mp4")
        self.assertFalse(self.worker.move([self.job], "skipped")[0][1])
        self.job.relative = Path("nested/猫.mp4")
        self.job.source.unlink()
        self.job.source.symlink_to(self.root / "other")
        (self.root / "other").write_bytes(self.original)
        self.assertFalse(self.worker.move([self.job], "skipped")[0][1])
        self.assertEqual((self.root / "other").read_bytes(), self.original)

    def test_output_symlink_and_unsupported_category_are_refused(self):
        self.output.mkdir()
        (self.root / "outside").mkdir()
        (self.output / "skipped").symlink_to(
            self.root / "outside", target_is_directory=True
        )
        self.assertFalse(self.worker.move([self.job], "skipped")[0][1])
        self.assertTrue(self.job.source.exists())
        with self.assertRaises(ValueError):
            self.worker.move([self.job], "completed")
        self.assertEqual(list((self.root / "outside").iterdir()), [])

    def test_corrupt_copy_never_removes_original(self):
        import petal.relocate as module

        real = module.digest_fd
        with patch.object(
            module,
            "digest_fd",
            side_effect=lambda fd, *args: (
                "bad"
                if os.fstat(fd).st_ino != self.job.source.stat().st_ino
                else real(fd, *args)
            ),
        ):
            result = self.worker.move([self.job], "skipped")
        self.assertFalse(result[0][1])
        self.assertEqual(self.job.source.read_bytes(), self.original)

    def test_disk_full_and_cancel_retain_original(self):
        with patch(
            "petal.relocate.os.write", side_effect=OSError("No space left on device")
        ):
            result = self.worker.move([self.job], "skipped")
        self.assertFalse(result[0][1])
        self.assertEqual(self.job.source.read_bytes(), self.original)
        self.worker.cancel.set()
        self.assertEqual(self.worker.move([self.job], "skipped"), [])
        self.assertTrue(self.job.source.exists())

    def test_failure_after_source_hold_restores_it(self):
        import petal.relocate as module

        real = module.digest_fd
        calls = [0]

        def fail_held(fd, *args):
            calls[0] += 1
            if calls[0] == 3:
                raise OSError("Injected read error after hold")
            return real(fd, *args)

        with patch.object(module, "digest_fd", side_effect=fail_held):
            result = self.worker.move([self.job], "skipped")
        self.assertFalse(result[0][1])
        self.assertEqual(self.job.source.read_bytes(), self.original)
        self.assertTrue(self.worker.move([self.job], "skipped")[0][1])

    def test_crash_after_hold_recovers_original_and_retry_uses_verified_copy(self):
        import petal.relocate as module

        real = module.rename_new

        def crash(*args):
            real(*args)
            raise PowerLoss()

        with patch.object(module, "rename_new", side_effect=crash):
            with self.assertRaises(PowerLoss):
                self.worker.move([self.job], "skipped")
        self.assertFalse(self.job.source.exists())
        recovered = Relocator(self.output, self.state)
        with output_lock(self.output):
            recovered.recover()
        self.assertEqual(self.job.source.read_bytes(), self.original)
        self.assertTrue(recovered.move([self.job], "skipped")[0][1])

    def test_crash_after_removal_recognizes_durable_copy(self):
        real = self.worker.save

        def crash():
            if any(r["phase"] == "moved" for r in self.worker.records.values()):
                raise PowerLoss()
            real()

        with patch.object(self.worker, "save", side_effect=crash):
            with self.assertRaises(PowerLoss):
                self.worker.move([self.job], "skipped")
        self.assertFalse(self.job.source.exists())
        recovered = Relocator(self.output, self.state)
        self.assertTrue(recovered.move([self.job], "skipped")[0][1])
        self.assertEqual(self.job.relocated_to.read_bytes(), self.original)

    def test_only_requested_status_moves_and_full_filename_is_retained(self):
        self.assertEqual(self.worker.move([self.job], "failed"), [])
        self.job.status = Status.FAILED
        self.assertTrue(self.worker.move([self.job], "failed")[0][1])
        self.assertEqual(
            self.job.relocated_to, self.output / "failed" / self.job.relative
        )

    def test_change_while_final_copy_is_read_restores_original(self):
        import petal.relocate as module

        real = module.digest_fd
        calls = [0]
        changed = b"edited while final copy was read"

        def edit(fd, *args):
            calls[0] += 1
            result = real(fd, *args)
            if calls[0] == 4:
                record = self.worker.records[str(self.job.source)]
                (self.job.source.parent / record["hold"]).write_bytes(changed)
            return result

        with patch.object(module, "digest_fd", side_effect=edit):
            result = self.worker.move([self.job], "skipped")
        self.assertFalse(result[0][1])
        self.assertEqual(self.job.source.read_bytes(), changed)

    def test_recovery_does_not_replace_a_new_source_at_original_path(self):
        import petal.relocate as module

        real = module.rename_new

        def crash(*args):
            real(*args)
            raise PowerLoss()

        with patch.object(module, "rename_new", side_effect=crash):
            with self.assertRaises(PowerLoss):
                self.worker.move([self.job], "skipped")
        self.job.source.write_bytes(b"new file")
        recovered = Relocator(self.output, self.state)
        with output_lock(self.output), self.assertRaises(FileExistsError):
            recovered.recover()
        self.assertEqual(self.job.source.read_bytes(), b"new file")
        hold = self.job.source.parent / recovered.records[str(self.job.source)]["hold"]
        self.assertEqual(hold.read_bytes(), self.original)

    def test_ordinary_error_after_source_rename_restores_the_original(self):
        import petal.relocate as module

        real = module.rename_new
        calls = [0]

        def fail_sync(*args):
            real(*args)
            calls[0] += 1
            if calls[0] == 1:
                raise OSError("Source directory sync failed")

        with patch.object(module, "rename_new", side_effect=fail_sync):
            result = self.worker.move([self.job], "skipped")
        self.assertFalse(result[0][1])
        self.assertEqual(self.job.source.read_bytes(), self.original)

    def test_cancel_during_final_checks_restores_source_and_allows_retry(self):
        import petal.relocate as module

        real = module.digest_fd
        calls = [0]

        def cancel(fd, *args):
            calls[0] += 1
            if calls[0] == 3:
                self.worker.cancel.set()
            return real(fd, *args)

        with patch.object(module, "digest_fd", side_effect=cancel):
            self.assertEqual(self.worker.move([self.job], "skipped"), [])
        self.assertEqual(self.job.source.read_bytes(), self.original)
        self.worker.cancel.clear()
        self.assertTrue(self.worker.move([self.job], "skipped")[0][1])

    def test_destination_replaced_during_final_check_never_removes_original(self):
        import petal.relocate as module

        real = module.digest_fd
        calls = [0]
        target = self.output / "skipped" / self.job.relative

        def replace(fd, *args):
            calls[0] += 1
            result = real(fd, *args)
            if calls[0] == 4:
                target.unlink()
                target.write_bytes(b"external replacement")
            return result

        with patch.object(module, "digest_fd", side_effect=replace):
            result = self.worker.move([self.job], "skipped")
        self.assertFalse(result[0][1])
        self.assertEqual(self.job.source.read_bytes(), self.original)
        self.assertEqual(target.read_bytes(), b"external replacement")

    def test_cross_filesystem_move_uses_a_verified_copy(self):
        with tempfile.TemporaryDirectory(
            prefix=".petal-transfer-test-", dir=Path.cwd()
        ) as folder:
            other = Path(folder)
            if other.stat().st_dev == self.source.stat().st_dev:
                self.skipTest("Workspace and temporary files share a filesystem")
            worker = Relocator(other / "output", self.state)
            result = worker.move([self.job], "skipped")
            self.assertTrue(result[0][1], result)
            self.assertEqual(self.job.relocated_to.read_bytes(), self.original)
            self.assertFalse(self.job.source.exists())
