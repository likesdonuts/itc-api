"""Next actions: stage, dates and rule-based deadlines for open investigations."""

from __future__ import annotations

import unittest
from datetime import date

from support import DataDirTestCase

from datalayer.nextactions import build, calendar
from ui import templates

TODAY = date(2026, 9, 25)


class TestCalendar(unittest.TestCase):
    def test_federal_holidays_with_weekend_observance(self):
        holidays = calendar.federal_holidays(2026)
        self.assertIn(date(2026, 7, 3), holidays)  # July 4 is a Saturday
        self.assertIn(date(2026, 6, 19), holidays)  # Juneteenth
        self.assertIn(date(2026, 11, 26), holidays)  # Thanksgiving
        self.assertIn(date(2026, 1, 19), holidays)  # MLK Day, third Monday
        self.assertIn(date(2027, 12, 31), calendar.federal_holidays(2027))  # New Year's 2028 is a Saturday

    def test_periods_start_the_first_business_day_after_the_event(self):
        # 201.14(a): an event on a Friday starts the count on Monday.
        self.assertEqual(calendar.period_end(date(2026, 10, 2), 12), date(2026, 10, 16))
        # A last day on a weekend runs to the next business day.
        self.assertEqual(calendar.period_end(date(2026, 9, 1), 12), date(2026, 9, 14))
        # Short periods count business days only (and Labor Day, 7 September, is not one).
        self.assertEqual(calendar.period_end(date(2026, 9, 4), 5), date(2026, 9, 14))

    def test_months_before_and_the_business_day_before(self):
        self.assertEqual(calendar.months_before(date(2027, 5, 17), 4), date(2027, 1, 17))
        self.assertEqual(calendar.months_before(date(2027, 6, 30), 4), date(2027, 2, 28))
        self.assertEqual(calendar.previous_business_day(date(2027, 1, 17)), date(2027, 1, 15))


def case(status="Active", phase="Violation", **fields):
    fields.setdefault("start_date", "2026-01-13")
    return {
        "investigation_number": "337-1478",
        "status": status,
        "phase": phase,
        "date_initiated": fields.get("start_date"),
        "stages": [{"is_current": True, "is_primary": True, "fields": fields}],
    }


def doc(day, title, type_="Notice", doc_id="1"):
    return {"id": doc_id, "document_date": day, "title": title, "document_type": type_}


def labels(result, basis=None):
    return [e.label for e in result.events if basis is None or e.basis == basis]


