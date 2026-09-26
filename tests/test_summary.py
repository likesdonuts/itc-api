"""The case summary, phase 1: which documents it would read, what that would
cost, the Section 337 primer, and the Summary tab. No model is called."""

from __future__ import annotations

import dataclasses
import unittest
from contextlib import contextmanager
from unittest import mock

from support import DataDirTestCase, FakeEdisClient, ids_row, write_snapshot

import cli
from datalayer import ingest
from datalayer.summary import config as summary_config
from datalayer.summary import estimate, pages, preview, primer
from datalayer.summary.select import select
from ui.render import render_site

ID = "ID/RD - Other Than Final on Violation"
_ids = iter(range(1000, 100000))


def doc(title, type_, day="2026-01-01", *, public=True, **extra):
    return {"id": str(next(_ids)), "title": title, "document_type": type_, "document_date": day,
            "security_level": "Public" if public else "Confidential", **extra}


def answer(title, who, firm, day="2026-02-01"):
    return doc(title, "Answer to Complaint", day, on_behalf_of=who, firm_organization=firm)


def titles(items):
    return [i.title for i in items]


class TestTheComplaint(unittest.TestCase):
    def test_the_complaint_itself_not_its_appendices_confidential_copies_or_supplements(self):
        docs = [
            doc("Public Complaint and Exhibits", "Complaint", "2026-01-02"),
            doc("Appendix B", "Complaint", "2026-01-02"),
            doc("Appendices A-H", "Complaint", "2026-01-02"),
            doc("Confidential Exhibits to Complaint", "Complaint", "2026-01-02", public=False),
            doc("First Public Supplement to the Complaint", "Complaint", "2026-01-10"),
        ]
        sel = select(docs)
        self.assertEqual(titles(sel.reading("complaint")), ["Public Complaint and Exhibits"])
        self.assertEqual(titles(sel.not_read), ["First Public Supplement to the Complaint"])
        self.assertEqual(sel.skipped_filings, 2)

    def test_the_latest_amended_complaint_replaces_the_original(self):
        docs = [
            doc("Public Complaint", "Complaint", "2026-01-02"),
            doc("Amended Complaint", "Complaint", "2026-02-01"),
            doc("Second Amended Complaint", "Complaint", "2026-03-01"),
        ]
        sel = select(docs)
        self.assertEqual(titles(sel.reading("complaint")), ["Second Amended Complaint"])
        self.assertEqual(
            {i.title: i.why for i in sel.not_read},
            {"Public Complaint": "Replaced by the amended complaint", "Amended Complaint": "Replaced by the amended complaint"},
        )

    def test_an_untitled_filing_stands_in_when_nothing_is_titled_a_complaint(self):
        docs = [doc("", "Complaint", "2026-01-02"), doc("", "Complaint", "2026-01-02")]
        [item] = select(docs).reading("complaint")
        self.assertEqual(item.id, docs[0]["id"])

    def test_the_notice_of_institution_but_not_its_federal_register_reprint(self):
        docs = [doc("F.R. Notice of Institution of Investigation", "Notice"),
                doc("Notice of Institution of Investigation", "Notice")]
        [item] = [i for i in select(docs).read if i.kind == "notice_of_institution"]
        self.assertEqual(item.id, docs[1]["id"])


class TestTheAnswers(unittest.TestCase):
    def test_one_answer_per_counsel_the_latest_and_never_an_exhibit(self):
        docs = [
            answer("Response of Globex Corp. to the Complaint", "Globex Corp.", "Adduci, Mastriani & Schaumberg LLP", "2026-02-01"),
            answer("Amended Response of Globex Corp.", "Globex Corp.", "Adduci, Mastriani and Schaumberg LLP", "2026-03-01"),
            answer("Exhibit A to Globex's Response", "Globex Corp.", "Adduci", "2026-02-01"),
        ]
        [item] = select(docs).reading("answers")
        self.assertEqual(item.title, "Amended Response of Globex Corp.")

    def test_a_respondent_that_changed_firms_is_still_one_group(self):
        docs = [
            answer("Response of Initech Ltd. to the Complaint", "Initech Ltd.", "Firm One LLP", "2026-02-01"),
            answer("Response of Initech, Ltd. to the Amended Complaint", "Initech, Ltd.", "Firm Two LLP", "2026-04-01"),
        ]
        [item] = select(docs).reading("answers")
        self.assertIn("Amended Complaint", item.title)

    def test_the_limit_reads_respondents_still_in_the_case_first(self):
        docs = [answer(f"Response of Party{n} Inc.", f"Party{n} Inc.", f"Firm {n} LLP", f"2026-02-0{n}")
                for n in range(1, 4)]
        docs.append(doc("Initial Determination Finding Respondent Party1 Inc. in Default", ID, "2026-05-01"))
        sel = select(docs, max_answer_groups=2)
        self.assertEqual([i.who for i in sel.reading("answers")], ["Party2 Inc.", "Party3 Inc."])
        [left] = [i for i in sel.not_read if i.kind == "answer"]
        self.assertEqual((left.who, left.why), ("Party1 Inc.", "Out of the case, and over the limit of answers read"))


