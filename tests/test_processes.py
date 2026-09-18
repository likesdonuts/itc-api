"""Layer/process tests that run entirely offline.

The EDIS API is replaced with a fake, so these cover the parts that are easy
to get wrong (which process calls what, how numbers resolve, what lands on
disk) without a token or a network round trip.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli  # noqa: E402
from datalayer import discovery, update  # noqa: E402
from datalayer.client import RssItem  # noqa: E402
from datalayer.store import Store, lookup_candidates, number_key  # noqa: E402
from ui.render import render_site  # noqa: E402


class FakeEdisClient:
    """Stands in for EdisClient and records what was asked of it."""

    def __init__(self, investigations=None, documents=None, attachments=None):
        self.investigations = investigations or {}
        self.documents = documents or {}
        self.attachments = attachments or {}
        self.investigation_calls: list[str] = []
        self.document_calls: list[str] = []
        self.downloads: list[tuple[str, str]] = []

    def get_investigation(self, number: str):
        self.investigation_calls.append(number)
        return self.investigations.get(number, [])

    def list_documents(self, number: str):
        self.document_calls.append(number)
        return self.documents.get(number, [])

    def list_attachments(self, document_id: str):
        return self.attachments.get(str(document_id), [])

    def download_attachment(self, document_id: str, attachment_id: str, dest: Path):
        self.downloads.append((str(document_id), str(attachment_id)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF-1.4 fake")
        return dest


@contextmanager
def _session(client):
    yield client


def patch_session(client):
    """Point both processes at the same fake EDIS session, and stub out the
    IDS feed so no test can reach the network.
    """
    return (
        mock.patch.multiple(
            "datalayer.discovery",
            edis_session=lambda token: _session(client),
            load_ids_lookup=lambda enabled=True, log=print: {},
        ),
        mock.patch.multiple(
            "datalayer.update",
            edis_session=lambda token: _session(client),
            load_ids_lookup=lambda enabled=True, log=print: {},
        ),
    )


def rss_item(docket: str, doc_id: str, doc_type: str = "Complaint") -> RssItem:
    return RssItem(
        title=f"Document Approved : {docket} Violation : Doc ID {doc_id}, {doc_type}",
        link=f"https://edis.usitc.gov/docid/{doc_id}",
        guid=doc_id,
        pub_date="Fri, 18 Sep 2026 12:00:00 GMT",
        pub_date_iso="2026-09-18T12:00:00+00:00",
        docket_number=docket,
        doc_id=doc_id,
        doc_type=doc_type,
    )


INSTITUTED_ROW = {
    "investigationNumber": "337-1478",
    "docketNumber": "3866",
    "investigationTitle": "Certain Wearable Devices",
    "investigationType": "Sec 337",
    "investigationStatus": "Active",
    "investigationPhase": "Violation",
}

INSTITUTED_DOCS = [
    {
        "id": "100",
        "documentType": "Complaint",
        "documentTitle": "Complaint of Acme",
        "securityLevel": "Public",
        "filedBy": "A. Lawyer",
        "onBehalfOf": "Acme Inc.",
        "firmOrganization": "Firm LLP",
        "documentDate": "2026/01/13 00:00:00",
        "officialReceivedDate": "2026/01/13 09:00:00",
    }
]


class ProcessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.site_dir = self.root / "site"
        self.data_dir.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def store(self) -> Store:
        return Store.load(self.data_dir)

    def run_discovery(self, client, items, **kwargs):
        store = kwargs.pop("store", None) or self.store()
        kwargs.setdefault("log", lambda msg: None)
        discovery_patch, update_patch = patch_session(client)
        with mock.patch("datalayer.discovery.fetch_rss", return_value=items), discovery_patch, update_patch:
            report = discovery.run(store, "fake-token", use_ids=False, **kwargs)
        return store, report

    def run_update(self, client, numbers, **kwargs):
        store = kwargs.pop("store", None) or self.store()
        kwargs.setdefault("log", lambda msg: None)
        discovery_patch, update_patch = patch_session(client)
        with discovery_patch, update_patch:
            report = update.run(store, "fake-token", numbers, use_ids=False, **kwargs)
        return store, report


class TestDiscovery(ProcessTestCase):
    def test_discovers_instituted_and_pending_cases(self):
        client = FakeEdisClient(
            investigations={"337-1478": [INSTITUTED_ROW]},
            documents={"337-1478": INSTITUTED_DOCS},
            attachments={"100": [{"id": "900", "originalFileName": "complaint.pdf"}], "555": []},
        )
        _, report = self.run_discovery(client, [rss_item("337-1478", "100"), rss_item("337-3936", "555")])

        self.assertEqual(report.feed_items, 2)
        self.assertEqual({r.key for r in report.added}, {"337-1478", "337-3936"})

        saved = json.loads((self.data_dir / "investigations.json").read_text())
        self.assertEqual(saved["337-1478"]["investigation_status"], "Active")
        self.assertEqual(saved["337-3936"]["investigation_status"], "Pending Institution")
        self.assertTrue((self.data_dir / "rss_log.json").exists())
        self.assertEqual(client.downloads, [("100", "900")])

    def test_second_run_skips_cases_already_tracked(self):
        client = FakeEdisClient(
            investigations={"337-1478": [INSTITUTED_ROW]}, documents={"337-1478": INSTITUTED_DOCS}
        )
        items = [rss_item("337-1478", "100")]
        self.run_discovery(client, items)

        fresh = FakeEdisClient(
            investigations={"337-1478": [INSTITUTED_ROW]}, documents={"337-1478": INSTITUTED_DOCS}
        )
        _, report = self.run_discovery(fresh, items)

        self.assertEqual(report.candidates, [])
        self.assertEqual(fresh.investigation_calls, [])

    def test_dry_run_logs_the_feed_without_calling_edis(self):
        client = FakeEdisClient()
        store, report = self.run_discovery(client, [rss_item("337-3936", "555")], dry_run=True)

        self.assertEqual(report.candidates, ["337-3936"])
        self.assertEqual(client.investigation_calls, [])
        self.assertEqual(store.investigations, {})
        self.assertIn("337-3936", json.loads((self.data_dir / "rss_log.json").read_text()))

    def test_limit_caps_how_many_new_dockets_are_fetched(self):
        client = FakeEdisClient()
        _, report = self.run_discovery(
            client, [rss_item("337-3936", "555"), rss_item("337-3937", "556")], limit=1
        )
        self.assertEqual(len(report.candidates), 1)

    def test_no_attachments_flag_records_metadata_only(self):
        client = FakeEdisClient(
            investigations={"337-1478": [INSTITUTED_ROW]},
            documents={"337-1478": INSTITUTED_DOCS},
            attachments={"100": [{"id": "900", "originalFileName": "complaint.pdf"}]},
        )
        store, _ = self.run_discovery(client, [rss_item("337-1478", "100")], download=False)

        self.assertEqual(client.downloads, [])
        self.assertEqual(store.documents["337-1478"][0]["attachments"], [])


class TestUpdate(ProcessTestCase):
    def seed(self) -> Store:
        client = FakeEdisClient(
            investigations={"337-1478": [INSTITUTED_ROW]},
            documents={"337-1478": INSTITUTED_DOCS},
        )
        store, _ = self.run_discovery(
            client, [rss_item("337-1478", "100"), rss_item("337-3936", "555")]
        )
        return store

    def test_updates_only_the_numbers_given(self):
        self.seed()
        client = FakeEdisClient(
            investigations={"337-1478": [{**INSTITUTED_ROW, "investigationStatus": "Terminated"}]},
            documents={"337-1478": INSTITUTED_DOCS},
        )
        store, report = self.run_update(client, ["337-1478"])

        self.assertEqual([r.key for r in report.updated], ["337-1478"])
        self.assertEqual(client.document_calls, ["337-1478"])
        self.assertEqual(store.investigations["337-1478"]["investigation_status"], "Terminated")
        self.assertEqual(store.investigations["337-3936"]["investigation_status"], "Pending Institution")

    def test_accepts_a_bare_serial_number(self):
        self.seed()
        client = FakeEdisClient(
            investigations={"337-1478": [INSTITUTED_ROW]}, documents={"337-1478": INSTITUTED_DOCS}
        )
        _, report = self.run_update(client, ["1478"])
        self.assertEqual([r.key for r in report.updated], ["337-1478"])

    def test_accepts_the_337_ta_spelling(self):
        self.seed()
        client = FakeEdisClient(
            investigations={"337-1478": [INSTITUTED_ROW]}, documents={"337-1478": INSTITUTED_DOCS}
        )
        _, report = self.run_update(client, ["337-TA-1478"])
        self.assertEqual([r.key for r in report.updated], ["337-1478"])

    def test_instituted_docket_is_renumbered_in_place(self):
        store = self.seed()
        pdf = self.data_dir / "documents" / "337-3936" / "555_1_complaint.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 fake")

        instituted = {
            "investigationNumber": "337-1501",
            "docketNumber": "3936",
            "investigationTitle": "Certain Widgets",
            "investigationStatus": "Active",
        }
        client = FakeEdisClient(
            investigations={"337-3936": [instituted]}, documents={"337-1501": []}
        )
        store, report = self.run_update(client, ["337-3936"], store=store)

        self.assertEqual([r.key for r in report.updated], ["337-1501"])
        self.assertNotIn("337-3936", store.investigations)
        self.assertIn("337-1501", store.investigations)
        self.assertTrue((self.data_dir / "documents" / "337-1501" / pdf.name).exists())
        self.assertFalse((self.data_dir / "documents" / "337-3936").exists())

    def test_known_only_refuses_unknown_numbers(self):
        self.seed()
        client = FakeEdisClient()
        _, report = self.run_update(client, ["337-9999"], known_only=True)

        self.assertEqual(report.results, [])
        self.assertEqual(client.investigation_calls, [])

    def test_unknown_number_is_looked_up_by_default(self):
        self.seed()
        client = FakeEdisClient(
            investigations={"337-1490": [{**INSTITUTED_ROW, "investigationNumber": "337-1490"}]},
            documents={"337-1490": []},
        )
        store, report = self.run_update(client, ["337-1490"])

        self.assertEqual([r.key for r in report.updated], ["337-1490"])
        self.assertIn("337-1490", store.investigations)

    def test_update_never_touches_the_rss_feed(self):
        self.seed()
        client = FakeEdisClient(
            investigations={"337-1478": [INSTITUTED_ROW]}, documents={"337-1478": INSTITUTED_DOCS}
        )
        with mock.patch("datalayer.discovery.fetch_rss", side_effect=AssertionError("RSS called")):
            self.run_update(client, ["337-1478"])


class TestCommandLine(ProcessTestCase):
    def invoke(self, argv, client, rss_items=()):
        discovery_patch, update_patch = patch_session(client)
        with mock.patch("cli.load_token", return_value="fake-token"), mock.patch(
            "datalayer.discovery.fetch_rss", return_value=list(rss_items)
        ), discovery_patch, update_patch:
            return cli.main(
                [
                    "--data-dir",
                    str(self.data_dir),
                    "--site-dir",
                    str(self.site_dir),
                    *argv,
                ]
            )

    def test_refresh_runs_both_processes_then_renders(self):
        client = FakeEdisClient(
            investigations={"337-1478": [INSTITUTED_ROW]},
            documents={"337-1478": INSTITUTED_DOCS},
        )
        with mock.patch("sys.stdout"):
            exit_code = self.invoke(["refresh"], client, [rss_item("337-1478", "100")])

        self.assertEqual(exit_code, 0)
        self.assertIn("337-1478", json.loads((self.data_dir / "investigations.json").read_text()))
        self.assertTrue((self.site_dir / "index.html").exists())
        # Discovery already fetched the case; refresh must not fetch it twice.
        self.assertEqual(client.document_calls, ["337-1478"])

    def test_render_needs_no_token(self):
        self.data_dir.joinpath("investigations.json").write_text("{}")
        with mock.patch("sys.stdout"), mock.patch(
            "cli.load_token", side_effect=AssertionError("token requested")
        ):
            exit_code = cli.main(
                ["--data-dir", str(self.data_dir), "--site-dir", str(self.site_dir), "render"]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue((self.site_dir / "index.html").exists())

    def test_update_without_numbers_is_an_error(self):
        with mock.patch("sys.stdout"):
            exit_code = cli.main(["--data-dir", str(self.data_dir), "update"])
        self.assertEqual(exit_code, 1)


class TestNumberResolution(unittest.TestCase):
    def test_number_key_collapses_spellings(self):
        self.assertEqual(number_key("337-TA-1478"), number_key("337-1478"))
        self.assertNotEqual(number_key("337-1478"), number_key("337-1479"))

    def test_lookup_candidates_are_ordered_most_specific_first(self):
        self.assertEqual(lookup_candidates("337-TA-1478"), ["337-TA-1478", "337-1478", "1478"])


class TestRenderLayer(ProcessTestCase):
    def build_store(self) -> Store:
        store = self.store()
        store.put(
            "337-1478",
            {
                "investigation_number": "337-1478",
                "title": "Certain Wearable Devices",
                "investigation_status": "Active",
                "date_initiated": "2026-01-13",
            },
            [{"id": "100", "document_type": "Complaint", "title": "Complaint", "attachments": []}],
        )
        store.save_investigations()
        return store

    def test_renders_from_disk_without_network_or_token(self):
        self.build_store()
        report = render_site(data_dir=self.data_dir, site_dir=self.site_dir, log=lambda msg: None)

        self.assertEqual(report.pages, 1)
        index = (self.site_dir / "index.html").read_text()
        self.assertIn("Certain Wearable Devices", index)
        self.assertTrue((self.site_dir / "investigations" / "337-1478.html").exists())

    def test_rendering_twice_is_stable_and_prunes_renumbered_pages(self):
        store = self.build_store()
        render_site(store, data_dir=self.data_dir, site_dir=self.site_dir, log=lambda msg: None)
        first = (self.site_dir / "index.html").read_text()

        stale = self.site_dir / "investigations" / "337-3936.html"
        stale.write_text("old page")
        render_site(store, data_dir=self.data_dir, site_dir=self.site_dir, log=lambda msg: None)

        self.assertEqual(first, (self.site_dir / "index.html").read_text())
        self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
