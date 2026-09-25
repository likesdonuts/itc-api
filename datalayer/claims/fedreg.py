"""The notice of institution, from the Federal Register.

The Commission publishes every notice of institution in the Federal Register,
and federalregister.gov serves them without a token. The notice is formulaic
and authoritative about what was instituted:

    ... by reason of infringement of one or more of claims 1-3 of the '294
    patent; claim 1 of the '508 patent; claims 1-3 of the '347 patent; and
    claims 1-7 of the '335 patent, and whether an industry in the United
    States exists ...

so the instituted claims are read from it by rule, with no model.

IDS's numbering fields can disagree with each other, so a notice is only used
when its text names this investigation's number and its title shares this
investigation's subject.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

from .text import ClaimListError, expand_claims, find_claim_refs, patent_for, patent_mentions, split_sentences

API = "https://www.federalregister.gov/api/v1/documents.json"
FIELDS = ["title", "publication_date", "document_number", "raw_text_url", "html_url", "type"]

_INSTITUTION_TITLE = re.compile(r"\binstitution\b", re.I)
_ISSUED = re.compile(r"\bIssued:\s*(?P<date>[A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4})")
_INSTITUTED_SENTENCE = re.compile(r"infringement of (?:one or more of )?claims?\b", re.I)


class FedRegError(RuntimeError):
    pass


@dataclass(frozen=True)
class Notice:
    document_number: str
    title: str
    publication_date: str
    html_url: str
    text: str

    @property
    def source_id(self) -> str:
        return f"FR:{self.document_number}"


class Fetcher(Protocol):
    def search(self, investigation_number: str) -> list[dict[str, Any]]: ...
    def text(self, document: dict[str, Any]) -> str: ...


class HttpFetcher:
    """federalregister.gov over HTTPS, caching each notice's text on disk
    (notices never change once published)."""

    def __init__(self, cache_dir: Path, timeout: float = 30.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout

    def _client(self):
        import httpx

        # Importing the EDIS client routes TLS through the Windows certificate
        # store, which corporate proxies need (see client.py).
        from .. import client as _edis  # noqa: F401

        return httpx.Client(timeout=self.timeout, follow_redirects=True)

    def search(self, investigation_number: str) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [
            ("conditions[term]", f'"{investigation_number}"'),
            ("conditions[agencies][]", "international-trade-commission"),
            ("order", "oldest"),
            ("per_page", "100"),
        ] + [("fields[]", f) for f in FIELDS]
        with self._client() as client:
            response = client.get(API, params=params)
        if response.status_code != 200:
            raise FedRegError(f"federalregister.gov returned HTTP {response.status_code}")
        return list(response.json().get("results") or [])

    def text(self, document: dict[str, Any]) -> str:
        cached = self.cache_dir / f"{document['document_number']}.txt"
        if cached.exists():
            return cached.read_text(encoding="utf-8")
        with self._client() as client:
            response = client.get(document["raw_text_url"])
        if response.status_code != 200:
            raise FedRegError(
                f"federalregister.gov returned HTTP {response.status_code} for {document['document_number']}"
            )
        text = html.unescape(re.sub(r"<[^>]+>", " ", response.text))
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text, encoding="utf-8")
        return text


def ta_number(key: str) -> str:
    """ "337-1366" -> "337-TA-1366", the form notices are indexed under."""
    head, _, serial = str(key).rpartition("-")
    return f"{head}-TA-{serial}" if head and "TA" not in head.upper() else str(key)


def _subject(title: str) -> str:
    subject = re.split(r";|\bNotice\b|\bInstitution\b", str(title or ""), maxsplit=1)[0]
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", subject.lower()).split())


def _same_investigation(notice_text: str, notice_title: str, number: str, case_title: str) -> bool:
    if number not in " ".join(notice_text.split()):
        return False
    ours, theirs = _subject(case_title), _subject(notice_title)
    if not ours or not theirs:
        return True
    # "Certain Semiconductor Devices and Products Containing the Same" vs
    # "Certain Semiconductor Devices, and Methods of Manufacturing Same and
    # Products Containing the Same": titles are amended, so compare loosely.
    return theirs.startswith(ours[:25]) or SequenceMatcher(None, ours, theirs).ratio() >= 0.6


def institution_notices(fetcher: Fetcher, key: str, case_title: str) -> list[Notice]:
    """This investigation's notices of institution, oldest first."""
    number = ta_number(key)
    notices = []
    for document in fetcher.search(number):
        if not _INSTITUTION_TITLE.search(str(document.get("title") or "")):
            continue
        text = fetcher.text(document)
        if not _same_investigation(text, document.get("title") or "", number, case_title):
            continue
        notices.append(
            Notice(
                document_number=str(document["document_number"]),
                title=str(document.get("title") or ""),
                publication_date=str(document.get("publication_date") or ""),
                html_url=str(document.get("html_url") or ""),
                text=text,
            )
        )
    return notices


def issued_date(notice: Notice) -> str:
    """The day the Commission issued the notice ("Issued: June 27, 2023"),
    which is when institution took effect; the Federal Register publishes it
    days later. Falls back to the publication date.
    """
    match = _ISSUED.search(" ".join(notice.text.split()))
    if match:
        for fmt in ("%B %d, %Y", "%b. %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(match.group("date"), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return notice.publication_date


@dataclass(frozen=True)
class InstitutedClaims:
    patent: str | None
    verbatim: str
    claims: tuple[int, ...]
    quote: str
    sentence: str
    note: str | None = None


def instituted_claims(notice: Notice, patents: list[str]) -> list[InstitutedClaims]:
    """The claims a notice institutes, per patent, each with the exact words
    it came from. A claim list that does not parse, or a patent that is not on
    the record's list, is kept with a note rather than dropped.
    """
    found = []
    for sentence in split_sentences(notice.text):
        if not _INSTITUTED_SENTENCE.search(sentence):
            continue
        mentions = patent_mentions(sentence, patents)
        for ref in find_claim_refs(sentence):
            patent = patent_for(ref, sentence, patents)
            # The quote runs from the claim list to the patent it names, so it
            # is always an exact substring: "claims 1-3 of the '294 patent".
            end = next((m.end for m in mentions if m.start >= ref.end), ref.end)
            quote = sentence[ref.start : end]
            note = None
            try:
                claims = tuple(expand_claims(ref.verbatim))
            except ClaimListError as exc:
                claims, note = (), str(exc)
            if patent is None and note is None:
                note = "the patent named here is not on the record's patent list"
            found.append(InstitutedClaims(patent, ref.verbatim, claims, quote, sentence, note))
    return found
