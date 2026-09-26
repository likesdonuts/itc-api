"""Next actions, phase 2: procedural schedules read from the orders."""

from __future__ import annotations

import dataclasses
import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

from support import DataDirTestCase

from datalayer.nextactions import build, orders


def doc(doc_id, day, title, type_="Order"):
    return {"id": str(doc_id), "document_date": day, "title": title, "document_type": type_, "security_level": "Public"}


class TestSelect(unittest.TestCase):
    def test_issued_orders_from_the_latest_full_schedule_on(self):
        documents = [
            doc(1, "2026-01-05", "(1) Setting a 16-Month Target Date (2) Setting Preliminary Conference"),
            doc(2, "2026-01-12", "Procedural Schedule"),
            doc(3, "2026-02-01", "Joint Motion to Amend the Procedural Schedule", type_="Motion"),
            doc(4, "2026-02-10", "Seeking Joint Submission on Target Date"),
            doc(5, "2026-03-01", "Adopted Procedural Schedule"),
            doc(6, "2026-04-01", "Amending Procedural Schedule"),
            doc(7, "2026-04-02", "F.R. Notice of Commission Determination Not to Review an ID Extending the Target Date",
                type_="Notice"),
            doc(8, "2026-05-01", "Commission Determination Not to Review an Initial Determination Extending the Target Date",
                type_="Notice"),
            doc(9, "2026-06-01", "Initial Determination Extending the Target Date", type_="ID/RD - Other Than Final on Violation"),
        ]
        # The latest full schedule (5), what followed it (6), and only the
        # latest of the orders that just move the target date (9).
        self.assertEqual([d["id"] for d in orders.select(documents)], ["5", "6", "9"])

    def test_before_the_commission_only_what_it_issued_since_the_final_id(self):
        documents = [
            doc(2, "2025-01-12", "Procedural Schedule"),
            doc(3, "2026-01-21", "Commission Decision to Review an Initial Determination in Part; Schedule for Filing "
                                 "Written Submissions on the Issues under Review and on Remedy", type_="Notice"),
        ]
        self.assertEqual([d["id"] for d in orders.select(documents, since="2025-08-01")], ["3"])


TABLE_TEXT = """Event
Date(S)
Exchange list of claim terms to be
Tuesday, September 29, 2026
construed
Deadline for seeking judicial
assistance with subpoenas, foreign
Thursday, 0ctober 15, 2026
process, and discovery disputes
Evidentiary Hearing
Tuesday, March 30, 2027;
Wednesday, April 7, 2027
Submit joint report on mediation
One week after the one-day mediation session
"""


class TestValidate(unittest.TestCase):
    def check(self, **event):
        base = {"event": "", "date": "", "end_date": "", "relative": "", "category": "other", "quote": "x"}
        kept, rejected = orders.validate([{**base, **event}], TABLE_TEXT, "2026-09-18")
        return (kept or rejected)[0]

    def test_a_table_row_split_across_lines_is_still_found(self):
        ok = self.check(event="Exchange list of claim terms to be construed", date="2026-09-29",
                        quote="Exchange list of claim terms to be construed Tuesday, September 29, 2026")
        self.assertNotIn("rejected", ok)
        # "0ctober" with a zero, as OCR read it.
        ok = self.check(event="Deadline for seeking judicial assistance with subpoenas", date="2026-10-15")
        self.assertNotIn("rejected", ok)

    def test_invented_dates_and_events_are_rejected(self):
        self.assertIn("rejected", self.check(event="Exchange list of claim terms to be construed", date="2026-12-24"))
        self.assertIn("rejected", self.check(event="Tutorial on the technology", date="2026-09-29"))
        self.assertIn("rejected", self.check(event="Anything", date="2031-01-01"))
        self.assertIn("rejected", self.check(event="Undated and unexplained"))

    def test_relative_deadlines_and_multi_day_events(self):
        ok = self.check(event="Submit joint report on mediation", relative="One week after the one-day mediation session")
        self.assertNotIn("rejected", ok)
        hearing = self.check(event="Evidentiary Hearing", date="2027-03-30", end_date="2027-04-07")
        self.assertEqual(hearing["end_date"], "2027-04-07")
        backwards = self.check(event="Evidentiary Hearing", date="2027-03-30", end_date="2027-03-01")
        self.assertEqual(backwards["end_date"], "")


