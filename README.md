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
  counsel.py            process 3: who represents whom, from the filings
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
  documents_index.json  documents per case            <- written by docs
  documents_state.json  when each case was last fetched
  counsel.json          firms and attorneys per case  <- written by counsel
  sync_log.csv          one row per sync, for watching the daily download
  documents/<number>/   downloaded PDFs (gitignored)
site/                 generated output
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

## Commands

| Command | Network | What it does |
| --- | --- | --- |
| `python cli.py sync` | IDS | Process 1. Downloads today's IDS file if it isn't stored yet and rebuilds every case record from it. |
| `python cli.py parse` | none | Rebuilds the case records from the newest stored snapshot. |
| `python cli.py docs 337-1478` | EDIS | Process 2. Fetches document lists and PDFs for the numbers you name. |
| `python cli.py docs --existing` | EDIS | Process 2 over every case you have already fetched documents for. |
| `python cli.py docs 337-1478 --appearances` | EDIS | Lists every document but downloads only the Notice of Appearance PDFs. |
| `python cli.py counsel` | none | Process 3. Rebuilds who represents whom from the documents on disk. Runs by itself after `sync`, `parse`, `docs` and `refresh`. |
| `python cli.py render` | none | UI layer. Rebuilds `site/` from `data/` and `ui_schema.json`. |
| `python cli.py serve` | localhost | Serves the site so its Update / Fetch docs buttons work. |
| `python cli.py fields` | none | Lists every field name `ui_schema.json` can use, with samples. |
| `python cli.py status` | none | Which snapshot is current, which cases have documents, and the last few syncs. |
| `python cli.py normalize` | none | Rewrites stored document dates to ISO 8601 in place. |
| `python cli.py refresh` | IDS + EDIS | The daily job: sync, re-fetch documents already on disk, render. |

Windows users can double-click `sync.bat`, `docs.bat` (it prompts for numbers),
`render.bat`, `serve.bat`, or `run.bat` (full refresh). `discover` and `update`
still work as the old names for `sync` and `docs`.

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
an older day off the end.

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
| `run_at`, `mode`, `outcome` | when, `download`/`cached`/`offline`, `ok`/`refused` |
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
writes `data/documents_index.json`, `data/documents_state.json` and files under
`data/documents/` -- nothing else. It has no route to the case writer, and a
test holds it to that.

There is no `docs` for all 1382 cases by default (`--all` exists, and it is
1382 EDIS calls); documents are fetched per case, when you want them.

### Counsel: who represents whom

```
python cli.py counsel                  # rebuild data/counsel.json, offline
python cli.py counsel --render         # ...and the site
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

- `type` -- `text`, `mono`, `long_text`, `date`, `bool`, `number`, `list`,
  `status` or `case_link`
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

### Updating a case from the page

```
python cli.py serve
```

This serves the site on <http://127.0.0.1:8765> and gives the two buttons on
each row of the list page something to call:

- **Update** refreshes that case's document list from EDIS, without
  downloading any PDFs.
- **Fetch docs** refreshes the list and also downloads any PDFs missing from
  `data/documents/`.

Both run the same documents process as `python cli.py docs`, with downloads off
or on, and neither touches case information. The row reports what happened and
the page reloads onto the freshly rendered data. One action runs at a time.
Opened straight from disk (`file://`) the buttons are disabled and the page
says why, since nothing is listening.

Only `site/` and `data/documents/` are reachable over HTTP; the document root
has to be the directory above them so the PDF links resolve, and `.env` lives
there.

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
`sync` rebuilds from those and the documents. `site/` is generated too.

The PDFs under `data/documents/` are not tracked either: they run to
gigabytes, and EDIS will serve them again. They live only on the machine that
downloaded them, so back that folder up some other way if you need to.

What is tracked is the record of what was fetched: the documents index, which
lists every document and its attachments, and `data/sync_log.csv`, a record
of downloads that already happened and cannot be reconstructed.

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
log, rendering, the local server, reading counsel from filings, and that
fetching documents leaves case information alone.

## Poking at the raw API

```
python inspect_edis.py 337-1478
```

Prints the raw XML EDIS returns, which is handy when a field looks wrong.
