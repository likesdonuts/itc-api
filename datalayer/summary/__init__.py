"""The case summary: a plain-English account of an investigation for
practitioners new to Section 337, written on demand.

    config.py     summary_config.json: models, budget, what is read
    select.py     which documents a summary reads, by rule and for free
    pages.py      page counts, from the PDFs on disk or EDIS's attachment list
    estimate.py   what a summary would read and cost, before anything is paid
    primer.py     the hand-written "How Section 337 works" explainer

Phase 1 decides what would be read and what it would cost; no model is
called. The notes and the writing come in phase 2.
"""
