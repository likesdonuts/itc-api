"""The EDIS documents process, and the line it must not cross.

The point of these is the isolation: fetching documents may add documents and
PDFs, and must leave investigation information and parties exactly as the IDS
ingest wrote them.
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest import mock

from support import (
    DataDirTestCase,
    EDIS_DOCUMENTS,
    FakeEdisClient,
    ids_row,
    write_snapshot,
)

from datalayer import docs, ingest
from datalayer.store import Store, lookup_candidates, number_key


@contextmanager
def _session(client):
    yield client


class DocsTestCase(DataDirTestCase):
    def seeded_store(self, rows=None) -> Store:
        store = self.store()
        snapshot = write_snapshot(self.ids_dir, rows or [ids_row()])
        ingest.parse_snapshot(store, snapshot, log=self.quiet)
        return store

    def fetch(self, client, store, numbers, **kwargs):
        kwargs.setdefault("log", self.quiet)
        with mock.patch.object(docs, "edis_session", lambda token: _session(client)):
            return docs.run(store, "fake-token", numbers, **kwargs)


class TestDocuments(DocsTestCase):
    def test_documents_and_pdfs_are_stored_for_the_case(self):
        store = self.seeded_store()
        client = FakeEdisClient(
            documents={"337-1478": EDIS_DOCUMENTS},
            attachments={"100": [{"id": "900", "originalFileName": "complaint.pdf"}]},
        )
        report = self.fetch(client, store, ["337-1478"])

        self.assertEqual([r.key for r in report.fetched], ["337-1478"])
        self.assertEqual(client.downloads, [("100", "900")])
        stored = self.read_documents()["337-1478"][0]
        self.assertEqual(stored["document_type"], "Complaint")
        self.assertEqual(stored["document_date"], "2026-01-13")
        self.assertEqual(
            stored["attachments"][0]["href"],
            "../../data/documents/337-1478/100_900_complaint.pdf",
        )
        self.assertTrue(
            (self.data_dir / "documents" / "337-1478" / "100_900_complaint.pdf").exists()
        )

    def test_it_records_when_a_case_was_last_fetched(self):
        store = self.seeded_store()
        client = FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        self.fetch(client, store, ["337-1478"])

        state = self.read_json("documents_state.json")["337-1478"]
        self.assertEqual(state["documents"], 1)
        self.assertTrue(state["fetched_at"])

    def test_metadata_only_skips_the_downloads(self):
        store = self.seeded_store()
        client = FakeEdisClient(
            documents={"337-1478": EDIS_DOCUMENTS},
            attachments={"100": [{"id": "900", "originalFileName": "complaint.pdf"}]},
        )
        self.fetch(client, store, ["337-1478"], download=False)

        self.assertEqual(client.downloads, [])
        self.assertEqual(store.documents["337-1478"][0]["attachments"], [])

    def test_numbers_are_matched_however_they_are_written(self):
        store = self.seeded_store()
        client = FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        for spelling in ("337-TA-1478", "1478", "337-1478"):
            report = self.fetch(client, store, [spelling])
            self.assertEqual([r.key for r in report.fetched], ["337-1478"], spelling)

    def test_a_case_edis_has_no_documents_for_is_skipped_not_emptied(self):
        store = self.seeded_store()
        store.put_documents("337-1478", EDIS_DOCUMENTS)
        report = self.fetch(FakeEdisClient(), store, ["337-1478"])

        self.assertEqual([r.key for r in report.failed], ["337-1478"])
        self.assertEqual(len(store.documents["337-1478"]), 1)

    def test_a_docket_edis_does_not_answer_for_is_reported_not_invented(self):
        # EDIS is the only source of documents now, so a pre-institution
        # docket it has nothing for simply has no documents.
        store = self.seeded_store(
            rows=[ids_row("337-3936", status="Pre-institution", docket="3936", start_date=None)]
        )
        report = self.fetch(FakeEdisClient(), store, ["337-3936"])

        self.assertEqual([r.key for r in report.failed], ["337-3936"])
        self.assertNotIn("337-3936", store.documents)

    def test_an_unknown_number_is_tried_anyway_unless_refused(self):
        store = self.seeded_store()
        client = FakeEdisClient(documents={"337-1490": EDIS_DOCUMENTS})

        report = self.fetch(client, store, ["337-1490"])
        self.assertEqual([r.key for r in report.fetched], ["337-1490"])

        refused = self.fetch(FakeEdisClient(), store, ["337-9999"], known_only=True)
        self.assertEqual(refused.results, [])

    def test_one_case_failing_does_not_stop_the_others(self):
        store = self.seeded_store(
            rows=[ids_row(), ids_row("337-1479", investigation_id=2, topic="Certain Widgets")]
        )
        client = FakeEdisClient(documents={"337-1479": EDIS_DOCUMENTS})
        report = self.fetch(client, store, ["337-1478", "337-1479"])

        self.assertEqual([r.key for r in report.failed], ["337-1478"])
        self.assertEqual([r.key for r in report.fetched], ["337-1479"])


class TestItLeavesCaseDataAlone(DocsTestCase):
    def test_fetching_documents_does_not_rewrite_investigations_json(self):
        store = self.seeded_store()
        before = (self.data_dir / "investigations.json").read_text(encoding="utf-8")

        client = FakeEdisClient(
            documents={
                "337-1478": [
                    {
                        **EDIS_DOCUMENTS[0],
                        # EDIS's own idea of the parties, which must not win.
                        "onBehalfOf": "Someone Else Entirely",
                    }
                ]
            }
        )
        self.fetch(client, store, ["337-1478"])

        after = (self.data_dir / "investigations.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)

        case = store.investigations["337-1478"]
        parties = [p["name"] for p in case["stages"][0]["lists"]["participants"]]
        self.assertEqual(parties, ["Acme Inc.", "Globex Corp."])
        self.assertEqual(case["title"], "Certain Wearable Devices")
        self.assertEqual(case["status"], "Active")

    def test_the_process_has_no_route_to_the_case_writer(self):
        store = self.seeded_store()
        client = FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        with mock.patch.object(
            Store, "save_cases", side_effect=AssertionError("case data written")
        ):
            self.fetch(client, store, ["337-1478"])

    def test_ingest_does_not_rewrite_the_documents_of_cases_it_reparses(self):
        store = self.seeded_store()
        client = FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        self.fetch(client, store, ["337-1478"])
        before = (self.data_dir / "documents_index" / "337-1478.json").read_text(encoding="utf-8")

        snapshot = write_snapshot(
            self.ids_dir, [ids_row(status="Terminated")], day="2026-09-23"
        )
        ingest.parse_snapshot(store, snapshot, log=self.quiet)

        self.assertEqual(
            before, (self.data_dir / "documents_index" / "337-1478.json").read_text(encoding="utf-8")
        )
        self.assertEqual(store.investigations["337-1478"]["status"], "Terminated")


class TestNumberResolution(unittest.TestCase):
    def test_number_key_collapses_spellings(self):
        self.assertEqual(number_key("337-TA-1478"), number_key("337-1478"))
        self.assertNotEqual(number_key("337-1478"), number_key("337-1479"))

    def test_lookup_candidates_are_ordered_most_specific_first(self):
        self.assertEqual(lookup_candidates("337-TA-1478"), ["337-TA-1478", "337-1478", "1478"])


if __name__ == "__main__":
    unittest.main()
