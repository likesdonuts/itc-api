"""The counsel process: reading who represents whom out of the filings.

Everything here is shaped on real EDIS metadata and Notice of Appearance
signature blocks (337-1478, 337-1514, 337-1518, 337-1520), since the parsing
is only as good as its fit to what firms actually file.
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest import mock

from support import DataDirTestCase, FakeEdisClient, ids_row, write_snapshot

from datalayer import cases, counsel, docs, ingest

PARTIES = [
    {"name": "Samsung Electronics Co., Ltd.", "role": "Complainant"},
    {"name": "Samsung Electronics America, Inc.", "role": "Complainant"},
    {"name": "Ouraring, Inc.", "role": "Respondent"},
    {"name": "Ōura Health Oy", "role": "Respondent"},
]

MAYER_BROWN_SIGNATURE = """Dated: January 15, 2026 Respectfully submitted,
/s/ Jasjit S. Vidwan
Jasjit S. Vidwan
James A. Fussell, III
So Ra Ko
Andrew Huntsinger
MAYER BROWN LLP
1999 K St. NW,
Washington, DC 20006
Tel: 202-263-5282

Robert G. Pluta
MAYER BROWN LLP
71 S. Wacker Drive
Chicago, IL 60606
Oura-ITC@mayerbrown.com

