"""Print the raw XML EDIS returns for a given docket/investigation number, so
you can see the real payload shape instead of the reverse-engineered docs.

Usage:
    python inspect_edis.py 337-1478
    python inspect_edis.py 337-1478 --suffix-only   # skip the as-given lookup, try just "1478"
"""

from __future__ import annotations

import sys
import xml.dom.minidom as minidom
from pathlib import Path

from datalayer.client import EdisAuthError, EdisClient, EdisError, load_env

ROOT = Path(__file__).parent


def pretty(xml_text: str) -> str:
    try:
        return minidom.parseString(xml_text).toprettyxml(indent="  ")
    except Exception:
        return xml_text


def show(label: str, xml_text: str) -> None:
    print(f"\n----- {label} -----")
    print(pretty(xml_text).strip())


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("Usage: python inspect_edis.py <docket-or-investigation-number> [--suffix-only]")
        return 1

    number = args[0]
    suffix_only = "--suffix-only" in args

    env = load_env(ROOT / ".env")
    token = env.get("EDIS_TOKEN")
    if not token:
        print("Missing EDIS_TOKEN in .env")
        return 1

    with EdisClient(token) as client:
        try:
            if not suffix_only:
                resp = client._request("GET", f"/investigation/{number}")
                show(f"GET /investigation/{number}", resp.text)

            suffix = number.rsplit("-", 1)[-1]
            if suffix != number:
                resp = client._request("GET", f"/investigation/{suffix}")
                show(f"GET /investigation/{suffix}", resp.text)

            resp = client._request(
                "GET", "/document", params={"investigationNumber": number, "pageNumber": 1}
            )
            show(f"GET /document?investigationNumber={number}&pageNumber=1", resp.text)

        except EdisAuthError as exc:
            print(f"AUTH ERROR: {exc}")
            return 1
        except EdisError as exc:
            print(f"ERROR: {exc}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
