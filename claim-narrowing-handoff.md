# Claim-narrowing feature: design handoff

Specification for building the claims analysis feature for USITC Section 337 investigations. Build
from this document. There is no existing implementation to preserve.

## Project context

- Each investigation record comes from the USITC IDS feed (`https://ids.usitc.gov/investigations.json`).
  Each record in the feed is its own record in the app, including separate phase records
  (Violation, Modification, Enforcement, Advisory).
- Filings come from the EDIS Data Web Service. EDIS returns XML, pages at 100 results, and needs an
  API token (which expires) to download attachments. Anonymous access still returns metadata for
  confidential filings. Only process public documents.
- The IDS numbering fields can disagree (one record had `Investigation Number` 337-1432 but
  `official_investigation_number` 337-1393). Validate against the full title before querying EDIS.

## Goal

For an investigation, track every asserted claim across six stages and show how the claim set
narrows:

1. Complaint (asserted)
2. Notice of institution (instituted)
3. To hearing (after withdrawals, settlements, summary determinations)
4. Final Initial Determination (findings per claim)
5. Commission final determination (affirmed or reversed)
6. Federal Circuit appeal

Track status per claim and per respondent, since terminations are often respondent-specific.

## User workflow

1. The user opens the list of 337 investigations.
2. The user opens a specific investigation record.
3. The record page shows the claims analysis control. Every 337 investigation record is eligible.

| Analysis built | New activity since build | Shows |
|---|---|---|
| No | n/a | "Create claims analysis" button, enabled |
| Yes | No | "Update claims analysis" button, disabled, with helper text "Up to date as of [build date]" |
| Yes | Yes | "Update claims analysis" button, enabled, with helper text "New activity since [build date]" |

Two additional states:

- **Building:** after the user clicks create or update, disable the button and show progress until
  the job finishes. Don't allow a second job for the same record while one is running.
- **Failed:** show the error and an enabled "Retry claims analysis" button. Keep the last successful
  analysis visible if one exists.

When an analysis exists, the record page shows a Claims tab (spec below). If a build finds no claim
information, the tab shows "No claim information found in the available documents" rather than an
empty matrix, and the record can still be updated later.

### Definitions

- **Source documents** are the EDIS documents for this record's investigation number and phase that
  the analysis reads:
  - the complaint, including amended complaints;
  - the notice of institution;
  - ALJ orders and initial determinations;
  - Commission notices, opinions, and final determinations.

  Put this definition in one configurable place (document type and title patterns), since it will
  need tuning.
- **Analysis built** means a stored analysis exists for the record. Store with it:
  - the build timestamp;
  - the set of EDIS document IDs processed;
  - a hash of the IDS record at build time;
  - the pipeline version.
- **New activity** means either of these since the last build:
  - public EDIS documents for the record whose IDs are not in the processed set;
  - a change in the IDS record (its hash differs from the stored one).

  Compute this from stored EDIS metadata and the IDS feed, refreshed by a periodic sync job, so the
  record page doesn't call EDIS on every load.

### Update behavior

"Update claims analysis" is incremental:

1. Extract events from new source documents only. New filings that aren't source documents need no
   model calls.
2. Merge the new events with the stored events.
3. Rerun the replay validator and corroboration check (below) over all events for the record.
4. Replace the stored analysis and update the build timestamp, processed document set, and IDS hash.

Corrections a user made to earlier events must survive an update.

## Cost tracking

Record the cost of every create, update, and retry run in a CSV file named `claims_costs.csv`.

- **Columns**, with a header row:
  - `investigation_number`: from the IDS record, e.g. 337-TA-1432;
  - `build_datetime`: when the run finished, as ISO 8601 in UTC, e.g. 2026-09-24T15:42:10Z;
  - `cost_usd`: total model cost of the run in US dollars, to six decimal places.
- **Append one row per run.** Create the file with the header if it doesn't exist. Never rewrite or
  reorder existing rows. Failed runs get a row too, since their tokens were still billed.
- **Compute cost from API usage, not estimates.** Sum `usage` from every model response in the run:
  `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens`.
  Multiply each by its per-model rate.
  - Keep the rates in a config table keyed by model ID, with a separate multiplier for Batch API
    requests. Don't hardcode prices in the pipeline code.
  - Check current rates on Anthropic's pricing page when setting up the table.
  - Include any Sonnet second-pass calls.
- **Make the file location configurable.**
- **Guard writes with a file lock** so concurrent builds can't interleave rows. On Windows, if the
  file sits in a synced folder such as OneDrive, retry briefly when the sync client holds the file.

## Pipeline

### 1. Rule-based extraction (no model)

- **Notice of institution:** parse the instituted patents and claims by rule. The notice is
  formulaic and authoritative. Federal Register notices are also available from the
  federalregister.gov API without an EDIS token.
- **Complaint:** locate the section that lists the asserted claims and send only that section to
  the model. Skip claim charts, which mention claims hundreds of times and add nothing.

### 2. Candidate sentences (regex, no model)

- Split each decisional document into sentences. Don't split on abbreviations such as "U.S.",
  "No.", "Inv.", "Fed. Cir.", "Inc.", or single capital initials.
- Find claim references with a pattern covering "claim 3", "claims 1-5, 8, and 12",
  "claims 1 through 4", and en or em dashes in ranges.
- Resolve the patent from the same sentence. Accept full numbers ("U.S. Patent No. 11,750,915") and
  short forms ("the '915 patent", with straight or curly apostrophes). Map both to the record's
  patent list from IDS. When the sentence doesn't say, leave the patent for the model to resolve.