Counsel for Respondent Ouraring Inc. and Oura
Health Oy
CERTIFICATE OF SERVICE
/s/ Nicole Lynch
Nicole Lynch
"""


def doc(doc_id, *, type_="Motion", title="Motion", filed_by=None, on_behalf_of=None, firm=None, day="2026-02-01"):
    return {
        "id": str(doc_id),
        "document_type": type_,
        "title": title,
        "filed_by": filed_by,
        "on_behalf_of": on_behalf_of,
        "firm_organization": firm,
        "document_date": day,
    }


def omm(doc_id, **kw):
    kw.setdefault("filed_by", "D. Sean Trainor")
    kw.setdefault("on_behalf_of", "Samsung Electronics America, Inc. and Samsung Electronic Co., Ltd.")
    return doc(doc_id, firm="O'Melveny & Myers LLP", **kw)


def mayer(doc_id, **kw):
    kw.setdefault("filed_by", "Jasjit S. Vidwan")
    kw.setdefault("on_behalf_of", "Ouraring Inc. and Oura Health Oy")
    return doc(doc_id, firm="Mayer Brown LLP", **kw)


def build(documents, participants=PARTIES, docs_dir=None, read_pdf=None, root=None):
    case = {"stages": [{"lists": {"participants": participants}}]}
    return counsel.build_case_counsel(
        case,
        documents,
        docs_dir=docs_dir or (root / "none"),
        read_pdf=read_pdf or (lambda path: ""),
        log=lambda message: None,
    )["representations"]


def by_firm(reps):
    return {rep["firm"]: rep for rep in reps}


def names(rep):
    return [a["name"] for a in rep["attorneys"]]


class TestNames(unittest.TestCase):
    def test_a_party_list_splits_without_breaking_company_suffixes(self):
        self.assertEqual(
            counsel.split_parties(
                "Samsung Electronics Co., Ltd., Samsung Electronics America, Inc., Ouraring Inc., and Ōura Health Oy"
            ),
            ["Samsung Electronics Co., Ltd.", "Samsung Electronics America, Inc.", "Ouraring Inc.", "Ōura Health Oy"],
        )

    def test_parties_match_through_typos_accents_and_spacing(self):
        matched = counsel.match_parties(
            "Samsung Electronic Co., Ltd., Ouraring Inc. and Oura Health Oy", PARTIES
        )
        self.assertEqual(
            [p["name"] for p in matched],
            ["Samsung Electronics Co., Ltd.", "Ouraring, Inc.", "Ōura Health Oy"],
        )
        ergo = [{"name": "The Ergobaby Carrier, Inc", "role": "Complainant"}]
        self.assertEqual(counsel.match_parties("The Ergo Baby Carrier, Inc.", ergo), ergo)

    def test_one_company_does_not_pass_for_its_sister(self):
        matched = counsel.match_parties("Samsung Electronics America, Inc.", PARTIES)
        self.assertEqual([p["name"] for p in matched], ["Samsung Electronics America, Inc."])

    def test_firm_spellings_agree(self):
        self.assertEqual(
            counsel.firm_key("Finnegan, Henderson, Farabow, Garrett & Dunner, L.L.P."),
            counsel.firm_key("Finnegan Henderson Farabow Garrett and Dunner LLP"),
        )
        self.assertEqual(
            counsel.firm_key("Fabricant Rubino Lambrianakos LLP"),
            counsel.firm_key("Fabricant, Rubino & Lambrianakos LLP"),
        )
        self.assertEqual(counsel.firm_key("O’Melveny & Myers"), counsel.firm_key("O'Melveny & Myers LLP"))


class TestTitles(unittest.TestCase):
    def test_a_notice_of_appearance_names_the_firm_and_the_lead(self):
        facts = counsel.parse_title(
            "Notice of Appearance of Mayer Brown LLP on Behalf of Ouraring Inc. and Oura Health Oy; "
            "Designation of Jasjit S. Vidwan as Lead Counsel"
        )
        self.assertEqual(facts.firms, ["Mayer Brown LLP"])
        self.assertEqual(facts.lead, "Jasjit S. Vidwan")

    def test_two_firms_on_one_notice_are_split_but_one_firm_with_and_is_not(self):
        facts = counsel.parse_title(
            "Notice of Appearance of Morrison & Foerster LLP and Goldman Ismail Tomaselli Brennan & Baum LLP "
            "on Behalf of Apple Inc.; Designation of Mary Prendergast as Lead Counsel"
        )
        self.assertEqual(
            facts.firms, ["Morrison & Foerster LLP", "Goldman Ismail Tomaselli Brennan & Baum LLP"]
        )
        facts = counsel.parse_title(
            "Notice of Appearance of Wilmer Cutler Pickering Hale and Dorr LLP on Behalf of SkylineDx USA, Inc."
        )
        self.assertEqual(facts.firms, ["Wilmer Cutler Pickering Hale and Dorr LLP"])

    def test_supplemental_notices_and_withdrawals(self):
        facts = counsel.parse_title(
            "Supplemental Notice of Appearance; Additional Attorneys from Leydig, Voit & Mayer, Ltd. "
            "on Behalf of Chervon North America, Inc."
        )
        self.assertEqual(facts.firms, ["Leydig, Voit & Mayer, Ltd."])
        facts = counsel.parse_title(
            "Notice of Withdrawal of Appearance of Andrew Huntsinger from Mayer Brown LLP on Behalf of Ouraring Inc."
        )
        self.assertEqual(facts.firms, ["Mayer Brown LLP"])
        self.assertEqual(facts.withdrawn, ["Andrew Huntsinger"])


class TestSignatures(unittest.TestCase):
    def test_names_are_told_from_firms_streets_and_phones(self):
        for line in ("Jasjit S. Vidwan", "James A. Fussell, III", "Bas de Blank", "Séké Godo", "So Ra Ko"):
            self.assertTrue(counsel.is_person_name(line), line)
        for line in (
            "MAYER BROWN LLP", "71 S. Wacker Drive", "Washington, DC 20006", "Tel: 202-263-5282",
            "Oura-ITC@mayerbrown.com", "Respectfully Submitted,", "Two Embarcadero Center",
            "Counsel for Respondents",
        ):
            self.assertFalse(counsel.is_person_name(line), line)

    def test_the_first_signature_block_is_read_up_to_counsel_for(self):
        signature = counsel.parse_signature(MAYER_BROWN_SIGNATURE, known_firms=["Mayer Brown LLP"])
        self.assertEqual(
            [name for name, _ in signature.attorneys],
            ["Jasjit S. Vidwan", "James A. Fussell, III", "So Ra Ko", "Andrew Huntsinger", "Robert G. Pluta"],
        )
        self.assertEqual({firm for _, firm in signature.attorneys}, {"MAYER BROWN LLP"})
        self.assertEqual(signature.emails, ["Oura-ITC@mayerbrown.com"])

    def test_a_partner_signing_as_a_professional_corporation_is_a_person(self):
        signature = counsel.parse_signature(
            "/s/ Paul F. Brinkman\nPaul F. Brinkman, P.C.\nDiva Hollis\nKIRKLAND & ELLIS LLP\n"
            "1301 Pennsylvania Avenue\nCounsel for Respondents\n",
            known_firms=["Kirkland & Ellis LLP"],
        )
        self.assertEqual(
            signature.attorneys,
            [("Paul F. Brinkman", "KIRKLAND & ELLIS LLP"), ("Diva Hollis", "KIRKLAND & ELLIS LLP")],
        )

    def test_no_signature_line_means_nothing_is_guessed(self):
        self.assertEqual(counsel.parse_signature("Jasjit S. Vidwan\nMAYER BROWN LLP").attorneys, [])


class TestRepresentations(DataDirTestCase):
    def test_parties_sharing_counsel_are_one_representation(self):
        reps = by_firm(build([omm(1), mayer(2)], root=self.root))
        self.assertEqual(
            [p["name"] for p in reps["O'Melveny & Myers LLP"]["parties"]],
            ["Samsung Electronics Co., Ltd.", "Samsung Electronics America, Inc."],
        )
        self.assertEqual(reps["O'Melveny & Myers LLP"]["roles"], ["Complainant"])
        self.assertEqual(reps["Mayer Brown LLP"]["roles"], ["Respondent"])

    def test_a_joint_filing_does_not_make_one_side_counsel_for_the_other(self):
        joint = omm(
            3,
            title="Joint Stipulation",
            on_behalf_of="Samsung Electronics Co., Ltd., Samsung Electronics America, Inc., Ouraring Inc., and Oura Health Oy",
        )
        reps = by_firm(build([omm(1), mayer(2), joint], root=self.root))
        self.assertEqual(reps["O'Melveny & Myers LLP"]["roles"], ["Complainant"])
        self.assertEqual(len(reps["O'Melveny & Myers LLP"]["parties"]), 2)

    def test_a_notice_of_appearance_decides_who_a_firm_acts_for(self):
        notice = mayer(
            1,
            type_="Notice of Appearance",
            title="Notice of Appearance of Mayer Brown LLP on Behalf of Ouraring Inc.; "
            "Designation of Jasjit S. Vidwan as Lead Counsel",
            on_behalf_of="Ouraring Inc.",
        )
        stray = mayer(2, on_behalf_of="Oura Health Oy")
        rep = by_firm(build([notice, stray], root=self.root))["Mayer Brown LLP"]
        self.assertEqual([p["name"] for p in rep["parties"]], ["Ouraring, Inc."])
        self.assertTrue(rep["attorneys"][0]["lead"])
        self.assertEqual(rep["appearances"][0]["id"], "1")

    def test_one_party_can_have_two_firms_and_withdrawals_are_kept(self):
        documents = [
            mayer(1, filed_by="Andrew Huntsinger", day="2026-01-15"),
            doc(2, type_="Notice of Appearance", firm="ArentFox Schiff LLP", filed_by="Christopher S. Schultz",
                on_behalf_of="Ouraring, Inc. and Oura Health Oy",
                title="Supplemental Notice of Appearance; Additional Attorneys from ArentFox Schiff LLP on Behalf of Ouraring, Inc."),
            mayer(3, type_="Notice of Withdrawal of Appearance", day="2026-06-04",
                  title="Notice of Withdrawal of Appearance of Andrew Huntsinger from Mayer Brown LLP on Behalf of Ouraring Inc."),
        ]
        reps = by_firm(build(documents, root=self.root))
        self.assertEqual(set(reps), {"Mayer Brown LLP", "ArentFox Schiff LLP"})
        withdrawn = next(a for a in reps["Mayer Brown LLP"]["attorneys"] if a["name"] == "Andrew Huntsinger")
        self.assertEqual(withdrawn["withdrawn_on"], "2026-06-04")
        # Withdrawn attorneys sort after the ones still on the case.
        self.assertEqual(reps["Mayer Brown LLP"]["attorneys"][-1]["name"], "Andrew Huntsinger")

    def test_commission_filings_and_non_party_comments_are_not_counsel(self):
        documents = [
            omm(1),
            doc(2, type_="Order", filed_by="Doris Johnson Hines", on_behalf_of="Administrative Law Judge", firm="USITC"),
            doc(3, type_="Comments/Response to Comments", filed_by="A. Lobbyist",
                on_behalf_of="Some Trade Association", firm="Policy Shop LLC"),
        ]
        self.assertEqual([r["firm"] for r in build(documents, root=self.root)], ["O'Melveny & Myers LLP"])

    def test_a_misspelled_firm_or_filing_vendor_folds_into_the_real_one(self):
        notice = mayer(1, type_="Notice of Appearance",
                       title="Notice of Appearance of Mayer Brown LLP on Behalf of Ouraring Inc. and Oura Health Oy")
        vendor = doc(2, firm="Innovative Litigation Solutions LLC", filed_by="Jasjit S. Vidwan",
                     on_behalf_of="Ouraring Inc. and Oura Health Oy")
        self.assertEqual([r["firm"] for r in build([notice, vendor], root=self.root)], ["Mayer Brown LLP"])

    def test_the_whole_team_comes_from_the_notice_pdf(self):
        pdf_dir = self.root / "documents" / "337-1478"
        pdf_dir.mkdir(parents=True)
        (pdf_dir / "869128_2478325_2478325.pdf").write_bytes(b"%PDF")
        notice = mayer(869128, type_="Notice of Appearance",
                       title="Notice of Appearance of Mayer Brown LLP on Behalf of Ouraring Inc. and Oura Health Oy; "
                             "Designation of Jasjit S. Vidwan as Lead Counsel")
        rep = build([notice], docs_dir=pdf_dir, read_pdf=lambda path: MAYER_BROWN_SIGNATURE)[0]

        self.assertEqual(
            names(rep),
            ["Jasjit S. Vidwan", "James A. Fussell, III", "So Ra Ko", "Andrew Huntsinger", "Robert G. Pluta"],
        )
        self.assertEqual(rep["emails"], ["Oura-ITC@mayerbrown.com"])
        self.assertEqual(rep["appearances"][0]["files"], ["869128_2478325_2478325.pdf"])

    def test_co_counsel_on_the_same_notice_acts_for_the_same_parties(self):
        pdf_dir = self.root / "documents" / "337-1520"
        pdf_dir.mkdir(parents=True)
        (pdf_dir / "893668_1_1.pdf").write_bytes(b"%PDF")
        apple = [{"name": "Apple Inc.", "role": "Respondent"}]
        notice = doc(893668, type_="Notice of Appearance", firm="Morrison & Foerster LLP",
                     filed_by="Mary Prendergast", on_behalf_of="Apple Inc.",
                     title="Notice of Appearance of Morrison & Foerster LLP and Goldman Ismail Tomaselli Brennan & Baum LLP "
                           "on Behalf of Apple Inc.; Designation of Mary Prendergast as Lead Counsel")
        signature = (
            "/s/ Mary Prendergast\nMary Prendergast\nMORRISON & FOERSTER LLP\n2100 L Street\n"
            "Alan E. Littmann\nGOLDMAN ISMAIL TOMASELLI BRENNAN & BAUM LLP\n200 South Wacker Drive\n"
            "Counsel for Respondent Apple Inc.\n"
        )
        reps = by_firm(build([notice], participants=apple, docs_dir=pdf_dir, read_pdf=lambda p: signature))
        goldman = reps["Goldman Ismail Tomaselli Brennan & Baum LLP"]
        self.assertEqual(names(goldman), ["Alan E. Littmann"])
        self.assertEqual(goldman["parties"], apple)
        self.assertEqual(names(reps["Morrison & Foerster LLP"]), ["Mary Prendergast"])


@contextmanager
def _session(client):
    yield client


class TestTheProcess(DataDirTestCase):
    def seeded(self):
        store = self.store()
        rows = [ids_row(complainants=("Acme Inc.", "Acme Holdings LLC"), respondents=("Globex Corp.",))]
        ingest.parse_snapshot(store, write_snapshot(self.ids_dir, rows), log=self.quiet)
        return store

    def test_it_writes_counsel_json_and_nothing_else(self):
        store = self.seeded()
        store.put_documents("337-1478", [
            doc(1, filed_by="A. Lawyer", on_behalf_of="Acme Inc. and Acme Holdings LLC", firm="Firm LLP"),
        ])
        store.save_documents()
        before = {
            name: (self.data_dir / name).read_text(encoding="utf-8")
            for name in ("investigations.json", "documents_index.json")
        }

        counsel.run(store, log=self.quiet)

        written = self.read_json("counsel.json")["337-1478"]["representations"][0]
        self.assertEqual(written["firm"], "Firm LLP")
        self.assertEqual([p["name"] for p in written["parties"]], ["Acme Inc.", "Acme Holdings LLC"])
        for name, text in before.items():
            self.assertEqual(text, (self.data_dir / name).read_text(encoding="utf-8"), name)

    def test_ids_participant_ids_are_kept_for_matching_across_cases(self):
        row = ids_row()
        row["Participants"][0]["Participant"]["ID"] = 6153
        built = cases.build_cases([row])["337-1478"]
        self.assertEqual(built["stages"][0]["lists"]["participants"][0]["participant_id"], 6153)


class TestAppearanceDownloads(DataDirTestCase):
    DOCUMENTS = [
        {"id": "1", "documentType": "Notice of Appearance", "documentTitle": "Notice of Appearance of Firm LLP",
         "securityLevel": "Public", "filedBy": "A. Lawyer", "onBehalfOf": "Acme Inc.", "firmOrganization": "Firm LLP"},
        {"id": "2", "documentType": "Motion", "documentTitle": "Motion to Compel",
         "securityLevel": "Public", "filedBy": "A. Lawyer", "onBehalfOf": "Acme Inc.", "firmOrganization": "Firm LLP"},
    ]

    def fetch(self, store, client, **kwargs):
        with mock.patch.object(docs, "edis_session", lambda token: _session(client)):
            return docs.run(store, "t", ["337-1478"], log=self.quiet, **kwargs)

    def client(self):
        return FakeEdisClient(
            documents={"337-1478": self.DOCUMENTS},
            attachments={"1": [{"id": "10", "originalFileName": "noa.pdf"}],
                         "2": [{"id": "20", "originalFileName": "motion.pdf"}]},
        )

    def test_only_notice_of_appearance_pdfs_are_downloaded(self):
        store = self.store()
        client = self.client()
        self.fetch(store, client, only_types=docs.APPEARANCE_TYPES)
        self.assertEqual(client.downloads, [("1", "10")])
        self.assertEqual(len(store.documents["337-1478"]), 2)

    def test_a_metadata_refresh_keeps_links_to_pdfs_already_on_disk(self):
        store = self.store()
        self.fetch(store, self.client())
        self.fetch(store, self.client(), download=False)
        kept = {d["id"]: [a["label"] for a in d["attachments"]] for d in store.documents["337-1478"]}
        self.assertEqual(kept, {"1": ["noa.pdf"], "2": ["motion.pdf"]})


if __name__ == "__main__":
    unittest.main()
