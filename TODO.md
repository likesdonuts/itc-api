# To do and known bugs

What is left to do, in one place. Add an item when something is found or
deferred; delete it (or move it to "Done recently") once its PR merges.
Details live in README.md (how things work) and PRODUCT_OVERVIEW.md (what
users see).

*Last updated: 2026-09-26*

## Bugs

- **Old three-digit cases have other cases' filings in their document lists.**
  EDIS's document search matches investigation numbers by prefix, so
  337-112's list also holds the filings of 337-1120 to 337-1129: 2,582 of
  its 2,623 documents. About 60 cases are affected (three-digit numbers with
  filings after 2024 is the tell), and their counsel, analytics counts, Next
  actions and Summary tab inherit the stray filings.
  - Fix idea: after listing, keep only documents that EDIS tags with the
    exact investigation number (check what the listing returns per document),
    or query with the full "337-TA-112" form if EDIS matches that exactly. Then
    re-list the affected cases and rebuild counsel and analytics.
  - Where: `datalayer/client.py` (`list_documents`), `datalayer/docs.py`.

## In progress

### Case summary (branch `summarization-phase-1`)

- [x] Phase 1: which documents a summary reads, page counts, cost preview,
      Summary tab, Section 337 primer draft.
- [ ] **Primer review.** A Section 337 practitioner reviews
      `content/section337_primer.md`, then sets `status: reviewed`,
      `reviewed_by` and `reviewed_on` at the top.
- [ ] Phase 2: complaint and answer summaries. Haiku takes notes per document
      (with page references), Sonnet 5 writes the prose; notes cached per
      document; "Write summary" button; pilot on 337-1366, 337-1384, 337-1417
      and two more (about $1-2). Stop at the $20 cap and ask before going on.
- [ ] Phase 3: dispositive rulings, the final ID and the Commission's
      decisions; updates that read only new documents; the claims analysis's
      findings folded in.
- [ ] Decide whether supplements to the complaint should ever be read (today:
      listed, not read).

## Paused

### Claims analysis

- [ ] **Phase 4: the rest of the Claims tab.** The side-panel timeline per
      claim, connector lines between a claim's chips, "Report a correction",
      and the summary card on the Overview tab (see
      `claim-narrowing-handoff.md`, "UI: Claims tab").
- [ ] **Phase 5: evaluation and scale.** Hand-label 20-30 records and measure
      per-stage precision and recall; tune the prefilter keywords and source
      definitions; Batch API backfill beyond the three pilot cases.
- Known gap: 337-1417's '607 patent shows "no claims recorded" -- its
  asserted claims are in a scanned table OCR can't read.

## Data cleanup

- [ ] **Ambiguous firm, attorney and company names: 64 pairs waiting.**
      `python cli.py decide` lists them; `decide <n> same|different|renamed`
      records each answer in `analytics_reference.json`. Until then each pair
      stays as two entries (undercounts, never wrongly merges).

## Maintenance

- [ ] **EDIS token expires 2026-10-06.** Generate a new one at
      edis.usitc.gov -> profile -> API Token Generator and paste it into `.env`.
- [ ] Record the old daily sync's end-to-end time (the baseline run started
      17:12 UTC 2026-09-26) in PRODUCT_OVERVIEW.md next to the new timings in
      `data/daily_sync_log.csv`.

## Ideas, not scheduled

- Next actions for remand, enforcement and modification proceedings (today:
  the violation phase only).
- Making the app available to other users: shared data location and hosted
  UI (options explored, nothing decided).
