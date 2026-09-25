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


APPLE_NOTICE = (
    "NOTICE OF LIMITED APPEARANCE FOR NON-PARTY APPLE INC. Please note the limited appearance of the "
    "following attorneys as counsel of record for non- party Apple Inc. ( “Apple”) for the limited "
    "purposes of responding to the subpoena duces tecum and subpoena ad testificandum served on Apple by "
    "Complainants InterDigital, Inc. and InterDigital VC Holdings, Inc. in the above-captioned investigation. "
    "Stephen R. Smith Brittany Cazakoff COOLEY LLP\n"
    "/s/ Stephen R. Smith\nStephen R. Smith\nBrittany Cazakoff\nCOOLEY LLP\n"
    "Counsel for Non-Party Apple Inc.\n"
)
ABC_NOTICE = (
    "Notice is hereby given, pursuant to Commission Rules 210.11 and 210.32, of the appearance of the "
    "following as counsel for non-party ABC Coke (“ABC Coke”) for the limited purpose of objecting to, "
    "moving to limit or quash, and/or otherwise responding to the subpoena duces tecum and ad testificandum "
    "(collectively, “Subpoenas”) served on September 11, 2026, by Respondents Italiana Coke S.r.l., "
    "Terminal Alti Fondali Savona S.r.l., and AMEX Coal Sp. z o.o. (collectively, “Respondents”) in the "
    "above-captioned investigation: Ryan W. O'Donnell VOLPE KOENIG"
)
INTERDIGITAL = [
    {"name": "InterDigital, Inc.", "role": "Complainant"},
    {"name": "InterDigital VC Holdings, Inc.", "role": "Complainant"},
    {"name": "Amazon.com, Inc.", "role": "Respondent"},
]