class TestLayering(DataDirTestCase):
    def cache(self, doc_id, day, role, events):
        orders.orders_dir(self.data_dir).mkdir(parents=True, exist_ok=True)
        record = {"doc_id": str(doc_id), "case": "337-1", "title": f"Order {doc_id}", "date": day, "role": role,
                  "events": events, "rejected": [], "prompt_version": orders.PROMPT_VERSION}
        (orders.orders_dir(self.data_dir) / f"{doc_id}.json").write_text(json.dumps(record), encoding="utf-8")

    def ev(self, name, day, category="other", **extra):
        return {"event": name, "date": day, "end_date": "", "relative": "", "category": category, "quote": name, **extra}

    def test_amendments_replace_the_dates_they_change_and_nothing_else(self):
        documents = [doc(1, "2026-01-12", "Procedural Schedule"), doc(2, "2026-03-01", "Amending Procedural Schedule")]
        self.cache(1, "2026-01-12", "full_schedule", [
            self.ev("File tentative list of witnesses a party will call to testify", "2026-03-13"),
            self.ev("Exchange initial expert reports", "2026-03-27", "expert"),
            self.ev("Exchange rebuttal expert reports", "2026-04-14", "expert"),
            self.ev("Evidentiary hearing", "2026-06-17", "hearing"),
        ])
        self.cache(2, "2026-03-01", "amendment", [
            self.ev("File tentative list of witnesses", "2026-03-20"),
            self.ev("Exchange initial expert reports", "2026-04-07", "expert"),
            self.ev("Hearing", "2026-06-24", "hearing", end_date="2026-06-30"),
        ])
        merged = {e["event"]: e for e in orders.schedule_events(self.data_dir, documents)}
        self.assertEqual(len(merged), 4)
        self.assertEqual(merged["File tentative list of witnesses"]["replaces"], "2026-03-13")
        self.assertEqual(merged["Exchange initial expert reports"]["date"], "2026-04-07")
        self.assertEqual(merged["Exchange rebuttal expert reports"]["date"], "2026-04-14")  # untouched
        self.assertEqual((merged["Hearing"]["date"], merged["Hearing"]["end"]), ("2026-06-24", "2026-06-30"))
        self.assertEqual(merged["Hearing"]["source"]["id"], "2")

    def test_an_order_restating_the_schedule_replaces_it(self):
        documents = [doc(1, "2026-01-12", "Procedural Schedule"),
                     doc(2, "2026-04-22", "Granting Joint Motion to Modify the Procedural Schedule")]
        self.cache(1, "2026-01-12", "full_schedule", [self.ev(f"Old event {i}", "2026-02-01") for i in range(10)])
        self.cache(2, "2026-04-22", "amendment", [self.ev(f"New event {i}", "2026-05-01") for i in range(8)])
        merged = orders.schedule_events(self.data_dir, documents)
        self.assertEqual({e["source"]["id"] for e in merged}, {"2"})

    def test_the_case_record_wins_for_its_own_milestones(self):
        case = {"investigation_number": "337-1", "status": "Active", "phase": "Violation",
                "stages": [{"is_current": True, "fields": {"start_date": "2026-01-13", "target_date": "2027-05-17",
                                                           "hearing_conf_start_date": "2026-09-16"}}]}
        schedule = [
            {"event": "Evidentiary hearing", "date": "2026-09-15", "category": "hearing"},
            {"event": "Target date", "date": "2027-05-17", "category": "target_date"},
            {"event": "Pre-hearing statements and briefs", "date": "2026-08-20", "category": "hearing"},
            {"event": "Close of fact discovery", "date": "2026-05-01", "category": "discovery",
             "source": {"id": "5", "title": "Procedural Schedule", "date": "2026-01-20"}},
        ]
        result = build.build_case(case, [], schedule=schedule, today=date(2026, 3, 1))
        from_orders = [e.label for e in result.events if e.basis == "order"]
        self.assertEqual(sorted(from_orders), ["Close of fact discovery", "Pre-hearing statements and briefs"])


def fake_client(events, role="full_schedule"):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        block = SimpleNamespace(type="tool_use", input={"role": role, "events": events})
        usage = SimpleNamespace(input_tokens=3000, output_tokens=500, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0, cache_creation=None)
        return SimpleNamespace(content=[block], usage=usage, stop_reason="tool_use")

    return SimpleNamespace(messages=SimpleNamespace(create=create)), calls


class TestReading(DataDirTestCase):
    def setUp(self):
        super().setUp()
        store = self.store()
        store.investigations = {"337-1": {"investigation_number": "337-1", "status": "Active", "phase": "Violation",
                                          "stages": [{"is_current": True, "fields": {"start_date": "2026-01-13"}}]}}
        pdf_dir = store.docs_dir / "337-1"
        pdf_dir.mkdir(parents=True)
        (pdf_dir / "7_1_schedule.pdf").write_bytes(b"%PDF")
        order = doc(7, "2026-01-20", "Procedural Schedule")
        order["attachments"] = [{"href": "../../data/documents/337-1/7_1_schedule.pdf", "label": "schedule.pdf"}]
        store.documents = {"337-1": [order]}
        self.store_ = store
        cfg = orders.load_config()
        self.cfg = dataclasses.replace(cfg, costs_csv=self.data_dir / "costs.csv")

    def run_orders(self, client, cfg=None):
        with mock.patch.object(orders, "load_config", return_value=cfg or self.cfg), \
             mock.patch("datalayer.claims.ocr.pdf_text", return_value=TABLE_TEXT):
            return orders.run(self.store_, token=None, client=client, log=self.quiet)

    def test_each_order_is_read_once_and_its_cost_logged(self):
        client, calls = fake_client([
            {"event": "Exchange list of claim terms to be construed", "date": "2026-09-29", "end_date": "",
             "relative": "", "category": "claim_construction", "quote": "claim terms"},
            {"event": "Invented deadline", "date": "2026-12-01", "end_date": "", "relative": "",
             "category": "other", "quote": "nothing like this"},
        ])
        first = self.run_orders(client)
        second = self.run_orders(client)
        self.assertEqual(len(calls), 1)
        self.assertEqual((first.read, first.events, first.rejected), (1, 1, 1))
        self.assertEqual(second.read, 0)
        self.assertIn("next-actions:337-1", (self.data_dir / "costs.csv").read_text(encoding="utf-8"))
        cached = orders.cached(self.data_dir, "7")
        self.assertEqual(cached["events"][0]["date"], "2026-09-29")

    def test_its_own_budget_stops_it(self):
        client, calls = fake_client([])
        report = self.run_orders(client, dataclasses.replace(self.cfg, budget_usd=0.0))
        self.assertEqual(calls, [])
        self.assertIn("Next actions budget", report.stopped)


if __name__ == "__main__":
    unittest.main()
