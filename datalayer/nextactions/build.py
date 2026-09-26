"""Each open investigation's next actions, from what is already on disk.

Three sources, each event labeled with the one it came from:

    case data   the IDS record's own dates for the current stage: target
                date, scheduled final initial determination, Markman and
                evidentiary hearings
    docket      when the final ID and the Commission's notices issued
    by rule     deadlines the rules of 19 CFR Part 210 set from those dates
                (verified against the regulation text, September 2026):

      210.10(a)(1)     institution decided within 30 days of the complaint
      210.51(a)        ALJ sets the target date within 45 days of institution;
                       the target date is for completion of the investigation
      210.42(a)(1)(i)  final ID no later than 4 months before the target date
      210.43(a)(1)     petitions for review within 12 days of service of the final ID
      210.43(c)        responses within 8 days of service of a petition
      210.42(h)(2)     final ID becomes the Commission's determination 60 days
                       after service unless the Commission orders review
      210.49(d)        Presidential review: 60 days from delivery of the
                       Commission's action to the President

Periods run from the day a document issued (its EDIS date), which is when
the Commission serves it electronically; a date the rules would move for
service by mail is not modeled. Rule dates are labeled as such because
orders and notices can and do change them.

The page chooses the *next* event against the day it is viewed, so this
file stays right between rebuilds; `waiting_on` is what the case is waiting
on when no dated event is ahead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..backfill import OPEN_STATUSES
from ..claims.timeline import final_id_issued
from ..claims.validate import is_final_id
from ..store import Store, load_json, save_json
from . import calendar, stays

Logger = Callable[[str], None]

NEXT_ACTIONS_FILE = "next_actions.json"

# Commission notices, by what they say happened (EDIS titles, as filed).
# Only notices dated on or after the final ID count, and only those about it:
# "Decision to Review an Initial Determination in Part; ... Remedy" is the
# final ID; a review of an ID granting summary determination is not.
_REVIEW_EXTENDED_RE = re.compile(
    r"extend\w*\s+the\s+(?:date|deadline)\s+for\s+(?:determining\s+whether\s+to\s+review|reviewing)", re.I
)
_NOT_REVIEWED_RE = re.compile(r"(?:determination|decision)\s+not\s+to\s+review\b", re.I)
_REVIEWED_RE = re.compile(r"(?:determination|decision)\s+to\s+review\b", re.I)
_ABOUT_FINAL_ID_RE = re.compile(
    r"final\s+initial\s+determination|\bon\s+violation\b|finding\s+(?:a\s+|no\s+)?violation|\bremedy\b", re.I
)
_FINAL_DETERMINATION_RE = re.compile(r"final\s+determination", re.I)
_NO_VIOLATION_RE = re.compile(r"\bno\s+violation\b|\bterminat", re.I)
_REMEDY_RE = re.compile(r"exclusion\s+order|cease\s+and\s+desist", re.I)


@dataclass
class Event:
    date: str | None
    label: str
    basis: str  # "case data" | "docket" | "by rule"
    kind: str = "deadline"  # "hearing" | "deadline" | "decision" | "milestone"
    cite: str | None = None
    note: str | None = None
    end: str | None = None  # the last day of a hearing that runs several days
    source: dict[str, Any] | None = None  # the docket document it rests on
    on_hold: bool | None = None  # a date a stay in effect has suspended

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class NextActions:
    case: str
    stage: str
    stage_label: str
    events: list[Event] = field(default_factory=list)
    waiting_on: str | None = None
    notes: list[str] = field(default_factory=list)
    stay: dict[str, Any] | None = None  # a whole-case stay in effect
    partial_stays: list[dict[str, Any]] = field(default_factory=list)
    out_of_case: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        events = sorted(self.events, key=lambda e: (e.date is None, e.date or ""))
        out = {
            "stage": self.stage,
            "stage_label": self.stage_label,
            "events": [e.to_dict() for e in events],
            "waiting_on": self.waiting_on,
            "notes": self.notes,
        }
        if self.stay:
            out["stay"] = self.stay
        if self.partial_stays:
            out["partial_stays"] = self.partial_stays
        if self.out_of_case:
            out["out_of_case"] = self.out_of_case
        return out


def _current_fields(case: dict[str, Any]) -> dict[str, Any]:
    stages = case.get("stages") or []
    stage = next((s for s in stages if s.get("is_current")), None) or next(
        (s for s in stages if s.get("is_primary")), None
    ) or (stages[0] if stages else {})
    return {k: v for k, v in (stage.get("fields") or {}).items() if v not in (None, "", "N/A")}


def _day(doc: dict[str, Any]) -> str | None:
    value = doc.get("document_date") or doc.get("official_received_date")
    return str(value)[:10] if value else None


def _source(doc: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(doc.get("id") or ""), "title": doc.get("title"), "date": _day(doc)}


def _latest(documents: list[dict[str, Any]], pattern: re.Pattern[str], *, types: tuple[str, ...] = ("Notice",),
            exclude: re.Pattern[str] | None = None, about: re.Pattern[str] | None = None,
            since: date | None = None) -> dict[str, Any] | None:
    found = [
        d for d in documents
        if d.get("document_type") in types and pattern.search(d.get("title") or "")
        and not (exclude and exclude.search(d.get("title") or ""))
        and (about is None or about.search(d.get("title") or ""))
        and _day(d) and (since is None or (_day(d) or "") >= since.isoformat())
        and not str(d.get("title") or "").startswith("F.R.")
    ]
    return max(found, key=lambda d: _day(d) or "") if found else None


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


# A case whose every date is older than this, with nothing ahead, is not
# waiting on anything this view can show.
DORMANT_AFTER_DAYS = 730


def build_case(case: dict[str, Any], documents: list[dict[str, Any]], *, today: date | None = None,
               schedule: list[dict[str, Any]] | None = None) -> NextActions | None:
    """The next actions of one open investigation, or None when it is closed.
    `schedule` is its procedural schedule as read from the orders
    (orders.schedule_events), when there is one."""
    result = _build_case(case, documents)
    today = today or date.today()
    if result is not None and schedule and result.stage in ("alj", "commission", "pre_institution"):
        _add_schedule(result, schedule)
    if result is not None and result.stage in ("alj", "commission", "pre_institution"):
        _apply_stays(result, documents or [], today)
    if result is None or result.stage in ("undated", "other", "concluded", "pre_institution"):
        return result
    dated = [calendar.parse(e.end or e.date) for e in result.events if e.date]
    latest = max((d for d in dated if d), default=None)
    if latest and (today - latest).days > DORMANT_AFTER_DAYS:
        result.stage, result.stage_label, result.waiting_on = "dormant", "No recent activity", None
        result.notes.append(
            f"The latest date on record is {latest.isoformat()}. The USITC still lists the investigation as open, "
            "which for older cases usually means its remedial orders remain in force; nothing further is scheduled."
        )
    return result


def _loose(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _apply_stays(result: NextActions, documents: list[dict[str, Any]], today: date) -> None:
    """Stays and respondents out of the case (stays.py), applied to the dates:
    a whole-case stay in effect puts every date from its start on hold; an
    order's date that names a respondent no longer in the case is dropped."""
    # Once the final ID is out, a stay of the ALJ's schedule is moot: only
    # what was stayed since counts.
    final_id = next((e.date for e in result.events if e.label == "Final initial determination issued"), None)
    status = stays.assess([d for d in documents if not final_id or _day(d) and _day(d) >= final_id], today)
    gone = stays.out_of_case(documents)
    result.out_of_case = gone
    names = [g["who"] for g in gone]
    if names:
        result.events = [e for e in result.events if not (e.basis == "order" and stays.names_party(e.label, names))]
    result.partial_stays = [
        p for p in status["partial"]
        if not any(_loose(p["who"]) in _loose(n) or _loose(n) in _loose(p["who"]) for n in names)
    ]
    stay = status["stay"]
    if stay:
        result.stay = stay
        for event in result.events:
            if event.date and event.date >= stay["since"] and event.basis != "docket":
                event.on_hold = True
        result.stage_label += " (stayed)"
        result.waiting_on = "The end of the stay"
    elif status["ended"]:
        ended = status["ended"]
        result.notes.append(
            f"A stay ordered on {ended['since']} has ended ({ended['ended_by']}): “{ended['source']['title']}”."
        )