class TestNonParties(DataDirTestCase):
    def test_only_a_non_partys_own_filings_name_it(self):
        own = [
            doc(1, type_="Notice of Appearance", on_behalf_of="Apple Inc.", firm="Cooley LLP",
                title="Notice of Limited Appearance of Cooley LLP on Behalf of Non-Party Apple, Inc.; "
                      "Designation of Stephen R. Smith as Lead Counsel"),
            doc(2, on_behalf_of="Hickman, Williams & Company", firm="FBT Gibbons LLP",
                title="Non-Party Hickman, Williams & Company's Unopposed Motion for Extension of Time"),
        ]
        self.assertEqual(counsel.non_party_names(own[0]), ["Apple Inc."])
        # A comma inside the name is not a list.
        self.assertEqual(counsel.non_party_names(own[1]), ["Hickman, Williams & Company"])

        mentions = [
            doc(3, type_="Order", on_behalf_of="Administrative Law Judge", firm="USITC",
                title="Granting Non-Party ABC Coke's Unopposed Motion for Extension of Time"),
            doc(4, on_behalf_of="InterDigital, Inc.", firm="McKool Smith P.C.",
                title="Complainants' Unopposed Motion for Entry of Supplemental Protective Order Governing "
                      "Discovery of Non-Party Confidential Material"),
            doc(5, on_behalf_of="Serendia, LLC", firm="Latham & Watkins LLP",
                title="Complainant Serendia LLC's Response to Non-Party Merle Richman's Motion to Quash"),
        ]
        for mention in mentions:
            self.assertEqual(counsel.non_party_names(mention), [], mention["title"])

    def test_the_purpose_of_a_limited_appearance_is_read_from_the_notice(self):
        apple = counsel.limited_purpose(APPLE_NOTICE)
        self.assertTrue(apple["purpose"].startswith("responding to the subpoena duces tecum"))
        self.assertTrue(apple["purpose"].endswith("InterDigital VC Holdings, Inc."))
        self.assertEqual(apple["served_by"], "Complainants")
        self.assertNotIn("served_on", apple)

        abc = counsel.limited_purpose(ABC_NOTICE)
        self.assertEqual(abc["served_by"], "Respondents")
        self.assertEqual(abc["served_on"], "2026-09-11")
        self.assertTrue(abc["subpoena"])
        self.assertIsNone(counsel.limited_purpose("Notice of Appearance of the following counsel"))

    def test_a_non_party_gets_its_counsel_and_its_reason(self):
        pdf_dir = self.root / "documents" / "337-1481"
        pdf_dir.mkdir(parents=True)
        (pdf_dir / "872720_2499478_2499478.pdf").write_bytes(b"%PDF")
        notice = doc(872720, type_="Notice of Appearance", on_behalf_of="Apple Inc.", firm="Cooley LLP",
                     filed_by="Stephen R. Smith",
                     title="Notice of Limited Appearance of Cooley LLP on Behalf of Non-Party Apple, Inc.; "
                           "Designation of Stephen R. Smith as Lead Counsel")
        case = {"stages": [{"lists": {"participants": INTERDIGITAL}}]}
        built = counsel.build_case_counsel(
            case, [notice], docs_dir=pdf_dir, read_pdf=lambda p: APPLE_NOTICE, log=lambda m: None
        )

        apple = built["non_parties"][0]
        self.assertEqual(apple["name"], "Apple Inc.")
        self.assertEqual(apple["role"], "Non-Party")
        self.assertEqual(apple["summary"], "Responding to subpoenas served by Complainants")
        self.assertEqual(apple["notice"]["files"], ["872720_2499478_2499478.pdf"])
        cooley = built["representations"][0]
        self.assertEqual(cooley["roles"], ["Non-Party"])
        self.assertEqual([a["name"] for a in cooley["attorneys"]], ["Stephen R. Smith", "Brittany Cazakoff"])

    def test_without_a_notice_the_reason_comes_from_its_own_filings(self):
        motion = doc(9, on_behalf_of="Merle Richman", firm="FisherBroyles, LLP", filed_by="Paul C. Goulet",
                     title="Non-Party Merle Richman's Motion to Quash or Otherwise Limit BTL's Subpoena")
        comment = doc(10, type_="Comments/Response to Comments", on_behalf_of="Google LLC",
                      firm="Wolf, Greenfield & Sacks, PC", filed_by="Gregory F. Corbett",
                      title="Non-Party Google LLC's Response to the Commission's Solicitation of Comments "
                            "Relating to the Public Interest")
        built = counsel.build_case_counsel(
            {"stages": []}, [motion, comment], docs_dir=self.root / "none", log=lambda m: None
        )
        summaries = {p["name"]: p["summary"] for p in built["non_parties"]}
        self.assertEqual(summaries["Merle Richman"], "Moved to quash or limit a subpoena")
        self.assertEqual(summaries["Google LLC"], "Filed comments on the public interest")
        self.assertEqual(built["non_parties"][0]["filings"][0]["id"], "9")

    def test_a_name_ids_lists_as_a_party_stays_that_party(self):
        mislabelled = doc(11, on_behalf_of="Amazon.com, Inc.", firm="Firm LLP",
                          title="Non-Party Amazon.com, Inc.'s Motion")
        case = {"stages": [{"lists": {"participants": INTERDIGITAL}}]}
        built = counsel.build_case_counsel(case, [mislabelled], docs_dir=self.root / "none", log=lambda m: None)
        self.assertEqual(built["non_parties"], [])
        self.assertEqual(built["representations"][0]["roles"], ["Respondent"])


def _np_filing(doc_id, who, *, title=None, notice=False, day="2026-02-01"):
    """A filing by a non-party, as a notice of limited appearance or its own motion."""
    if notice:
        title = title or f"Notice of Limited Appearance of Firm LLP on Behalf of Non-Party {who}"
        return doc(doc_id, type_="Notice of Appearance", on_behalf_of=who, firm="Firm LLP", title=title, day=day)
    return doc(doc_id, on_behalf_of=who, firm="Firm LLP", title=title or f"Non-Party {who}'s Motion", day=day)


