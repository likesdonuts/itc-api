"""The case summary, phase 2: notes on the complaint, the notice and the
answers, checked against their pages; the summary written from the notes,
checked against its citations; the budget; and partial downloads. The model
and EDIS are fakes."""

from __future__ import annotations

import dataclasses
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import DataDirTestCase, FakeEdisClient

from datalayer import docs
from datalayer.summary import build, config as summary_config, facts, fetch, notes, text, write
from datalayer.summary.select import select

COMPLAINT_PAGE = ("Complainant Acme Inc. alleges that Globex Corp. imports into the United States "
                  "wireless earbuds that infringe claims 1-5 of U.S. Patent No. 10,945,648.")
ANSWER_PAGE = "Globex denies infringement and asserts that the '648 patent is invalid as obvious over Smith."


def response(tool_input, *, tokens_in=10_000, tokens_out=800, stop="tool_use"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input=tool_input)],
        usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0),
        stop_reason=stop,
    )


class FakeModel:
    """Answers record_notes from the page it was shown, and write_summary
    citing the first notes."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        user = kwargs["messages"][0]["content"]
        if kwargs["tool_choice"]["name"] == notes.TOOL_NAME:
            if "response (answer)" in user:
                return response({"overview": "Globex answers.", "points": [
                    {"topic": "invalidity", "point": "Globex says the patent is obvious.", "page": 1,
                     "quote": "asserts that the '648 patent is invalid as obvious over Smith"},
                    {"topic": "non_infringement", "point": "Made up.", "page": 1, "quote": "a quote that is on no page at all"},
                ]})
            if "complaint:" in user or "is a complaint" in user:
                return response({"overview": "Acme's complaint.", "points": [
                    {"topic": "infringement", "point": "Acme alleges Globex's earbuds infringe the '648 patent.",
                     "page": 2, "quote": "wireless earbuds that infringe claims 1-5 of U.S. Patent No. 10,945,648"},
                ]})
            return response({"overview": "Notice.", "points": []})
        return response({
            "headline": "Acme accuses Globex of importing infringing earbuds.",
            "about": [{"text": "Acme and Globex make earbuds.", "cites": ["n1"]},
                      {"text": "An uncited paragraph.", "cites": []}],
            "allegations": [{"text": "Acme alleges infringement.", "cites": ["n1", "n99"]}],
            "answers": [{"group": "A1", "paragraph": {"text": "Globex says the patent is obvious.", "cites": ["n2"]}}],
        }, tokens_in=3000, tokens_out=900)


class TestPages(unittest.TestCase):
    def test_the_first_and_last_pages(self):
        self.assertEqual(text.wanted_pages(10, 3, 2), [1, 2, 3, 9, 10])
        self.assertEqual(text.wanted_pages(4, 3, 2), [1, 2, 3, 4])
        self.assertEqual(text.wanted_pages(5, 60, 0), [1, 2, 3, 4, 5])

    def test_the_model_sees_page_numbers_and_the_gaps(self):
        prompt = text.as_prompt({1: "one", 2: "two", 9: "nine"})
        self.assertIn('<page n="2">\ntwo\n</page>', prompt)
        self.assertIn("[pages 3-8 not included]", prompt)


class TestNotesChecks(unittest.TestCase):
    def test_a_point_is_kept_only_if_its_quote_is_on_its_page_or_next_to_it(self):
        texts = {1: "Intro page.", 2: COMPLAINT_PAGE, 3: "Other page."}
        points = [
            {"topic": "infringement", "point": "ok", "page": 2, "quote": "wireless earbuds that infringe claims 1-5"},
            {"topic": "infringement", "point": "off by one", "page": 3, "quote": "imports into the United States wireless"},
            {"topic": "infringement", "point": "made up", "page": 2, "quote": "Globex has admitted everything in writing"},
            {"topic": "infringement", "point": "short", "page": 2, "quote": "earbuds"},
            {"topic": "gossip", "point": "no such topic", "page": 2, "quote": "wireless earbuds that infringe claims"},
            {"topic": "infringement", "point": "OCR slip", "page": 2, "quote": "wireless earbuds that infrlnge claims 1-5 of U.S."},
        ]
        kept, rejected = notes.validate(points, texts, "complaint")
        self.assertEqual([(p["point"], p["page"]) for p in kept], [("ok", 2), ("off by one", 2), ("OCR slip", 2)])
        self.assertEqual([p["rejected"] for p in rejected], [
            "quote not found in the filing", "quote too short to check", "unknown topic"])


class TestWriterChecks(unittest.TestCase):
    def test_unknown_citations_and_uncited_paragraphs_are_dropped(self):
        numbered = {"n1": {}, "n2": {}}
        groups = {"A1": {"who": "Globex Corp.", "doc_id": "7"}}
        answer = {"headline": "H.", "about": [{"text": "Cited.", "cites": ["n1", "n9"]}, {"text": "Bare.", "cites": []}],
                  "allegations": [], "answers": [{"group": "A1", "paragraph": {"text": "Denies.", "cites": ["n2"]}},
                                                 {"group": "A7", "paragraph": {"text": "Who?", "cites": ["n2"]}},
                                                 {"group": "A1", "paragraph": {"text": "Also.", "cites": ["n1"]}}]}
        summary, warnings = write.check(answer, numbered, groups)
        self.assertEqual(summary["about"], [{"text": "Cited.", "cites": ["n1"]}])
        [globex] = summary["answers"]
        self.assertEqual((globex["who"], [p["text"] for p in globex["paragraphs"]]), ("Globex Corp.", ["Denies.", "Also."]))
        self.assertEqual(len(warnings), 3)


class BuildTestCase(DataDirTestCase):
    def setUp(self):
        super().setUp()
        self.cfg = dataclasses.replace(summary_config.load(), costs_csv=self.data_dir / "summary_costs.csv")
        store = self.store()
        store.investigations = {"337-1": {"investigation_number": "337-TA-1", "title": "Certain Earbuds",
                                          "status": "Active"}}
        self.complaint = {"id": "100", "title": "Public Complaint and Exhibits", "document_type": "Complaint",
                          "document_date": "2026-01-02", "security_level": "Public", "attachments": []}
        self.answer = {"id": "200", "title": "Response of Globex Corp. to the Complaint",
                       "document_type": "Answer to Complaint", "document_date": "2026-02-02",
                       "security_level": "Public", "on_behalf_of": "Globex Corp.", "firm_organization": "Firm LLP",
                       "attachments": []}
        store.put_documents("337-1", [self.complaint, self.answer])
        store.save_cases()
        store.save_documents()
        self.store_ = store
        # The complaint filing: a cover letter, the complaint, an exhibit.
        self.edis = FakeEdisClient(attachments={
            "100": [{"id": "11", "pageCount": "2"}, {"id": "12", "pageCount": "40"}, {"id": "13", "pageCount": "300"}],
            "200": [{"id": "21", "pageCount": "30"}],
        })
        self.pages = {"12": {1: "VERIFIED COMPLAINT UNDER SECTION 337", 2: COMPLAINT_PAGE},
                      "13": {1: "EXHIBIT 1"}, "11": {1: "Cover letter"}, "21": {1: ANSWER_PAGE}}

    def fake_pages(self, pdf, numbers, **kwargs):
        att = Path(pdf).stem.split("_")[1]
        return {n: t for n, t in self.pages.get(att, {}).items() if n in set(numbers)}

    def run_build(self, model=None, **kwargs):
        @contextmanager
        def session(token):
            yield self.edis

        with mock.patch("datalayer.runner.edis_session", session), \
                mock.patch.object(text, "pages", self.fake_pages):
            return build.run(self.store_, "337-1", token="t", config=self.cfg, model_client=model or FakeModel(),
                             log=lambda m: None, **kwargs)


class TestTheBuild(BuildTestCase):
    def test_notes_then_a_summary_whose_citations_all_trace_to_quotes(self):
        model = FakeModel()
        result = self.run_build(model)

        # Only the complaint itself was downloaded -- not its cover letter or exhibit.
        self.assertEqual(sorted(self.edis.downloads), [("100", "12"), ("200", "21")])
        self.assertEqual(result["sources"], ["100", "200"])
        self.assertEqual(result["summary"]["headline"], "Acme accuses Globex of importing infringing earbuds.")
        self.assertEqual(result["summary"]["about"], [{"text": "Acme and Globex make earbuds.", "cites": ["n1"]}])
        self.assertEqual(result["notes"]["n1"]["page"], 2)
        self.assertEqual(result["summary"]["answers"][0]["who"], "Globex Corp.")
        self.assertEqual(len(result["warnings"]), 2)  # the uncited paragraph, the unknown n99
        answer_notes = notes.cached(self.data_dir, "200", self.cfg.notes_version)
        self.assertEqual(len(answer_notes["rejected"]), 1)

        # Notes go to Haiku, the writing to Sonnet 5.
        self.assertEqual([c["model"] for c in model.calls],
                         ["claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001", "claude-sonnet-5"])
        self.assertGreater(result["cost_usd"], 0)
        self.assertIn("337-TA-1", self.cfg.costs_csv.read_text(encoding="utf-8"))

        # The complaint is marked only partly downloaded, so "Fetch documents" completes it.
        index = {d["id"]: d for d in self.store().documents["337-1"]}
        self.assertTrue(index["100"]["attachments_partial"])
        self.assertNotIn("attachments_partial", index["200"])
        self.assertEqual(build.state(self.store_, "337-1", result, self.cfg)["state"], "up_to_date")

    def test_the_tab_shows_the_summary_with_citations_that_open_the_page(self):
        from ui import templates

        result = self.run_build()
        html = templates._written_summary(result, self.store().documents["337-1"])
        self.assertIn("Acme accuses Globex of importing infringing earbuds.", html)
        self.assertIn('href="../../data/documents/337-1/100_12_12.pdf#page=2"', html)
        self.assertIn(">p. 2</a>", html)  # a paragraph citing only the complaint
        self.assertIn("wireless earbuds that infringe claims 1-5", html)  # the quote, on hover
        self.assertIn("<h4>Globex Corp.</h4>", html)
        button, _ = templates._summary_action(build.state(self.store_, "337-1", result, self.cfg), None)
        self.assertIn('data-current="1"', button)

    def test_a_second_run_pays_for_nothing_it_has_read(self):
        self.run_build()
        again = FakeModel()
        result = self.run_build(again)
        self.assertEqual(again.calls, [])
        self.assertEqual(result["cost_usd"], 0)

    def test_it_stops_at_the_budget_and_keeps_what_was_paid_for(self):
        self.cfg = dataclasses.replace(self.cfg, budget_usd=0.08)
        with self.assertRaises(build.BudgetReached) as stopped:
            self.run_build()
        self.assertIn("raise budget_usd in summary_config.json", str(stopped.exception))
        record = build.load(self.data_dir, "337-1")
        self.assertTrue(record["last_attempt"]["budget"])
        self.assertEqual(build.state(self.store_, "337-1", record, self.cfg)["state"], "budget")
        # The complaint's notes were paid for and are kept for next time.
        self.assertIsNotNone(notes.cached(self.data_dir, "100", self.cfg.notes_version))

    def test_a_new_answer_makes_the_summary_due_an_update(self):
        result = self.run_build()
        self.store_.documents["337-1"].append({**self.answer, "id": "300", "on_behalf_of": "Initech Ltd.",
                                                "firm_organization": "Other LLP"})
        state = build.state(self.store_, "337-1", result, self.cfg)
        self.assertEqual((state["state"], state["new"]), ("new_documents", 1))


class TestFacts(unittest.TestCase):
    def test_the_claims_analysis_findings_grouped_by_patent_with_checked_events_only(self):
        source = {"id": "900", "document_type": "ID/RD - Final on Violation", "files": ["900_1_1.pdf"]}
        analysis = {"events": [
            {"action": "found_infringed", "status": "ok", "patent": "10,945,648", "claims": [1, 2],
             "respondents": ["ALL"], "date": "2026-05-01", "source": source, "quote": "claims 1 and 2 are infringed"},
            {"action": "found_infringed", "status": "ok", "patent": "10,945,648", "claims": [3, 5],
             "respondents": ["ALL"], "date": "2026-05-01", "source": source, "quote": "q"},
            {"action": "found_invalid", "status": "needs_review", "patent": "10,945,648", "claims": [4],
             "respondents": ["ALL"], "date": "2026-05-01", "source": source, "quote": "q"},
            {"action": "asserted", "status": "ok", "patent": "10,945,648", "claims": [1], "source": source},
        ]}
        [fact] = facts.claims_facts(analysis)
        self.assertEqual(fact["text"], "claims 1-3, 5 of the '648 patent: found infringed, per the final ID")
        self.assertEqual(fact["doc_id"], "900")

    def test_title_facts_come_from_the_documents_noted_by_title(self):
        docs = [{"id": "7", "title": "Initial Determination Terminating Respondent Globex Based on a Settlement Agreement",
                 "document_type": "ID/RD - Other Than Final on Violation", "document_date": "2026-03-01",
                 "security_level": "Public"}]
        [fact] = facts.title_facts(select(docs))
        self.assertEqual((fact["label"], fact["doc_id"], fact["date"]), ("Termination", "7", "2026-03-01"))

    def test_the_writer_may_cite_facts_but_not_invent_them(self):
        known = write.number_facts([{"doc_id": "7", "label": "Termination", "date": "d", "title": "T"}],
                                   [{"doc_id": "9", "text": "claims 1-3: found infringed", "date": "d"}])
        answer = {"headline": "H", "about": [], "allegations": [], "answers": [], "rulings": [],
                  "decisions": [{"text": "Found infringed.", "cites": ["c1", "c9"]}],
                  "standing": [{"text": "Globex settled.", "cites": ["t1"]}]}
        summary, warnings = write.check(answer, {}, {}, known)
        self.assertEqual(summary["decisions"], [{"text": "Found infringed.", "cites": ["c1"]}])
        self.assertEqual(summary["standing"], [{"text": "Globex settled.", "cites": ["t1"]}])
        self.assertEqual(len(warnings), 1)


class TestDecisionsInTheBuild(BuildTestCase):
    def setUp(self):
        super().setUp()
        self.final_id = {"id": "300", "title": "Initial Determination on Violation of Section 337",
                         "document_type": "ID/RD - Final on Violation", "document_date": "2026-08-01",
                         "security_level": "Public", "attachments": []}
        self.settled = {"id": "400", "title": "Initial Determination Terminating Respondent Initech Based on a Settlement Agreement",
                        "document_type": "ID/RD - Other Than Final on Violation", "document_date": "2026-04-01",
                        "security_level": "Public", "attachments": []}
        self.store_.documents["337-1"] += [self.final_id, self.settled]
        self.store_.save_documents()
        self.edis.attachments["300"] = [{"id": "31", "pageCount": "120"}]
        self.pages["31"] = {1: "The ALJ finds a violation of Section 337 by Globex as to the '648 patent claims 1-5."}

    def test_the_final_id_is_read_and_the_writer_sees_the_title_and_claims_facts(self):
        from datalayer.claims import build as claims_build
        from datalayer.store import save_json

        save_json(claims_build.analysis_path(self.data_dir, "337-1"), {"built_at": "2026-09-01", "events": [
            {"action": "violation", "status": "ok", "patent": "unknown", "claims": [], "respondents": ["ALL"],
             "date": "2026-08-01", "source": {"id": "300", "document_type": "ID/RD - Final on Violation"},
             "quote": "a violation"}]})
        model = FakeModel()
        result = self.run_build(model)
        self.assertIn("300", result["sources"])
        final_call = model.calls[-2]["messages"][0]["content"]
        self.assertIn("final initial determination", final_call)
        writer = model.calls[-1]["messages"][0]["content"]
        self.assertIn("[t1] (2026-04-01, Termination)", writer)
        self.assertIn("[c1] (2026-08-01) violation of Section 337 found, per the final ID", writer)
        self.assertEqual(result["facts"]["t1"]["doc_id"], "400")
        self.assertEqual(build.state(self.store_, "337-1", result, self.cfg)["state"], "up_to_date")

        # A rebuilt claims analysis makes the summary due for rewriting, with nothing new to read.
        save_json(claims_build.analysis_path(self.data_dir, "337-1"), {"built_at": "2026-09-20", "events": []})
        self.assertEqual(build.state(self.store_, "337-1", result, self.cfg),
                         {"state": "new_documents", "new": 0, "built_at": result["built_at"]})

    def test_a_summary_written_by_phase_2_is_due_for_rewriting(self):
        result = self.run_build()
        result["phase"] = 2
        self.assertEqual(build.state(self.store_, "337-1", result, self.cfg)["state"], "new_documents")


class TestPartialDownloads(DataDirTestCase):
    def test_fetch_documents_completes_a_document_the_summary_downloaded_in_part(self):
        store = self.store()
        store.investigations = {"337-1": {"investigation_number": "337-1", "status": "Active"}}
        row = {"id": "100", "documentType": "Complaint", "documentTitle": "Public Complaint",
               "securityLevel": "Public", "documentDate": "2026/01/02 00:00:00"}
        path = store.docs_dir / "337-1" / "100_12_12.pdf"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"%PDF-1.4")
        store.put_documents("337-1", [{"id": "100", "attachments_partial": True,
                                       "attachments": [{"href": "../../data/documents/337-1/100_12_12.pdf", "label": "12.pdf"}]}])
        client = FakeEdisClient(documents={"337-1": [row]},
                                attachments={"100": [{"id": "11"}, {"id": "12"}]})

        @contextmanager
        def session(token):
            yield client

        with mock.patch.object(docs, "edis_session", session):
            docs.run(store, "t", ["337-1"], log=lambda m: None)
        self.assertEqual(client.downloads, [("100", "11")])
        [doc] = store.documents["337-1"]
        self.assertNotIn("attachments_partial", doc)
        self.assertEqual(len(doc["attachments"]), 2)


if __name__ == "__main__":
    unittest.main()