_KINDS = {
    "hearing": "hearing", "claim_construction": "hearing", "initial_determination": "decision",
    "target_date": "decision",
}


def _add_schedule(result: NextActions, schedule: list[dict[str, Any]]) -> None:
    """The procedural schedule's dates, minus the milestones the case record
    already gives (it is kept current by the USITC, so it wins)."""
    have = {e.label for e in result.events if e.basis == "case data"}
    for item in schedule:
        name = str(item.get("event") or "")
        lower, category = name.lower(), item.get("category")
        if (
            (category == "target_date" and any(label.startswith("Target date") for label in have))
            or (category == "hearing" and "evidentiary hearing" in lower and "Evidentiary hearing" in have
                and not re.search(r"pre-?hearing|brief|statement", lower))
            or (category == "claim_construction" and "hearing" in lower
                and "Markman (claim construction) hearing" in have)
            or (category == "initial_determination"
                and "Final initial determination on violation (scheduled)" in have)
        ):
            continue
        notes = []
        if item.get("relative"):
            notes.append(str(item["relative"]))
        if item.get("replaces") and item.get("replaces") != item.get("date"):
            notes.append(f"moved from {item['replaces']}")
        note = "; ".join(notes)
        result.events.append(Event(
            date=item.get("date"), end=item.get("end"), label=name, basis="order", kind=_KINDS.get(category, "deadline"),
            note=(note[:1].upper() + note[1:]) or None, source=item.get("source"),
        ))


