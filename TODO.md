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

### Case summary (branch `summarization-phase-3`)

- [x] Phase 1: which documents a summary reads, page counts, cost preview,
      Summary tab, Section 337 primer draft.
- [x] Phase 2: complaint, notice and answer notes (Haiku, quotes checked),
      summary written by Sonnet 5 with page citations. Pilot: 337-1366,
      337-1384, 337-1417 ($0.52 spent of $20, failed attempt included).
- [x] Case history: every dispositive event from the titles, on the tab as
      "What happened", and checked into the summary (branch `summary-fixes`).
- [ ] Summaries written before the case history (337-1366, 337-1384,
      337-1417, 337-1270, 337-1500) show **Update summary**; each rewrite is
      only the writing call, about $0.05-0.10.
- [ ] **Read by section, not fixed page ranges.** Answers are read at their
      first 4 and last 20 pages, final IDs at their first 20 and last 15, so a
      long document can lose its middle. Find the "Affirmative Defenses" or
      "Conclusions of Law" heading (and a final ID's table of contents) in the
      free text layer and read those pages.
- [ ] **Check the summary against structured data:** IDS status
      ("Terminated") against "Where it stands"; give the writer the IDS party
      list so each company keeps one spelling.
- [ ] **Faster: notes in parallel,** about four documents at once (the
      budget check reserving for all of them). Roughly 3-4x less model time.
- [ ] **Faster: one shared page-text cache** for claims, Next actions and
      summaries, so each scanned page is OCR'd once; today the summary
      re-OCRs pages the claims analysis already read (no page boundaries in
      its cache).
- [ ] Case history on the Overview tab too (it needs no summary).
- [ ] Link case-history events with no PDF on disk to EDIS (needs EDIS's
      public document URL format).
- [ ] **Primer review.** A Section 337 practitioner reviews
      `content/section337_primer.md`, then sets `status: reviewed`,
      `reviewed_by` and `reviewed_on` at the top.
- [x] Phase 3: dispositive rulings, the final ID and the Commission's
      decisions; title facts and claims-analysis facts; updates when any of
      those change. Pilot: 5 cases, $1.43 of the $20 budget spent in all.
- [ ] **Federal Circuit appeals in the summary.** Not covered yet. Sources
      checked 2026-09-26:
      - *Which cases were appealed, and the appeal's status:* the IDS file we
        already download (`stages.lists.associated_litigation`, court
        "CAFC"): 128 appeals across 87 investigations, each with its number
        ("24-1300"), name, docketing date and status ("Pending", "Closed",
        "Remanded", "Pending (Consolidated Lead)"). Free; could become facts
        the writer cites ("appeal 25-1408 pending").
      - *The court's opinion:* CourtListener's API (Free Law Project), looked
        up by the IDS number (`court=cafc`, `docket_number=24-1300`); plain
        text, no OCR; free with an API token in `.env`. Verified: 337-1270 ->
        24-1300 -> *Crocs, Inc. v. ITC*, published opinion of 2026-01-08.
        Match on the number: case names are abbreviated ("Crocs, Inc. v.
        Itc"). Unchecked: whether it has Rule 36 one-line affirmances.
      - *Alternatives:* the Federal Circuit's own site (cafc.uscourts.gov,
        Opinions & Orders; PDFs, no API); PACER for the full appeal docket
        and briefs ($0.10 a page, $3 cap per document); **Bloomberg Law's
        MCP connector** for opinions and/or dockets. Bloomberg Law needs
        authorizing in claude.ai's connector settings, and an MCP connector
        is reachable from a Claude session, not from the app's own Python --
        so it suits looking cases up by hand or a Claude-run backfill, while
        the daily app would need Bloomberg's own API and licence.
      - Then: a decided appeal's opinion becomes one more document for notes
        (about $0.03 each); "Where it stands" says affirmed, reversed or
        remanded.
- [ ] Busy cases run long (337-1270: about 1,700 words). Consider a shorter
      summary with the detail left to the cited pages.
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
