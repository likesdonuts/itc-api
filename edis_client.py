"""Client for the USITC EDIS data API, the public IDS investigation feed, and
the 337-complaint RSS feed.

EDIS reference (endpoints, headers, XML field names) was reverse-engineered
from the open-source patent-client-agents connector
(https://github.com/parkerhancock/patent-client-agents), which documents this
exact API against a live EDIS token.
"""

from __future__ import annotations

import base64
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
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

# RSS item titles look like:
#   "Document Approved : 337-3936 Violation : Doc ID 894182, Complaint"
# The "337-3936" is a raw USITC docket number for a complaint that has not
# yet been instituted as a formal investigation (which would get a
# "337-TA-####" number instead). The RSS feed is how you find out about a
# complaint before EDIS has an /investigation record for it at all.
RSS_TITLE_RE = re.compile(
    r"(?P<docket>337-\d+)\s+Violation\s*:\s*Doc(?:ument)?\s*ID\s*(?P<doc_id>\d+)\s*,\s*(?P<doc_type>.+)$",
    re.IGNORECASE,
)
DOCKET_NUMBER_RE = re.compile(r"337-\d+")
DOC_ID_RE = re.compile(r"Doc(?:ument)?\s*ID[:\s]*(\d+)", re.IGNORECASE)


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

    def list_documents(self, investigation_number: str, max_pages: int = 50) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
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


@dataclass
class RssItem:
    title: str
    link: str
    guid: str
    pub_date: str | None
    pub_date_iso: str | None
    docket_number: str | None
    doc_id: str | None
    doc_type: str | None


def _parse_pub_date(pub_date: str | None) -> str | None:
    if not pub_date:
        return None
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(pub_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def fetch_rss(url: str, timeout: float = 30.0) -> list[RssItem]:
    resp = httpx.get(url, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    items: list[RssItem] = []
    for item in root.findall(".//item"):
        title = _text(item.find("title")) or ""
        link = _text(item.find("link")) or ""
        guid = _text(item.find("guid")) or link or title
        pub_date = _text(item.find("pubDate"))
        description = _text(item.find("description")) or ""
        haystack = f"{title} {description}"

        docket_number: str | None = None
        doc_id: str | None = None
        doc_type: str | None = None

        strict = RSS_TITLE_RE.search(haystack)
        if strict:
            docket_number = strict.group("docket")
            doc_id = strict.group("doc_id")
            doc_type = strict.group("doc_type").strip()
        else:
            docket_match = DOCKET_NUMBER_RE.search(haystack)
            doc_id_match = DOC_ID_RE.search(haystack)
            docket_number = docket_match.group(0) if docket_match else None
            doc_id = doc_id_match.group(1) if doc_id_match else None

        items.append(
            RssItem(
                title=title,
                link=link,
                guid=guid,
                pub_date=pub_date,
                pub_date_iso=_parse_pub_date(pub_date),
                docket_number=docket_number,
                doc_id=doc_id,
                doc_type=doc_type,
            )
        )
    return items


def fetch_ids_investigations(timeout: float = 30.0) -> dict[str, dict[str, Any]]:
    """Public, unauthenticated feed with a 'Start Date' field EDIS itself lacks."""
    resp = httpx.get(IDS_URL, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) else payload
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(data, list):
        return result
    for row in data:
        number = row.get("Investigation Number")
        if number:
            result[number] = row
    return result
