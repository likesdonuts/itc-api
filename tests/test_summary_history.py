"""The case history: every dispositive event, from the documents' titles."""

from __future__ import annotations

import unittest

from support import DataDirTestCase

from datalayer.summary import history, write
from datalayer.summary import facts as summary_facts
from ui import templates

ID = "ID/RD - Other Than Final on Violation"
_ids = iter(range(5000, 100000))


def doc(title, type_, day, *, public=True):
    return {"id": str(next(_ids)), "title": title, "document_type": type_, "document_date": day,
            "security_level": "Public" if public else "Confidential"}


# 337-1462, as its docket reads.
DOCKET_1462 = [
    doc("Institution of Investigation", "Notice", "2025-11-24"),
    doc("Initial Determination Setting the Target Date", ID, "2025-12-05"),
    doc("Commission Determination Not to Review an Initial Determination Setting an 18-Month Target Date", "Notice", "2025-12-17"),
    doc("Initial Determination Granting Complainants’ Second Amended Unopposed Motion for Termination of the Investigation "
        "as to all Respondents Based on Withdrawal of the Complaint", ID, "2026-07-22", public=False),
    doc("Initial Determination Granting Complainants’ Second Amended Unopposed Motion for Termination of the Investigation "
        "as to all Respondents Based on Withdrawal of the Complaint", ID, "2026-08-06"),
    doc("Commission Determination Not to Review an Initial Determination Terminating the Investigation Based on Withdrawal "
        "of the Complaint; Termination of the Investigation", "Notice", "2026-08-21"),
    doc("F.R. Notice of a Commission Determination Not To Review an Initial Determination Terminating the Investigation",
        "Notice", "2026-08-25"),
    doc("Complainants' Unopposed Motion for Termination of the Investigation", "Motion", "2026-07-01"),
]


class TestTheHistory(unittest.TestCase):
    def test_337_1462_termination_and_the_commission_letting_it_stand(self):
        events = history.events(DOCKET_1462)
        self.assertEqual([(e.date, e.kind) for e in events], [
            ("2025-11-24", "institution"),
            ("2026-07-22", "termination"),  # dated by its confidential version, linked to its public one
            ("2026-08-21", "not_reviewed"),
        ])
        self.assertEqual(events[1].doc_id, DOCKET_1462[4]["id"])
        self.assertEqual(len(events[1].versions), 2)

    def test_what_counts_and_what_is_housekeeping(self):
        cases = {
            "Notice of Commission Determination to Terminate the Investigation in Its Entirety": "determination",
            "Commission Determination of No Violation of Section 337; Termination of Investigation": "determination",
            "Commission Determination to Review in Part a Final Initial Determination Finding a Violation": "review",
            "Notice of Commission Determination Not to Review an Initial Determination Finding Three Respondents in Default": "not_reviewed",
            "Commission Determination to Extend the Target Date": None,
            "Notice of Change of Commission Investigative Attorney": None,
        }
        for title, kind in cases.items():
            self.assertEqual(history.classify(doc(title, "Notice", "2026-01-01")), kind, title)
        self.assertEqual(history.classify(doc("Order: Reversal of Initial Determination and Remand", "Order, Commission", "2026-01-01")), "order")
        self.assertEqual(history.classify(doc("Remand Initial Determination", ID, "2026-01-01")), "remand_id")
        self.assertEqual(history.classify(doc(
            "Initial Determination Terminating Respondent Globex Based on Settlement and Staying the Procedural Schedule", ID,
            "2026-01-01")), "termination")

    def test_the_alj_notice_of_a_final_id_dates_it_and_the_decision_is_linked(self):
        notice = doc("Notice regarding Initial Determination on Violation of Section 337", "Notice", "2024-07-05")
        public = doc("Initial Determination on Violation of Section 337 and Recommended Determination", "ID/RD - Final on Violation", "2024-08-05")
        [event] = history.events([notice, public])
        self.assertEqual((event.kind, event.date, event.doc_id), ("final_id", "2024-07-05", public["id"]))

    def test_versions_with_small_title_slips_are_one_event(self):
        a = doc("Initial Determination Granting Crocs, Inc.’s and Fullbeauty Brands Inc.’s Joint Motion to Terminate", ID,
                "2021-09-09", public=False)
        b = doc("Initial Determination Granting Crocs, Inc.’s and Fullbeauty Brand Inc.’s Joint Motion to Terminate", ID, "2021-09-09")
        self.assertEqual(len(history.events([a, b])), 1)


class TestTheWriterAccountsForIt(unittest.TestCase):
    def test_an_event_that_changes_the_case_and_is_not_cited_is_flagged(self):
        known = write.number_facts(summary_facts.title_facts(DOCKET_1462), [])
        self.assertEqual([f["must"] for f in known.values()], [False, True, True])
        message = write.user_message(key="337-1462", title="T", status="Terminated", numbered={}, groups={}, facts=known)
        self.assertIn("[t2]* (2026-07-22, Termination)", message)
        answer = {"headline": "H", "about": [], "allegations": [], "answers": [], "rulings": [], "decisions": [],
                  "standing": [{"text": "The complaint was withdrawn.", "cites": ["t2"]}]}
        _, warnings = write.check(answer, {}, {}, known)
        self.assertEqual(warnings, ["the summary does not mention 1 event(s) of the case history: 2026-08-21 Commission let stand"])


class TestTheTab(DataDirTestCase):
    def test_what_happened_is_listed_without_a_summary(self):
        html = templates._case_history(DOCKET_1462)
        self.assertIn("What happened <small>(3 events)</small>", html)
        self.assertIn("Commission let stand", html)
        self.assertIn("Dated by its first version", html)
        self.assertNotIn("Target Date", html)


if __name__ == "__main__":
    unittest.main()
