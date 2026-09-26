"""Client for the USITC EDIS data API and the public IDS investigation feed.

EDIS reference (endpoints, headers, XML field names) was reverse-engineered
from the open-source patent-client-agents connector
(https://github.com/parkerhancock/patent-client-agents), which documents this
exact API against a live EDIS token.
"""

from __future__ import annotations

import base64
import html
import json
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    # On corporate machines behind a TLS-inspecting proxy, Python's bundled
    # certifi CA list doesn't include the proxy's root cert even though
    # Windows itself trusts it. truststore makes ssl verification go through
    # the OS certificate store instead, which fixes CERTIFICATE_VERIFY_FAILED
    # in that setup without disabling verification.
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

EDIS_BASE_URL = "https://edis.usitc.gov/data"
IDS_URL = "https://ids.usitc.gov/investigations.json"

# Every EDIS request this process has made, by kind, for the daily sync's
# timing log (dailylog.py reads the difference across a run).
REQUESTS: Counter = Counter()


def _request_kind(path: str) -> str:
    if path.startswith("/document"):
        return "list"
    if path.startswith("/attachment"):
        return "attachments"
    if path.startswith("/download"):
        return "download"
    return "other"


class EdisError(RuntimeError):
    pass


class EdisAuthError(EdisError):
    pass


def load_env(path: Path) -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE per line, '#' comments)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def decode_jwt_exp(token: str) -> datetime | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
        exp = claims.get("exp")
        if exp is None:
            return None
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _text(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    text = elem.text.strip()
    if not text:
        return None
    # EDIS's own data is double HTML-escaped (e.g. "O&amp;#39;Melveny" in the
    # raw XML), so after ElementTree's one pass of unescaping we're left with
    # literal "&#39;" etc. still in the text. Unescape again to get the real
    # characters before we re-escape for HTML output downstream.
    return html.unescape(text)


def _elements_to_dict(elem: ET.Element) -> dict[str, Any]:
    return {child.tag: _text(child) for child in elem}


class EdisClient:
    """Talks to https://edis.usitc.gov/data.

    IMPORTANT: edis.usitc.gov sits behind Akamai bot management, which 403s
    any request carrying a custom or browser-impersonating User-Agent. Do not
    set a User-Agent header here — httpx's native default
    ("python-httpx/x.y.z") is what gets allowed through.
    """

    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self.token = token
        self.token_expires_at = decode_jwt_exp(token)
        self._client = httpx.Client(
            base_url=EDIS_BASE_URL,
            headers={
                "Accept": "application/xml",
                "Authorization": f"Bearer {token}",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EdisClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _check_token_fresh(self) -> None:
        if self.token_expires_at and datetime.now(timezone.utc) >= self.token_expires_at:
            raise EdisAuthError(
                f"EDIS_TOKEN expired at {self.token_expires_at.isoformat()}. "
                "Generate a new one at https://edis.usitc.gov -> profile -> "
                "API Token Generator, and update .env."
            )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self._check_token_fresh()
        REQUESTS[_request_kind(path)] += 1
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code in (401, 403):
            raise EdisAuthError(
                f"EDIS returned {resp.status_code} for {path}. This usually means "
                "the token expired or was rejected. Generate a new token at "
                "https://edis.usitc.gov -> profile -> API Token Generator."
            )
        resp.raise_for_status()
        return resp

    def get_investigation(self, investigation_number: str) -> list[dict[str, Any]]:
        resp = self._request("GET", f"/investigation/{investigation_number}")
        root = ET.fromstring(resp.text)
        return [_elements_to_dict(inv) for inv in root.findall(".//investigation")]

    # 50 pages (1,000 documents) cut 337-TA-395's docket short; the largest
    # dockets run to several thousand.
    def list_documents(
        self, investigation_number: str, max_pages: int = 1000, *, known_ids: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Every document EDIS lists for a case, newest first -- or, given the
        ids already on file (`known_ids`), only as far as the first page made
        up entirely of them: the new filings sit on the pages before it.
        `last_listing_complete` says which it was.
        """
        documents: list[dict[str, Any]] = []
        self.last_listing_complete = True
        page = 1
        while page <= max_pages:
            resp = self._request(
                "GET",
                "/document",
                params={"investigationNumber": investigation_number, "pageNumber": page},
            )
            root = ET.fromstring(resp.text)
            batch = [_elements_to_dict(doc) for doc in root.findall(".//document")]
            if not batch:
                break
            documents.extend(batch)
            if known_ids and all(str(doc.get("id")) in known_ids for doc in batch):
                self.last_listing_complete = False
                break
            # Don't assume a page size (observed page size is 20, not the 100
            # the old EDIS3 guide documented) -- just keep paging until a page
            # comes back empty, bounded by max_pages as a safety cap.
            page += 1
        return documents

    def list_attachments(self, document_id: str) -> list[dict[str, Any]]:
        resp = self._request("GET", f"/attachment/{document_id}")
        root = ET.fromstring(resp.text)
        return [_elements_to_dict(att) for att in root.findall(".//attachment")]

    def download_attachment(self, document_id: str, attachment_id: str, dest: Path) -> Path:
        resp = self._request(
            "GET",
            f"/download/{document_id}/{attachment_id}",
            headers={"Accept": "*/*"},
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest
