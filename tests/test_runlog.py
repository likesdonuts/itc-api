"""The CSV a sync leaves behind, and what it should let you notice.

These lean on the numbers meaning what they say, since the whole point of
data/sync_log.csv is to spot a day where they don't.
"""

from __future__ import annotations

import csv
import json
import unittest
from datetime import datetime, timezone
from unittest import mock

from support import DataDirTestCase, ids_row, payload, write_snapshot

from datalayer import ids, ingest, runlog

NOON = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)


class RunLogTestCase(DataDirTestCase):
    def sync(self, rows, *, day="2026-09-22", at="120000", store=None, **kwargs):
        store = store or self.store()
        snapshot = write_snapshot(self.ids_dir, rows, day=day, at=at)
        report = ingest.parse_snapshot(store, snapshot, log=self.quiet, **kwargs)
        return store, report

    def rows(self):
        return runlog.read(self.data_dir)

    def last(self):
        return self.rows()[-1]


class TestWhatIsRecorded(RunLogTestCase):
    def test_a_sync_appends_one_row_naming_the_snapshot_it_read(self):
        self.sync([ids_row(), ids_row("337-1479", investigation_id=2)])
        rows = self.rows()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["snapshot"], "investigations-2026-09-22T120000Z.json.gz")
        self.assertEqual(row["snapshot_taken_at"], "2026-09-22T12:00:00+00:00")
        self.assertGreater(int(row["snapshot_bytes"]), 0)
        self.assertEqual(row["feed_date"], "2026-09-21T22:00:01.996+00:00")
        self.assertEqual(row["outcome"], "ok")
        self.assertTrue(row["run_at"])

    def test_the_counts_say_how_much_of_the_file_was_used(self):
        antidumping = {
            "Investigation Number": "731-1234",
            "Investigation Categories": [{"Name": "731 - Antidumping"}],
        }
        self.sync([ids_row(), ids_row(phase="Remand", investigation_id=2), antidumping])
        row = self.last()

        self.assertEqual(row["rows_total"], "3")
        self.assertEqual(row["rows_337"], "2")
        self.assertEqual(row["cases_in_file"], "1")
        self.assertEqual(row["stages_in_file"], "2")

    def test_the_first_run_counts_every_case_as_new_and_none_as_changed(self):
        self.sync([ids_row(), ids_row("337-1479", investigation_id=2)])
        row = self.last()

        self.assertEqual(row["cases_added"], "2")
        self.assertEqual(row["cases_changed"], "0")
        self.assertEqual(row["cases_left_feed"], "0")

    def test_a_second_run_of_the_same_file_reports_nothing_changed(self):
        rows = [ids_row(), ids_row("337-1479", investigation_id=2)]
        store, _ = self.sync(rows)
        self.sync(rows, store=store, day="2026-09-23")
        row = self.last()

        # Only the sync timestamps differ between the two runs, and those are
        # not what "changed" is supposed to mean.
        self.assertEqual(row["cases_added"], "0")
        self.assertEqual(row["cases_changed"], "0")
        self.assertEqual(row["status_changes"], "0")

    def test_a_status_the_commission_moved_is_counted_twice_over(self):
        store, _ = self.sync([ids_row(status="Active"), ids_row("337-1479", investigation_id=2)])
        self.sync(
            [ids_row(status="Terminated"), ids_row("337-1479", investigation_id=2)],
            store=store,
            day="2026-09-23",
        )
        row = self.last()

        self.assertEqual(row["cases_changed"], "1")
        self.assertEqual(row["status_changes"], "1")

    def test_a_change_that_is_not_a_status_change_is_told_apart(self):
        store, _ = self.sync([ids_row(topic="Certain Wearable Devices")])
        self.sync([ids_row(topic="Certain Wearable Devices and Parts")], store=store,
                  day="2026-09-23")
        row = self.last()

        self.assertEqual(row["cases_changed"], "1")
        self.assertEqual(row["status_changes"], "0")

    def test_a_withdrawn_case_leaves_the_file_but_not_the_site(self):
        store, _ = self.sync([ids_row(), ids_row("337-1479", investigation_id=2)])
        self.sync([ids_row("337-1479", investigation_id=2)], store=store, day="2026-09-23")
        row = self.last()

        self.assertEqual(row["cases_in_file"], "1")
        self.assertEqual(row["cases_left_feed"], "1")
        self.assertEqual(row["cases_withdrawn_total"], "1")
        self.assertEqual(row["cases_on_site"], "2")

    def test_the_mode_says_where_the_snapshot_came_from(self):
        store, _ = self.sync([ids_row()], mode="download")
        self.sync([ids_row()], store=store, day="2026-09-23", mode="cached")
        self.sync([ids_row()], store=store, day="2026-09-24")

        self.assertEqual([r["mode"] for r in self.rows()], ["download", "cached", "offline"])

    def test_the_mode_a_real_run_records_reflects_whether_it_downloaded(self):
        raw = json.dumps(payload([ids_row()])).encode()
        store = self.store()
        with mock.patch.object(ids, "download", return_value=raw):
            ingest.run(store, ids_dir=self.ids_dir, now=NOON, log=self.quiet)
            ingest.run(store, ids_dir=self.ids_dir, now=NOON, log=self.quiet)
        ingest.run(store, ids_dir=self.ids_dir, offline=True, log=self.quiet)

        self.assertEqual([r["mode"] for r in self.rows()], ["download", "cached", "offline"])
        self.assertTrue(all(float(r["seconds"]) >= 0 for r in self.rows()))

    def test_a_renumbered_docket_is_recorded(self):
        store = self.store()
        store.put_documents("337-3866", [{"id": "700", "attachments": []}])
        self.sync([ids_row("337-1478", docket="3866")], store=store)

        self.assertEqual(self.last()["cases_renumbered"], "1")