- Prefilter by language:
  - Keep sentences with ruling language: "GRANTED", "is terminated", "finds", "determines",
    "ORDERED", "hereby".
  - Drop sentences with only argument language ("argues", "contends", "submits", "asserts that").
  - Drop sentences under headings that describe the parties' positions.
  - Make both keyword lists configurable.

### 3. Model extraction (Claude Haiku 4.5, `claude-haiku-4-5-20251001`)

- Send 15–25 candidate sentences from the same document per call. Give each sentence an ID and
  include the preceding sentence as context.
- Include the record's patent list and respondent list in each request.
- Force a tool call that returns a list of events. Each event has:
  - `sentence_id`;
  - `patent_number` (exactly as listed, or "unknown");
  - `claims_verbatim` (the claim list exactly as written);
  - `action`;
  - `speaker`;
  - `respondents` (names exactly as listed, or `["ALL"]`);
  - `quote` (shortest exact substring of the sentence supporting the event).
- One sentence can yield several events when it gives different outcomes for different claims,
  products, or respondents.
- Put the definitions below in a cached system prompt. Use the Batch API for historical backfill.

**Action values:**

| Value | Meaning |
|---|---|
| `asserted` | Complainant asserts infringement of the claims |
| `instituted` | Commission institutes the investigation as to the claims |
| `added` | Claims added, e.g. by amending the complaint and notice |
| `withdrawn` | Terminated because the complainant withdrew or moved to terminate them |
| `terminated_settlement` | Terminated because of a settlement, consent order, or license |
| `found_infringed` | ALJ or Commission finds the claims infringed or finds a violation on them |
| `found_not_infringed` | ALJ or Commission finds the claims not infringed |
| `found_invalid` | Claims found invalid or unpatentable, including on summary determination |
| `found_not_invalid` | Claims found not shown invalid |
| `technical_prong` | Ruling on whether domestic industry products practice the claims |
| `not_reviewed` | Commission declines to review an ID concerning the claims, so it becomes final |
| `mention_only` | Claims only mentioned, summarized, or argued about |

**Speaker values:**

| Value | Meaning |
|---|---|
| `tribunal_ruling` | A ruling, finding, order, or determination this document itself makes |
| `tribunal_recital` | A recap of earlier procedural history or an earlier ruling |
| `party_argument` | A party's or OUII's position, argument, allegation, or request |
| `other` | None of the above |

### 4. Validation (code)

- **Output checks.** Each event must pass all of these, or it is kept with status `needs_review`
  and does not change claim status:
  - the quote is a substring of its sentence;
  - the patent is in the record's list;
  - the claim list parses;
  - the respondents are on the record's list.
- **Replay validator.** Sort status-changing events by effective date and flag contradictions:
  - a claim found infringed after being terminated;
  - a finding for a claim that never went to hearing;
  - a claim reappearing without an `added` event.
- **Cross-document corroboration.** Commission notices restate the Final ID's outcome. Agreement
  marks both events `corroborated`. Disagreement sends both to review.
- **Optional second pass.** Flagged events may go to Claude Sonnet before human review. Make this a
  config flag, off by default.

## Rules

- **Claim numbers never come from a model.** Models return the claim list verbatim ("1-5, 8, and 12")
  and code expands it. Reject ranges that run backward or span more than 150 claims.
- **Only rulings change claim status.** In decisional documents, an event is status-changing only if
  its speaker is `tribunal_ruling`. In complaints, only `asserted` counts. Party and OUII arguments
  never change status, even when they describe the ruling they want.
- **Use effective dates.** An ALJ order terminating claims is an initial determination. It takes
  effect when the Commission declines review, so use the date of the Commission's non-review notice,
  not the ID's issue date.
- **Every event keeps provenance:** EDIS document ID, supporting quote, method, and any validation
  notes.

## Testing

- Unit-test sentence splitting, claim-reference matching, patent resolution, and range expansion on
  real passages, including "U.S. Patent No." inside a sentence and curly apostrophes.
- Hand-label 20–30 records: asserted, instituted, and final claim sets per patent and per respondent.
  Measure per-stage precision and recall, and use the results to tune the prefilter keyword lists and
  the source-document definition.

## UI: Claims tab

**Matrix.**
- Rows are claims grouped by patent, with an "indep." badge on independent claims. Each patent group
  header shows its number and a short count summary.
- Columns are the six stages above. Each column header shows the count of claims remaining at that
  stage: asserted, instituted, went to hearing, found infringed (Final ID), violation found
  (Commission), on appeal.
- Each cell is a status chip:
  - Asserted, Instituted, and In case: neutral grey
  - Withdrawn: dashed outline
  - Settled: solid outline
  - Infringed: solid navy (#1F4E8C)
  - Not infringed: pale blue
  - Invalid: orange (#B8541A)
  - On appeal: navy outline

  Statuses must differ in lightness, not only hue.
- A thin connector line joins a claim's chips while it stays in the case, so where the line ends
  shows when the claim left.
- An orange dot on a chip marks an event with `needs_review` status.
- For large complaints (50+ claims), collapse each patent to one summary row that expands on click.

**Controls.**
- A respondent filter: All respondents, or one respondent. The per-respondent view reflects
  respondent-specific terminations.
- A "Hide withdrawn claims" checkbox.

**Side panel.** Clicking a chip opens the claim's history as a timeline. Each event shows:
- the stage;
- a plain-language description;
- the source document (title and EDIS ID, linked);
- how the event was determined: "Parsed by rule", "Extracted by Haiku", "Derived from event
  history", "From IDS associated-litigation record", or "Needs review" with the reason.

The panel ends with "Open source document" and "Report a correction" buttons.

**Summary card (record overview).** One-sentence summary plus a patent-by-stage count table
(Asserted, Instituted, To hearing, Infringed at Final ID, Violation at Commission) and a link to the
Claims tab.
