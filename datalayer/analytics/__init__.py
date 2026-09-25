"""Representation analytics: who represents whom, across every case.

Phase 1 is entity resolution -- turning the spellings in the filings into
firms, attorneys and companies that can be counted:

    names.py       normalizing and splitting names, deterministically
    cluster.py     merging spellings into entities, each merge with its reason
    firms.py       law firms (and the pro se, in-house and other filers)
    companies.py   parties, with former names and corporate families
    attorneys.py   attorneys, with the firms they were at and when
    review.py      the few pairs the rules cannot settle, asked of Claude Haiku
    build.py       the run: reads data/, writes data/analytics/
    report.py      the match-quality report

It reads counsel.json, the case records and the document lists, and writes
data/analytics/ and nothing else -- not even state.json, which the tracker
rewrites while its jobs run. Nothing else runs it: it is started by
hand (python cli.py analytics) or by the analytics app.
"""
