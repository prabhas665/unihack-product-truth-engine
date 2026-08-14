"""Regression tests proving the official UniHack CSVs are fully removed.

These guardrails are the acceptance criteria for eliminating the production
dependency on the two official files:

1. An MPN that is NOT in the official sample dataset still enters the real
   discovery/enrichment flow and produces exactly 252 delivery columns.
2. The delivery always has exactly the 252 official headers.
3. The frozen header artifact is byte/exact compatible with the official
   contract (order + recorded SHA-256).
4. No production runtime code reads the official sample-input or
   expected-output CSV (verified statically and at request time).
"""

from __future__ import annotations

import builtins
import csv
import hashlib
import pathlib

import pytest
from fastapi.testclient import TestClient
from unittest import mock

from app.main import app
from app.unihack import DELIVERY_HEADERS, DeliverySchema
from app.unihack.delivery_headers import SOURCE_FILENAME, SOURCE_SHA256
from app.unihack.paths import delivery_fixture_path

UNKNOWN_MPN = "ZZZ-UNKNOWN-MPN-NOT-IN-DATASET"

_OFFICIAL_FILENAMES = {
    "unihack_ sample dataset - input.csv",
    "unihack_ expected output - delivery format.csv",
}


# --------------------------------------------------------------------------
# 1. unknown MPN still enters the real enrichment flow
# --------------------------------------------------------------------------


def test_unknown_mpn_enters_enrichment_flow():
    client = TestClient(app)
    response = client.post(
        "/api/enrich",
        json={
            "Mfg_Part_Num": UNKNOWN_MPN,
            "Part_Desc": "A gadget that is not in the official sample dataset",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["delivery"]["column_count"] == 252
    assert len(data["delivery"]["headers"]) == 252


# --------------------------------------------------------------------------
# 2. delivery always has exactly 252 official headers
# --------------------------------------------------------------------------


def test_frozen_schema_has_exactly_252_headers():
    schema = DeliverySchema.frozen()
    assert schema.count == 252
    assert len(DELIVERY_HEADERS) == 252
    assert all(name.strip() != "" for name in DELIVERY_HEADERS)
    assert len(set(DELIVERY_HEADERS)) == 252


# --------------------------------------------------------------------------
# 3. headers/order byte-exact compatible with the official contract
# --------------------------------------------------------------------------


def test_headers_byte_exact_with_official_contract():
    with delivery_fixture_path().open("r", encoding="utf-8-sig", newline="") as fh:
        reference_header = next(csv.reader(fh))
    # Exact header tokens, exact order.
    assert list(DELIVERY_HEADERS) == reference_header
    # The recorded SHA-256 must still match the fixture file.
    digest = hashlib.sha256(delivery_fixture_path().read_bytes()).hexdigest()
    assert digest == SOURCE_SHA256
    assert SOURCE_FILENAME.endswith("Unihack_ Expected Output - Delivery Format.csv")


# --------------------------------------------------------------------------
# 4. no runtime code reads the official CSVs
# --------------------------------------------------------------------------


def test_runtime_source_never_references_official_accessors():
    runtime_dirs = [
        pathlib.Path(__file__).resolve().parents[1] / d
        for d in [
            "app/api",
            "app/pipeline",
            "app/db",
            "app/identity",
            "app/sources",
            "app/validation",
            "app/core",
            "app/descriptions",
            "app/config",
            "app/evaluation",
            "app/unihack",
        ]
    ]
    forbidden = ["unihack_input_path", "delivery_reference_path"]
    hits: list[tuple[str, str]] = []
    for directory in runtime_dirs:
        for py_file in directory.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for symbol in forbidden:
                if symbol in text:
                    hits.append((str(py_file), symbol))
    assert hits == [], f"production references to official CSV accessors: {hits}"


def test_runtime_never_opens_official_csv():
    """A live request path must not open either official file."""
    real_open = builtins.open

    def guard(path, *args, **kwargs):
        if any(name in str(path).lower() for name in _OFFICIAL_FILENAMES):
            raise AssertionError(f"runtime opened official CSV: {path}")
        return real_open(path, *args, **kwargs)

    with mock.patch("builtins.open", side_effect=guard):
        client = TestClient(app)
        # Single enrichment for an unknown MPN.
        assert (
            client.post(
                "/api/enrich",
                json={"Mfg_Part_Num": UNKNOWN_MPN, "Part_Desc": "gadget"},
            ).status_code
            == 200
        )
        # Batch with caller-supplied rows.
        assert (
            client.post(
                "/api/batch", json={"rows": [{"Mfg_Part_Num": UNKNOWN_MPN}]}
            ).status_code
            == 200
        )
        # Lookup + dashboard read from the DB only.
        assert client.get("/api/lookup", params={"mpn": UNKNOWN_MPN}).status_code == 200
        assert client.get("/api/dashboard").status_code == 200
