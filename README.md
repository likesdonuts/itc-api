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

## Layout

```
cli.py                one entry point for every command
ui_schema.json        which IDS fields the site shows, and what to call them
schema.py             loads and applies that mapping (used by both layers)
dates.py              what a date from each feed means, shared by both layers
server.py             local server so the page buttons can run the data layer
datalayer/            DATA LAYER - talks to IDS/EDIS/RSS, owns data/
  ids.py                the daily download and the snapshots on disk
  flatten.py            one IDS row -> plain named values
  cases.py              rows -> one record per investigation, with its stages
  ingest.py             process 1: rebuild every case from a snapshot
  docs.py               process 2: EDIS documents for named cases only
  feed.py               the complaint RSS feed (document IDs, brand-new dockets)
  client.py             HTTP client for the EDIS API and the RSS feed
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
  rss_log.json          what the complaint feed has reported
  documents/<number>/   downloaded PDFs
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
| `python cli.py sync` | IDS + RSS | Process 1. Downloads today's IDS file if it isn't stored yet and rebuilds every case record from it. |
| `python cli.py parse` | none | Rebuilds the case records from the newest stored snapshot. |
| `python cli.py docs 337-1478` | EDIS | Process 2. Fetches document lists and PDFs for the numbers you name. |
| `python cli.py docs --existing` | EDIS | Process 2 over every case you have already fetched documents for. |
| `python cli.py render` | none | UI layer. Rebuilds `site/` from `data/` and `ui_schema.json`. |
| `python cli.py serve` | localhost | Serves the site so its Update / Fetch docs buttons work. |
| `python cli.py fields` | none | Lists every field name `ui_schema.json` can use, with samples. |
| `python cli.py status` | none | Which snapshot is current, and which cases have documents. |
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

The download is stored as `data/ids/investigations-YYYY-MM-DD.json.gz` and is
never rewritten, so every day you keep a copy you can go back to; run again on
the same day and it reuses that copy rather than re-downloading 37 MB.

`data/investigations.json` is then rebuilt from the snapshot in full. That is
deliberate: when the Commission renumbers or retitles something, the rebuilt
file reflects it instead of accumulating stale entries. The run reports what
appeared and what disappeared since last time.

To have it happen daily on Windows, point Task Scheduler at `sync.bat`, or:

```
schtasks /create /tn "ITC 337 sync" /tr "\"%CD%\sync.bat\"" /sc daily /st 07:00
```

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
python cli.py docs --existing                    # refresh what you already have
```

Only the numbers you pass are requested. Numbers are matched loosely, so
`337-TA-1478`, `337-1478` and `1478` all reach the same case. This process
writes `data/documents_index.json`, `data/documents_state.json` and files under
`data/documents/` -- nothing else. It has no route to the case writer, and a
test holds it to that.

There is no `docs` for all 1382 cases by default (`--all` exists, and it is
1382 EDIS calls); documents are fetched per case, when you want them.

### The field mapping (`ui_schema.json`)

The IDS file carries far more about an investigation than a page should show,
so the site does not name IDS fields in code. `ui_schema.json` lists the
sections, the labels and the field names:

```json
{ "label": "Target Date", "source": "target_date", "type": "date" }
{ "label": "Complainant(s)", "source": "participants",
  "where": { "role": "Complainant" }, "item": "name", "type": "list" }
```

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

### The complaint RSS feed

The IDS file already lists pre-institution dockets (as `337-3936`, status
"Pre-institution"), so the feed is no longer how cases are discovered. It is
still read during `sync` for two things: a complaint it names before IDS's
next rebuild is kept on the site as a placeholder until IDS lists it, and its
EDIS document IDs are the only route to the PDFs of a case EDIS has no
investigation record for yet.

Once a complaint is instituted, IDS lists it under a real number that keeps the
docket as a field (`337-1521`, docket `3936`). The sync notices that, and moves
the documents and PDFs already downloaded for `337-3936` onto the new number.

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
| RSS `pubDate` | `Fri, 18 Sep 2026 11:39:05 GMT` | RFC 822 |

`04-05-2026` from IDS is 5 April, but read day-first it is 4 May. So `dates.py`
decides the order from the source format, never from the value, the data layer
stores everything as ISO 8601, and the site renders `18 Sep 2026` -- day,
month, year, month named so it cannot be misread.

Documents fetched before this keep working, and `python cli.py normalize`
rewrites them in place without any API calls.

## What is not in git

`data/ids/` (the snapshots) and `data/investigations.json` are both rebuilt
from the public feed by one offline-friendly command, and both are large and
change every day, so they are not tracked. `site/` is generated too. What is
tracked is the work you cannot re-download for free: the documents index and
the PDFs under `data/documents/`.

After pulling, run:

```
python cli.py sync --render
```

## Tests

```
python -m unittest discover -s tests
```

They need neither a token nor a network connection: the IDS feed is replaced
with rows shaped like the real thing and EDIS with a fake client. They cover
the snapshot store, flattening, stage grouping, the field mapping, rendering,
the local server, and that fetching documents leaves case information alone.

## Poking at the raw API

```
python inspect_edis.py 337-1478
```

Prints the raw XML EDIS returns, which is handy when a field looks wrong.