class TestRulingsAndDecisions(unittest.TestCase):
    def test_summary_determination_rulings_are_read_and_procedure_is_not(self):
        docs = [
            doc("Initial Determination Granting Respondents' Motion for Summary Determination of No Domestic Industry", ID, "2026-05-01"),
            doc("Denying Complainant's Motion for Summary Determination of Infringement", "Order", "2026-04-01"),
            doc("Granting Complainant's Motion for Extension of Time to Respond to Motion for Summary Determination", "Order"),
            doc("Initial Determination Extending the Target Date", ID),
        ]
        sel = select(docs)
        self.assertEqual(len(sel.reading("rulings")), 2)
        # Grants first when the limit bites.
        [first] = select(docs, max_rulings=1).reading("rulings")
        self.assertIn("Granting", first.title)
        # "Determination" contains "terminat": a target date is not a termination.
        self.assertEqual(sel.noted, [])

    def test_terminations_and_defaults_are_noted_from_their_titles(self):
        docs = [
            doc("Initial Determination Terminating Respondent Globex Based on a Settlement Agreement", ID),
            doc("Initial Determination Finding Respondent Initech in Default", ID),
        ]
        sel = select(docs)
        self.assertEqual([i.kind for i in sel.noted], ["termination", "default"])
        self.assertEqual(sel.read, [])

    def test_the_final_id_the_commissions_decisions_and_its_remedies(self):
        docs = [
            doc("Initial Determination on Violation of Section 337", "ID/RD - Final on Violation", "2026-06-01"),
            doc("[Corrected] Initial Determination on Violation of Section 337", "ID/RD - Final on Violation", "2026-06-10"),
            doc("Commission Determination to Review in Part a Final Initial Determination Finding a Violation", "Notice", "2026-08-01"),
            doc("F.R. Commission Determination to Review in Part a Final Initial Determination", "Notice", "2026-08-05"),
            doc("Commission Determination to Extend the Target Date", "Notice"),
            doc("Commission Determination Not to Review an Initial Determination Granting Summary Determination", "Notice"),
            doc("Commission Opinion", "Opinion, Commission", "2026-10-01"),
            doc("Limited Exclusion Order", "Order, Commission", "2026-10-01"),
        ]
        sel = select(docs)
        self.assertEqual([i.kind for i in sel.reading("decisions")], ["final_id", "commission_notice", "commission_opinion"])
        self.assertTrue(sel.reading("decisions")[0].title.startswith("[Corrected]"))
        self.assertEqual(sorted(i.kind for i in sel.noted), ["not_reviewed", "remedy"])

    def test_only_public_documents(self):
        docs = [doc("Commission Opinion", "Opinion, Commission", public=False)]
        self.assertEqual(select(docs).read, [])


