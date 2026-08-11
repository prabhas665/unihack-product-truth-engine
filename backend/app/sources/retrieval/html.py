"""HTML evidence fetcher: retrieve an approved URL and extract readable text.

Uses httpx for transport (already a project dependency) and the stdlib
HTMLParser for text extraction - no browser engine, no JS rendering.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from app.core.domain import SourceType
from app.sources.candidates import SourceCandidate
from app.sources.retrieval.limits import RetrievalLimits
from app.sources.retrieval.models import (
    EvidenceRecord,
    ExtractionStatus,
    RetrievalError,
    RetrievalErrorKind,
    RetrievalStatus,
)
from app.sources.retrieval.transport import download

HTML_SOURCE_TYPES = frozenset(
    {
        SourceType.MANUFACTURER_PRODUCT_PAGE,
        SourceType.MANUFACTURER_CATALOGUE,
        SourceType.MANUFACTURER_DIGITAL_ASSET,
        SourceType.UNKNOWN,
    }
)

ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


class _TextExtractor(HTMLParser):
    """Minimal readable-text extractor; drops script/style/noscript content."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag in (
            "p", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
            "tr", "div", "section",
        ):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0 and data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self._parts)).strip()


def extract_html_text(html: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception as exc:
        raise RetrievalError(
            RetrievalErrorKind.HTML_EXTRACTION,
            f"HTML text extraction failed: {exc}",
        ) from exc
    text = extractor.text()
    metadata = extract_page_metadata(html)
    if metadata:
        missing = "\n".join(
            part for part in metadata.split("\n") if part not in text
        )
        if missing:
            text = missing + "\n\n" + text
    return text


def extract_html_title(html: str) -> str:
    match = re.search(
        r"<title[^>]*>(.*?)</title>", html, flags=re.DOTALL | re.IGNORECASE
    )
    if not match:
        return ""
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_NAME_RE = re.compile(
    r'(?:name|property)\s*=\s*["\'](description|og:title|og:description)["\']',
    re.IGNORECASE,
)
_META_CONTENT_RE = re.compile(
    r'content\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE
)


def extract_page_metadata(html: str) -> str:
    """Page-level declarations (<title> + meta/OG description tags).

    Product pages often render their real content client-side; the title and
    meta description tags are the server-declared product identity, so they
    are made part of the extractable evidence text (entities decoded).
    """
    parts: list[str] = []
    title = extract_html_title(html)
    if title:
        parts.append(title)
    for tag in _META_TAG_RE.findall(html):
        if not _META_NAME_RE.search(tag):
            continue
        match = _META_CONTENT_RE.search(tag)
        if match:
            parts.append(unescape(match.group(1)).strip())
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            unique.append(part)
    return "\n".join(unique)


def extract_canonical_url(html: str, base_url: str) -> str:
    """Resolve the canonical link relative to the page URL, if present."""
    for match in re.finditer(r"<link\b[^>]*>", html, flags=re.IGNORECASE):
        tag = match.group(0)
        if not re.search(
            r'rel\s*=\s*["\']canonical["\']', tag, flags=re.IGNORECASE
        ):
            continue
        href = re.search(
            r'href\s*=\s*["\']([^"\']+)["\']', tag, flags=re.IGNORECASE
        )
        if href:
            return urljoin(base_url, href.group(1).strip())
    return ""


class HtmlFetcher:
    """Retrieves an approved HTTP(S) URL and extracts readable text."""

    name = "html"
    supported_types = HTML_SOURCE_TYPES

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        # transport injection keeps tests fully offline (httpx.MockTransport).
        self._transport = transport

    def supports(self, candidate: SourceCandidate) -> bool:
        return candidate.source_type in self.supported_types

    def fetch(
        self, candidate: SourceCandidate, limits: RetrievalLimits
    ) -> EvidenceRecord:
        content_type, final_url, body = download(
            candidate.url, limits, limits.max_bytes, self._transport
        )
        if not content_type.startswith(ALLOWED_CONTENT_TYPES):
            raise RetrievalError(
                RetrievalErrorKind.INVALID_CONTENT_TYPE,
                f"unexpected content type '{content_type}' for HTML fetch "
                f"of {candidate.url}",
                content_type=content_type,
            )

        html = body.decode("utf-8", errors="replace")
        text = extract_html_text(html)
        title = extract_html_title(html)
        canonical = extract_canonical_url(html, candidate.url)

        return EvidenceRecord(
            source_candidate_id=candidate.id or candidate.url,
            url=candidate.url,
            final_url=canonical or final_url,
            source_type=candidate.source_type,
            title=title,
            text=text,
            content_type=content_type,
            retrieval_status=RetrievalStatus.SUCCESS,
            extraction_status=ExtractionStatus.EXTRACTED,
        )
