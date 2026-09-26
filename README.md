# ITC Section 337 investigation tracker

Tracks every USITC Section 337 investigation: it downloads the Commission's
own investigations file once a day, turns it into one record per
investigation, renders a static site from it, and -- for the cases you ask
about -- pulls document lists and public PDFs from the EDIS API.

Two sources, two jobs, no overlap:

| | Source | Owns |
| --- | --- | --- |
| **Case information** | `ids.usitc.gov/investigations.json`, daily | numbers, titles, stages, dates, parties, IP |
| **Documents** | EDIS API, on request | document lists and the PDFs under `data/documents/` |

The EDIS side cannot write a case record, so asking for documents never
overwrites investigation information or parties.

A third, offline process reads what those two wrote and works out **counsel**:
which firms and attorneys represent which parties (see
[Counsel](#counsel-who-represents-whom)).

## Layout

```
cli.py                one entry point for every command
ui_schema.json        which IDS fields the site shows, and what to call them
schema.py             loads and applies that mapping (used by both layers)
dates.py              what a date from each feed means, shared by both layers
server.py             local server so the page buttons can run the data layer
datalayer/            DATA LAYER - talks to IDS and EDIS, owns data/
  ids.py                the daily download and the snapshots on disk
  flatten.py            one IDS row -> plain named values
  cases.py              rows -> one record per investigation, with its stages
  ingest.py             process 1: rebuild every case from a snapshot
  docs.py               process 2: EDIS documents for named cases only
  backfill.py           process 2, once: document lists for every case, resumable
  counsel.py            process 3: who represents whom, from the filings
  nextactions/          each open case's stage, dates and rule-based deadlines
  analytics/            representation analytics: firms, attorneys, companies as entities
  client.py             HTTP client for the EDIS API and the IDS file
  store.py              reads/writes data/*.json, resolves case numbers
  config.py             paths, feed URLs, .env token loading
  normalize.py          offline maintenance: rewrite stored dates to ISO
ui/                   UI LAYER - reads data/ + ui_schema.json, owns site/
  templates.py          all HTML/CSS
  render.py             writes site/index.html and site/investigations/*.html
data/                 the handoff between the layers
  ids/                  dated snapshots of the IDS file (gitignored)
  investigations.json   one record per investigation  <- written by ingest
  documents_index/      one <number>.json per case: its documents  <- written by docs
  documents_state.json  when each case was last fetched, and which were backfilled
  counsel.json          firms and attorneys per case  <- written by counsel
  next_actions.json     each open case's next actions  <- written by next-actions (with counsel)
  analytics/            firm, attorney and company entities  <- written by analytics
  sync_log.csv          one row per sync, for watching the daily download
  documents/<number>/   downloaded PDFs (gitignored)
site/                 generated output
analytics_ui/         THE ANALYTICS APP'S UI - data/analytics/ -> site_analytics/ (bundle, page, render)
analytics_server.py   the analytics app's server (port 8766): Rebuild button, daily rebuild on opening
site_analytics/       generated analytics app
tests/                offline tests; no token, no network
```

The layers only meet at `data/`. The data layer never renders HTML, and the UI
layer never makes a network call or needs a token, so you can restyle the site
or re-map its fields as often as you like without re-fetching anything.

## Setup

```
pip install -r requirements.txt
python cli.py sync --render
```

That is enough for the whole site: the IDS file is public, so case information
needs no credentials.

For documents, put an EDIS API token in a `.env` file next to `cli.py`:

```
EDIS_TOKEN=<your token>
```

Tokens come from <https://edis.usitc.gov> → profile → API Token Generator, and
they expire; when one does, the documents command stops with an `AUTH ERROR`
telling you to generate a new one. Everything else keeps working without it.

## Using the app

Double-click **`ITC Tracker.bat`** (a desktop shortcut to it works too). It
starts the app and opens it in your browser; if the app is already running it
just opens another tab. Keep its window open while you work, and close it to
stop.

The panel at the top of the list page does the day's work:

- **Run daily sync** downloads today's case data, then re-lists the documents
  of every case whose documents you have collected before and downloads only
  their Notice of Appearance PDFs, then rebuilds attorneys and pages. If the
  IDS download fails (it is tried three times), the rest still runs on the
  case data already on disk, and the job ends with a warning saying so.
  Backfilled cases (below) are included only while they are open.
- **Backfill all cases** lists the documents of every case that has no list
  yet, without PDFs, so counsel covers the whole history. It is long (see
  [the backfill](#the-backfill-every-cases-document-list)); **Stop** ends it
  after the current case, and running it again continues where it stopped.
  The status line says how many cases are still without a list.
- **Tick cases** in the list, then **Fetch documents** (lists and downloads
  every PDF not on disk yet) or **Update lists** (lists only, no downloads).
- Each case's own page has the same **Fetch documents** / **Update list**
  buttons for that case.
- An open case's page has a **Next actions** tab: its stage, what comes next
  and when, every date on record, and what it is waiting on (see
  [Next actions](#next-actions)).

Its status line says whether today's sync has run, which day's case data is
loaded, when documents were last fetched, and when the EDIS token expires.

The list's **Docs** column counts the documents EDIS lists for each case, and
**With PDFs** how many of those have a PDF on this computer, so a case reads
as not fetched (0), listed only (44 / 0) or downloaded (44 / 42).
Confidential documents never have PDFs, so a fully downloaded case can still
be a few short.

Every case also shows when **its own** documents were last fetched: the
**Docs fetched** column on the list, and the status line and Documents heading
on its page, in green when that was today. Tick cases that were already
fetched today and the panel says so ("3 cases selected · 2 fetched today"),
so a case that is already current isn't fetched again by accident. Cases
fetched before the app kept these times show "unknown" until their next
fetch. A
job runs in the background with its progress shown on the page, one at a time,
and the page reloads onto the new data when it finishes. The token is read
when a job starts, so after pasting a new one into `.env` there is nothing to
restart.

### The analytics app

Double-click **`ITC Analytics.bat`** for the representation analytics, a
separate app from the tracker (its own window, on <http://127.0.0.1:8766>):

- **Leaderboards**: law firms and attorneys by the number of investigations
  they appeared in, with how many for complainants, for respondents, and
  still open.
- **Firms, Attorneys, Companies**: every one, and a page for each. A firm's
  page lists the companies it represented (with their role), the companies
  it opposed, its attorneys and its cases; an attorney's page adds the firms
  they were at and when; a company's page lists the firms and attorneys that
  represented it and every party it faced, in which direction.
- **Caseload over time** on each firm's and attorney's page: bars for each
  year, counting the cases it was working on then (from its first filing in
  a case to its last), split into cases since closed and cases still open.
- **Co-counsel**: on a firm's page, the firms that appeared on the same side
  of its cases; the Co-counsel page ranks every pair.
- **Companies** leads with the frequent fliers: the companies most often sued
  (repeat respondents) and most often suing (repeat complainants). A
  company's page adds its litigation history by year, as complainant and as
  respondent, including cases filed under former names.
- **Two-way disputes**: pairs of companies where each has been the
  complainant against the other; the same list for one company on its page.
- **Families**: tick "Group companies by family" to count related companies
  together ("samsung" for Samsung Electronics, Samsung Display, ...). Each
  family has a page. It is a grouping by the name's leading brand word, not
  a statement of ownership; `analytics_reference.json` `companies.families`
  can pin a company to a family by hand.
- **Search** (top right) finds firms, attorneys and companies by any spelling,
  former name or trade name.
- **Filters** on every view: which side (complainants, respondents,
  non-parties), which years (by the year the investigation started), law
  firms only (leaving out companies filing for themselves, pro se
  individuals and the like), and grouping by family. They are remembered in
  the browser.
- **Review** lists the name pairs waiting for you (`python cli.py decide`).

It rebuilds its data the first time it is opened each day, and when its
**Rebuild** button is pressed -- never because the tracker synced or fetched
something. The status line says when it was built and whether the tracker's
counsel data has changed since. Case numbers are shown, not linked: the two
apps stay separate.

Everything below is what those buttons run, for when you want a step on its
own from the command line.

## Commands

| Command | Network | What it does |
| --- | --- | --- |
| `python cli.py sync` | IDS | Process 1. Downloads today's IDS file if it isn't stored yet and rebuilds every case record from it. |
| `python cli.py parse` | none | Rebuilds the case records from the newest stored snapshot. |
| `python cli.py docs 337-1478` | EDIS | Process 2. Fetches document lists and PDFs for the numbers you name. |
| `python cli.py docs --existing` | EDIS | Process 2 over every case you have already fetched documents for. |
| `python cli.py docs 337-1478 --appearances` | EDIS | Lists every document but downloads only the Notice of Appearance PDFs. |
| `python cli.py backfill` | EDIS | Lists the documents (no PDFs) of every case that has no list yet, newest first; resumable. Refuses while the app is open (use its button). |
| `python cli.py claims 337-1366` | Federal Register | Builds or updates the claims analysis for the investigations you name (also the **Create / Update claims analysis** button on a case page). |
| `python cli.py counsel` | none | Process 3. Rebuilds who represents whom from the documents on disk. Runs by itself after `sync`, `parse`, `docs` and `refresh`. |
| `python cli.py next-actions` | none | Rebuilds each open case's next actions (`data/next_actions.json`). Runs by itself with counsel, after every sync and fetch. |
| `python cli.py analytics` | Anthropic (a few cents) | Rebuilds the representation analytics entities in `data/analytics/` and the analytics app's data. Only ever run by hand or by the analytics app; `--no-review` makes no model calls. |
| `python cli.py analytics-serve` | localhost | Opens the analytics app (what `ITC Analytics.bat` runs) on port 8766. |
| `python cli.py decide` | none | Lists the analytics name pairs that need a person; `decide 3 same` records an answer in `analytics_reference.json` and rebuilds. |
| `python cli.py render` | none | UI layer. Rebuilds `site/` from `data/` and `ui_schema.json`. |
| `python cli.py serve` | localhost | Opens the app (what `ITC Tracker.bat` runs): the site plus its buttons. |
| `python cli.py fields` | none | Lists every field name `ui_schema.json` can use, with samples. |
| `python cli.py status` | none | Which snapshot is current, which cases have documents, and the last few syncs. |
| `python cli.py normalize` | none | Rewrites stored document dates to ISO 8601 in place. |
| `python cli.py refresh` | IDS + EDIS | The daily job: sync, re-fetch documents already on disk, render. A failed IDS download does not stop the documents; the command then exits with 1. |

Besides `ITC Tracker.bat` and `ITC Analytics.bat`, Windows users can double-click `sync.bat`,
`docs.bat` (it prompts for numbers), `render.bat`, `serve.bat`, or `run.bat`
(full refresh). `discover` and `update` still work as the old names for `sync`
and `docs`.

### Process 1: the daily IDS sync

```
python cli.py sync                  # download today's file, rebuild the cases
python cli.py sync --render         # ...and rebuild the site
python cli.py sync --force          # download again even if today's is stored
python cli.py sync --keep 90        # keep 90 days of snapshots instead of 30
python cli.py parse                 # rebuild from the newest snapshot, offline
```

The download is stored as
`data/ids/investigations-2026-09-23T134502Z.json.gz` -- the moment it arrived,
UTC -- and is never rewritten, so every copy you keep is one you can go back
to and a `--force` download cannot overwrite the morning's. Run again on the
same day without `--force` and it reuses the copy rather than re-downloading
37 MB. `--keep` counts days, not files, so taking a second copy never pushes
an older day off the end. A download that fails, or comes back as something
other than the complete feed, is tried again after 10 and then 30 seconds.

`data/investigations.json` is then rebuilt from the snapshot in full. That is
deliberate: when the Commission renumbers or retitles something, the rebuilt
file reflects it instead of accumulating stale entries. The run reports what
appeared and what disappeared since last time.

To have it happen daily on Windows, point Task Scheduler at `sync.bat`, or:

```
schtasks /create /tn "ITC 337 sync" /tr "\"%CD%\sync.bat\"" /sc daily /st 07:00
```

#### Watching the daily download (`data/sync_log.csv`)

Every sync appends a row, whether it downloaded, reused today's copy, or
re-parsed offline. Open it in a spreadsheet and an anomaly shows up as a
number that moved when it shouldn't have:

| Column | What it should look like |
| --- | --- |
| `run_at`, `mode`, `outcome` | when, `download`/`cached`/`offline`, `ok`/`refused`/`failed` (`failed`: the download failed on every try; the reason is in `note`) |
| `snapshot`, `snapshot_taken_at`, `snapshot_bytes` | which file was read, and its size -- a download that came back short shows up here first |
| `feed_date` | the Commission's own timestamp inside the file. It should advance each day; the same value twice means you re-read the same data |
| `rows_total`, `rows_337` | rows in the file and how many were Section 337. Both should barely move day to day |
| `cases_in_file`, `stages_in_file` | after grouping the rows by investigation number |
| `cases_added` | new investigation numbers. A handful at most |
| `cases_changed`, `status_changes` | cases the file actually changed, and how many of those were a status. Ignoring the sync timestamps, so a day with nothing new reads as 0 -- not everything |
| `cases_left_feed`, `cases_withdrawn_total` | dropped this run, and carried in total |
| `cases_renumbered` | dockets instituted under a new number |
| `cases_on_site` | what the site will hold, cases in the file plus withdrawn |
| `seconds`, `note` | how long it took, and why a run was refused |

`python cli.py status` prints the last five rows. A refused snapshot is logged
too, with `outcome` as `refused` and the reason in `note`, so the guard below
leaves a record rather than a gap.

#### When a case stops appearing in the feed

Rebuilding in full would otherwise mean a case the feed drops loses its page.
It doesn't: the case is kept with whatever the last snapshot that listed it
said, its page carries a notice giving that date, and its row on the list page
is flagged and grouped under "No longer in the IDS feed" so the status filter
finds all of them at once. Nothing about it changes again until the feed lists
it, at which point it is rebuilt from the feed like any other and the mark
goes. Documents are untouched throughout — they are on the EDIS side.

Because cases in the IDS file are historical and effectively never leave, a
snapshot that drops more than 2% of the cases on disk (and more than ten of
them) is far likelier to be an incomplete download than a real withdrawal.
Sync refuses such a snapshot and changes nothing rather than marking a
thousand pages withdrawn:

```
IDS ERROR: snapshot 2026-09-25 lists 156 of the 1381 cases on disk, dropping
1225 (89%). The IDS feed is a historical file that should never lose that many
in a day, so this one is most likely incomplete and nothing has been changed.
Check the download, or re-run with --allow-removals if the withdrawals are real.
```

### Stages: why one investigation can have several records

IDS files a row per *stage*, not per investigation. 337-1478 appears once as
its "Violation" phase, again as a "Remand", again as a "Bond Return", each with
its own internal ID, dates, participants and status. Of 1680 Section 337 rows
in the feed, 1382 are distinct investigation numbers, and 171 of those numbers
have between two and eight rows.

Keying the feed by investigation number (what this app used to do) silently
kept one row and dropped the rest. Instead, a case here is **one record that
carries all of its stages**:

- the **primary** stage is the Violation phase -- the investigation proper.
  It is what the Investigation Information and Parties sections describe.
- the **current** stage is whichever started most recently. The case status and
  phase come from it, so a case in remand reads as being in remand.
- every stage is listed in a **Stages** table on the case page, each row
  linking to that stage's own details further down the page. A case with a
  single stage shows no table -- the sections above already are that stage.

A field in the schema can ask for the current stage instead of the primary one
with `"stage": "current"`.

### Process 2: documents from EDIS

```
python cli.py docs 337-1478
python cli.py docs 337-TA-1478 337-3936 --render
python cli.py docs 337-1478 --no-attachments     # metadata only, no PDFs
python cli.py docs 337-1478 --appearances        # only Notice of Appearance PDFs
python cli.py docs --existing                    # refresh what you already have
```

A document that isn't downloaded on a run keeps the links to any of its PDFs
already on disk, so a metadata-only refresh never orphans files you fetched
earlier.

Only the numbers you pass are requested. Numbers are matched loosely, so
`337-TA-1478`, `337-1478` and `1478` all reach the same case. This process
writes `data/documents_index/`, `data/documents_state.json` and files under
`data/documents/` -- nothing else. It has no route to the case writer, and a
test holds it to that.

The document lists are stored one file per case (`data/documents_index/337-1478.json`).
A single file would pass GitHub's 100 MB limit once every case is listed,
and a save rewrites only the cases whose list changed, so a daily sync's git
diff is the few open cases it touched. A leftover `data/documents_index.json`
from before is read and converted on the next save.

EDIS returns a case's documents 20 to a page; the client reads up to 1,000
pages. (It used to stop at 50 pages, which cut 337-TA-395 off at exactly
1,000 documents -- "Update list" on it once fetches the rest.)

#### The backfill: every case's document list

```
python cli.py backfill                 # all cases without a list, newest first
python cli.py backfill --limit 100     # just the 100 newest
python cli.py backfill --retry-empty   # ask again about cases EDIS had nothing for
```

Counsel, and the representation analytics built on it, can only cover cases
whose document list is on disk. The backfill lists the rest: metadata only,
no PDFs, roughly one EDIS request per 20 filings -- over a thousand cases,
about 12,000-15,000 requests, a few hours with its half-second pause between
cases. So it is built to be interrupted:

- newest cases first, so a partial run already covers the ones that matter
- progress saved every 25 cases, so Stop, a closed window or an expired token
  loses at most that many
- a case already listed is skipped, so running it again continues; a case
  EDIS has nothing for (many pre-EDIS cases) is recorded as `edis_empty` and
  not asked again unless `--retry-empty`

Backfilled cases are marked `"backfill": true` in `documents_state.json`. The
daily sync refreshes a backfilled case only while its status is open (Active,
Pending before the ALJ or the Commission, Pre-institution), so it stays at the
cases that can still get filings instead of growing to every case on file.
Fetching or updating a case by hand clears the mark: it becomes one of yours
and is refreshed daily like any other. `docs --existing` and `--all` are
refreshes and keep the mark.

Run it from the app's button: the app runs one job at a time, so nothing else
writes the document lists meanwhile. The command refuses while the app is
open for that reason.

### Counsel: who represents whom

```
python cli.py counsel                  # rebuild data/counsel.json, offline
python cli.py counsel --render         # ...and the site
python cli.py counsel --verbose        # ...listing each non-party and where its reason stands
python cli.py docs --existing --appearances   # fetch the PDFs that name whole teams
```

IDS lists the parties and nothing about their lawyers. EDIS lists every
filing with who filed it, for whom and from which firm. The counsel process
reads the documents index, matches each filing's "on behalf of" text to the
case's IDS parties, and records **representations**: one firm acting for a
set of parties in one case. So several complainants can share a firm, two
respondents can have different ones, and one party can have two.

It reads three things, each more detailed than the last:

| Source | Gives | Needs |
| --- | --- | --- |
| Every filing's metadata | the firm, its parties, the filing attorney | the document list |
| Notice of Appearance titles | the firm(s), the parties, the lead counsel; supplemental notices add attorneys, withdrawals remove them | the document list |
| Notice of Appearance PDFs | the whole team from the signature block, and the service email | `docs --appearances` |

A few rules keep the picture honest:

- **A notice of appearance says who a firm acts for.** Where a firm has filed
  one, its parties come from its notices. Otherwise they come from its filings.
- **Joint filings are skipped.** A stipulation filed by the complainant's firm
  "on behalf of" every party names both sides, so it is left out rather than
  making that firm counsel for the respondents.
- **The Commission's own filings and non-party comments are not counsel.**
  Orders, OUII designations, and comments from firms that never act for a
  party are left out.
- **Near-duplicates fold together.** "Fabricant Rubino Lambrianakos LLP" and
  "Fabricant, Rubino & Lambrianakos LLP" are one firm, "Bas de Blanc" and
  "Bas de Blank" are one attorney. A "firm" whose only attorneys all belong to
  a firm that appeared (a filing vendor, a misspelling) folds into that firm.

**Non-parties** -- companies or people subpoenaed into a case -- are never in
the IDS feed, but their own filings name them: "Notice of Limited Appearance
of Cooley LLP on Behalf of **Non-Party** Apple, Inc.", "**Non-Party** ABC
Coke's Unopposed Motion ...". Only filings a non-party makes itself count;
an order granting its motion, or a party's response to it, merely mentions
it. Each non-party is then matched like any party, so its counsel is found
the same way, and the reason it is in the case is read from its notice of
limited appearance ("for the limited purpose of responding to the subpoena
... served on September 11, 2026, by Respondents ..."), which gives who
served the subpoena and when. A non-party with no notice gets its reason
from its own filings (a motion to quash, public-interest comments). They are
listed under **Non-Party(s)** on the case page, with the notice's sentence
and their filings under **Details**, and the list page search finds them.

Notices that skip the "limited purpose" wording are read too ("... who has
been served with a Subpoena Duces Tecum ...", "... to address issues related
to a Subpoena ... issued on behalf of Respondents ..."), as are proposed
intervenors. A scanned notice with no text layer is OCR'd locally, the same
way as the claims analysis, and the text is cached in `data/counsel_text/`.

Names are tidied as they are read: a joint filing ("MediaTek Inc. and
MediaTek USA Inc.") becomes one non-party per name when every piece is a
name by itself, so "Alliance of U.S. Startups and Inventors for Jobs" and
"President and Fellows of Harvard College" stay whole. A leading "Non-Party"
or "Dr." comes off, redacted names ("[ ]") are skipped, and one company under
two corporate forms in a case ("Google Inc." / "Google LLC") is one non-party,
shown under its most common spelling.

The run sums the non-parties up in one line: how many have their reason read,
how many have a notice whose PDF is not downloaded yet (Run daily sync, or
`docs --existing --appearances`, downloads them), how many notices give no
reason, and how many never filed one. `--verbose` lists each.

Party names are matched loosely, so accents, punctuation and small typos
("Samsung Electronic Co., Ltd.") still match, but one company cannot pass for
its sister ("Samsung Electronics America"). A firm whose parties match no IDS
party is still shown, under "Other counsel of record", and the run lists it
so you can check it.

On the case page, the **Parties and Counsel** section lists each side's
parties grouped by the firms representing them. Parties with exactly the same
firms share a block. Lead counsel is marked, withdrawn attorneys are struck
through, and a long team folds after the first six names. The list page
search also finds cases by firm or attorney.

The IDS participant ID (the same for one company in every case) is kept on
each party, for matching across cases later.

### Representation analytics (phase 1: entities)

```
python cli.py analytics                # rebuild data/analytics/, asking the model about borderline pairs
python cli.py analytics --no-review    # rules only; no model calls
```

The aggregate views (firm and attorney leaderboards, who opposed whom,
co-counsel, company litigation histories) need every spelling of a firm,
attorney or company to count as one. Phase 1 builds those entities; the
separate analytics app (phase 2) will show them. **Nothing else runs it**:
not the daily sync, not document fetches, not counsel. It reads
`counsel.json`, the case records and the document lists, and writes only
`data/analytics/`:

| File | What |
| --- | --- |
| `firms.json` | each firm: display name, `kind`, every spelling with its filing count, cases, predecessor/successor links |
| `attorneys.json` | each attorney: spellings, the firms they were at with first/last dates and cases, cases as lead |
| `companies.json` | each company: spellings, former names, trade names, IDS participant ids, family, cases with roles |
| `representations.json` | per case and representation: firm ids, attorney ids, the companies acted for with their roles, dates |
| `needs_review.json` | pairs neither the rules nor the model settled, with the model's answer if any |
| `report.md` | the match-quality report: counts, merges by rule with examples, the largest entities with what was folded in, non-law-firm filers, what still needs a person |
| `meta.json` | when it was built, and the counts (its own record: it never writes `state.json`, which the tracker rewrites while its jobs run) |
| `review_decisions.json` | the model's answers (tracked in git: each was paid for) |

Ids are the display name's normalized key (`firm:kirkland-ellis`,
`atty:s-alex-lasher`, `co:apple-inc`), so they read well and stay put.

How names become entities (`datalayer/analytics/`), deterministic first:

- **Split before anything else** (`names.split_firm_field`). `;` and ` / `
  always separate firms. A legal suffix followed by more text ends one firm
  where the rest is itself a firm ("Winston Taylor LLP, DLA Piper LLP (US),
  ..., and WilmerHale"), so commas inside one name ("Finnegan, Henderson,
  Farabow, Garrett & Dunner, LLP") never split it. Old filings that run
  firms together with no separator ("fenwick and west finnegan henderson ...
  morrison and foerster") are split only when every word is covered by firms
  seen elsewhere. Anything else that looks like several firms ("et al", two
  suffixes) goes to review instead of being guessed.
- **Normalize** (`names.firm_core`, `company_form`, `parse_person`): case,
  accents, punctuation, "&"/"and", "The"/"Law Offices of", "(DC)", legal
  forms. Companies keep their corporate form as part of the key: a company's
  "Inc." and "LLC" are different legal entities.
- **Merge, with a recorded reason** (`cluster.py`, a union-find): typos
  (rapidfuzz ratio >= 90 *and* every differing word >= 85 similar to its
  counterpart, so "Shenzhen Carku Technology" never merges with "Shenzhen
  Yark Technology"; a differing place or number never merges); firm short
  forms ("Pillsbury Winthrop"), only when two or more words shorter and one
  firm has that start; wrapped-signature fragments ("Nickel, PC" alongside
  Foster, Murphy, Altman & Nickel in every case); company former names
  (f/k/a, n/k/a -- but not d/b/a, which Sam's East and Sam's West share);
  a company name with no form when only one form exists; attorneys with
  compatible names at one firm, or across firms when no clashing name exists.
- **Reference list** (`analytics_reference.json`, tracked, edit by hand):
  firm aliases, predecessor firms (linked, never merged -- Troutman Pepper
  Hamilton Sanders -> Troutman Pepper Locke), forced kinds, and merge /
  keep-apart pairs for firms, companies and attorneys. It wins over every
  rule and over the model.
- **Kind** (`firms._kind`): law firm, self-represented (a company or person
  filing for itself, or its in-house counsel), organization, government, or
  company. Only law firms belong on the law-firm leaderboards.
- **Review** (`review.py`): pairs the rules put in between -- borderline
  similarity, a word added, a short form several firms share, a firm field
  that cannot be split cleanly, a compatible attorney pair while a clashing
  name exists -- are sent to Claude Haiku in batches of 25 with context
  (spellings, cases, years, attorneys, roles). An answer is applied only when
  the model is sure (0.85; 0.95 for "same" on companies whose names differ
  by a word, which is more often a subsidiary than a typo), cached by the
  pair's id so it is never paid for twice, and anything else is left
  unmerged and listed in `needs_review.json` for a person to settle in the
  reference file. Changing the prompt (`PROMPT_VERSION`) asks again. Costs
  go to `data/claims_costs.csv` as `analytics-review` rows and count against
  the same `budget_usd` as the claims analysis. The first full review of
  the 185 cases then on file was 47 pairs for $0.034.

#### Settling the pairs that need a person (`python cli.py decide`)

```
python cli.py decide              # the pairs waiting, numbered, with the model's view
python cli.py decide 3            # one pair in full: spellings, cases, roles, firms, years, the model's reasoning
python cli.py decide 3 same       # record the answer and rebuild (about two seconds, no model calls)
```

Until a pair is decided its two names stay separate, which can only
undercount, never merge two real entities. The answers
(`datalayer/analytics/decide.py`) are written into `analytics_reference.json`,
where they can be read, edited or undone by hand:

| Answer | For | Written to | Effect |
| --- | --- | --- | --- |
| `same` | any pair | `<kind>.merge` | one entity |
| `different` | any pair | `<kind>.keep_apart` | two entities, and the pair leaves the list |
| `a-became-b` / `b-became-a` | firms | `firms.predecessors` (keyed by the newer name) | two firms, linked as predecessor and successor |
| `split "A LLP" "B PC"` | a firm field | `firms.splits` | the field counts as those firms |
| `one` | a firm field | `firms.splits` | the field is a single firm |

Names are written as filed (the pair's first spelling), and normalize to the
same key the pair was found by. A rebuild renumbers the list, so run
`decide` again after each answer. Commit `analytics_reference.json` with the
next PR so the answers are kept.

Coverage follows `counsel.json`: firms and attorneys exist only for cases
with a document list, which is what the backfill is for; companies come
from the IDS records of every case.

### Next actions

```
python cli.py next-actions --render    # rebuild data/next_actions.json and the pages, offline
```

What happens next in each open investigation, on a **Next actions** tab of its
page. Phase 1 uses no model and no network (`datalayer/nextactions/`); every
date carries its basis:

| Basis | Where the date comes from |
| --- | --- |
| case data | the IDS record's current stage: target date, scheduled final initial determination, Markman and evidentiary hearings (about 60 of the open cases have them) |
| docket | when the final ID and the Commission's notices issued (titles as EDIS lists them; Federal Register reprints ignored) |
| by rule | 19 CFR Part 210 applied to those dates, with the citation |

The rules, as checked against the regulation text (September 2026):

| Rule | Deadline |
| --- | --- |
| 210.10(a)(1) | institution decided within 30 days of the complaint (35 with temporary relief; can be postponed) |
| 210.51(a) | ALJ sets the target date within 45 days of institution; the target date is for completion of the investigation |
| 210.42(a)(1)(i) | final ID no later than 4 months before the target date (moved back to a business day) |
| 210.43(a)(1), (c) | petitions for review 12 days after service of the final ID; responses 8 days after a petition |
| 210.42(h)(2) | the final ID becomes the Commission's determination 60 days after service unless review is ordered |
| 210.49(d) | Presidential review: 60 days from delivery of the Commission's action |

Days are counted as 19 CFR 201.14(a) says: from the first business day after
the event, the last day moved to the next business day when it is a weekend
or federal holiday (5 U.S.C. 6103, with weekend observance), and periods
under 7 days counted in business days (`nextactions/calendar.py`). Periods
run from a document's EDIS date, its electronic service; extra days for
mail service are not modeled.

The stage follows the docket: before the ALJ until a final ID issues; then
the review deadlines, replaced by an undated entry once the Commission
extends the review date; then "decided to review" or "not to review" (a
final ID of no violation left unreviewed concludes it); then a final
determination, with a remedy starting Presidential review. A stay order
without a later order lifting it adds a note.

Two kinds of open case have nothing to show, and say why. The USITC lists
about 90 investigations from the 1970s to the 2000s as "Active" with no
dates at all (typically their remedial orders are still in force): "No
schedule on record". Any case whose latest date is over two years old with
nothing ahead: "No recent activity". Remand, enforcement, modification and
advisory proceedings are out of scope for now and say so.

The page picks the *next* event, and how many days away each one is,
against the day it is viewed, so the tab stays right between syncs; with
nothing dated ahead it shows "Awaiting decision" and what the case is waiting
on. `next_actions.json` is rebuilt with counsel after every sync and fetch.

Phase 2 will add the dates in the ALJs' procedural schedules (discovery,
expert reports, briefs), read from the scheduling orders with Claude Haiku
under its own $20 budget.

### Claims analysis

```
python cli.py claims 337-1366 337-TA-1384 --render
```

Tracks every asserted claim of an investigation through six stages --
complaint, institution, hearing, Final ID, Commission, Federal Circuit -- per
patent and per respondent. The design is in `claim-narrowing-handoff.md`; it
is being built in phases. A build runs:

1. **By rule, free:** the instituted claims, from the notice of institution.
2. **By rule, free:** the source documents' PDFs are downloaded from EDIS if
   missing (with the EDIS token), their text read -- scanned pages, common in
   public Final IDs, with local OCR -- and candidate sentences picked: ones
   that name claims and use ruling language (in the complaint, assertion
   language). Claim charts and exhibits, and sections on the parties'
   positions, are skipped. The keyword lists are in `claims_config.json`.
3. **By model:** 15-25 candidate sentences at a time go to Claude Haiku 4.5,
   forced to answer with a tool listing each event: sentence, patent, claim
   list as written, action, speaker, respondents, and a supporting quote. The
   system prompt (definitions and worked examples) is cached.
4. **By code:** every event is checked -- quote in the sentence, claim list
   in the sentence and expandable, patent and respondents on the record's
   lists -- and one that fails is kept as `needs_review`, changing nothing.
   Only rulings change a claim's status.

5. **By rule, free:** rulings that name no claims -- the Commission's
   case-wide "no violation" / "a violation", terminations "as to" named
   respondents (settlement or withdrawal), findings of default, and relief
   against defaulting respondents under section 337(g)(1) -- are read from the
   documents and applied to every claim still in the case, for those
   respondents ("Derived from event history").

6. **By rule, free: effective dates.** An ALJ's initial determination takes
   effect when the Commission declines to review it, so its events are dated
   by that notice: the ID opens "ORDER NO. 12: ...", and the Commission's
   notice says it "has determined not to review an initial determination
   ... (Order No. 12)". A Final ID's findings are dated by its issue -- the
   earliest of its versions in the documents index, confidential ones
   included -- not by its public version, often filed weeks later.
7. **By code, free: checks across all the events.** The *replay validator*
   replays each claim's rulings in effective-date order and flags a finding
   after the claim was terminated, a finding for a claim never instituted,
   and a claim coming back without an `added` event. *Corroboration* matches
   the Final ID's findings with the Commission notices that restate them:
   agreement marks both "Corroborated"; a violation against no violation
   sends both to review. A flagged event changes nothing until a person
   looks at it.
8. **Optional, paid: a second pass.** With `"second_pass": true` in
   `claims_config.json` (off by default), events that failed an output check
   go to Claude Sonnet 5 with the same sentence, and its reading replaces
   them only if it passes the same checks. Each is tried once.

An update reads only source documents it has not read before, keeping the
earlier events (and any corrections) without paying for them again, and
re-runs every rule and check over all the events. `--reread` reads every
source document again (and pays for it again) when the way documents are read
has changed.

On the Claims tab, **Respondent** switches between all respondents and each
one: a settlement or default takes that respondent's claims out as of its
date, and where respondents part ways the all-respondents view says "Varies
by respondent". **Hide withdrawn claims** does what it says. A Commission
notice declining to review an ID dates that ID's terminations, per the spec's
effective-date rule.

Phase 1 laid the foundations:

- **Instituted claims** are read by rule from the Commission's notice of
  institution, fetched from federalregister.gov (no token). A notice is only
  used when its text names this investigation's number and its title shares
  its subject, since IDS's numbering fields can disagree.
- **The text tools** later phases build on: sentence splitting that keeps
  "U.S.", "No.", "Inv.", "Fed. Cir." and "Dec. 1" intact; claim references
  ("claims 1-5, 8, and 12", "1 through 4", en and em dashes); patents, full
  ("U.S. Patent No. 8,350,294") or short ("the ’294 patent"), mapped to the
  record's IDS patent list. Claim numbers only ever come from code, which
  refuses ranges that run backward or span more than 150 claims.
- **Source documents** -- complaint, notice of institution, ALJ orders and
  IDs, Commission notices, opinions and determinations -- are defined in
  `claims_config.json` by document type and title pattern, public only.
  Parties' filings are never sources, whatever their titles say.

An analysis is stored in `data/claims/<number>.json` with its build time, every
public document ID it saw, a fingerprint of the IDS record, and the pipeline
version. The case page compares those with what is on disk to show **Create**,
**Update** (new documents or an IDS change since the build), **Up to date as
of ...** (disabled), or **Retry** after a failure, which keeps the last good
analysis. Once built, the page gains a **Claims** tab: the claims-by-stage
matrix and, for each status, the event behind it with its source and quote.

Every build appends a row to `data/claims_costs.csv`
(`investigation_number,build_datetime,cost_usd`), failed ones too, under a
file lock. The cost is computed from each model response's `usage` --
uncached input, cache writes, cache reads and output, each at its own rate
from the price table in `claims_config.json` (with a multiplier for Batch API
requests) -- not estimated. Before each model call, the most that call could
cost is checked against `budget_usd` less everything already in the log, and
the build stops, keeping what it has, rather than go over.

Model calls need `ANTHROPIC_API_KEY` in `.env`, and
`ANTHROPIC_WORKSPACE_ID` too if the key is not scoped to a workspace.

### The field mapping (`ui_schema.json`)

The IDS file carries far more about an investigation than a page should show,
so the site does not name IDS fields in code. `ui_schema.json` lists the
sections, the labels and the field names:

```json
{ "label": "Target Date", "source": "target_date", "type": "date" }
{ "label": "Complainant(s)", "source": "participants",
  "where": { "role": "Complainant" }, "item": "name", "type": "list" }
```

A section's `kind` is `fields` (a grid of the fields below), `parties` (the
parties grouped with their counsel, with `roles` as `{label, role}` pairs),
`stages` or `documents`.

- `type` -- `text`, `mono`, `long_text`, `date`, `datetime` (a time the app
  recorded, shown in local time), `bool`, `number`, `list`, `status` or
  `case_link`
- `stage` -- `primary` (default) or `current`
- `where` -- keep only list items matching these values
- `item` -- which part of a list item to show (default `label`)
- `limit` -- show at most this many list items

`source` is looked up as a case field, then a stage field, then a stage list.
Field names are derived from IDS's own labels, so anything in the feed is
reachable: "Hearing/Conf Start Date" is `hearing_conf_start_date`,
"F.R. Citation for Notice of Institution" is
`fr_citation_for_notice_of_institution`. To see them all, with how many cases
have them and a sample value:

```
python cli.py fields
```

Edit the file, run `python cli.py render`, refresh the browser. Fields with
nothing in them are left off the page, and a `source` that no case can answer
is reported when rendering rather than silently showing blank.

### When a docket becomes an investigation number

The IDS file lists pre-institution dockets too (as `337-3936`, status
"Pre-institution"), so every case on the site comes from that one file. Once a
complaint is instituted, IDS lists it under a real number that keeps the docket
as a field (`337-1521`, docket `3936`). The sync notices that, and moves the
documents and PDFs already downloaded for `337-3936` onto the new number.

### The app's server (`server.py`)

`ITC Tracker.bat` runs `python cli.py serve`, which serves the site on
<http://127.0.0.1:8765> and runs the page's buttons as background jobs:

| Endpoint | What it does |
| --- | --- |
| `POST /api/jobs` `{"kind": "daily"}` | sync, then documents (appearance PDFs only) for cases already collected and open backfilled cases, then counsel and render |
| `POST /api/jobs` `{"kind": "documents", "numbers": [...], "download": true}` | the documents process for those cases, with or without PDFs, then counsel and render |
| `POST /api/jobs` `{"kind": "backfill"}` | the backfill, then counsel and render; stoppable |
| `POST /api/jobs/stop` | ask the running job to stop after its current step; only the backfill can be (409 otherwise) |
| `GET /api/jobs/current` | the running or last job: its state, its outcome, and the tail of its log |
| `GET /api/status` | the last sync, fetch and backfill (with how many cases still have no list), and the token's expiry, from `state.json` and `.env` |

A job refuses to start, with the reason on the page, when another is running,
when a case number is not on disk, or when a documents job has no token or an
expired one. The daily job still updates case data without a token and says
the documents were skipped. The console window shows the full log of every
job.

Opened straight from disk (`file://`), the pages still read fine, but the
buttons are disabled and the page says how to start the app, since nothing is
listening.

Only `site/`, `data/documents/` and `/api/` are reachable over HTTP; the
document root has to be the directory above them so the PDF links resolve, and
`.env` lives there.

### The analytics app (`analytics_server.py`, `analytics_ui/`)

`ITC Analytics.bat` runs `python cli.py analytics-serve`, a second server on
<http://127.0.0.1:8766> that serves only `site_analytics/`:

| Endpoint | What it does |
| --- | --- |
| `POST /api/rebuild` | the analytics build (with the model review of new borderline pairs), then the page's data; one at a time (409 otherwise) |
| `GET /api/jobs/current` | the running or last rebuild, with its log |
| `GET /api/status` | when the analytics were built, whether `counsel.json` changed since (`meta.json` records the counsel file's timestamp it was built from), how many pairs need review |

On opening, `analytics_server.start` rebuilds when the last build was not
today (local time); otherwise it only makes sure the page exists. Without an
Anthropic key the rebuild runs without the review and ends as a warning. It
reads the tracker's files, which are always replaced whole, and writes only
`data/analytics/` and `site_analytics/`, so it can run while the tracker
does.

The page is one HTML file drawing every view in the browser from `data.js`
(`analytics_ui/bundle.py`): cases (`[number, title, year started, status,
open, year ended]`), firms, attorneys and companies as lists, and
representations as `[case, [firms], [attorneys], [[company, role]], side,
first filing year, last filing year]` referring to them by position -- about 1.3 MB for 179 cases with
counsel. `data.js` sets `window.ANALYTICS` rather than being fetched, so the
page also reads when opened from disk (without Rebuild). Views are addressed
by the hash (`#/firm/firm:kirkland-ellis`), so they can be bookmarked. Who a
firm or attorney *opposed* is read off each case: the companies on the other
side (intervenors count with respondents; non-parties oppose no one). The
timelines, co-counsel pairs, frequent fliers and two-way disputes are all
computed in the page from the same data, so every filter applies to them;
the definitions are at the top of `analytics_ui/page.py`. The charts are
inline SVG, with no charting library. A closed case with no end date in the
IDS feed (about a quarter of them) counts in a caseload for the years its
firms filed in it, or its start year when that is all there is.

## Dates

Each source writes dates differently, and two of the shapes cannot be told
apart by looking at one value:

| Source | Example | Order |
| --- | --- | --- |
| IDS investigations | `01-13-2026` | US month first |
| IDS date objects | `{"date": "2026-05-26T12:00:00.000+00:00"}` | ISO 8601 |
| EDIS documents | `2026/09/18 11:39:00` | year first |

`04-05-2026` from IDS is 5 April, but read day-first it is 4 May. So `dates.py`
decides the order from the source format, never from the value, the data layer
stores everything as ISO 8601, and the site renders `18 Sep 2026` -- day,
month, year, month named so it cannot be misread.

Documents fetched before this keep working, and `python cli.py normalize`
rewrites them in place without any API calls.

## What is not in git

`data/ids/` (the snapshots) and `data/investigations.json` are both rebuilt
from the public feed by one offline-friendly command, and both are large and
change every day, so they are not tracked. Nor is `data/counsel.json`, which
`sync` rebuilds from those and the documents (nor `data/next_actions.json`,
likewise), nor `data/analytics/` (rebuilt
by `analytics`) except its `review_decisions.json`, which was paid for.
`site/` is generated too.

The PDFs under `data/documents/` are not tracked either: they run to
gigabytes, and EDIS will serve them again. They live only on the machine that
downloaded them, so back that folder up some other way if you need to.

What is tracked is the record of what was fetched: the documents index
(`data/documents_index/`, one file per case), which lists every document and
its attachments and took hours of EDIS requests to build, and
`data/sync_log.csv`, a record of downloads that already happened and cannot
be reconstructed.

After pulling, run:

```
python cli.py sync --render
```

On a fresh clone the index lists documents whose PDFs aren't on disk yet;
`python cli.py docs --existing` downloads them again (it needs an EDIS token).

## Tests

```
python -m unittest discover -s tests
```

They need neither a token nor a network connection: the IDS feed is replaced
with rows shaped like the real thing and EDIS with a fake client. They cover
the snapshot store, flattening, stage grouping, the field mapping, the sync
log, rendering, the local server, reading counsel from filings, the
analytics name matching (on spellings taken from the real filings, with the
model replaced by a fake client), and that fetching documents leaves case
information alone.

## Poking at the raw API

```
python inspect_edis.py 337-1478
```

Prints the raw XML EDIS returns, which is handy when a field looks wrong.
