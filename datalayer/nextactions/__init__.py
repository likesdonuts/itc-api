"""Next actions: what happens next in each open investigation.

    calendar.py   counting days as the Commission's rules do (19 CFR 201.14)
    build.py      each open case's stage, its dated events and what it is
                  waiting on, from the case data, the docket and the rules

Phase 1 uses no model and makes no network call: dates come from the IDS
case record (target date, scheduled final ID, hearings), from the docket
(when the final ID and the Commission's notices issued), and from the rules
of 19 CFR Part 210 applied to those. It writes data/next_actions.json,
rebuilt with counsel after every sync and fetch, like counsel.json.

Scope is the original violation phase; remand, enforcement, modification
and advisory proceedings run on their own schedules and are not covered.
"""