class TestStages(unittest.TestCase):
    def build(self, record, documents=(), today=TODAY):
        return build.build_case(record, list(documents), today=today)

    def test_closed_cases_have_none(self):
        self.assertIsNone(self.build(case(status="Terminated")))

    def test_before_the_alj_with_the_case_records_dates(self):
        result = self.build(case(
            target_date="2027-05-17", initial_determination_date="2027-01-15",
            hearing_conf_start_date="2026-09-16", hearing_conf_end_date="2026-09-22",
            markman_hearing_start_date="2026-06-03",
        ))
        self.assertEqual(result.stage, "alj")
        by_label = {e.label: e for e in result.events}
        self.assertEqual(by_label["Evidentiary hearing"].end, "2026-09-22")
        self.assertEqual(by_label["Final initial determination due no later than"].date, "2027-01-15")
        self.assertEqual(by_label["Final initial determination due no later than"].cite, "19 CFR 210.42(a)(1)(i)")
        self.assertIn("final initial determination", result.waiting_on)

    def test_without_a_target_date_the_rule_says_when_it_is_set(self):
        result = self.build(case(start_date="2026-08-03"))
        [rule] = [e for e in result.events if e.basis == "by rule"]
        self.assertEqual((rule.label, rule.date), ("ALJ to set the target date", "2026-09-17"))

    def test_after_the_final_id_the_review_deadlines(self):
        documents = [doc("2026-09-01", "Initial Determination on Violation of Section 337",
                         type_="ID/RD - Final on Violation", doc_id="9")]
        result = self.build(case(target_date="2027-01-04"), documents)
        self.assertEqual(result.stage, "commission")
        dates = {e.label: e.date for e in result.events}
        self.assertEqual(dates["Petitions for review of the final ID due"], "2026-09-14")
        self.assertEqual(dates["Responses to petitions for review due"], "2026-09-22")
        self.assertEqual(dates["Commission to decide whether to review the final ID"], "2026-11-02")
        issued = next(e for e in result.events if e.label == "Final initial determination issued")
        self.assertEqual(issued.source["id"], "9")

    def test_an_extended_review_deadline_has_no_rule_date(self):
        documents = [
            doc("2026-09-01", "Initial Determination on Violation of Section 337", type_="ID/RD - Final on Violation"),
            doc("2026-10-20", "Commission Decision Extending the Date for Reviewing the Final Initial Determination"),
        ]
        result = self.build(case(target_date="2027-01-04"), documents)
        review = next(e for e in result.events if e.label == "Commission to decide whether to review the final ID")
        self.assertIsNone(review.date)
        self.assertIn("extended", review.note)

    def test_under_review_it_waits_for_the_final_determination(self):
        documents = [
            doc("2025-08-01", "Initial Determination on Violation of Section 337", type_="ID/RD - Final on Violation"),
            # Reviewing an earlier, non-final ID is not the review of the final ID...
            doc("2025-06-01", "Commission Determination to Review an Initial Determination Granting Summary Determination"),
            # ...and the Federal Register copy of a notice is not a second notice.
            doc("2026-01-26", "F.R. Notice of Commission Decision to Review an Initial Determination in Part; Remedy"),
            doc("2026-01-21", "Commission Decision to Review an Initial Determination in Part; Schedule for Filing "
                              "Written Submissions on the Issues under Review and on Remedy"),
        ]
        result = self.build(case(target_date="2026-09-29"), documents)
        decided = next(e for e in result.events if e.label == "Commission decided to review the final ID")
        self.assertEqual(decided.date, "2026-01-21")
        self.assertEqual(result.waiting_on, "The Commission's final determination")

    def test_no_review_of_a_no_violation_id_concludes_it(self):
        documents = [
            doc("2026-05-01", "Initial Determination on Violation of Section 337", type_="ID/RD - Final on Violation"),
            doc("2026-07-01", "Commission Determination Not to Review a Final Initial Determination Finding No "
                              "Violation; Termination of the Investigation"),
        ]
        result = self.build(case(), documents)
        self.assertEqual(result.stage, "concluded")
        self.assertIsNone(result.waiting_on)

    def test_a_remedy_starts_presidential_review(self):
        documents = [
            doc("2026-05-01", "Initial Determination on Violation of Section 337", type_="ID/RD - Final on Violation"),
            doc("2026-09-01", "Commission's Final Determination Finding a Violation of Section 337; Issuance of a "
                              "Limited Exclusion Order and Cease and Desist Orders"),
        ]
        result = self.build(case(), documents)
        self.assertEqual(result.stage, "presidential")
        self.assertIn("2026-10-31", [e.date for e in result.events])

    def test_pre_institution(self):
        result = self.build(case(status="Pre-institution", start_date=None,
                                 initiating_document_received_date="2026-09-10"))
        self.assertEqual(result.stage, "pre_institution")
        [decision] = [e for e in result.events if e.basis == "by rule"]
        self.assertEqual((decision.date, decision.cite), ("2026-10-13", "19 CFR 210.10(a)(1)"))

    def test_old_listings_and_other_phases_say_why_there_is_nothing(self):
        undated = self.build(case(start_date=None))
        self.assertEqual(undated.stage, "undated")
        dormant = self.build(case(start_date="2008-01-02", target_date="2009-05-01"))
        self.assertEqual(dormant.stage, "dormant")
        remand = self.build(case(phase="Remand"))
        self.assertEqual(remand.stage, "other")
        self.assertIn("violation phase only", remand.notes[0])

    def test_a_stay_in_effect_is_noted(self):
        documents = [doc("2026-04-01", "Order Staying the Investigation Pending Reexamination", type_="Order")]
        result = self.build(case(target_date="2027-05-17"), documents)
        self.assertTrue(any("stay" in n.lower() for n in result.notes))
        lifted = documents + [doc("2026-06-01", "Order Lifting the Stay", type_="Order")]
        self.assertFalse(any("stay" in n.lower() for n in self.build(case(target_date="2027-05-17"), lifted).notes))


class TestTheProcessAndThePage(DataDirTestCase):
    def test_run_writes_open_cases_only(self):
        store = self.store()
        store.investigations = {
            "337-1478": case(target_date="2027-05-17"),
            "337-1400": {**case(status="Terminated"), "investigation_number": "337-1400"},
        }
        out = build.run(store, log=self.quiet)
        self.assertEqual(list(out["cases"]), ["337-1478"])
        self.assertEqual(build.load(self.data_dir)["cases"]["337-1478"]["stage"], "alj")

    def test_the_tab_lists_every_event_with_its_basis(self):
        record = build.build_case(case(target_date="2027-05-17", initial_determination_date="2027-01-15"), [],
                                  today=TODAY).to_dict()
        html = templates._with_tabs("<p>overview</p>", None, record, "2026-09-25T12:00:00+00:00")
        self.assertIn('data-tab="next"', html)
        self.assertIn('data-date="2027-01-15"', html)
        self.assertIn("19 CFR 210.42(a)(1)(i)", html)
        self.assertIn("pill-amber", html)  # by rule
        self.assertIn("Waiting on:", html)
        self.assertEqual(templates._with_tabs("<p>overview</p>", None, None), "<p>overview</p>")


if __name__ == "__main__":
    unittest.main()
