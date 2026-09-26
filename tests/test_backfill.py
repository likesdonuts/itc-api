"""The one-time backfill of document lists: order, resuming, and what the
daily sync does with the cases it filled in.
"""

from __future__ import annotations

import unittest
from datetime import date
from contextlib import contextmanager
from unittest import mock

from support import EDIS_DOCUMENTS, DataDirTestCase, FakeEdisClient

from datalayer import backfill, docs
from datalayer.client import EdisAuthError


@contextmanager
def _session(client):
    yield client


class BackfillTestCase(DataDirTestCase):
    def seeded(self, cases):
        """`cases`: {key: (status, date_initiated)}."""
        store = self.store()
        store.investigations = {
            key: {"investigation_number": key, "status": status, "date_initiated": started}
            for key, (status, started) in cases.items()
        }
        store.save_cases()
        return store

    def run_backfill(self, store, client, **kwargs):
        kwargs.setdefault("pause", 0)
        with mock.patch.object(backfill, "edis_session", lambda token: _session(client)):
            return backfill.run(store, "token", log=self.quiet, **kwargs)


class TestBackfill(BackfillTestCase):
    CASES = {
        "337-1400": ("Terminated", "2024-03-01"),
        "337-1500": ("Active", "2026-05-01"),
        "337-0100": ("Terminated", None),
        "337-1450": ("Terminated", "2025-06-01"),
    }

    def test_it_lists_every_case_without_a_list_newest_first_and_downloads_nothing(self):
        store = self.seeded(self.CASES)
        store.put_documents("337-1450", [{"id": "1"}])
        client = FakeEdisClient(documents={k: EDIS_DOCUMENTS for k in ("337-1400", "337-1500", "337-0100")})

        report = self.run_backfill(store, client)

        self.assertEqual(report.listed, ["337-1500", "337-1400", "337-0100"])
        self.assertEqual(client.downloads, [])
        self.assertNotIn("337-1450", [call for call in client.document_calls])
        saved = self.store()
        self.assertTrue(saved.documents_state["337-1500"]["backfill"])
        self.assertEqual(saved.documents["337-1400"][0]["firm_organization"], "Firm LLP")
        self.assertEqual(saved.state["runs"]["backfill"]["remaining"], 0)

    def test_a_case_edis_has_nothing_for_is_remembered_not_asked_again(self):
        store = self.seeded({"337-0100": ("Terminated", None)})
        self.run_backfill(store, FakeEdisClient())

        self.assertTrue(self.store().documents_state["337-0100"]["edis_empty"])
        self.assertEqual(backfill.targets(self.store()), [])
        self.assertEqual(backfill.targets(self.store(), retry_empty=True), ["337-0100"])

    def test_stopping_keeps_what_was_done_and_the_next_run_continues(self):
        store = self.seeded(self.CASES)
        client = FakeEdisClient(documents={k: EDIS_DOCUMENTS for k in self.CASES})
        asked = iter([False, False, True])

        first = self.run_backfill(store, client, should_stop=lambda: next(asked))
        self.assertEqual(first.listed, ["337-1500", "337-1450"])
        self.assertEqual(first.stopped, "stopped on request")
        self.assertEqual(sorted(self.store().documents), ["337-1450", "337-1500"])

        second = self.run_backfill(self.store(), client)
        self.assertEqual(second.listed, ["337-1400", "337-0100"])

    def test_an_expired_token_saves_progress_and_ends_the_run(self):
        store = self.seeded(self.CASES)

        class Expiring(FakeEdisClient):
            def list_documents(self, number):
                if len(self.document_calls) >= 1:
                    raise EdisAuthError("EDIS_TOKEN expired")
                return super().list_documents(number)

        report = self.run_backfill(store, Expiring(documents={"337-1500": EDIS_DOCUMENTS}))

        self.assertIn("expired", report.stopped)
        self.assertEqual(list(self.store().documents), ["337-1500"])
        self.assertEqual(self.store().state["runs"]["backfill"]["remaining"], 3)

    def test_limit_takes_the_newest(self):
        store = self.seeded(self.CASES)
        report = self.run_backfill(
            store, FakeEdisClient(documents={k: EDIS_DOCUMENTS for k in self.CASES}), limit=1
        )
        self.assertEqual(report.listed, ["337-1500"])
        self.assertEqual(report.remaining, 3)