class TestNonPartyNames(DataDirTestCase):
    def test_a_joint_filing_is_split_when_every_piece_is_a_whole_name(self):
        cases_ = {
            "MediaTek Inc. and MediaTek USA Inc.": ["MediaTek Inc.", "MediaTek USA Inc."],
            "Samsung Electronics America, Inc. and Samsung Austin Semiconductor, LLC":
                ["Samsung Electronics America, Inc.", "Samsung Austin Semiconductor, LLC"],
            "Google LLC, Mobashar Yazdani, and Shuai Jiang": ["Google LLC", "Mobashar Yazdani", "Shuai Jiang"],
            # Not a list: a piece of it is not a name on its own.
            "Alliance of U.S. Startups and Inventors for Jobs": ["Alliance of U.S. Startups and Inventors for Jobs"],
            "Hickman, Williams & Company": ["Hickman, Williams & Company"],
        }
        for who, expected in cases_.items():
            self.assertEqual(counsel.non_party_names(_np_filing(1, who, notice=True)), expected, who)

    def test_harvards_corporate_name_is_kept_whole(self):
        filing = _np_filing(
            1, "President and Fellows of Harvard College and Eric Mazur",
            title="Notice of Limited Appearance of Troutman Pepper Hamilton Sanders LLP on Behalf of "
                  "Non-Parties President and Fellows of Harvard College and Eric Mazur",
            notice=True,
        )
        self.assertEqual(
            counsel.non_party_names(filing), ["President and Fellows of Harvard College", "Eric Mazur"]
        )
        self.assertEqual(
            counsel.non_party_names(_np_filing(2, "President and Fellows of Harvard College")),
            ["President and Fellows of Harvard College"],
        )

    def test_labels_come_off_and_redacted_names_are_skipped(self):
        self.assertEqual(
            counsel.non_party_names(_np_filing(1, "Non-Party Rakuten Symphony USA LLC", notice=True)),
            ["Rakuten Symphony USA LLC"],
        )
        self.assertEqual(counsel.non_party_names(_np_filing(2, "Dr. William Wilcox")), ["William Wilcox"])
        self.assertEqual(counsel.non_party_names(_np_filing(3, "[ ]", title="Non-Party [ ]'s Opposition")), [])

    def test_one_company_under_two_corporate_forms_is_one_non_party(self):
        filings = [
            _np_filing(1, "Google Inc.", day="2026-01-01"),
            _np_filing(2, "Google LLC", day="2026-01-02"),
            _np_filing(3, "Google LLC", day="2026-01-03"),
        ]
        built = counsel.build_case_counsel({"stages": []}, filings, docs_dir=self.root / "none", log=lambda m: None)
        self.assertEqual([p["name"] for p in built["non_parties"]], ["Google LLC"])
        self.assertEqual(len(built["non_parties"][0]["filings"]), 3)
        # Their representation names the same spelling.
        self.assertEqual([p["name"] for p in built["representations"][0]["parties"]], ["Google LLC"])

    def test_a_different_company_with_a_similar_name_stays_apart(self):
        filings = [_np_filing(1, "MediaTek Inc."), _np_filing(2, "MediaTek USA Inc.")]
        built = counsel.build_case_counsel({"stages": []}, filings, docs_dir=self.root / "none", log=lambda m: None)
        self.assertEqual([p["name"] for p in built["non_parties"]], ["MediaTek Inc.", "MediaTek USA Inc."])


MEDIATEK_NOTICE = (
    "Notice is hereby given of the limited appearance of the undersigned as counsel for non- party "
    "MediaTek USA Inc. (“MediaTek USA”) to address issues related to a “Subpoena Duces "
    "Tecum and Ad Testificandum to MediaTek USA Inc.,” dated May 23, 2025, received by MediaTek USA "
    "on May 27, 2025, and issued on behalf of Respondents in connection with the above-captioned "
    "investigation (the “Subpoena”). This limited appearance does not waive any rights of MediaTek USA."
)
TERNS_NOTICE = (
    "Notice is hereby given of the limited appearance of the undersigned counsel for Non- Party Terns "
    "Pharmaceuticals, Inc. who has been served with a Subpoena Duces Tecum and Ad Testificandum in the "
    "above-captioned Investigation. Pursuant to Commission Rule 210.7(b), Stephen Smith is designated."
)
INTERVENOR_NOTICE = (
    "Pursuant to 19 C.F.R. 201.11, please note the appearance of the following attorneys as counsel for "
    "Proposed Intervenor Xenogenic Development LLC in the above-captioned investigation: J.C. Rozendaal"
)


