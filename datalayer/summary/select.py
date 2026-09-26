"""Which documents a case summary reads -- by rule, from the documents index,
for free.

A case's docket runs to hundreds of filings and a complaint alone to
thousands of pages, nearly all of them exhibits. A summary reads only what
says something a newcomer needs, and as little of each as will do:

    complaint     the complaint itself: the latest amended complaint if there
                  is one, else the original public filing. Supplements,
                  appendices and exhibits are not read.
    notice of     what the Commission instituted, against whom.
    institution
    answers       one per counsel (respondents sharing a firm file the same
                  answer), up to max_answer_groups; respondents still in the
                  case first. The rest are listed by name.
    rulings       the ALJ's rulings on summary determination -- the ruling,
                  not the motion and the responses, since a ruling sets out
                  both sides. Grants before denials, then newest, up to
                  max_rulings.
    decisions     the final ID, the Commission's notices of a decision to
                  review or of a final determination, and its opinions.

Some events need no reading at all, because the title says it all:
terminations (settlement, withdrawal, consent order), defaults, the
Commission declining to review a dispositive ruling, and the remedial
orders it issued. These are noted from their titles.

Only public documents are ever read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..nextactions import stays

SECTIONS = ("complaint", "answers", "rulings", "decisions")


@dataclass
class Item:
    section: str  # one of SECTIONS
    kind: str  # complaint, notice_of_institution, answer, ruling, final_id, commission_notice, commission_opinion, ...
    doc: dict[str, Any]
    why: str = ""  # plain English: why read, noted or left
    who: str = ""  # an answer's respondents

    @property
    def id(self) -> str:
        return str(self.doc.get("id") or "")

    @property
    def title(self) -> str:
        # EDIS titles often carry a mangled apostrophe ("Commission�s").
        title = " ".join(str(self.doc.get("title") or "").replace("�", "'").split())
        return title or f"Untitled {self.doc.get('document_type') or 'document'}"

    @property
    def day(self) -> str:
        return _day(self.doc)


@dataclass
class Selection:
    read: list[Item] = field(default_factory=list)  # opened and read
    noted: list[Item] = field(default_factory=list)  # from the title alone
    not_read: list[Item] = field(default_factory=list)  # listed, with the reason
    skipped_filings: int = 0  # appendices, exhibits and the like, not listed one by one

    def reading(self, section: str) -> list[Item]:
        return [i for i in self.read if i.section == section]


def _day(doc: dict[str, Any]) -> str:
    return str(doc.get("document_date") or doc.get("official_received_date") or "")[:10]


def _public(doc: dict[str, Any]) -> bool:
    return str(doc.get("security_level") or "").lower() == "public"


def _title(doc: dict[str, Any]) -> str:
    return " ".join(str(doc.get("title") or "").split())


def _order(doc: dict[str, Any]) -> tuple[str, int]:
    ident = str(doc.get("id") or "")
    return (_day(doc), int(ident) if ident.isdigit() else 0)


# -- the complaint ------------------------------------------------------------

_SUPPLEMENT = re.compile(r"\bsupplement", re.I)
_AMENDED = re.compile(r"\bamended\s+complaint\b", re.I)
_COMPLAINT_WORD = re.compile(r"\bcomplaint\b", re.I)
_NOT_A_COMPLAINT = re.compile(
    r"\bappendi(?:x|ces)\b|patent\s+papers|file\s+histor|certified\s+cop|\bletter\b|cover\s+sheet"
    r"|certificate\s+of\s+service|\bconfidential\b|\bverification\b|\bproposed\b|\bpublic\s+interest\b",
    re.I,
)


def _complaint(documents: list[dict[str, Any]], sel: Selection) -> None:
    filings = [d for d in documents if d.get("document_type") == "Complaint" and _public(d)]
    bodies, supplements, untitled = [], [], []
    for doc in filings:
        title = _title(doc)
        if not title:
            untitled.append(doc)
        elif _NOT_A_COMPLAINT.search(title) or not _COMPLAINT_WORD.search(title):
            sel.skipped_filings += 1  # "Appendix B", "Public Exhibits 1-50", "Patent Papers"
        elif _SUPPLEMENT.search(title):
            supplements.append(doc)
        else:
            bodies.append(doc)

    amended = sorted((d for d in bodies if _AMENDED.search(_title(d))), key=_order)
    originals = sorted((d for d in bodies if not _AMENDED.search(_title(d))), key=_order)
    if amended:
        chosen = amended[-1]
        why = "The latest amended complaint, which replaces the original"
        superseded = originals + amended[:-1]
    elif originals:
        chosen = originals[0]
        why = "The complaint itself; its exhibits and appendices are not read"
        superseded = originals[1:]
    elif untitled:
        chosen = sorted(untitled, key=_order)[0]
        why = "An untitled complaint filing; its first pages show which file is the complaint itself"
        superseded = []
        untitled = untitled[1:]
    else:
        return
    sel.read.append(Item("complaint", "complaint", chosen, why))
    for doc in superseded:
        reason = "Replaced by the amended complaint" if amended else "A further part of the same complaint filing"
        sel.not_read.append(Item("complaint", "complaint", doc, reason))
    for doc in sorted(supplements, key=_order):
        sel.not_read.append(Item(
            "complaint", "supplement", doc,
            "Supplements mostly add exhibits or correct details; the complaint and the notice of institution say what the case is about",
        ))
    sel.skipped_filings += len(untitled)


_INSTITUTION = re.compile(r"notice\s+of\s+institution|institution\s+of\s+(?:the\s+)?investigation", re.I)
# "F.R. Notice of ...", "F.R. Commission Final Determination ...": the
# Federal Register's reprint of a notice already on the docket.
_FR_REPRINT = re.compile(r"^\s*f\.\s*r\.\s", re.I)


def _institution(documents: list[dict[str, Any]], sel: Selection) -> None:
    notices = sorted(
        (d for d in documents
         if d.get("document_type") == "Notice" and _public(d)
         and _INSTITUTION.search(_title(d)) and not _FR_REPRINT.search(_title(d))),
        key=_order,
    )
    if notices:
        sel.read.append(Item("complaint", "notice_of_institution", notices[0],
                             "What the Commission instituted, and against whom"))


# -- the answers ----------------------------------------------------------------

_ANSWER_WORD = re.compile(r"\b(?:response|answer)\b", re.I)
_NOT_AN_ANSWER = re.compile(
    r"\bexhibit|\bappendi|\bverification\b|certificate\s+of\s+service|\bletter\b|public\s+interest"
    r"|\bdeclaration\b|\bconfidential\b|\berrata\b",
    re.I,
)


_NAME_NOISE = re.compile(
    r"\b(?:and|the|inc|incorporated|ltd|limited|llc|llp|pllc|lp|co|corp|corporation|company|gmbh|plc|pc|p\s*c)\b"
)


def _name_key(value: Any) -> str:
    """"Adduci, Mastriani & Schaumberg LLP" and "Adduci, Mastriani and
    Schaumberg LLP" alike: lower case, letters and digits, no company forms."""
    words = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower().replace("&", " "))
    return " ".join(_NAME_NOISE.sub(" ", words).split())