class TestTheDailySyncAfterABackfill(BackfillTestCase):
    def test_backfilled_cases_are_refreshed_only_while_open(self):
        store = self.seeded({
            "337-1400": ("Terminated", "2024-01-01"),  # collected by hand
            "337-1500": ("Active", "2026-01-01"),  # backfilled, open
            "337-1450": ("Terminated", "2025-01-01"),  # backfilled, closed
        })
        for key in store.investigations:
            store.put_documents(key, [{"id": "1", "document_date": "2026-09-01"}])
        store.documents_state["337-1500"]["backfill"] = True
        store.documents_state["337-1450"]["backfill"] = True

        # Collected by hand but closed: weekly, so due once its last refresh is a week old.
        store.documents_state["337-1400"]["fetched_at"] = "2026-09-10T00:00:00+00:00"
        self.assertEqual(backfill.daily_targets(store, today=date(2026, 9, 25)), ["337-1400", "337-1500"])
        store.documents_state["337-1400"]["fetched_at"] = "2026-09-22T00:00:00+00:00"
        self.assertEqual(backfill.daily_targets(store, today=date(2026, 9, 25)), ["337-1500"])

    def test_how_often_each_kind_of_case_is_refreshed(self):
        store = self.seeded({
            "337-1500": ("Active", "2026-01-01"),  # live, by hand
            "337-1400": ("Terminated", "2024-01-01"),  # closed, by hand
            "337-1401": ("Active", "2019-01-01"),  # quiet, by hand
            "337-1501": ("Active", "2026-01-01"),  # live, backfilled
            "337-055": ("Active", None),  # quiet, backfilled
            "337-1450": ("Terminated", "2025-01-01"),  # closed, backfilled
        })
        dates = {"337-1500": "2026-09-01", "337-1400": "2026-09-01", "337-1401": "2020-01-01",
                 "337-1501": "2026-09-01", "337-055": "1979-03-01", "337-1450": "2026-09-01"}
        for key, day in dates.items():
            store.put_documents(key, [{"id": key, "document_date": day}])
        for key in ("337-1501", "337-055", "337-1450"):
            store.documents_state[key]["backfill"] = True
        every = {k: backfill.refresh_every(store, k, today=date(2026, 9, 25)) for k in dates}
        self.assertEqual(every, {"337-1500": 1, "337-1400": 7, "337-1401": 7, "337-1501": 1, "337-055": 30,
                                 "337-1450": None})

    def test_an_old_active_listing_is_checked_monthly_not_daily(self):
        # The USITC lists some decades-old investigations as "Active".
        store = self.seeded({"337-055": ("Active", None)})  # backfilled, nothing filed in years
        store.put_documents("337-055", [{"id": "1", "document_date": "1979-03-01"}])
        store.documents_state["337-055"]["backfill"] = True
        today = date(2026, 9, 25)

        store.documents_state["337-055"]["fetched_at"] = "2026-09-20T00:00:00+00:00"
        self.assertEqual(backfill.daily_targets(store, today=today), [])
        store.documents_state["337-055"]["fetched_at"] = "2026-08-01T00:00:00+00:00"
        self.assertEqual(backfill.daily_targets(store, today=today), ["337-055"])

        # One recent filing makes it a daily case again.
        store.documents["337-055"].append({"id": "3", "document_date": "2026-09-10"})
        store.documents_state["337-055"]["fetched_at"] = "2026-09-20T00:00:00+00:00"
        self.assertEqual(backfill.daily_targets(store, today=today), ["337-055"])

    def test_a_routine_refresh_keeps_the_mark_and_a_fetch_by_hand_clears_it(self):
        store = self.seeded({"337-1500": ("Active", "2026-01-01")})
        store.put_documents("337-1500", [{"id": "1"}])
        store.documents_state["337-1500"]["backfill"] = True
        client = FakeEdisClient(documents={"337-1500": EDIS_DOCUMENTS})

        with mock.patch.object(docs, "edis_session", lambda token: _session(client)):
            docs.run(store, "token", ["337-1500"], download=False, by_hand=False, log=self.quiet)
            self.assertTrue(store.documents_state["337-1500"]["backfill"])
            docs.run(store, "token", ["337-1500"], download=False, log=self.quiet)
            self.assertNotIn("backfill", store.documents_state["337-1500"])


if __name__ == "__main__":
    unittest.main()
