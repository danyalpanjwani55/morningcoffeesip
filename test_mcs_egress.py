"""Tests for mcs_egress (BUILD-SPEC-03 §5c). Proves the data-boundary gate
independently of genesis (it now lives at the repo root), and proves the
re-export from genesis_contracts is the SAME object."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import mcs_egress
from mcs_egress import EgressGate, PrivateDataEgressError

# Make the genesis flat-import modules importable from the repo root so test #7
# can verify the re-export.
sys.path.insert(0, str(Path(__file__).resolve().parent / "genesis"))


def test_1_public_passes_through():
    s = "a tight public spec, no secrets"
    assert EgressGate().guard(s) == s


def test_2_secret_raises():
    with pytest.raises(PrivateDataEgressError):
        EgressGate().guard("api_key = sk-ABCD1234ABCD1234ABCD")  # pragma: allowlist secret


def test_3_unclassifiable_is_private():
    assert EgressGate().classify("") == "private"
    assert EgressGate().classify(None) == "private"
    assert EgressGate().classify("   ") == "private"


def test_4_password_is_private():
    assert EgressGate().classify("password: hunter2longvalue") == "private"  # pragma: allowlist secret


def test_5_ssn_shape_is_private():
    assert EgressGate().classify("123-45-6789") == "private"  # pragma: allowlist secret


def test_6_extra_pattern_hook():
    gate = EgressGate(extra_patterns=[re.compile("PROJECT-X")])
    assert gate.classify("about PROJECT-X") == "private"


def test_7_reexport_is_same_object():
    from genesis_contracts import EgressGate as G2
    assert G2 is mcs_egress.EgressGate