def _build_case(case: dict[str, Any], documents: list[dict[str, Any]]) -> NextActions | None:
    number = str(case.get("investigation_number") or "")
    status = case.get("status")
    if status not in OPEN_STATUSES or case.get("withdrawn"):
        return None
    fields = _current_fields(case)
    phase = str(case.get("phase") or "Violation")

    if status == "Pre-institution":
        return _pre_institution(number, fields, case)
    result = NextActions(case=number, stage="alj", stage_label="Before the administrative law judge")
    if phase != "Violation":
        result.stage, result.stage_label = "other", f"{phase} proceeding"
        result.notes.append(
            f"This case is in a {phase.lower()} proceeding. Next actions cover the original violation phase only for now."
        )
        return result

    documents = documents or []
    target = calendar.parse(fields.get("target_date"))
    instituted = calendar.parse(fields.get("start_date") or case.get("date_initiated"))
    final_id = calendar.parse(final_id_issued(documents))
    scheduled_id = calendar.parse(fields.get("initial_determination_date"))

    # Some investigations decades old are still listed as active -- their
    # remedial orders remain in force -- with no dates for anything.
    if not (instituted or target or final_id or scheduled_id):
        result.stage, result.stage_label = "undated", "No schedule on record"
        result.notes.append(
            "The USITC lists this investigation as active but gives no dates for it. That is usual for older "
            "investigations whose exclusion or cease-and-desist orders remain in force, so there is no "
            "next action to work out."
        )
        return result

    # -- dates the case record carries -------------------------------------
    for start, end, label in (
        ("markman_hearing_start_date", "markman_hearing_end_date", "Markman (claim construction) hearing"),
        ("hearing_conf_start_date", "hearing_conf_end_date", "Evidentiary hearing"),
    ):
        if fields.get(start):
            result.events.append(Event(
                date=str(fields[start])[:10], end=str(fields.get(end) or "")[:10] or None, label=label,
                basis="case data", kind="hearing",
            ))
    if target:
        result.events.append(Event(
            date=_iso(target), label="Target date for completion of the investigation", basis="case data",
            kind="decision", cite="19 CFR 210.51(a)",
            note="The Commission aims to issue its final determination by this date.",
        ))
    elif instituted:
        result.events.append(Event(
            date=_iso(calendar.period_end(instituted, 45)), label="ALJ to set the target date",
            basis="by rule", cite="19 CFR 210.51(a)", note="Within 45 days after institution.",
        ))

    # -- before the final initial determination -----------------------------
    if not final_id:
        if scheduled_id:
            result.events.append(Event(
                date=_iso(scheduled_id), label="Final initial determination on violation (scheduled)",
                basis="case data", kind="decision",
            ))
        if target:
            result.events.append(Event(
                date=_iso(calendar.previous_business_day(calendar.months_before(target, 4))),
                label="Final initial determination due no later than",
                basis="by rule", kind="decision", cite="19 CFR 210.42(a)(1)(i)",
                note="4 months before the target date (the business day before, when that falls on a weekend or holiday).",
            ))
        result.waiting_on = "The administrative law judge's final initial determination on violation"
        return result

    # -- after the final initial determination ------------------------------
    result.stage, result.stage_label = "commission", "Before the Commission"
    same_day = [d for d in documents if _day(d) == final_id.isoformat()
                and is_final_id(d.get("document_type"), d.get("title"))]
    # The ID itself rather than an exhibit list filed under the same type.
    final_id_doc = next((d for d in same_day if re.search(r"initial\s+determination", d.get("title") or "", re.I)),
                        same_day[0] if same_day else None)
    result.events.append(Event(
        date=_iso(final_id), label="Final initial determination issued", basis="docket", kind="milestone",
        source=_source(final_id_doc) if final_id_doc else None,
    ))
    determination = _latest(documents, _FINAL_DETERMINATION_RE, exclude=re.compile(r"to\s+review|extend", re.I),
                            since=final_id)
    if determination:
        return _after_final_determination(result, determination)

    about = dict(about=_ABOUT_FINAL_ID_RE, since=final_id)
    reviewed = _latest(documents, _REVIEWED_RE, exclude=re.compile(r"\bnot\s+to\s+review\b|extend", re.I), **about)
    not_reviewed = _latest(documents, _NOT_REVIEWED_RE, exclude=re.compile(r"extend", re.I), **about)
    extended = _latest(documents, _REVIEW_EXTENDED_RE, since=final_id)

    if not reviewed and not not_reviewed:
        petitions = calendar.period_end(final_id, 12)
        result.events.append(Event(
            date=_iso(petitions), label="Petitions for review of the final ID due", basis="by rule",
            cite="19 CFR 210.43(a)(1)", note="12 days after service of the final initial determination.",
        ))
        result.events.append(Event(
            date=_iso(calendar.period_end(petitions, 8)), label="Responses to petitions for review due",
            basis="by rule", cite="19 CFR 210.43(c)",
            note="8 days after service of a petition; shown for petitions filed on the last day.",
        ))
        if extended:
            result.events.append(Event(
                date=None, label="Commission to decide whether to review the final ID", basis="docket",
                kind="decision", source=_source(extended),
                note=f"The Commission extended this deadline on {_day(extended)}; the new date is in that notice.",
            ))
        else:
            result.events.append(Event(
                date=_iso(calendar.period_end(final_id, 60)), label="Commission to decide whether to review the final ID",
                basis="by rule", kind="decision", cite="19 CFR 210.42(h)(2)",
                note="Otherwise the final ID becomes the Commission's determination 60 days after service.",
            ))
        result.waiting_on = "The Commission's decision whether to review the final initial determination"
        return result

    decided = reviewed if reviewed and (not not_reviewed or _day(reviewed) >= _day(not_reviewed)) else not_reviewed
    if decided is not_reviewed and _NO_VIOLATION_RE.search(str(not_reviewed.get("title") or "")):
        # A final ID of no violation that the Commission leaves alone ends the phase.
        result.events.append(Event(date=_day(decided), label="Commission decided not to review the final ID",
                                   basis="docket", kind="milestone", source=_source(decided)))
        result.stage, result.stage_label, result.waiting_on = "concluded", "Concluded", None
        result.notes.append("The final initial determination of no violation became the Commission's determination.")
        return result
    result.events.append(Event(
        date=_day(decided), label=("Commission decided to review the final ID" if decided is reviewed
                                   else "Commission decided not to review the final ID"),
        basis="docket", kind="milestone", source=_source(decided),
    ))
    result.waiting_on = (
        "The Commission's final determination" if decided is reviewed
        else "The Commission's determination on remedy, the public interest and bonding"
    )
    return result


