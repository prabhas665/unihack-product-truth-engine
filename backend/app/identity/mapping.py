"""Verified-first identity resolution (Step 14B, D2).

The official delivery format expects MANUFACTURER_NAME / BRAND_NAME /
TRADE_NAME to carry the *verified* OEM identity, not the raw input tokens
(which are placeholders for ~80% of rows). This module resolves that identity
from trusted sources only:

1. MPN -> verified (manufacturer, brand) seed entry (highest confidence),
   BUT only when the input does NOT contradict it: an input brand or
   Part_Manuf token that itself resolves in the registry to a DIFFERENT
   company overrides/rejects the MPN seed so a wrong seed row can never be
   stamped as verified.
2. a real DIB/E1 brand token -> curated brand seed
3. a real Part_Manuf token -> curated manufacturer seed

Anything not backed by a trusted source stays blank - it is never filled with
a placeholder or a guessed value. Placeholder detection reuses the official
tokens from app.unihack.parser.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.unihack.parser import (
    PLACEHOLDER_DIB_BRAND,
    PLACEHOLDER_E1_BRAND,
    PLACEHOLDER_PART_MANUF,
    PLACEHOLDER_UNILOG_BRAND,
)

# Data variants seen in the dataset that are not the four official tokens.
_EXTRA_PLACEHOLDERS = {"COMMODITY - UNBRANDED", "COMMODITY-UNBRANDED"}

PLACEHOLDER_TOKENS = {
    PLACEHOLDER_E1_BRAND,
    PLACEHOLDER_UNILOG_BRAND,
    PLACEHOLDER_DIB_BRAND,
    PLACEHOLDER_PART_MANUF,
    "",
} | _EXTRA_PLACEHOLDERS


def _normalize_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d


@dataclass
class VerifiedIdentity:
    manufacturer: str = ""
    brand: str = ""
    trade_name: str = ""
    provenance: str = ""


@dataclass
class VerifiedBrandLookup:
    by_mpn: dict[str, dict] = field(default_factory=dict)
    by_brand: dict[str, dict] = field(default_factory=dict)
    by_manufacturer: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "VerifiedBrandLookup":
        """Load the bundled seed (backend/data/verified_brands.json)."""
        path = (
            Path(__file__).resolve().parent.parent.parent
            / "data"
            / "verified_brands.json"
        )
        if not path.is_file():
            return cls()
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            by_mpn={k.upper(): v for k, v in data.get("by_mpn", {}).items()},
            by_brand={
                k.lower(): v for k, v in data.get("by_brand", {}).items()
            },
            by_manufacturer={
                k.lower(): v for k, v in data.get("by_manufacturer", {}).items()
            },
        )

    def domains_for(
        self,
        mpn: str | None,
        e1_brand: str | None,
        dib_brand: str | None,
        part_manuf: str | None,
    ) -> list[str]:
        """Curated manufacturer domains the product is *allowed* to trust.

        Mirrors ``resolve_verified_identity`` priority so a domain is only
        trusted when the product is itself verified to that manufacturer:
        1. MPN seed -> domains
        2. a real DIB/E1 brand seed -> domains
        3. a real Part_Manuf seed -> domains
        """
        domains: list[str] = []

        def collect(entry: dict) -> None:
            for d in entry.get("domains", []) or []:
                norm = _normalize_domain(d)
                if norm:
                    domains.append(norm)

        if mpn and mpn.upper() in self.by_mpn:
            collect(self.by_mpn[mpn.upper()])

        if not domains:
            candidate = (
                dib_brand
                if not is_placeholder(dib_brand)
                else (e1_brand if not is_placeholder(e1_brand) else "")
            )
            if candidate and candidate.lower() in self.by_brand:
                collect(self.by_brand[candidate.lower()])

        if not domains and part_manuf and not is_placeholder(part_manuf):
            base = part_manuf.split("(")[0].strip().lower()
            key = base or part_manuf.lower()
            if key in self.by_manufacturer:
                collect(self.by_manufacturer[key])

        seen: set[str] = set()
        out: list[str] = []
        for d in domains:
            if d not in seen:
                seen.add(d)
                out.append(d)
        return out


def is_placeholder(value: str | None) -> bool:
    """True for any official placeholder token, blank, or known data variant."""
    if value is None:
        return True
    return value.strip() in PLACEHOLDER_TOKENS


def _normalize_company(value: str) -> str:
    """Lowercase, punctuation-stripped form used for company comparison."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _company_tokens(value: str) -> set[str]:
    """Alphanumeric tokens of a company name (used for fuzzy matching)."""
    return set(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _same_company(a: str, b: str) -> bool:
    """True when two company names plausibly denote the same entity."""
    if not a or not b:
        return False
    na, nb = _normalize_company(a), _normalize_company(b)
    if na == nb:
        return True
    ta, tb = _company_tokens(a), _company_tokens(b)
    return bool(ta and tb and (ta & tb))


def _seed_contradicted(
    entry: dict,
    dib_brand: str | None,
    e1_brand: str | None,
    part_manuf: str | None,
    lookup: "VerifiedBrandLookup",
) -> bool:
    """True when a registry-backed input signal contradicts an MPN seed.

    Only inputs that RESOLVE in the registry count as signals - an unknown
    distributor name ("Appliance Dealers Cooperative") or a placeholder never
    triggers a conflict, so a correct seed is not lost to noise. A seed whose
    manufacturer/brand differs from a resolved input signal is rejected so a
    wrong seed row (e.g. a dirty curated entry) can never ship as "verified".
    """
    seed_manuf = (entry.get("manufacturer", "") or "").strip()
    seed_brand = (entry.get("brand", "") or "").strip()

    brand_signal = ""
    for candidate in (dib_brand, e1_brand):
        if candidate and not is_placeholder(candidate):
            brand_signal = candidate.strip()
            break
    if brand_signal:
        resolved = lookup.by_brand.get(brand_signal.lower())
        if resolved:
            r_manuf = (resolved.get("manufacturer", "") or "").strip()
            r_brand = (resolved.get("brand", "") or "").strip()
            if r_manuf and seed_manuf and not _same_company(r_manuf, seed_manuf):
                return True
            if r_brand and seed_brand and not _same_company(r_brand, seed_brand):
                return True

    if part_manuf and not is_placeholder(part_manuf):
        base = part_manuf.split("(")[0].strip().lower()
        key = base or part_manuf.lower()
        resolved = lookup.by_manufacturer.get(key)
        if resolved:
            r_manuf = (resolved.get("manufacturer", "") or "").strip()
            if r_manuf and seed_manuf and not _same_company(r_manuf, seed_manuf):
                return True

    return False


def resolve_verified_identity(
    mpn: str,
    e1_brand: str,
    dib_brand: str,
    part_manuf: str,
    lookup: VerifiedBrandLookup,
) -> VerifiedIdentity:
    """Resolve the verified (manufacturer, brand, trade_name) for a product.

    Priority: MPN seed (unless contradicted by a registry-backed input
    signal) -> real DIB/E1 brand seed -> real Part_Manuf seed.
    Returns an empty (blank) identity when nothing is verified.
    """
    manufacturer = ""
    brand = ""
    trade_name = ""
    provenance: list[str] = []

    mpn_entry = lookup.by_mpn.get(mpn.upper()) if mpn else None
    if mpn_entry and not _seed_contradicted(
        mpn_entry, dib_brand, e1_brand, part_manuf, lookup
    ):
        manufacturer = mpn_entry.get("manufacturer", "") or ""
        brand = mpn_entry.get("brand", "") or ""
        trade_name = mpn_entry.get("trade_name", "") or ""
        provenance.append("mpn")

    if not brand:
        candidate = (
            dib_brand
            if not is_placeholder(dib_brand)
            else (e1_brand if not is_placeholder(e1_brand) else "")
        )
        if candidate and candidate.lower() in lookup.by_brand:
            entry = lookup.by_brand[candidate.lower()]
            brand = entry.get("brand", "") or candidate
            if not manufacturer:
                manufacturer = entry.get("manufacturer", "") or ""
            provenance.append("brand")

    if not manufacturer and part_manuf and not is_placeholder(part_manuf):
        # Part_Manuf often carries a parenthetical code, e.g. "Freud Inc (2435)".
        base = part_manuf.split("(")[0].strip().lower()
        key = base or part_manuf.lower()
        if key in lookup.by_manufacturer:
            entry = lookup.by_manufacturer[key]
            if entry.get("manufacturer"):
                manufacturer = entry["manufacturer"]
            if not brand and entry.get("brand"):
                brand = entry["brand"]
            if entry.get("manufacturer") or entry.get("brand"):
                provenance.append("manufacturer")

    return VerifiedIdentity(
        manufacturer=manufacturer,
        brand=brand,
        trade_name=trade_name,
        provenance=";".join(provenance),
    )
