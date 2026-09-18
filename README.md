# ITC Section 337 docket tracker

Tracks newly filed USITC Section 337 complaints and their dockets: it watches
the EDIS complaint RSS feed, pulls investigation and document data from the
EDIS API, downloads the public PDFs, and renders a static site you can open
straight from disk.

## Layout

```
cli.py                one entry point for every command
datalayer/            DATA LAYER - talks to EDIS/RSS, owns data/
  client.py             HTTP client for the EDIS API, IDS feed and RSS feed
  config.py             paths, feed URLs, .env token loading
  store.py              reads/writes data/*.json, resolves case numbers
  records.py            builds the stored record shape for one case
  discovery.py          process 1: new case discovery
  update.py             process 2: targeted update
  runner.py             shared session handling for both processes
ui/                   UI LAYER - reads data/, owns site/
  templates.py          all HTML/CSS
  render.py             writes site/index.html and site/investigations/*.html
data/                 the handoff between the layers (JSON + downloaded PDFs)
site/                 generated output
tests/                offline tests against a fake EDIS API
```

The layers only meet at `data/`. The data layer never renders HTML, and the UI
layer never makes a network call or needs a token, so you can restyle the site
as often as you like without re-fetching anything.

## Setup

```
pip install -r requirements.txt
```

Put your EDIS API token in a `.env` file next to `cli.py`:

```
EDIS_TOKEN=<your token>
```

Tokens come from <https://edis.usitc.gov> → profile → API Token Generator, and
they expire; when one does, every data command stops with an `AUTH ERROR`
telling you to generate a new one. The UI layer keeps working without a token.

## Commands

| Command | Network | What it does |
| --- | --- | --- |
| `python cli.py discover` | RSS + EDIS | Process 1. Reads the complaint feed, logs it, and fetches every docket that has no record yet. |
| `python cli.py update 337-1478 337-3936` | EDIS | Process 2. Re-pulls exactly the case numbers you name. |
| `python cli.py update --all` | EDIS | Process 2 over every case already on disk. |
| `python cli.py render` | none | UI layer. Rebuilds `site/` from `data/`. |
| `python cli.py status` | none | Lists what is tracked, with document and file counts. |
| `python cli.py refresh` | RSS + EDIS | Discover, then update everything, then render. The old all-in-one behaviour. |

Windows users can double-click `discover.bat`, `update.bat` (it prompts for
numbers), `render.bat`, or `run.bat` (full refresh).

### Process 1: new case discovery

```
python cli.py discover                 # find and fetch anything new
python cli.py discover --dry-run       # refresh the RSS log, report what's new, call nothing
python cli.py discover --limit 2       # fetch at most two new dockets
python cli.py discover --render        # ...and rebuild the site afterwards
```

This is the only process that touches the RSS feed. A docket counts as new
when nothing in `data/investigations.json` matches it, so re-running it is
cheap: already-tracked dockets are left alone.

### Process 2: targeted update

```
python cli.py update 337-1478
python cli.py update 337-TA-1478 337-3936 --render
python cli.py update --all --no-attachments
```

No feed, no surprises: only the numbers you pass are requested from EDIS.
Numbers are matched loosely, so `337-TA-1478`, `337-1478` and `1478` all reach
the same case. A number that isn't tracked yet is still looked up (pass
`--known-only` to refuse it instead), which is how you add a case by hand.

Both processes accept `--no-attachments` (record document metadata but skip
the PDF downloads, much faster) and `--no-ids` (skip the public IDS feed that
supplies investigation start dates).

### UI layer

```
python cli.py render
```

Reads `data/investigations.json` and `data/documents_index.json` and rewrites
the site. Edit `ui/templates.py`, re-run this, refresh the browser. Pages for
cases that no longer exist under that number are removed.

## How a case is stored

A complaint appears in the RSS feed before EDIS has an investigation record
for it, under a raw docket number like `337-3936`. Until it is instituted it is
stored as a "Pending Institution" record built from the feed plus attachment
lookups. Once EDIS answers for it, the case is renumbered (for example to
`337-1501`), and the update process moves the stored record, the RSS history
and the already-downloaded PDFs over to the new number rather than leaving a
duplicate behind.

## Tests

```
python -m unittest discover -s tests
```

The tests replace the EDIS client with a fake, so they need neither a token nor
a network connection. They cover which process calls what, how case numbers
resolve, the renumbering path, and that rendering works from disk alone.

## Poking at the raw API

```
python inspect_edis.py 337-1478
```

Prints the raw XML EDIS returns, which is handy when a field looks wrong.