class TestNonPartyReasons(DataDirTestCase):
    def test_subpoena_notices_without_the_limited_purposes_formula(self):
        mediatek = counsel.limited_purpose(MEDIATEK_NOTICE)
        self.assertTrue(mediatek["subpoena"])
        self.assertEqual(mediatek["served_by"], "Respondents")
        self.assertTrue(mediatek["purpose"].startswith("a “Subpoena Duces Tecum"))

        terns = counsel.limited_purpose(TERNS_NOTICE)
        self.assertTrue(terns["subpoena"])
        self.assertTrue(terns["purpose"].startswith("a Subpoena Duces Tecum"))

    def test_a_proposed_intervenor(self):
        reason = counsel.limited_purpose(INTERVENOR_NOTICE)
        self.assertEqual(reason, {"purpose": "proposed intervenor", "intervenor": True})
        self.assertEqual(counsel._non_party_summary(reason, []), "Proposed intervenor")

    def test_the_summary_says_where_the_reason_stands(self):
        not_downloaded = {"notice": {"id": "1", "files": []}}
        read_no_reason = {"notice": {"id": "1", "files": ["1_2_3.pdf"]}}
        self.assertEqual(counsel.non_party_status(not_downloaded), "not_downloaded")
        self.assertIn("not been downloaded", counsel._non_party_summary(not_downloaded, []))
        self.assertEqual(counsel.non_party_status(read_no_reason), "no_reason")
        self.assertEqual(
            counsel._non_party_summary(read_no_reason, []), "Appeared through counsel; the notice does not say why"
        )
        self.assertEqual(counsel.non_party_status({}), "no_notice")
        self.assertEqual(counsel._non_party_summary({}, []), "Appears through its own filings")

    def test_a_scanned_notice_is_ocrd_once_and_cached(self):
        pdf = self.root / "1_2_3.pdf"
        pdf.write_bytes(b"%PDF")
        cache = self.root / "cache"
        from datalayer.claims import ocr

        with mock.patch.object(counsel, "pdf_text", return_value=""), mock.patch.object(
            ocr, "available", return_value=True
        ), mock.patch.object(ocr, "pdf_text", return_value=TERNS_NOTICE) as scanned:
            read = counsel.notice_reader(cache, log=lambda m: None)
            self.assertEqual(read(pdf), TERNS_NOTICE)
            self.assertEqual(read(pdf), TERNS_NOTICE)
        scanned.assert_called_once()
        self.assertTrue((cache / "1_2_3.pdf.ocr.txt").exists())

    def test_a_notice_with_a_text_layer_is_not_ocrd(self):
        from datalayer.claims import ocr

        with mock.patch.object(counsel, "pdf_text", return_value=TERNS_NOTICE), mock.patch.object(
            ocr, "pdf_text", side_effect=AssertionError("OCR'd")
        ):
            read = counsel.notice_reader(self.root / "cache", log=lambda m: None)
            self.assertEqual(read(self.root / "x.pdf"), TERNS_NOTICE)


class TestTheLog(DataDirTestCase):
    def run_counsel(self, **kwargs):
        store = self.store()
        store.put_documents("337-1478", [
            _np_filing(1, "Apple Inc.", notice=True),
            _np_filing(2, "Merle Richman"),
        ])
        store.save_documents()
        lines: list[str] = []
        counsel.run(store, log=lines.append, **kwargs)
        return lines

    def test_non_parties_are_one_line_with_what_to_do_next(self):
        lines = self.run_counsel()
        self.assertFalse(any("non-party Apple" in line for line in lines))
        summary = next(line for line in lines if line.startswith("Non-parties:"))
        self.assertIn("2 in 1 case(s)", summary)
        self.assertIn("1 whose notice PDF is not downloaded yet", summary)
        self.assertIn("1 that filed no notice", summary)
        self.assertTrue(any("Run daily sync" in line for line in lines))
        self.assertTrue(any("--verbose" in line for line in lines))

    def test_verbose_lists_each_one(self):
        lines = self.run_counsel(verbose=True)
        self.assertIn("  337-1478: non-party Apple Inc. (notice PDF not downloaded yet)", lines)
        self.assertIn(
            "  337-1478: non-party Merle Richman (no notice filed; described from its own filings)", lines
        )


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

    def test_a_refresh_relinks_pdfs_whose_links_were_lost(self):
        store = self.store()
        self.fetch(store, self.client())
        # An older refresh dropped the links; the files stayed on disk.
        for document in store.documents["337-1478"]:
            document["attachments"] = []
        self.fetch(store, self.client(), download=False)
        relinked = {d["id"]: [a["href"] for a in d["attachments"]] for d in store.documents["337-1478"]}
        self.assertEqual(
            relinked,
            {
                "1": ["../../data/documents/337-1478/1_10_noa.pdf"],
                "2": ["../../data/documents/337-1478/2_20_motion.pdf"],
            },
        )


if __name__ == "__main__":
    unittest.main()
