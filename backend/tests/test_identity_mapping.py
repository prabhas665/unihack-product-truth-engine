"""Tests for verified-first identity resolution (Step 14B, D2)."""

from app.identity.mapping import (
    PLACEHOLDER_TOKENS,
    VerifiedBrandLookup,
    is_placeholder,
    resolve_verified_identity,
)


def test_is_placeholder_detects_official_and_variant_tokens():
    for token in (
        "-- Unbranded --",
        "-- No Unilog Brand --",
        "-- No DIB Brand --",
        "-",
        "COMMODITY - UNBRANDED",
        "",
        None,
    ):
        assert is_placeholder(token)
    assert not is_placeholder("DEWALT")
    assert not is_placeholder("Freud Inc (2435)")


def test_lookup_default_loads_bundled_seed():
    lookup = VerifiedBrandLookup.default()
    assert "PDSH4816AF" in lookup.by_mpn
    assert "dewalt" in lookup.by_brand
    assert "freud inc" in lookup.by_manufacturer


def test_resolve_by_mpn_wins_over_brand_and_manufacturer():
    lookup = VerifiedBrandLookup.default()
    verified = resolve_verified_identity(
        "PDSH4816AF",
        "-- Unbranded --",
        "-- No DIB Brand --",
        "Appliance Dealers Cooperative (APPDE)",
        lookup,
    )
    assert verified.manufacturer == "Electrolux"
    assert verified.brand == "FRIGIDAIRE"
    assert verified.provenance == "mpn"


def test_resolve_mpn_seed_rejected_when_brand_contradicts():
    lookup = VerifiedBrandLookup.default()
    verified = resolve_verified_identity(
        "XLC02ZW",
        "-- Unbranded --",
        "DEWALT",
        "Makita Usa Inc (5142)",
        lookup,
    )
    # DEWALT resolves in the registry to Stanley Black & Decker, which
    # contradicts the Makita seed: the seed must never be stamped.
    assert verified.manufacturer == "Stanley Black & Decker"
    assert verified.brand == "DEWALT"
    assert verified.provenance == "brand"


def test_resolve_mpn_seed_rejected_when_part_manuf_contradicts():
    lookup = VerifiedBrandLookup.default()
    verified = resolve_verified_identity(
        "WDTS7024RZ",
        "-- Unbranded --",
        "-- No DIB Brand --",
        "Freud Inc (2435)",
        lookup,
    )
    assert verified.manufacturer == "Freud"
    assert verified.brand == "Freud"
    assert verified.provenance == "manufacturer"


def test_resolve_mpn_seed_kept_when_input_compatible():
    lookup = VerifiedBrandLookup.default()
    verified = resolve_verified_identity(
        "XLC02ZW",
        "-- Unbranded --",
        "-- No DIB Brand --",
        "Makita Usa Inc (5142)",
        lookup,
    )
    assert verified.manufacturer == "Makita Usa Inc"
    assert verified.brand == "Makita"
    assert verified.provenance == "mpn"


def test_resolve_mpn_seed_kept_when_input_is_unknown_distributor():
    lookup = VerifiedBrandLookup.default()
    verified = resolve_verified_identity(
        "PDSH4816AF",
        "-- Unbranded --",
        "-- No DIB Brand --",
        "Acme Distribution LLC",
        lookup,
    )
    # An unknown distributor name is not a registry-backed signal and must
    # never defeat a correct seed.
    assert verified.manufacturer == "Electrolux"
    assert verified.brand == "FRIGIDAIRE"
    assert verified.provenance == "mpn"


def test_resolve_by_real_dib_brand():
    lookup = VerifiedBrandLookup.default()
    verified = resolve_verified_identity(
        "SOME-MPN",
        "-- Unbranded --",
        "DEWALT",
        "Jam Industrial Supply LLC (JAMIN)",
        lookup,
    )
    assert verified.brand == "DEWALT"
    assert verified.manufacturer == "Stanley Black & Decker"
    assert verified.provenance == "brand"


def test_resolve_by_real_part_manuf():
    lookup = VerifiedBrandLookup.default()
    verified = resolve_verified_identity(
        "SOME-MPN",
        "-- Unbranded --",
        "-- No DIB Brand --",
        "Freud Inc (2435)",
        lookup,
    )
    assert verified.manufacturer == "Freud"
    assert verified.brand == "Freud"
    assert verified.provenance == "manufacturer"


def test_resolve_blank_when_nothing_verified():
    lookup = VerifiedBrandLookup.default()
    verified = resolve_verified_identity(
        "UNKNOWN-MPN",
        "-- Unbranded --",
        "-- No DIB Brand --",
        "-",
        lookup,
    )
    assert verified.manufacturer == ""
    assert verified.brand == ""
    assert verified.provenance == ""


def test_resolve_ignores_placeholder_brand_tokens():
    lookup = VerifiedBrandLookup.default()
    verified = resolve_verified_identity(
        "UNKNOWN-MPN",
        "COMMODITY - UNBRANDED",
        "-- No DIB Brand --",
        "-",
        lookup,
    )
    assert verified.brand == ""


def test_domains_for_mpn_seed():
    lookup = VerifiedBrandLookup.default()
    assert lookup.domains_for(
        "WDTS7024RZ", "-- Unbranded --", "-- No DIB Brand --", "X (APPDE)",
    ) == ["whirlpool.com", "whirlpool.ca"]


def test_domains_for_whirlpool_mpn_includes_ca_and_com():
    lookup = VerifiedBrandLookup.default()
    out = lookup.domains_for("WDTS7024RZ", "-- Unbranded --", "-- No DIB Brand --", "-")
    assert "whirlpool.com" in out
    assert "whirlpool.ca" in out


def test_domains_for_real_brand_seed():
    lookup = VerifiedBrandLookup.default()
    assert lookup.domains_for(
        "SOME-MPN", "-- Unbranded --", "Milwaukee", "Jam Industrial Supply LLC (JAMIN)",
    ) == ["milwaukeetool.com"]


def test_domains_for_real_manufacturer_seed():
    lookup = VerifiedBrandLookup.default()
    assert lookup.domains_for(
        "SOME-MPN", "-- Unbranded --", "-- No DIB Brand --", "Makita Usa Inc (5142)",
    ) == ["makitatools.com"]


def test_domains_for_nothing_verified_is_empty():
    lookup = VerifiedBrandLookup.default()
    assert lookup.domains_for(
        "UNKNOWN-MPN", "-- Unbranded --", "-- No DIB Brand --", "-",
    ) == []


def test_domains_for_normalizes_and_dedupes():
    lookup = VerifiedBrandLookup.default()
    out = lookup.domains_for(
        "WDTS7024RZ", "-- Unbranded --", "-- No DIB Brand --", "Whirlpool Corporation",
    )
    # MPN seed already matched: brand/manufacturer paths are skipped.
    assert out == ["whirlpool.com", "whirlpool.ca"]
    # stored as bare (no scheme / no www)
    assert all(not d.startswith("www.") for d in out)
