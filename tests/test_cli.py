"""Command-level tests: which command talks to what, and what lands on disk."""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from unittest import mock

from support import (
    DataDirTestCase,
    EDIS_DOCUMENTS,
    FakeEdisClient,
    ids_row,
    payload,
    write_snapshot,
)

import cli
from datalayer import docs, ids


@contextmanager
def _session(client):
    yield client


class CliTestCase(DataDirTestCase):
    def run_cli(self, argv, *, capture=False):
        full = [
            "--data-dir", str(self.data_dir),
            "--site-dir", str(self.site_dir),
            "--ids-dir", str(self.ids_dir),
            *argv,
        ]
        if not capture:
            with mock.patch("sys.stdout"):
                return cli.main(full), ""
        printed: list[str] = []
        # Modules capture `print` in their default `log=` argument, so those
        # calls bypass a builtins patch; sys.stdout catches what is left.
        with mock.patch("builtins.print", lambda *a, **kw: printed.append(" ".join(map(str, a)))):
            with mock.patch("sys.stdout"):
                exit_code = cli.main(full)
        return exit_code, "\n".join(printed)

    def invoke(self, argv, *, client=None, raw=None, capture=False):
        """Run a command with every network call faked out.

        `raw` is the bytes the IDS download returns; without it a download is
        an assertion failure, so a command that should not reach the feed
        cannot quietly do so.
        """
        download = (
            mock.patch.object(ids, "download", return_value=raw)
            if raw is not None
            else mock.patch.object(ids, "download", side_effect=AssertionError("downloaded"))
        )
        with download, mock.patch(
            "cli.load_token", return_value="fake-token"
        ), mock.patch.object(
            docs, "edis_session", lambda token: _session(client or FakeEdisClient())
        ):
            return self.run_cli(argv, capture=capture)

    def seed_cases(self, rows=None, *, day="2026-09-22"):
        write_snapshot(self.ids_dir, rows or [ids_row()], day=day)
        self.invoke(["parse"])


class TestSync(CliTestCase):
    def test_sync_downloads_parses_and_can_render(self):
        raw = json.dumps(payload([ids_row()])).encode()
        exit_code, _ = self.invoke(["sync", "--render"], raw=raw)

        self.assertEqual(exit_code, 0)
        cases = self.read_json("investigations.json")
        self.assertEqual(cases["337-1478"]["title"], "Certain Wearable Devices")
        self.assertTrue((self.site_dir / "index.html").exists())
        self.assertTrue((self.site_dir / "investigations" / "337-1478.html").exists())

    def test_sync_needs_no_edis_token(self):
        raw = json.dumps(payload([ids_row()])).encode()
        with mock.patch("cli.load_token", side_effect=AssertionError("token requested")):
            with mock.patch.object(ids, "download", return_value=raw):
                exit_code, _ = self.run_cli(["sync"])
        self.assertEqual(exit_code, 0)

    def test_parse_rebuilds_from_the_stored_snapshot_without_downloading(self):
        write_snapshot(self.ids_dir, [ids_row()])
        exit_code, _ = self.invoke(["parse", "--render"])

        self.assertEqual(exit_code, 0)
        self.assertIn("337-1478", self.read_json("investigations.json"))
        self.assertTrue((self.site_dir / "investigations" / "337-1478.html").exists())


class TestDocs(CliTestCase):
    def test_docs_fetches_the_numbers_given(self):
        self.seed_cases()
        client = FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        exit_code, _ = self.invoke(["docs", "337-1478"], client=client)

        self.assertEqual(exit_code, 0)
        self.assertEqual(client.document_calls, ["337-1478"])
        self.assertIn("337-1478", self.read_json("documents_index.json"))

    def test_docs_without_numbers_is_an_error_that_says_what_to_do(self):
        self.seed_cases()
        exit_code, output = self.invoke(["docs"], capture=True)
        self.assertEqual(exit_code, 1)
        self.assertIn("--existing", output)

    def test_docs_existing_refreshes_only_cases_already_fetched(self):
        self.seed_cases(
            [ids_row(), ids_row("337-1479", investigation_id=2, topic="Certain Widgets")]
        )
        self.invoke(
            ["docs", "337-1478"], client=FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        )

        again = FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        self.invoke(["docs", "--existing"], client=again)
        self.assertEqual(again.document_calls, ["337-1478"])

    def test_docs_leaves_the_case_records_alone(self):
        # `invoke` also fails the test if the command reaches the IDS feed.
        self.seed_cases()
        before = (self.data_dir / "investigations.json").read_text(encoding="utf-8")
        client = FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        exit_code, _ = self.invoke(["docs", "337-1478"], client=client)

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            before, (self.data_dir / "investigations.json").read_text(encoding="utf-8")
        )


class TestOtherCommands(CliTestCase):
    def test_render_needs_no_token_and_makes_no_calls(self):
        self.seed_cases()
        with mock.patch("cli.load_token", side_effect=AssertionError("token requested")):
            exit_code, _ = self.run_cli(["render"])

        self.assertEqual(exit_code, 0)
        self.assertTrue((self.site_dir / "index.html").exists())

    def test_refresh_syncs_then_refreshes_documents_already_fetched(self):
        # An older snapshot on disk, so the sync inside refresh really
        # downloads today's rather than reusing it.
        self.seed_cases(day="2020-01-01")
        self.invoke(
            ["docs", "337-1478"], client=FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        )

        raw = json.dumps(payload([ids_row(status="Terminated")])).encode()
        again = FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        exit_code, _ = self.invoke(["refresh"], client=again, raw=raw)

        self.assertEqual(exit_code, 0)
        self.assertEqual(again.document_calls, ["337-1478"])
        self.assertEqual(self.read_json("investigations.json")["337-1478"]["status"], "Terminated")
        self.assertTrue((self.site_dir / "index.html").exists())

    def test_refresh_still_refreshes_documents_when_the_case_download_fails(self):
        self.seed_cases(day="2020-01-01")
        self.invoke(
            ["docs", "337-1478"], client=FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        )

        again = FakeEdisClient(documents={"337-1478": EDIS_DOCUMENTS})
        with mock.patch.object(ids, "RETRY_WAITS", ()):
            exit_code, output = self.invoke(
                ["refresh"], client=again, raw=b"<html>maintenance</html>", capture=True
            )

        self.assertEqual(exit_code, 1)
        self.assertIn("IDS ERROR", output)
        self.assertEqual(again.document_calls, ["337-1478"])
        self.assertTrue((self.site_dir / "index.html").exists())

    def test_fields_lists_what_the_schema_can_name(self):
        self.seed_cases()
        exit_code, output = self.invoke(["fields"], capture=True)

        self.assertEqual(exit_code, 0)
        self.assertIn("start_date", output)
        self.assertIn("participants", output)
        self.assertIn("STAGE LISTS", output)

    def test_status_reports_the_snapshot_and_what_has_documents(self):
        self.seed_cases()
        _, output = self.invoke(["status"], capture=True)

        self.assertIn("2026-09-22", output)
        self.assertIn("No documents fetched yet", output)

    def test_the_old_command_names_still_work_and_say_what_they_are_now(self):
        raw = json.dumps(payload([ids_row()])).encode()
        exit_code, output = self.invoke(["discover"], raw=raw, capture=True)

        self.assertEqual(exit_code, 0)
        self.assertIn("'discover' is now 'sync'", output)
        self.assertIn("337-1478", self.read_json("investigations.json"))


if __name__ == "__main__":
    unittest.main()
