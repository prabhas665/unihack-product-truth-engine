"""Identity bootstrap: resolve manufacturer identity for unknown MPNs.

When the verified brand registry does not contain the MPN, this module:
1. Searches the web for the exact MPN (no domain restrictions)
2. Retrieves candidate pages (bypassing SourcePolicy -- read-only verification)
3. Verifies exact MPN presence in page text
4. Validates manufacturer consistency against Part_Manuf input
5. Checks for sibling product contamination
6. Returns a bootstrapped identity with trust domain

This is NOT a trust policy weakening -- it is a pre-verification stage
that feeds into the normal trusted pipeline. SourcePolicy is never modified.

Provenance: bootstrap runs are tagged with trust_status="run_verified"
and carry the original source URL, evidence_id, and verification reason.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

from app.config import settings
from app.core.domain import ProductIdentity, SourceType
from app.identity.mapping import _company_tokens
from app.sources.candidates import (
    CandidateStatus,
    DiscoveryMethod,
    SourceCandidate,
    normalize_domain,
)
from app.sources.discovery import DiscoveryContext, SourceProvider
from app.sources.errors import ProviderUnavailableError
from app.sources.providers.search import build_search_query
from app.sources.retrieval.models import (
    EvidenceRecord,
    RetrievalStatus,
)
from app.utils.retry import retry_call

MARKETPLACE_LABELS = frozenset({"amazon", "ebay", "aliexpress", "alibaba"})

_MPN_TOKEN_RE = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*")
_DIGIT_RE = re.compile(r"\d")

_COMPANY_SUFFIX_RE = re.compile(
    r"([A-Z][A-Za-z0-9\s&.,'-]{2,50})\s+"
    r"(Inc|LLC|Ltd|Corp|GmbH|AG|SAS|SRL|BV|Pty|Co|SA|Pty Ltd)\b"
)

_PLACEHOLDER_TOKENS = frozenset({
    "", "-- No DIB Brand --", "-- No Unilog Brand --",
    "-- Unbranded --", "-- No Part Manuf --",
    "COMMODITY - UNBRANDED", "COMMODITY-UNBRANDED",
})

_GENERIC_DOMAINS = frozenset({
    "example.com", "example.org", "example.net",
    "test.com", "localhost", "invalid",
})

_SUFFIX_TOKENS = frozenset({
    "inc", "llc", "ltd", "corp", "gmbh", "ag", "sas", "srl",
    "bv", "pty", "co", "sa", "pby", "kg", "ab",
})


def _strict_company_match(a: str, b: str) -> bool:
    """Company name match that ignores common corporate suffix tokens.

    Filters out Inc/LLC/GmbH/etc tokens before comparing so that
    'Freud Inc' and 'Bosch Power Tools Inc' are NOT treated as the
    same company (they only share the suffix 'Inc').
    """
    if not a or not b:
        return False
    tokens_a = _company_tokens(a) - _SUFFIX_TOKENS
    tokens_b = _company_tokens(b) - _SUFFIX_TOKENS
    if not tokens_a or not tokens_b:
        return False
    return bool(tokens_a & tokens_b)


@dataclass
class BootstrapProvenance:
    source_url: str
    evidence_id: str
    verification_reason: str
    trust_status: str = "run_verified"


@dataclass
class BootstrapResult:
    success: bool
    manufacturer: str = ""
    brand: str = ""
    domain: str = ""
    evidence_summary: str = ""
    failure_reason: str = ""
    provenance: BootstrapProvenance | None = None
    bootstrap_evidence: list[EvidenceRecord] = field(default_factory=list)


def _mpn_tokens(value: str) -> set[str]:
    return {
        token
        for token in _MPN_TOKEN_RE.findall((value or "").upper())
        if len(token) >= 4 and _DIGIT_RE.search(token)
    }


def _is_marketplace(domain: str) -> bool:
    labels = {part for part in domain.split(".") if part}
    return bool(labels & MARKETPLACE_LABELS)


def _filter_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    out: list[SourceCandidate] = []
    seen: set[str] = set()
    for c in candidates:
        url = (c.url or "").strip()
        if not url or url in seen:
            continue
        try:
            scheme = urlsplit(url).scheme.lower()
        except ValueError:
            continue
        if scheme not in ("http", "https"):
            continue
        domain = normalize_domain(url)
        if _is_marketplace(domain):
            continue
        seen.add(url)
        out.append(c)
    return out


def _mpn_present_in_text(text: str, mpn: str) -> bool:
    if not text or not mpn:
        return False
    return mpn.upper() in text.upper()


def _check_sibling_contamination(
    text: str, url: str, title: str, mpn: str
) -> tuple[bool, str]:
    requested_tokens = _mpn_tokens(mpn)
    if not requested_tokens:
        return True, ""
    all_record_tokens = (
        _mpn_tokens(url) | _mpn_tokens(title) | _mpn_tokens(text)
    )
    exact = requested_tokens & all_record_tokens
    if exact:
        return True, ""
    # Flag sibling tokens with similar product-code structure.
    # Exclude all-digit tokens (year ranges like 2020, 2020-2025).
    # If requested has hyphens, flag tokens with hyphens.
    # If requested has no hyphens, flag tokens with hyphens (product-like) AND tokens without hyphens.
    requested_has_hyphen = any("-" in t for t in requested_tokens)
    sibling_tokens = {
        t for t in (all_record_tokens - requested_tokens)
        if not t.isdigit() and (
            (requested_has_hyphen and "-" in t) or
            (not requested_has_hyphen and "-" in t) or
            (not requested_has_hyphen and "-" not in t)
        )
    }
    if sibling_tokens:
        return False, f"sibling product tokens: {','.join(sorted(sibling_tokens))}"
    return True, ""


def _extract_manufacturer_from_text(text: str, url: str, title: str) -> str:
    search_text = text[:3000] if text else ""
    combined = f"{title or ''} {search_text}"
    for match in _COMPANY_SUFFIX_RE.finditer(combined):
        name = match.group(0).strip()
        name = re.sub(r"\s+", " ", name)
        if len(name) >= 5:
            return name
    # Fallback: extract manufacturer from domain name (e.g., diablotools.com -> Diablo)
    domain = normalize_domain(url)
    if domain and domain not in _GENERIC_DOMAINS:
        base = domain.split(".")[0] if "." in domain else domain
        if base and len(base) >= 3 and not _is_marketplace(domain):
            return base.capitalize()
    return ""


def _extract_brand_from_text(text: str, url: str, title: str, mpn: str | None = None) -> str:
    if title:
        parts = re.split(r"[\-\u2013|,]", title, maxsplit=1)
        if parts:
            candidate = parts[0].strip()
            if 2 <= len(candidate) <= 60:
                # MPN must never be interpreted as brand
                if mpn and candidate.strip().upper() == mpn.strip().upper():
                    return ""
                # Also guard against MPN token appearing as brand when title is just MPN
                if mpn and _mpn_tokens(candidate) & _mpn_tokens(mpn):
                    # If candidate is exactly the MPN token set, not a brand
                    if candidate.strip().upper() in {t.upper() for t in _mpn_tokens(mpn)}:
                        return ""
                return candidate
    return ""


def _verify_strong_evidence(
    text: str,
    url: str,
    title: str,
    mpn: str,
    part_manuf_input: str,
) -> tuple[bool, str]:
    if not _mpn_present_in_text(text, mpn):
        return False, f"MPN '{mpn}' not found in page text"

    domain = normalize_domain(url)
    if _is_marketplace(domain):
        return False, f"marketplace domain: {domain}"

    is_clean, sibling_reason = _check_sibling_contamination(text, url, title, mpn)
    if not is_clean:
        return False, f"sibling contamination: {sibling_reason}"

    url_path_has_mpn = _mpn_present_in_text(url, mpn)

    extracted_manufacturer = _extract_manufacturer_from_text(text, url, title)

    part_manuf_clean = (part_manuf_input or "").strip()
    is_placeholder = part_manuf_clean in _PLACEHOLDER_TOKENS

    if not is_placeholder and part_manuf_clean:
        if not extracted_manufacturer:
            return False, "Part_Manuf provided but no manufacturer extracted from page"
        if not _strict_company_match(extracted_manufacturer, part_manuf_clean):
            manuf_tokens = _company_tokens(part_manuf_clean) - _SUFFIX_TOKENS
            domain_tokens = _company_tokens(domain) - _SUFFIX_TOKENS
            if not (manuf_tokens & domain_tokens):
                # Accept when the exact MPN appears in the URL path on a
                # non-generic, non-marketplace domain — this is the strongest
                # product identity signal (the site explicitly claims this product).
                if not (url_path_has_mpn and domain not in _GENERIC_DOMAINS):
                    return False, (
                        f"manufacturer mismatch: page='{extracted_manufacturer}' "
                        f"vs input='{part_manuf_clean}'"
                    )
    else:
        if not extracted_manufacturer:
            return False, "no manufacturer signal found on page and no Part_Manuf input"

    return True, "MPN verified, manufacturer consistent, no contamination"


def _select_domain(evidence: list[EvidenceRecord]) -> str:
    domains = [normalize_domain(r.url) for r in evidence if normalize_domain(r.url)]
    if not domains:
        return ""
    return Counter(domains).most_common(1)[0][0]


def _gemini_fallback_search(product: ProductIdentity) -> list[SourceCandidate]:
    """Use Gemini grounding API directly when normal providers return no results."""
    from app.sources.providers.gemini_search import (
        GeminiSearchApiClient,
        _build_candidates,
        _parse_grounding_chunks,
    )

    keys = list(settings.gemini_api_keys or [])
    if not keys:
        return []

    mpn = (product.mpn or "").strip()
    manufacturer = (product.manufacturer or "").strip()
    brand = (product.brand or "").strip()
    query = f"{mpn}"
    if manufacturer:
        query = f"{manufacturer} {query}"
    elif brand:
        query = f"{brand} {query}"

    try:
        client = GeminiSearchApiClient(api_keys=keys)
        response = client.grounding_request(query)
        chunks = _parse_grounding_chunks(response)
        return _build_candidates(chunks, product)
    except Exception:
        return []


def _duckduckgo_fallback_search(product: ProductIdentity) -> list[SourceCandidate]:
    """Use DuckDuckGo free search when normal providers and Gemini return nothing."""
    try:
        from app.sources.providers.duckduckgo_search import DuckDuckGoSearchProvider
        provider = DuckDuckGoSearchProvider()
        return provider.discover(product, None)
    except Exception:
        return []


def bootstrap_identity(
    product: ProductIdentity,
    providers: list[SourceProvider],
    retriever: Callable[[SourceCandidate], EvidenceRecord],
    *,
    part_manuf_input: str = "",
    max_candidates: int = 3,
) -> BootstrapResult:
    mpn = (product.mpn or "").strip()
    if not mpn:
        return BootstrapResult(
            success=False, failure_reason="no MPN provided for bootstrap"
        )

    query = build_search_query(product)
    if not query:
        return BootstrapResult(
            success=False, failure_reason="could not build search query"
        )

    all_candidates: list[SourceCandidate] = []
    for provider in providers:
        try:
            ctx = DiscoveryContext(
                product=product,
                manufacturer_domains=[],
                query_biased=False,
            )
            result = retry_call(
                lambda: provider.discover(product, ctx),
                attempts=settings.discovery_retry_attempts,
                base_delay=settings.retry_base_delay_seconds,
                should_retry=lambda exc: isinstance(
                    exc, ProviderUnavailableError
                ),
            )
            all_candidates.extend(result)
        except Exception:
            continue

    if not all_candidates:
        gemini_fallback = _gemini_fallback_search(product)
        if gemini_fallback:
            all_candidates.extend(gemini_fallback)

    if not all_candidates:
        ddg_fallback = _duckduckgo_fallback_search(product)
        if ddg_fallback:
            all_candidates.extend(ddg_fallback)

    if not all_candidates:
        return BootstrapResult(success=False, failure_reason="no search results found")

    filtered = _filter_candidates(all_candidates)
    if not filtered:
        return BootstrapResult(
            success=False,
            failure_reason="all candidates filtered (marketplace or non-http)",
        )

    verified_evidence: list[EvidenceRecord] = []
    retrieved_urls: set[str] = set()
    last_failure_reason = ""
    for candidate in filtered[:max_candidates]:
        url = (candidate.url or "").strip()
        if url in retrieved_urls:
            continue
        retrieved_urls.add(url)
        allowed_candidate = candidate.model_copy(
            update={"status": CandidateStatus.ALLOWED}
        )
        try:
            record = retriever(allowed_candidate)
        except Exception:
            continue
        if record.retrieval_status != RetrievalStatus.SUCCESS:
            continue
        if not (record.text or "").strip():
            last_failure_reason = f"empty text for {url}"
            continue
        passed, reason = _verify_strong_evidence(
            record.text or "",
            record.url or "",
            record.title or "",
            mpn,
            part_manuf_input,
        )
        if passed:
            verified_evidence.append(record)
        else:
            last_failure_reason = f"{url}: {reason}"

    if not verified_evidence:
        msg = last_failure_reason or "MPN verification failed on all retrieved pages"
        return BootstrapResult(success=False, failure_reason=msg)

    domain = _select_domain(verified_evidence)
    if not domain:
        return BootstrapResult(
            success=False,
            failure_reason="could not determine domain from verified evidence",
        )

    manufacturer = ""
    for record in verified_evidence:
        mfr = _extract_manufacturer_from_text(
            record.text or "", record.url or "", record.title or ""
        )
        if mfr:
            manufacturer = mfr
            break

    brand = ""
    for record in verified_evidence:
        br = _extract_brand_from_text(
            record.text or "", record.url or "", record.title or "", mpn=mpn
        )
        if br:
            brand = br
            break

    first = verified_evidence[0]
    provenance = BootstrapProvenance(
        source_url=first.url or "",
        evidence_id=first.evidence_id,
        verification_reason=(
            f"{len(verified_evidence)} page(s) verified; "
            f"MPN present, manufacturer consistent, no contamination"
        ),
    )

    summary = (
        f"{len(verified_evidence)}/{len(filtered)} candidates verified; "
        f"domain={domain}"
    )

    return BootstrapResult(
        success=True,
        manufacturer=manufacturer,
        brand=brand,
        domain=domain,
        evidence_summary=summary,
        provenance=provenance,
        bootstrap_evidence=verified_evidence,
    )
