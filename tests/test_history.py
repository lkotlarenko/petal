import tempfile
import unittest
from pathlib import Path

from petal.history import History, summary
from petal.models import Job, Status


class HistoryTests(unittest.TestCase):
    def test_large_history_roundtrips_and_report_is_an_independent_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            history = History(root)
            for i in range(550):
                job = Job(
                    root / f"{i}.mp4",
                    Path(f"{i}.mp4"),
                    root / "out" / f"{i}.mkv",
                    status=Status.COMPLETED,
                    size=100,
                    final_size=40,
                    output_root=root / "out",
                )
                history.jobs[job.source] = job
            history.set_report(list(history.jobs.values()), root / "out")
            next(iter(history.jobs.values())).status = Status.WAITING
            self.assertEqual(summary(history.report)["completed"], 550)
            history.save()
            restored = History(root)
            self.assertEqual(len(restored.jobs), 550)
            self.assertEqual(summary(restored.report)["saved"], 33000)
            self.assertEqual(restored.report[0].output_root, root / "out")

    def test_malformed_history_does_not_crash_or_become_a_verification_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for data in (
                "{broken",
                "[]",
                '{"jobs": null}',
                '{"jobs":[{"status":"bad"}]}',
            ):
                (root / "history.json").write_text(data)
                self.assertEqual(History(root).jobs, {})

    def test_interrupted_live_rows_restore_as_cancelled_not_completed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            history = History(root)
            job = Job(
                root / "source.mp4",
                Path("source.mp4"),
                root / "out.mkv",
                status=Status.VERIFYING,
            )
            history.jobs[job.source] = job
            history.save()
            restored = History(root)
            self.assertEqual(restored.jobs[job.source].status, Status.CANCELLED)
