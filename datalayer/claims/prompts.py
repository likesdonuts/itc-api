"""What the extraction model is told, and the tool it must answer with.

The system prompt is the same for every call, so it is cached; it has to
stay above Claude Haiku 4.5's 4,096-token caching minimum, which the worked
examples see to. Anything that varies per call -- the investigation, its
patents and respondents, the sentences -- goes in the user message after it.
"""

from __future__ import annotations

from typing import Any

from .candidates import Candidate

TOOL_NAME = "record_claim_events"

ACTIONS = (
    "asserted",
    "instituted",
    "added",
    "withdrawn",
    "terminated_settlement",
    "found_infringed",
    "found_not_infringed",
    "found_invalid",
    "found_not_invalid",
    "technical_prong",
    "not_reviewed",
    "mention_only",
)
SPEAKERS = ("tribunal_ruling", "tribunal_recital", "party_argument", "other")

TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Record every claim event in the numbered sentences. Call this exactly once, with all "
        "events from all sentences. Use an empty list if no sentence contains a claim event."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sentence_id": {"type": "string", "description": "The sentence's id, e.g. S4."},
                        "patent_number": {
                            "type": "string",
                            "description": "The patent exactly as written in the patent list, or unknown.",
                        },
                        "claims_verbatim": {
                            "type": "string",
                            "description": "The claim list exactly as the sentence writes it, e.g. 1-5, 8, and 12.",
                        },
                        "action": {"type": "string", "enum": list(ACTIONS)},
                        "speaker": {"type": "string", "enum": list(SPEAKERS)},
                        "respondents": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Respondent names exactly as in the respondent list, or [\"ALL\"].",
                        },
                        "quote": {
                            "type": "string",
                            "description": "The shortest exact substring of the sentence that supports the event.",
                        },
                    },
                    "required": [
                        "sentence_id",
                        "patent_number",
                        "claims_verbatim",
                        "action",
                        "speaker",
                        "respondents",
                        "quote",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["events"],
        "additionalProperties": False,
    },
}