class TestARefusedSnapshot(RunLogTestCase):
    def refuse(self):
        rows = [ids_row(f"337-{1400 + n}", investigation_id=n) for n in range(60)]
        store, _ = self.sync(rows)
        with self.assertRaises(ingest.SuspectSnapshotError):
            self.sync(rows[:10], store=store, day="2026-09-23")
        return store

    def test_it_is_logged_rather_than_leaving_a_gap(self):
        self.refuse()
        rows = self.rows()

        self.assertEqual([r["outcome"] for r in rows], ["ok", "refused"])
        self.assertIn("most likely incomplete", rows[-1]["note"])
        self.assertEqual(rows[-1]["cases_left_feed"], "50")

    def test_the_refused_row_does_not_claim_the_site_changed(self):
        self.refuse()
        row = self.last()

        # Nothing was written, so no case was withdrawn and none carried over.
        self.assertEqual(row["cases_withdrawn_total"], "0")
        self.assertEqual(row["note"].count("\n"), 0)


class TestAFailedDownload(RunLogTestCase):
    def test_it_is_logged_with_the_reason(self):
        failure = ids.IdsError("IDS download failed: 502 Bad Gateway")
        with mock.patch.object(ids, "RETRY_WAITS", ()), mock.patch.object(
            ids, "download", side_effect=failure
        ):
            with self.assertRaises(ids.IdsError):
                ingest.run(self.store(), ids_dir=self.ids_dir, now=NOON, log=self.quiet)

        row = self.last()
        self.assertEqual(row["outcome"], "failed")
        self.assertEqual(row["mode"], "download")
        self.assertEqual(row["snapshot"], "")
        self.assertIn("502 Bad Gateway", row["note"])


class TestTheFileItself(RunLogTestCase):
    def test_the_header_is_written_once_and_the_rows_pile_up(self):
        store, _ = self.sync([ids_row()])
        self.sync([ids_row()], store=store, day="2026-09-23")
        self.sync([ids_row()], store=store, day="2026-09-24")

        with runlog.path_for(self.data_dir).open(encoding="utf-8", newline="") as handle:
            lines = list(csv.reader(handle))

        self.assertEqual(lines[0], list(runlog.FIELDS))
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(len(line) == len(runlog.FIELDS) for line in lines))

    def test_a_log_from_an_older_column_set_is_set_aside_not_appended_to(self):
        path = runlog.path_for(self.data_dir)
        path.write_text("run_at,cases\n2026-09-21T00:00:00+00:00,3\n", encoding="utf-8")

        self.sync([ids_row()])

        self.assertEqual(len(self.rows()), 1)
        kept = list(self.data_dir.glob("sync_log-before-*.csv"))
        self.assertEqual(len(kept), 1)
        self.assertIn("run_at,cases", kept[0].read_text(encoding="utf-8"))

    def test_reading_the_last_few_runs(self):
        store = self.store()
        for n in range(4):
            store, _ = self.sync([ids_row()], store=store, day=f"2026-09-2{n}")

        self.assertEqual(len(runlog.read(self.data_dir, last=2)), 2)
        self.assertEqual(runlog.read(self.data_dir / "nowhere"), [])


if __name__ == "__main__":
    unittest.main()
