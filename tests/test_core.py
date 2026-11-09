"""Offline unit tests: run `python -m unittest discover -s tests -v`."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yingxu.engine import JobCancelled, JobResult, _make_filters, _select_parameters, _publish_no_overwrite
from yingxu.models import Preset, make_jobs
from yingxu.queue import BatchRunner


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "素材.mp4"
        self.source.write_bytes(b"test")
        self.output = self.root / "out"
        self.preset = Preset("test", (-0.01, 0.01), (0.99, 1.01),
                             (0.98, 1.02), (-0.1, 0.1))

    def test_seeded_parameters_are_reproducible(self):
        tasks = make_jobs([self.source], self.output, 3, 1234, self.preset)
        a = [_select_parameters(j) for j in tasks]
        b = [_select_parameters(j) for j in tasks]
        self.assertEqual(a, b)
        self.assertNotEqual(a[0], a[1])

    def test_source_cannot_be_overwritten(self):
        with self.assertRaises(ValueError):
            make_jobs([self.source], self.source.parent, 1, 1, self.preset)

    def test_prevent_existing_output_overwrite(self):
        self.output.mkdir()
        (self.output / "素材_v001.mp4").write_bytes(b"original")
        temp = self.output / "temporary.mp4"
        temp.write_bytes(b"new")
        published = _publish_no_overwrite(temp, self.output, "素材_v001")
        self.assertEqual(published.name, "素材_v001_001.mp4")
        self.assertEqual((self.output / "素材_v001.mp4").read_bytes(), b"original")
        self.assertEqual(published.read_bytes(), b"new")
        self.assertFalse(temp.exists())

    def test_filter_identity(self):
        self.assertEqual(_make_filters({"brightness": 0, "contrast": 1,
                                         "saturation": 1, "audio_gain_db": 0}), ([], []))

    def test_queue_retries_then_succeeds(self):
        jobs = make_jobs([self.source], self.output, 1, 2, self.preset)
        attempts = []
        def fake_run(job, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("transient failure")
            return JobResult(self.output / "success.mp4", {"brightness": 0})
        with patch("yingxu.queue.run_job", fake_run):
            runner = BatchRunner(jobs, retries=1)
            result = runner.run()[jobs[0].job_id]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(result.status, "success")

    def test_cancel_pending(self):
        jobs = make_jobs([self.source], self.output, 2, 2, self.preset)
        with patch("yingxu.queue.run_job", side_effect=AssertionError("should not run")):
            runner = BatchRunner(jobs)
            runner.cancel([j.job_id for j in jobs])
            results = runner.run()
        self.assertEqual([r.status for r in results.values()], ["cancelled", "cancelled"])

    def test_invalid_preset_range(self):
        p = self.root / "invalid.json"
        p.write_text(json.dumps({"name": "bad", "contrast": [1.1, 0.9]}))
        with self.assertRaises(ValueError):
            Preset.from_file(p)


if __name__ == "__main__":
    unittest.main()