def _after_final_determination(result: NextActions, determination: dict[str, Any]) -> NextActions:
    day = calendar.parse(_day(determination))
    title = str(determination.get("title") or "")
    result.events.append(Event(
        date=_day(determination), label="Commission final determination", basis="docket", kind="milestone",
        source=_source(determination),
    ))
    if _REMEDY_RE.search(title) and day:
        result.stage, result.stage_label = "presidential", "Presidential review"
        result.events.append(Event(
            date=_iso(day + timedelta(days=60)), label="Presidential review period ends (approximately)",
            basis="by rule", kind="decision", cite="19 CFR 210.49(d)",
            note="60 days from delivery of the Commission's action to the President, usually the day it issues; "
                 "the orders become final the day after, unless disapproved.",
        ))
        result.waiting_on = "The end of the Presidential review period"
    elif _NO_VIOLATION_RE.search(title):
        result.stage, result.stage_label = "concluded", "Concluded"
        result.waiting_on = None
        result.notes.append("The Commission's final determination ends the violation phase.")
    else:
        result.waiting_on = "Any remaining Commission action"
    return result


def _pre_institution(number: str, fields: dict[str, Any], case: dict[str, Any]) -> NextActions:
    result = NextActions(case=number, stage="pre_institution", stage_label="Complaint filed, not yet instituted")
    filed = calendar.parse(fields.get("initiating_document_received_date") or case.get("date_initiated"))
    if filed:
        result.events.append(Event(date=_iso(filed), label="Complaint filed", basis="case data", kind="milestone"))
        result.events.append(Event(
            date=_iso(calendar.period_end(filed, 30)), label="Commission to decide whether to institute",
            basis="by rule", kind="decision", cite="19 CFR 210.10(a)(1)",
            note="Within 30 days of the complaint (35 when temporary relief is requested); "
                 "exceptional circumstances or a complainant's request can postpone it.",
        ))
    result.waiting_on = "The Commission's decision whether to institute an investigation"
    return result


# --------------------------------------------------------------------------
# The process


def path_for(data_dir: Path) -> Path:
    return Path(data_dir) / NEXT_ACTIONS_FILE


def load(data_dir: Path) -> dict[str, Any]:
    return load_json(path_for(data_dir), {})


def run(store: Store, *, log: Logger = print) -> dict[str, Any]:
    """Rebuild data/next_actions.json for every open investigation."""
    from .orders import schedule_events

    cases = {}
    for number, case in sorted(store.investigations.items()):
        documents = store.documents.get(number) or []
        built = build_case(case, documents, schedule=schedule_events(store.data_dir, documents))
        if built is not None:
            cases[number] = built.to_dict()
    out = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cases": cases}
    save_json(path_for(store.data_dir), out)
    stages: dict[str, int] = {}
    for built in cases.values():
        stages[built["stage_label"]] = stages.get(built["stage_label"], 0) + 1
    log(f"Next actions: {len(cases)} open case(s): " + ", ".join(f"{n} {s.lower()}" for s, n in sorted(stages.items())) + ".")
    return out