class EstimateTestCase(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = dataclasses.replace(summary_config.load(), costs_csv=self.data_dir / "summary_costs.csv")
        self.store_ = self.store()
        self.complaint = doc("Public Complaint and Exhibits", "Complaint")
        self.ruling = doc("Denying Motion for Summary Determination of Invalidity", "Order")
        self.store_.put_documents("337-1", [self.complaint, self.ruling])
        # The cover letter, the complaint itself, then exhibits.
        self.attachments = {
            self.complaint["id"]: [{"id": "11", "pageCount": "2"}, {"id": "12", "pageCount": "45"},
                                   {"id": "13", "pageCount": "900"}],
            self.ruling["id"]: [{"id": "21", "pageCount": "6"}],
        }


class TestTheEstimate(EstimateTestCase):
    def test_before_counting_each_document_is_costed_at_its_limit(self):
        est = estimate.build(self.store_, "337-1", self.cfg)
        self.assertFalse(est.complete)
        self.assertEqual([line.read_pages for line in est.lines], [60, 40])

    def test_counted_pages_from_edis_and_the_cost_they_come_to(self):
        client = FakeEdisClient(attachments=self.attachments)
        ids = [self.complaint["id"], self.ruling["id"]]
        self.assertEqual(pages.fill(self.store_, "337-1", ids, client=client), 2)
        with mock.patch.object(client, "list_attachments", side_effect=AssertionError("asked again")):
            self.assertEqual(pages.fill(self.store_, "337-1", ids, client=client), 0)

        est = estimate.build(self.store_, "337-1", self.cfg)
        self.assertTrue(est.complete)
        complaint, ruling = est.lines
        self.assertEqual((complaint.main_id, complaint.main_pages, complaint.total_pages, complaint.body),
                         ("12", 45, 947, "assumed"))
        self.assertEqual((complaint.read_pages, ruling.read_pages), (45, 6))

        cfg = self.cfg
        haiku, sonnet = cfg.rates(cfg.notes_model), cfg.rates(cfg.writer_model)
        notes = sum((cfg.prompt_tokens + n * cfg.tokens_per_page) * haiku.input + cfg.notes_output_tokens * haiku.output
                    for n in (45, 6)) / 1e6
        writing = ((cfg.writer_prompt_tokens + 2 * cfg.notes_output_tokens) * sonnet.input
                   + cfg.writer_output_tokens * sonnet.output) / 1e6
        self.assertAlmostEqual(est.notes_usd, notes)
        self.assertAlmostEqual(est.writer_usd, writing)
        self.assertEqual(est.writer_model, "claude-sonnet-5")
        self.assertIn("About $", preview.describe(est)[-2])

    def test_the_spend_so_far_is_read_from_its_own_cost_log(self):
        self.cfg.costs_csv.write_text("investigation_number,build_datetime,cost_usd\n337-TA-1,x,1.5\n", encoding="utf-8")
        est = estimate.build(self.store_, "337-1", self.cfg)
        self.assertEqual((est.spent_usd, est.budget_usd), (1.5, 20.0))


class TestThePrimer(DataDirTestCase):
    def test_a_draft_says_so_and_the_markdown_becomes_html(self):
        path = self.root / "primer.md"
        path.write_text("status: draft\nreviewed_by:\n\n## Stages\n\n1. **Complaint** filed\n2. *Institution*\n\n"
                        "A <b>paragraph</b>\ncontinued.\n\n- one\n", encoding="utf-8")
        loaded = primer.load(path)
        self.assertFalse(loaded.reviewed)
        self.assertEqual(
            loaded.html,
            "<h3>Stages</h3>\n<ol><li><strong>Complaint</strong> filed</li><li><em>Institution</em></li></ol>\n"
            "<p>A &lt;b&gt;paragraph&lt;/b&gt; continued.</p>\n<ul><li>one</li></ul>",
        )

    def test_a_reviewed_primer(self):
        path = self.root / "primer.md"
        path.write_text("status: reviewed\nreviewed_by: A. Lawyer\nreviewed_on: 2026-10-01\n\nText.\n", encoding="utf-8")
        loaded = primer.load(path)
        self.assertTrue(loaded.reviewed)
        self.assertEqual((loaded.reviewed_by, loaded.html), ("A. Lawyer", "<p>Text.</p>"))

    def test_the_shipped_primer_is_a_draft_until_reviewed(self):
        shipped = primer.load()
        self.assertIsNotNone(shipped)
        self.assertIn("<h3>How a case moves</h3>", shipped.html)


class TestTheSummaryTab(DataDirTestCase):
    def setUp(self):
        super().setUp()
        store = self.store()
        ingest.parse_snapshot(store, write_snapshot(self.ids_dir, [ids_row(), ids_row("337-1479", investigation_id=2)]),
                              log=self.quiet)
        store.put_documents("337-1478", [doc("Public Complaint and Exhibits", "Complaint")])
        self.store_ = store

    def page(self, number):
        return (self.site_dir / "investigations" / f"{number}.html").read_text(encoding="utf-8")

    def test_a_case_with_documents_has_the_tab_and_one_without_does_not(self):
        render_site(self.store_, data_dir=self.data_dir, site_dir=self.site_dir, log=self.quiet)
        with_docs = self.page("337-1478")
        self.assertIn('data-tab="summary"', with_docs)
        self.assertIn("Public Complaint and Exhibits", with_docs)
        self.assertIn('data-job="summary_estimate"', with_docs)  # not counted yet
        self.assertIn("How Section 337 investigations work", with_docs)
        self.assertIn("Draft: awaiting review", with_docs)
        self.assertNotIn('data-tab="summary"', self.page("337-1479"))


@contextmanager
def _session(client):
    yield client


class TestTheCommand(EstimateTestCase):
    def test_summary_plan_counts_pages_and_prints_the_plan(self):
        self.store_.investigations = {"337-1": {"investigation_number": "337-1", "status": "Active"}}
        self.store_.save_cases()
        self.store_.save_documents()
        client = FakeEdisClient(attachments=self.attachments)
        printed = []
        with mock.patch("datalayer.runner.edis_session", lambda token: _session(client)), \
                mock.patch("cli.load_token", return_value="t"), \
                mock.patch.object(summary_config, "load", return_value=self.cfg), \
                mock.patch("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a)))):
            code = cli.main(["--data-dir", str(self.data_dir), "summary-plan", "337-1"])
        self.assertEqual(code, 0)
        text = "\n".join(printed)
        self.assertIn("45 of the complaint's 45 pages", text)
        self.assertIn("About $", text)


if __name__ == "__main__":
    unittest.main()
