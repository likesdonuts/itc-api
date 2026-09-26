"""The faster daily sync: new filings only, no repeat attachment lookups,
and a timing log."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from support import DataDirTestCase, FakeEdisClient

from datalayer import client as edis_client
from datalayer import dailylog, docs


@contextmanager
def _session(client):
    yield client


def rows(ids, type_="Order"):
    """EDIS rows, newest first, as the API lists them."""
    return [{"id": str(i), "documentType": type_, "documentTitle": f"Doc {i}", "securityLevel": "Public",
             "documentDate": "2026/09/01 00:00:00"} for i in ids]


class SyncTestCase(DataDirTestCase):
    def run_docs(self, client, keys=("337-1",), **kwargs):
        with mock.patch.object(docs, "edis_session", lambda token: _session(client)):
            return docs.run(self.store_, "token", list(keys), log=self.quiet, **kwargs)

    def setUp(self):
        super().setUp()
        self.store_ = self.store()
        self.store_.investigations = {"337-1": {"investigation_number": "337-1", "status": "Active"}}


class TestNewFilingsOnly(SyncTestCase):
    def test_only_the_new_pages_are_read_and_the_rest_kept(self):
        docket = rows(range(100, 40, -1))  # 60 documents: three pages
        full = FakeEdisClient(documents={"337-1": docket})
        self.run_docs(full, download=False)
        self.assertEqual(full.pages_read, 3)

        # Two new filings: page 1 has them and 18 known, page 2 is all known.
        client = FakeEdisClient(documents={"337-1": rows([102, 101]) + docket})
        report = self.run_docs(client, download=False, new_only=True)
        self.assertEqual(client.pages_read, 2)
        self.assertFalse(report.results[0].full)
        ids = [d["id"] for d in self.store_.documents["337-1"]]
        self.assertEqual(ids[:3], ["102", "101", "100"])
        self.assertEqual(len(ids), 62)
        self.assertEqual(self.store_.documents_state["337-1"]["listing"], "new only")

    def test_nothing_new_is_one_request(self):
        docket = rows(range(100, 40, -1))
        self.run_docs(FakeEdisClient(documents={"337-1": docket}), download=False)
        client = FakeEdisClient(documents={"337-1": docket})
        self.run_docs(client, download=False, new_only=True)
        self.assertEqual(client.pages_read, 1)

    def test_a_full_listing_is_due_weekly_and_whenever_asked_for(self):
        docket = rows(range(100, 40, -1))
        self.run_docs(FakeEdisClient(documents={"337-1": docket}), download=False)
        state = self.store_.documents_state["337-1"]
        state["full_listed_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        client = FakeEdisClient(documents={"337-1": docket})
        report = self.run_docs(client, download=False, new_only=True)
        self.assertTrue(report.results[0].full)
        self.assertEqual(client.pages_read, 3)
        # A fetch by hand lists it all, whatever the date.
        by_hand = FakeEdisClient(documents={"337-1": docket})
        self.run_docs(by_hand, download=False)
        self.assertEqual(by_hand.pages_read, 3)


class TestAppearancePdfsOnDisk(SyncTestCase):
    def test_a_notice_whose_pdf_is_on_disk_is_not_asked_about_again(self):
        notice = rows([500], type_="Notice of Appearance")
        attachments = {"500": [{"id": "9", "originalFileName": "notice.pdf"}]}
        first = FakeEdisClient(documents={"337-1": notice}, attachments=attachments)
        with mock.patch.object(first, "list_attachments", wraps=first.list_attachments) as asked:
            self.run_docs(first, only_types=docs.APPEARANCE_TYPES)
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(len(first.downloads), 1)

        again = FakeEdisClient(documents={"337-1": notice}, attachments=attachments)
        with mock.patch.object(again, "list_attachments", wraps=again.list_attachments) as asked:
            self.run_docs(again, only_types=docs.APPEARANCE_TYPES)
        self.assertEqual(asked.call_count, 0)
        self.assertEqual(len(self.store_.documents["337-1"][0]["attachments"]), 1)


class TestTimingLog(DataDirTestCase):
    def test_steps_counts_and_requests_are_recorded(self):
        timer = dailylog.DailyTimer()
        with timer.step("ingest"):
            pass
        with timer.step("documents"):
            edis_client.REQUESTS["list"] += 3
            edis_client.REQUESTS["attachments"] += 1
        timer.count("cases", 2)
        row = timer.row("ok", "done")
        dailylog.append(self.data_dir, row)
        [saved] = dailylog.read(self.data_dir)
        self.assertEqual((saved["outcome"], saved["cases"], saved["requests_list"], saved["requests_attachments"]),
                         ("ok", "2", "3", "1"))
        self.assertEqual(saved["seconds_counsel"], "")  # a step that did not run
        self.assertIn("4 EDIS requests", dailylog.summary(row))


if __name__ == "__main__":
    unittest.main()