def _groups(answers: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Answers that share counsel or respondents are one group: a
    respondent's answer to the amended complaint joins its first answer,
    even when it has changed firms since."""
    parent = list(range(len(answers)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[str, int] = {}
    for i, doc in enumerate(answers):
        for field_ in ("firm_organization", "on_behalf_of"):
            key = _name_key(doc.get(field_))
            if not key:
                continue
            key = f"{field_}:{key}"
            if key in seen:
                parent[root(i)] = root(seen[key])
            else:
                seen[key] = i
    grouped: dict[int, list[dict[str, Any]]] = {}
    for i, doc in enumerate(answers):
        grouped.setdefault(root(i), []).append(doc)
    return list(grouped.values())


def _answers(documents: list[dict[str, Any]], sel: Selection, limit: int) -> None:
    answers = []
    for doc in documents:
        if doc.get("document_type") != "Answer to Complaint" or not _public(doc):
            continue
        title = _title(doc)
        if title and (_NOT_AN_ANSWER.search(title) or not _ANSWER_WORD.search(title)):
            continue
        answers.append(doc)

    gone = " ".join(o.get("who") or "" for o in stays.out_of_case(documents))
    picked = []
    for docs in _groups(answers):
        # The latest full answer stands for the group (an amended answer, or
        # one to the amended complaint, replaces the first); a supplement only
        # when that is all there is.
        full = [d for d in docs if not _SUPPLEMENT.search(_title(d))] or docs
        chosen = sorted(full, key=_order)[-1]
        names: dict[str, str] = {}
        for doc in sorted(docs, key=_order, reverse=True):
            name = " ".join(str(doc.get("on_behalf_of") or "").split())
            if name and _name_key(name) not in names:
                names[_name_key(name)] = name
        who = "; ".join(names.values()) or str(chosen.get("firm_organization") or chosen.get("filed_by") or "").strip()
        # Out of the case only when every respondent the group answered for is.
        left = bool(gone) and bool(names) and all(stays.names_party(gone, [n]) for n in names.values())
        first_filed = min(_order(d) for d in docs)
        picked.append((left, first_filed, chosen, who))

    picked.sort(key=lambda p: (p[0], p[1]))
    for index, (left, _, doc, who) in enumerate(picked):
        if index < limit:
            why = "Their defenses and denials" + (" (no longer in the case)" if left else "")
            sel.read.append(Item("answers", "answer", doc, why, who=who))
        else:
            why = "Out of the case, and over the limit of answers read" if left else f"Over the limit of {limit} answers read"
            sel.not_read.append(Item("answers", "answer", doc, why, who=who))


# -- rulings, and what titles alone say --------------------------------------------

_SD = re.compile(r"summary\s+determination", re.I)
_SD_MOTION = re.compile(r"motions?\s+for\s+(?:a\s+)?(?:partial\s+)?summary\s+determination", re.I)
_RULING_VERB = re.compile(r"\b(?:granting|denying|dismissing)\b", re.I)
_PROCEDURAL = re.compile(
    r"extension|extend|\btime\b|\bleave\b|schedul|strike|\bbrief|page\s+limit|to\s+file|to\s+respond|oral\s+argument|withdraw\s+(?:its|the|their)\s+motion",
    re.I,
)
_DEFAULT = re.compile(r"\bdefault", re.I)
# \b: "Determination" contains "terminat".
_TERMINATION = re.compile(r"\bterminat|\bwithdraw|\bsettle|consent\s+order|\barbitration", re.I)
_DENIAL = re.compile(r"^\W*(?:\(\d+\)\s*)?(?:\[?corrected\]?\s*)?(?:public\s+version\s*)?(?:order\s+no\.?\s*\d+\s*:?\s*)?"
                     r"(?:initial\s+determ\w*\s+|id\s+)?(?:denying|dismissing)\b", re.I)
_VARIANT = re.compile(r"\[?\bcorrected\b\]?|\bpublic\s+version\b|\(public\)|order\s+no\.?\s*\d+\s*:?", re.I)


def _is_sd_ruling(doc: dict[str, Any]) -> bool:
    title = _title(doc)
    if not _SD.search(title) or _PROCEDURAL.search(title):
        return False
    if doc.get("document_type") == "ID/RD - Other Than Final on Violation":
        return True
    return bool(_SD_MOTION.search(title) and _RULING_VERB.search(title))


def _latest_versions(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A corrected or public version replaces the one before it: the same
    title, bar "[Corrected]", within 45 days."""
    kept: list[dict[str, Any]] = []
    for doc in sorted(docs, key=_order):
        bare = " ".join(_VARIANT.sub(" ", _title(doc)).lower().split())
        for i, earlier in enumerate(kept):
            if " ".join(_VARIANT.sub(" ", _title(earlier)).lower().split()) == bare and _close(earlier, doc):
                kept[i] = doc
                break
        else:
            kept.append(doc)
    return kept


def _close(a: dict[str, Any], b: dict[str, Any]) -> bool:
    try:
        return abs((date.fromisoformat(_day(b)) - date.fromisoformat(_day(a))).days) <= 45
    except ValueError:
        return False


def _rulings(documents: list[dict[str, Any]], sel: Selection, limit: int) -> None:
    rulings = []
    for doc in documents:
        if not _public(doc):
            continue
        kind = doc.get("document_type")
        if kind not in ("ID/RD - Other Than Final on Violation", "Order"):
            continue
        title = _title(doc)
        if _is_sd_ruling(doc):
            rulings.append(doc)
        elif kind == "ID/RD - Other Than Final on Violation":
            if _DEFAULT.search(title):
                sel.noted.append(Item("rulings", "default", doc, "Default"))
            elif _TERMINATION.search(title):
                sel.noted.append(Item("rulings", "termination", doc, "Termination"))

    rulings = _latest_versions(rulings)
    # Grants change the case; denials only keep it on course. Newest first within each.
    rulings.sort(key=_order, reverse=True)
    rulings.sort(key=lambda d: bool(_DENIAL.search(_title(d))))
    for index, doc in enumerate(rulings):
        denied = bool(_DENIAL.search(_title(doc)))
        if index < limit:
            why = "Denies summary determination: the issue goes to the hearing" if denied else "Decides an issue before the hearing"
            sel.read.append(Item("rulings", "ruling", doc, why))
        else:
            sel.not_read.append(Item("rulings", "ruling", doc, f"Over the limit of {limit} rulings read"))


# -- the ALJ's final ID and the Commission ----------------------------------------------

_NOT_FINAL_ID = re.compile(r"^\s*recommended\s+determination|\benforcement\b|\badvisory\b|\bmodification\b", re.I)
_NOT_TO_REVIEW = re.compile(r"\bnot\s+to\s+review\b", re.I)
_TO_REVIEW = re.compile(r"\bto\s+review\b", re.I)
_MERITS = re.compile(r"final\s+initial|summary\s+determination|violation|remand", re.I)
# Housekeeping about a merits decision, not the decision.
_HOUSEKEEPING = re.compile(
    r"\bextend|target\s+date|deadline|declassif|redact|public\s+version|investigative\s+attorney|schedul", re.I
)
_FINAL = re.compile(
    r"final\s+determination|issuance\s+of\s+(?:an?\s+)?(?:limited|general)|finding\s+(?:a\s+|no\s+)?violation"
    r"|termination\s+of\s+the\s+investigation\s+with",
    re.I,
)
_REMEDY = re.compile(r"exclusion\s+order|cease\s+and\s+desist|consent\s+order", re.I)


def _decisions(documents: list[dict[str, Any]], sel: Selection) -> None:
    public = [d for d in documents if _public(d)]

    finals = _latest_versions([
        d for d in public
        if d.get("document_type") == "ID/RD - Final on Violation" and not _NOT_FINAL_ID.search(_title(d))
    ])
    if finals:
        sel.read.append(Item("decisions", "final_id", finals[-1],
                             "The ALJ's decision after the hearing: its summary and its conclusions"))
        for doc in finals[:-1]:
            sel.not_read.append(Item("decisions", "final_id", doc, "An earlier final ID; the latest is read"))

    for doc in sorted(public, key=_order):
        if doc.get("document_type") != "Notice":
            continue
        title = _title(doc)
        if _FR_REPRINT.search(title) or "commission" not in title.lower() or _HOUSEKEEPING.search(title):
            continue
        if _NOT_TO_REVIEW.search(title):
            if _MERITS.search(title):
                sel.noted.append(Item("decisions", "not_reviewed", doc, "The Commission let the ruling stand"))
        elif (_TO_REVIEW.search(title) and _MERITS.search(title)) or _FINAL.search(title):
            sel.read.append(Item("decisions", "commission_notice", doc, "What the Commission decided, and why it reviewed"))

    opinions = _latest_versions([d for d in public if d.get("document_type") == "Opinion, Commission"])
    for doc in opinions[-2:]:
        sel.read.append(Item("decisions", "commission_opinion", doc, "The Commission's reasons"))
    for doc in opinions[:-2]:
        sel.not_read.append(Item("decisions", "commission_opinion", doc, "An earlier opinion; the latest two are read"))

    for doc in sorted(public, key=_order):
        if doc.get("document_type") == "Order, Commission" and _REMEDY.search(_title(doc)):
            sel.noted.append(Item("decisions", "remedy", doc, "Remedy issued"))


def select(documents: list[dict[str, Any]], *, max_answer_groups: int = 5, max_rulings: int = 10) -> Selection:
    """What a summary of this case would read, note from titles, and leave."""
    documents = list(documents or [])
    sel = Selection()
    _complaint(documents, sel)
    _institution(documents, sel)
    _answers(documents, sel, max_answer_groups)
    _rulings(documents, sel, max_rulings)
    _decisions(documents, sel)
    order = {name: i for i, name in enumerate(SECTIONS)}
    for items in (sel.read, sel.noted, sel.not_read):
        items.sort(key=lambda i: (order[i.section], i.day, i.id))
    return sel
