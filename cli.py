"""Entry point for the ITC 337 docket tracker.

The work is split into two independent layers so that changing how the site
looks never means re-fetching anything:

  Data layer (datalayer/)  -- talks to EDIS/RSS, writes data/*.json + PDFs
  UI layer   (ui/)         -- reads data/*.json, writes site/*.html

and the data layer has two separate processes:

  discover  RSS feed + EDIS API; finds and fetches cases we don't track yet
  update    EDIS API only, for the specific investigation numbers you name

Examples:
    python cli.py discover                  # look for newly filed complaints
    python cli.py discover --dry-run        # just log the feed, no EDIS calls
    python cli.py update 337-1478 337-3936  # re-pull only those two cases
    python cli.py update --all              # re-pull everything on disk
    python cli.py render                    # rebuild the site, offline
    python cli.py status                    # what's tracked, no network
    python cli.py normalize                 # rewrite stored dates to ISO, offline
    python cli.py refresh                   # discover + update --all + render
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from datalayer import discovery, normalize, update
from datalayer.config import DATA_DIR, MissingTokenError, SITE_DIR, load_token
from datalayer.runner import ProcessAborted
from datalayer.store import Store
from ui.render import render_site


def _add_fetch_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-attachments",
        action="store_true",
        help="record document metadata only; skip downloading PDFs",
    )
    parser.add_argument(
        "--no-ids",
        action="store_true",
        help="skip the public IDS feed (start dates may be missing)",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="rebuild the site afterwards (same as running 'render')",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--site-dir", type=Path, default=SITE_DIR, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser(
        "discover",
        help="new case discovery: RSS feed + EDIS API for dockets we don't track yet",
    )
    p_discover.add_argument(
        "--limit", type=int, help="fetch at most this many newly discovered dockets"
    )
    p_discover.add_argument(
        "--dry-run",
        action="store_true",
        help="refresh the RSS log and report what's new, but make no EDIS calls",
    )
    _add_fetch_flags(p_discover)

    p_update = sub.add_parser(
        "update",
        help="targeted update: re-pull the investigation numbers you name from EDIS",
    )
    p_update.add_argument(
        "numbers",
        nargs="*",
        help="investigation or docket numbers, e.g. 337-1478 337-TA-1478 337-3936",
    )
    p_update.add_argument("--all", action="store_true", help="update every case already on disk")
    p_update.add_argument(
        "--known-only",
        action="store_true",
        help="refuse numbers that aren't tracked yet instead of asking EDIS about them",
    )
    _add_fetch_flags(p_update)

    sub.add_parser("render", help="UI layer only: rebuild site/ from data/ (offline)")
    sub.add_parser("status", help="list what's tracked on disk (offline)")
    sub.add_parser(
        "normalize", help="rewrite stored dates to ISO 8601 in place (offline, no API calls)"
    )

    p_refresh = sub.add_parser(
        "refresh", help="discover, then update everything, then render (the old refresh.py)"
    )
    _add_fetch_flags(p_refresh)

    return parser


def _render(store: Store, site_dir: Path) -> None:
    print("Rendering site...")
    report = render_site(store, site_dir=site_dir)
    print(f"Open: {report.index_path}")


def cmd_discover(args: argparse.Namespace, store: Store) -> int:
    token = None if args.dry_run else load_token()
    report = discovery.run(
        store,
        token,
        limit=args.limit,
        download=not args.no_attachments,
        use_ids=not args.no_ids,
        dry_run=args.dry_run,
    )
    print(
        f"\nDiscovery done. {len(report.added)} new case(s) added, "
        f"{report.downloaded} attachment(s) downloaded, {len(report.failed)} skipped."
    )
    if args.render and not args.dry_run:
        _render(store, args.site_dir)
    elif report.added:
        print("Run 'python cli.py render' to show them on the site.")
    return 0


def cmd_update(args: argparse.Namespace, store: Store) -> int:
    numbers = list(args.numbers)
    if args.all:
        numbers = store.tracked_numbers()
    if not numbers:
        print("Give one or more investigation numbers, or use --all.")
        return 1

    report = update.run(
        store,
        load_token(),
        numbers,
        download=not args.no_attachments,
        use_ids=not args.no_ids,
        known_only=args.known_only,
    )
    print(
        f"\nUpdate done. {len(report.updated)} case(s) updated, "
        f"{report.downloaded} attachment(s) downloaded, {len(report.failed)} skipped."
    )
    if args.render:
        _render(store, args.site_dir)
    return 0 if report.updated or not report.failed else 1


def cmd_render(args: argparse.Namespace, store: Store) -> int:
    _render(store, args.site_dir)
    return 0


def cmd_normalize(args: argparse.Namespace, store: Store) -> int:
    normalize.run(store)
    _render(store, args.site_dir)
    return 0


def cmd_status(args: argparse.Namespace, store: Store) -> int:
    rows = store.summary_rows()
    if not rows:
        print("Nothing tracked yet. Run 'python cli.py discover'.")
        return 0

    width = max(len(row["investigation_number"]) for row in rows)
    print(f"{'NUMBER'.ljust(width)}  {'STATUS':<20} {'DOCS':>5} {'FILES':>6}  LAST REFRESHED")
    for row in rows:
        refreshed = (row["last_refreshed"] or "never")[:19]
        print(
            f"{row['investigation_number'].ljust(width)}  {row['status'][:20]:<20} "
            f"{row['documents']:>5} {row['attachments']:>6}  {refreshed}"
        )
    print(f"\n{len(rows)} case(s) tracked in {store.data_dir}.")
    return 0


def cmd_refresh(args: argparse.Namespace, store: Store) -> int:
    token = load_token()
    download = not args.no_attachments
    use_ids = not args.no_ids

    discovery_report = discovery.run(store, token, download=download, use_ids=use_ids)
    print()
    just_fetched = {r.key for r in discovery_report.results if r.ok}
    stale = [n for n in store.tracked_numbers() if n not in just_fetched]
    update_report = update.run(store, token, stale, download=download, use_ids=use_ids)

    _render(store, args.site_dir)
    downloaded = discovery_report.downloaded + update_report.downloaded
    print(
        f"\nDone. {len(store.investigations)} investigation(s) tracked, "
        f"{downloaded} attachment(s) downloaded this run."
    )
    return 0


COMMANDS = {
    "discover": cmd_discover,
    "update": cmd_update,
    "render": cmd_render,
    "status": cmd_status,
    "normalize": cmd_normalize,
    "refresh": cmd_refresh,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = Store.load(args.data_dir)
    try:
        return COMMANDS[args.command](args, store)
    except MissingTokenError as exc:
        print(exc)
        return 1
    except ProcessAborted as exc:
        print(f"AUTH ERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        store.save_investigations()
        print("\nInterrupted; data collected so far has been saved.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
