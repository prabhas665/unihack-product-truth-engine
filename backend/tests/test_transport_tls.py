"""Tests for the verified TLS-chain augmentation in the retrieval transport.

These are fully offline: they verify the vendored GoDaddy G2 intermediate
parses, chains to a certifi-trusted root, never disables verification, and
that the mock-transport (offline) path used by the rest of the suite is
unchanged.
"""

from __future__ import annotations

import base64
import ssl
from pathlib import Path

import certifi
import httpx
import pytest

from app.sources.retrieval.transport import (
    _EXTRA_CA_PEM,
    _verified_verify_arg,
    download,
)


def _cn_of(cert_dict: dict) -> str | None:
    for rdn in cert_dict.get("subject", []):
        for pair in rdn:
            if len(pair) == 2 and pair[0] == "commonName":
                return pair[1]
    return None


def test_vendored_pem_parses_and_is_godaddy_g2_intermediate():
    assert _EXTRA_CA_PEM.is_file(), "vendored intermediate PEM must be committed"
    text = _EXTRA_CA_PEM.read_text(encoding="utf-8")
    assert text.startswith("-----BEGIN CERTIFICATE-----")
    assert text.strip().endswith("-----END CERTIFICATE-----")

    # Loading it as a verify location proves it is a valid, parseable X.509
    # certificate (and is how it will be used at request time).
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=str(_EXTRA_CA_PEM))
    subjects = [_cn_of(c) for c in ctx.get_ca_certs()]
    assert "Go Daddy Secure Certificate Authority - G2" in subjects


def test_vendored_intermediate_chains_to_certifi_root():
    bundle = Path(certifi.where()).read_text(encoding="utf-8")
    # The intermediate's issuer (GoDaddy G2 root) must already be trusted by
    # certifi: adding the intermediate therefore grants no new trust authority.
    assert "Go Daddy Root Certificate Authority - G2" in bundle
    assert "Go Daddy Secure Certificate Authority - G2" not in bundle


def test_augmented_context_keeps_verification_enabled():
    ctx = _verified_verify_arg()
    assert ctx is not None, "vendored PEM present -> augmented context returned"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    subjects = [_cn_of(c) for c in ctx.get_ca_certs()]
    assert "Go Daddy Secure Certificate Authority - G2" in subjects


def test_no_verify_false_in_implementation():
    source = (
        Path(__file__).parent.parent
        / "app"
        / "sources"
        / "retrieval"
        / "transport.py"
    )
    text = source.read_text(encoding="utf-8")
    assert "verify=False" not in text
    assert "verify = False" not in text


def test_download_with_mock_transport_ignores_augmentation():
    """The injected mock transport path (used by other offline tests) is
    untouched: when a transport is supplied, no `verify` arg is added and the
    mock is used verbatim."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="body", headers={"content-type": "text/html"}
        )

    result = download(
        "https://example.invalid/product",
        limits=type("L", (), {"timeout_seconds": 5, "user_agent": "ua"})(),
        max_bytes=1024,
        transport=httpx.MockTransport(handler),
    )
    assert result[0] == "text/html"
    assert result[2] == b"body"