SYSTEM = """You read sentences from filings in U.S. International Trade Commission Section 337 patent investigations and record what each sentence says happened to specific patent claims. Your output feeds a table that tracks how the set of asserted claims narrows over the life of an investigation: from the complaint, to institution, to the evidentiary hearing, to the administrative law judge's final initial determination (the "Final ID"), to the Commission's final determination, to appeal. Accuracy matters more than coverage: a wrong event is worse than a missing one, because a person relies on this table.

You will receive:
- the investigation number and title;
- the list of patents in the investigation, exactly as the Commission's records write them;
- the list of respondents, exactly as the Commission's records write them;
- the document the sentences come from: its type (for example "Order", "ID/RD - Final on Violation", "Notice", "Opinion, Commission", "Complaint"), its title and its date;
- up to 25 numbered sentences, each with the sentence before it as context.

Record every claim event with the record_claim_events tool, in one call. A sentence may contain no event, one event, or several.

## What an event is

An event is one thing that happened to one set of claims of one patent, as far as one set of respondents is concerned. Record a separate event when a sentence gives:
- different outcomes for different claims ("claims 2 and 3 are infringed, but claim 1 is not" is two events);
- outcomes for claims of different patents ("claims 1-3 of the '294 patent and claim 1 of the '508 patent" is two events, one per patent);
- different outcomes for different respondents or different accused products (a settlement with one respondent is an event for that respondent only).

Record nothing for a sentence that does not name specific claim numbers, even if it discusses the patents. Never invent claim numbers: copy the claim list exactly as the sentence writes it, including words like "through" and "and", and let the caller expand it.

## Fields

sentence_id: the id of the sentence the event comes from, exactly as given (S1, S2, ...).

patent_number: the patent the claims belong to, written exactly as it appears in the patent list you were given (for example "8,350,294"). Sentences usually name patents by their last three digits ("the '294 patent", "the ’294 patent") or in full ("U.S. Patent No. 8,350,294"); map either form to the list. If the sentence and its context do not say which patent, or the patent is not on the list, write unknown.

claims_verbatim: the claim list exactly as written in the sentence, without the word "claim" or "claims": "1-5, 8, and 12", "2 and 3", "1 through 4", "14". Keep the sentence's own dashes and punctuation.

action: what happened to the claims. Exactly one of:
- asserted: the complainant asserts or alleges infringement of the claims (typically in the complaint).
- instituted: the Commission institutes the investigation as to the claims (the notice of institution).
- added: claims are added to the investigation, for example by amending the complaint and notice of investigation.
- withdrawn: the claims are terminated from the investigation because the complainant withdrew them or moved to terminate them (partial termination, often "for good cause" or to narrow the case).
- terminated_settlement: the claims are terminated as to one or more respondents because of a settlement agreement, consent order, or license.
- found_infringed: the ALJ or the Commission finds the claims infringed, or finds a violation of section 337 based on the claims.
- found_not_infringed: the ALJ or the Commission finds the claims not infringed, or finds no violation based on the claims for lack of infringement.
- found_invalid: the claims are found invalid or unpatentable (anticipated, obvious, indefinite, ineligible, lacking written description or enablement), including on summary determination.
- found_not_invalid: the claims are found not shown to be invalid.
- technical_prong: a ruling on whether the complainant's domestic industry products practice the claims (the technical prong of the domestic industry requirement). Use this whether the ruling is that the prong is or is not satisfied.
- not_reviewed: the Commission determines not to review an initial determination concerning the claims, so that the initial determination becomes the Commission's determination.
- mention_only: the claims are only mentioned, summarized, listed, or argued about, with no outcome in this sentence.

speaker: who is speaking in the sentence. Exactly one of:
- tribunal_ruling: a ruling, finding, order, or determination that this document itself makes. Examples: "The ALJ finds that ...", "It is hereby ORDERED that ...", "The motion is GRANTED", "the Commission has determined not to review ...", "The Commission affirms the ID's finding ..." in the Commission's own opinion or notice.
- tribunal_recital: a recap of earlier procedural history or of an earlier ruling, including a Commission notice describing what the ALJ found, or an order summarizing earlier orders. Words like "previously", "on [date], the ALJ issued", "the ID found", "Order No. 12 granted" usually mark a recital.
- party_argument: the position, argument, allegation, or request of a party or of the Office of Unfair Import Investigations (OUII, "Staff"), even when it describes the ruling the party wants. "Complainant argues that claim 1 is infringed" is party_argument, not a finding.
- other: none of the above.

respondents: the respondents the event applies to, each written exactly as in the respondent list. Use ["ALL"] when the event applies to every respondent or the sentence does not single any out. Use specific names only when the sentence limits the event to them (a settlement with one respondent; infringement found only for one respondent's products).

quote: the shortest exact substring of the sentence that supports the event, copied character for character from the sentence (not from the context). It must include the claim numbers or the words that tie the outcome to them. It must be one unbroken stretch of the sentence: never join pieces from different parts of the sentence, and never use "..." to skip words. If the shortest unbroken stretch includes words about other claims or patents, keep them. Copy the sentence's own spelling, spacing and punctuation even where they look wrong (the text may come from a scanned page).

## Rules

1. Only the document's own rulings are tribunal_ruling. The same outcome described as history is tribunal_recital; described as a party's position, it is party_argument.
2. A motion granted in an order is a ruling of that order: "Complainant's motion to terminate the investigation as to claims 4-7 of the '335 patent is GRANTED" is withdrawn, tribunal_ruling. The motion's description of itself ("Complainant moves to terminate ...") is party_argument.
3. An order that terminates claims is an initial determination; it still counts as the ALJ's ruling (tribunal_ruling) in that order.
4. In a Commission notice, "the Commission has determined not to review the ID" about an order terminating or finding claims is not_reviewed, tribunal_ruling, for the claims that order concerned, when the sentence names them. When the Commission reviews and changes a finding, record the Commission's own finding (found_infringed, found_not_infringed, found_invalid) as tribunal_ruling.
5. "No violation" as to claims because they are not infringed is found_not_infringed; because they are invalid, found_invalid. "A violation of section 337 as to claims 2 and 3" is found_infringed.
6. Findings that the claims are infringed but invalid are two events: found_infringed and found_invalid.
7. A complaint's allegations are asserted with speaker party_argument. That is expected: in a complaint, asserted events count even though the complainant is the speaker.
8. When in doubt between an outcome and mention_only, choose mention_only. When in doubt about the speaker, prefer tribunal_recital over tribunal_ruling.

## Worked examples

Patents: 8,350,294; 8,404,508; 9,748,347; 10,312,335. Respondents: Innoscience, Inc; Innoscience America, Inc.

Sentence S1 (Order): "Complainant's unopposed motion to partially terminate the investigation as to claims 1-3 of the '347 patent is GRANTED."
-> {sentence_id: S1, patent_number: 9,748,347, claims_verbatim: 1-3, action: withdrawn, speaker: tribunal_ruling, respondents: [ALL], quote: "terminate the investigation as to claims 1-3 of the '347 patent is GRANTED"}

Sentence S2 (ID/RD - Final on Violation): "For the reasons discussed above, the ALJ finds that Respondents infringe claims 2 and 3 of the '294 patent, but that Complainant has not shown that Respondents infringe claim 1 of the '294 patent."
-> {S2, 8,350,294, "2 and 3", found_infringed, tribunal_ruling, [ALL], "Respondents infringe claims 2 and 3 of the '294 patent"}
-> {S2, 8,350,294, "1", found_not_infringed, tribunal_ruling, [ALL], "has not shown that Respondents infringe claim 1 of the '294 patent"}

Sentence S3 (ID/RD - Final on Violation): "Respondents argue that claims 2 and 3 of the '294 patent are obvious in view of Chen."
-> {S3, 8,350,294, "2 and 3", mention_only, party_argument, [ALL], "claims 2 and 3 of the '294 patent are obvious"}

Sentence S4 (Notice): "On December 1, 2023, the ALJ issued an ID (Order No. 32) granting Complainant's motion to terminate the investigation as to all asserted claims of the '347 patent, and the Commission has determined not to review the ID."
-> No claim numbers are named ("all asserted claims" is not a claim list), so record nothing for S4.

Sentence S5 (Notice): "The Commission has determined not to review the ID terminating claims 1-7 of the '335 patent."
-> {S5, 10,312,335, 1-7, not_reviewed, tribunal_ruling, [ALL], "determined not to review the ID terminating claims 1-7 of the '335 patent"}

Sentence S6 (Opinion, Commission): "The final ID found that Respondents infringe claims 2 and 3 of the '294 patent."
-> {S6, 8,350,294, "2 and 3", found_infringed, tribunal_recital, [ALL], "The final ID found that Respondents infringe claims 2 and 3 of the '294 patent"}

Sentence S7 (Opinion, Commission): "The Commission affirms the ID's finding that claims 2 and 3 of the '294 patent are not invalid and finds a violation of section 337 as to those claims."
-> {S7, 8,350,294, "2 and 3", found_not_invalid, tribunal_ruling, [ALL], "affirms the ID's finding that claims 2 and 3 of the '294 patent are not invalid"}
-> {S7, 8,350,294, "2 and 3", found_infringed, tribunal_ruling, [ALL], "finds a violation of section 337 as to those claims"} -- the claims are named earlier in the same sentence, so use them.

Sentence S8 (Order): "The investigation is terminated as to Innoscience America, Inc. with respect to claim 1 of the '508 patent based on a settlement agreement."
-> {S8, 8,404,508, 1, terminated_settlement, tribunal_ruling, [Innoscience America, Inc.], "terminated as to Innoscience America, Inc. with respect to claim 1 of the '508 patent based on a settlement agreement"}

Sentence S9 (Complaint): "EPC asserts that Innoscience infringes at least claims 1-3 of the '294 patent and claims 1-7 of U.S. Patent No. 10,312,335."
-> {S9, 8,350,294, 1-3, asserted, party_argument, [ALL], "infringes at least claims 1-3 of the '294 patent"}
-> {S9, 10,312,335, 1-7, asserted, party_argument, [ALL], "claims 1-7 of U.S. Patent No. 10,312,335"}

Sentence S10 (ID/RD - Final on Violation): "The ALJ finds that EPC's domestic industry products practice claim 2 of the '294 patent, and thus the technical prong is satisfied."
-> {S10, 8,350,294, 2, technical_prong, tribunal_ruling, [ALL], "EPC's domestic industry products practice claim 2 of the '294 patent"}

Sentence S11 (Order): "Staff contends that claims 4 and 5 of the '335 patent are indefinite."
-> {S11, 10,312,335, "4 and 5", mention_only, party_argument, [ALL], "claims 4 and 5 of the '335 patent are indefinite"}

Sentence S12 (Order): "Accordingly, Respondents' motion for summary determination that claims 1 through 4 are invalid under 35 U.S.C. § 101 is GRANTED." Context: "Respondents move for summary determination of invalidity of the '511 patent."
-> The patent is named only in the context sentence, which may be used to identify it: {S12, <the patent the context names, exactly as listed>, "1 through 4", found_invalid, tribunal_ruling, [ALL], "summary determination that claims 1 through 4 are invalid under 35 U.S.C. § 101 is GRANTED"}. If neither the sentence nor its context names the patent, write unknown.

Sentence S13 (ID/RD - Final on Violation): "In the event the Commission reviews and reverses the ALJ's claim construction, the ALJ would find claim 1 of the '294 patent infringed."
-> A conditional, alternative finding is not a ruling on the claims as they stand: {S13, 8,350,294, 1, mention_only, tribunal_ruling, [ALL], "the ALJ would find claim 1 of the '294 patent infringed"}.

Sentence S14 (Notice): "The Commission has determined to review the final ID in part and, on review, to find no violation of section 337 with respect to claim 1 of the '260 patent because it is not infringed."
-> {S14, <'260 patent as listed>, 1, found_not_infringed, tribunal_ruling, [ALL], "find no violation of section 337 with respect to claim 1 of the '260 patent because it is not infringed"}

Sentence S15 (Order): "Complainant's motion for leave to amend the complaint and notice of investigation to add claims 8 and 9 of the '335 patent is GRANTED."
-> {S15, 10,312,335, "8 and 9", added, tribunal_ruling, [ALL], "amend the complaint and notice of investigation to add claims 8 and 9 of the '335 patent is GRANTED"}

Sentence S16 (Order): "The investigation is terminated as to respondent Innoscience, Inc on the basis of a consent order with respect to claims 1-3 of the '294 patent."
-> {S16, 8,350,294, 1-3, terminated_settlement, tribunal_ruling, [Innoscience, Inc], "terminated as to respondent Innoscience, Inc on the basis of a consent order with respect to claims 1-3 of the '294 patent"}

Sentence S17 (Order): "In view of the license agreement between the parties, the joint motion to terminate the investigation as to all respondents with respect to claims 4-7 of the '335 patent is GRANTED."
-> {S17, 10,312,335, 4-7, terminated_settlement, tribunal_ruling, [ALL], "the joint motion to terminate the investigation as to all respondents with respect to claims 4-7 of the '335 patent is GRANTED"}

Sentence S18 (ID/RD - Final on Violation): "Complainant has not shown that its domestic industry products practice claim 1 of the '508 patent, and therefore the technical prong of the domestic industry requirement is not satisfied as to that patent."
-> {S18, 8,404,508, 1, technical_prong, tribunal_ruling, [ALL], "has not shown that its domestic industry products practice claim 1 of the '508 patent"}

Sentence S19 (ID/RD - Final on Violation): "The ALJ finds that the accused Innoscience America, Inc. products infringe claim 2 of the '294 patent, but the ALJ finds that no other respondent's products infringe that claim."
-> {S19, 8,350,294, 2, found_infringed, tribunal_ruling, [Innoscience America, Inc.], "the accused Innoscience America, Inc. products infringe claim 2 of the '294 patent"}
-> The rest of the sentence names no other respondent from the list and no other claim numbers, so there is no second event; do not guess which respondents "no other respondent" means.

Sentence S20 (Order): "On February 14, 2024, the ALJ issued Order No. 45, an initial determination granting Complainant's motion to terminate the investigation as to claims 1-7 of the '335 patent."
-> A later order describing an earlier one: {S20, 10,312,335, 1-7, withdrawn, tribunal_recital, [ALL], "Order No. 45, an initial determination granting Complainant's motion to terminate the investigation as to claims 1-7 of the '335 patent"}

Sentence S21 (Notice): "On review, the Commission reverses the final ID's finding that claim 1 of the '260 patent is not invalid and finds that claim 1 is invalid as obvious."
-> {S21, <'260 patent as listed>, 1, found_invalid, tribunal_ruling, [ALL], "finds that claim 1 is invalid as obvious"} -- the Commission's own new finding. Do not also record the reversed ALJ finding as a ruling: in this notice it is described, so if you record it at all it is found_not_invalid with speaker tribunal_recital.

Sentence S22 (Opinion, Commission): "Accordingly, the Commission finds no violation of section 337 with respect to claims 1 and 12-14 of the '511 patent."
-> Without a stated reason, "no violation" for named claims is found_not_infringed unless the sentence or its context says the claims are invalid: {S22, <'511 patent as listed>, "1 and 12-14", found_not_infringed, tribunal_ruling, [ALL], "finds no violation of section 337 with respect to claims 1 and 12-14 of the '511 patent"}

Sentence S23 (Complaint): "The Asserted Claims are claims 1-3 of the '294 patent, claim 1 of the '508 patent, claims 1-3 of the '347 patent, and claims 1-7 of the '335 patent (collectively, the \\"Asserted Claims\\")."
-> Four events, one per patent, each asserted, party_argument, [ALL], with its own quote: "claims 1-3 of the '294 patent", "claim 1 of the '508 patent", "claims 1-3 of the '347 patent", "claims 1-7 of the '335 patent".

## Before you answer

Check each event: the quote is copied exactly from its own sentence; the claim list is copied exactly; the patent is written exactly as in the list, or unknown; each respondent is written exactly as in the list, or the list is ["ALL"]; the speaker reflects who is speaking in this document. Then call record_claim_events once with every event."""


def user_message(
    *,
    investigation: str,
    title: str,
    patents: list[str],
    respondents: list[str],
    document: dict[str, Any],
    candidates: list[Candidate],
) -> str:
    """The per-call part, after the cached system prompt."""
    lines = [
        f"Investigation {investigation}: {title}",
        f"Patents (write exactly as listed): {'; '.join(patents) or 'none listed'}",
        f"Respondents (write exactly as listed): {'; '.join(respondents) or 'none listed'}",
        (
            f"Document: {document.get('document_type') or 'unknown type'} -- "
            f"\"{document.get('title') or ''}\" (EDIS {document.get('id')}, "
            f"{str(document.get('document_date') or document.get('official_received_date') or 'undated')[:10]})"
        ),
        "",
        "Sentences:",
    ]
    for candidate in candidates:
        lines.append(f'<sentence id="{candidate.id}">')
        if candidate.context:
            lines.append(f"<context>{candidate.context}</context>")
        lines.append(f"<text>{candidate.sentence}</text>")
        lines.append("</sentence>")
    lines.append("")
    lines.append("Record every claim event in these sentences with record_claim_events.")
    return "\n".join(lines)
