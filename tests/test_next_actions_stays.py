"""Next actions, phase 3: stays, respondents out of the case, and the list page."""

from __future__ import annotations

import json
import re
import unittest
from datetime import date

from datalayer.nextactions import build, stays
from ui import templates

TODAY = date(2026, 9, 26)


def doc(doc_id, day, title, type_="Order"):
    return {"id": str(doc_id), "document_date": day, "title": title, "document_type": type_}


class TestStays(unittest.TestCase):
    def test_a_whole_case_stay_and_its_end_date(self):
        documents = [
            doc(1, "2026-08-25", "Granting Joint Motion to Stay the Procedural Schedule"),
            doc(2, "2026-09-10", "Granting Joint Motion to Extend Stay of Procedural Deadlines until September 30, 2026"),
        ]
        status = stays.assess(documents, TODAY)
        self.assertEqual((status["stay"]["since"], status["stay"]["until"]), ("2026-08-25", "2026-09-30"))
        self.assertTrue(status["stay"]["extended"])
        self.assertEqual(stays.assess(documents, date(2026, 10, 5))["ended"]["ended_by"], "ran to 2026-09-30")

    def test_for_a_number_of_days(self):
        status = stays.assess([doc(1, "2026-09-20", "Staying the Investigation for Fourteen Days")], TODAY)
        self.assertEqual(status["stay"]["until"], "2026-10-04")

    def test_denials_are_not_stays_and_rescheduling_ends_one(self):
        self.assertIsNone(stays.assess([doc(1, "2026-09-01", "Denying Respondents' Motion to Stay")], TODAY)["stay"])
        documents = [doc(1, "2026-06-11", "Granting Unopposed Motion to Stay Investigation Pending a Final Ruling"),
                     doc(2, "2026-07-20", "Setting Amended Procedural Schedule")]
        status = stays.assess(documents, TODAY)
        self.assertIsNone(status["stay"])
        self.assertIn("set the schedule again", status["ended"]["ended_by"])

    def test_a_stay_for_some_respondents_leaves_the_rest(self):
        documents = [
            doc(1, "2025-08-13", "Granting Joint Motion to Stay the Procedural Schedule with Respect to HP"),
            doc(2, "2025-09-03", "Extending Stay of Procedural Schedule as to HP"),
            doc(3, "2026-09-24", "Granting Complainant's Motion to Stay All Deadlines as to the Samsung, Google, and "
                                 "Garmin Respondents"),
        ]
        status = stays.assess(documents, TODAY)
        self.assertIsNone(status["stay"])
        self.assertEqual([(p["who"], p["since"]) for p in status["partial"]],
                         [("HP", "2025-09-03"), ("the Samsung, Google, and Garmin Respondents", "2026-09-24")])


class TestOutOfCase(unittest.TestCase):
    def test_named_terminations_are_final_once_the_commission_declines_review(self):
        documents = [
            doc(1, "2026-03-01", "Initial Determination Terminating the Investigation as to Vizio, Inc. Based on "
                                 "Settlement", type_="ID/RD - Other Than Final on Violation"),
            doc(2, "2026-03-25", "Commission Determination Not to Review an Initial Determination Terminating the "
                                 "Investigation as to Certain Respondents", type_="Notice"),
            doc(3, "2026-09-01", "Initial Determination Granting Joint Motion to Terminate the Investigation as to "
                                 "the Linksys Respondents and Limiting Service", type_="ID/RD - Other Than Final on Violation"),
            doc(4, "2026-09-02", "Initial Determination Finding Respondent Unicorn Network, LLC in Default",
                type_="ID/RD - Other Than Final on Violation"),
            doc(5, "2026-09-03", "Initial Determination Terminating the Investigation as to Claims 3-5 of the '123 "
                                 "Patent", type_="ID/RD - Other Than Final on Violation"),
            doc(6, "2026-09-04", "Denying Motion to Terminate the Investigation as to Acme"),
        ]
        out = {o["who"]: o for o in stays.out_of_case(documents)}
        self.assertEqual(sorted(out), ["Unicorn Network, LLC", "Vizio, Inc", "the Linksys Respondents"])
        self.assertTrue(out["Vizio, Inc"]["final"])
        self.assertFalse(out["the Linksys Respondents"]["final"])
        self.assertEqual(out["Unicorn Network, LLC"]["how"], "in default")


def case(**fields):
    fields.setdefault("start_date", "2026-01-13")
    return {"investigation_number": "337-1", "status": "Active", "phase": "Violation", "date_initiated": "2026-01-13",
            "stages": [{"is_current": True, "fields": fields}]}


class TestApplied(unittest.TestCase):
    def test_a_stay_puts_later_dates_on_hold_and_names_what_it_waits_on(self):
        documents = [doc(1, "2026-08-25", "Granting Joint Motion to Stay the Procedural Schedule")]
        schedule = [
            {"event": "Close of fact discovery", "date": "2026-08-01", "category": "discovery"},
            {"event": "Exchange initial expert reports", "date": "2026-10-01", "category": "expert"},
        ]
        result = build.build_case(case(target_date="2027-05-17"), documents, schedule=schedule, today=TODAY)
        held = {e.label: bool(e.on_hold) for e in result.events}
        self.assertEqual(held["Close of fact discovery"], False)
        self.assertEqual(held["Exchange initial expert reports"], True)
        self.assertEqual(result.waiting_on, "The end of the stay")

    def test_an_order_date_for_a_respondent_out_of_the_case_is_dropped(self):
        documents = [doc(1, "2026-05-01", "Initial Determination Terminating the Investigation as to Vizio, Inc.",
                         type_="ID/RD - Other Than Final on Violation")]
        schedule = [
            {"event": "Vizio to produce source code", "date": "2026-10-01", "category": "discovery"},
            {"event": "Exchange initial expert reports", "date": "2026-10-01", "category": "expert"},
        ]
        result = build.build_case(case(), documents, schedule=schedule, today=TODAY)
        self.assertEqual([e.label for e in result.events if e.basis == "order"], ["Exchange initial expert reports"])
        self.assertEqual(result.to_dict()["out_of_case"][0]["who"], "Vizio, Inc")


class TestListPage(unittest.TestCase):
    def test_next_deadline_cell_and_due_panel(self):
        record = build.build_case(case(target_date="2026-09-30", initial_determination_date="2026-09-28"), [],
                                  today=TODAY).to_dict()
        cell = templates._next_deadline_cell({"items": templates._upcoming(record, "2026-09-19"), "today": "2026-09-26"})
        self.assertIn("28 Sep 2026", cell)
        self.assertIn("data-next=", cell)
        self.assertIn("Stayed", templates._next_deadline_cell({"stayed": True}))

        panel = templates._due_panel([{"investigation_number": "337-1", "title": "Certain Widgets"}], {"337-1": record},
                                     "2026-09-19")
        data = json.loads(re.search(r'<script type="application/json" id="due-data">(.*?)</script>', panel).group(1))
        self.assertEqual(data[0][:4], ["2026-09-28", "337-1", "Certain Widgets", "Final initial determination on violation (scheduled)"])
        self.assertTrue(data[0][4].endswith("#next"))


if __name__ == "__main__":
    unittest.main()
