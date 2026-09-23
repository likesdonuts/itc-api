"""The daily download: what gets stored, when it is skipped, what is pruned."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from support import DataDirTestCase, ids_row, payload, write_snapshot

from datalayer import ids

NOON = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)


class TestSnapshots(DataDirTestCase):
    def test_a_download_is_stored_gzipped_and_stamped_with_the_time(self):
        raw = json.dumps(payload([ids_row()])).encode()
        with mock.patch.object(ids, "download", return_value=raw) as download:
            result = ids.sync(ids_dir=self.ids_dir, now=NOON, log=self.quiet)

        download.assert_called_once()
        self.assertTrue(result.downloaded)
        self.assertEqual(
            result.snapshot.path.name, "investigations-2026-09-22T120000Z.json.gz"
        )
        self.assertEqual(result.snapshot.day, "2026-09-22")
        self.assertEqual(result.snapshot.taken_at, "2026-09-22T12:00:00+00:00")
        self.assertGreater(result.snapshot.size, 0)
        self.assertEqual(result.snapshot.load()["count"], 1)
        self.assertEqual(result.rows, 1)

    def test_the_second_run_of_the_day_does_not_download_again(self):
        write_snapshot(self.ids_dir, [ids_row()], day="2026-09-22")
        with mock.patch.object(ids, "download", side_effect=AssertionError("downloaded")):
            result = ids.sync(ids_dir=self.ids_dir, now=NOON, log=self.quiet)
        self.assertFalse(result.downloaded)

    def test_force_downloads_again_without_overwriting_the_first_copy(self):
        first = write_snapshot(self.ids_dir, [ids_row()], day="2026-09-22", at="060000")
        raw = json.dumps(payload([ids_row(), ids_row("337-1479", investigation_id=2)])).encode()
        with mock.patch.object(ids, "download", return_value=raw):
            result = ids.sync(ids_dir=self.ids_dir, now=NOON, force=True, log=self.quiet)

        self.assertTrue(result.downloaded)
        self.assertEqual(result.rows, 2)
        self.assertTrue(first.path.exists())
        self.assertEqual(
            [s.stamp for s in ids.snapshots(self.ids_dir)],
            ["2026-09-22T060000Z", "2026-09-22T120000Z"],
        )

    def test_the_newest_copy_of_the_day_is_the_one_parsed(self):
        write_snapshot(self.ids_dir, [ids_row()], day="2026-09-22", at="060000")
        newest = write_snapshot(self.ids_dir, [ids_row()], day="2026-09-22", at="180000")

        self.assertEqual(ids.latest(self.ids_dir).path, newest.path)
        self.assertEqual(ids.snapshot_for("2026-09-22", self.ids_dir).path, newest.path)

    def test_only_the_newest_days_are_kept(self):
        for day in ("2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22"):
            write_snapshot(self.ids_dir, [ids_row()], day=day)
        removed = ids.prune(self.ids_dir, keep=2)

        self.assertEqual(len(removed), 2)
        self.assertEqual([s.day for s in ids.snapshots(self.ids_dir)], ["2026-09-21", "2026-09-22"])
        self.assertEqual(ids.latest(self.ids_dir).day, "2026-09-22")

    def test_pruning_counts_days_so_a_second_copy_costs_nothing(self):
        # Two copies of one day plus one of another is two days, not three.
        write_snapshot(self.ids_dir, [ids_row()], day="2026-09-21", at="060000")
        write_snapshot(self.ids_dir, [ids_row()], day="2026-09-22", at="060000")
        write_snapshot(self.ids_dir, [ids_row()], day="2026-09-22", at="180000")

        self.assertEqual(ids.prune(self.ids_dir, keep=2), [])
        self.assertEqual(len(ids.snapshots(self.ids_dir)), 3)

    def test_a_snapshot_stored_before_the_stamp_still_reads(self):
        legacy = self.ids_dir
        legacy.mkdir(parents=True, exist_ok=True)
        write_snapshot(self.ids_dir, [ids_row()], day="2026-09-20").path.rename(
            legacy / "investigations-2026-09-20.json.gz"
        )
        stored = ids.snapshots(self.ids_dir)

        self.assertEqual([s.day for s in stored], ["2026-09-20"])
        self.assertIsNone(stored[0].taken_at)
        self.assertEqual(stored[0].load()["count"], 1)

    def test_a_response_that_is_not_the_feed_is_refused(self):
        with mock.patch.object(ids, "download", return_value=b"<html>maintenance</html>"):
            with self.assertRaises(ids.IdsError):
                ids.sync(ids_dir=self.ids_dir, now=NOON, log=self.quiet)
        self.assertEqual(ids.snapshots(self.ids_dir), [])

    def test_a_truncated_snapshot_is_reported_not_treated_as_empty(self):
        snapshot = write_snapshot(self.ids_dir, [ids_row()], day="2026-09-22")
        snapshot.path.write_bytes(b"\x1f\x8b truncated")
        with self.assertRaises(ids.IdsError):
            snapshot.load()

    def test_only_section_337_rows_are_kept(self):
        antidumping = {
            "Investigation Number": "731-1234",
            "Investigation Categories": [{"Name": "731 - Antidumping"}],
        }
        rows = ids.section_337_rows(payload([ids_row(), antidumping]))
        self.assertEqual([row["Investigation Number"] for row in rows], ["337-1478"])

    def test_a_337_row_without_categories_is_still_recognized(self):
        self.assertTrue(ids.is_section_337({"Investigation Number": "337-3936"}))
        self.assertFalse(ids.is_section_337({"Investigation Number": "701-99"}))


if __name__ == "__main__":
    unittest.main()
