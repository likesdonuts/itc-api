"""Process 4 -- the claims analysis: how an investigation's claim set narrows.

For one investigation it tracks every asserted claim through six stages --
complaint, institution, hearing, Final ID, Commission, Federal Circuit -- per
patent and per respondent, from the filings. It writes data/claims/<number>.json
and appends each run's model cost to data/claims_costs.csv, and nothing else.

Phase 1 (this) reads only what rules can: the instituted claims, from the
Commission's notice of institution in the Federal Register. The model-based
extraction, validation and full matrix come in later phases.

    config.py   claims_config.json: source documents, keyword lists, costs file
    text.py     sentences, claim references, claim-list expansion, patents
    fedreg.py   the notice of institution from federalregister.gov
    build.py    one build: events, the processed-document set, the IDS hash
    status.py   create / up to date / new activity / failed, for the page
    matrix.py   events -> one row per claim, one column per stage
    costs.py    the cost log, one locked append per run
"""

from __future__ import annotations

from . import build, config, costs, fedreg, matrix, status, text

__all__ = ["build", "config", "costs", "fedreg", "matrix", "status", "text"]
