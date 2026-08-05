"""Tests for the privacy-safe live acceptance record."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_live_acceptance import REQUIRED_SCENARIOS, validate


def test_example_acceptance_record_is_complete() -> None:
    path = Path(__file__).parent / "fixtures" / "live_acceptance.json"
    assert validate(json.loads(path.read_text(encoding="utf-8"))) == []


def test_acceptance_rejects_missing_scenario_and_sensitive_fields() -> None:
    record = {
        "format_version": 1,
        "release": "0.1.0",
        "home_assistant_version": "2026.7.0",
        "tested_at": "2026-08-05T12:00:00Z",
        "portal_profile": "synthetic-tenant",
        "capabilities": ["water_consumption"],
        "scenarios": {scenario: "pass" for scenario in REQUIRED_SCENARIOS},
        "evidence": {"meter_number": "must-not-be-recorded"},
    }
    record["scenarios"].pop("restart")

    errors = validate(record)
    assert any("restart" in error for error in errors)
    assert any("forbidden sensitive field" in error for error in errors)


def test_acceptance_requires_typed_portal_capabilities() -> None:
    record = {
        "format_version": 1,
        "release": "0.1.0",
        "home_assistant_version": "2026.7.0",
        "tested_at": "2026-08-05T12:00:00Z",
        "portal_profile": " ",
        "capabilities": ["water_consumption", "water_consumption"],
        "scenarios": {scenario: "pass" for scenario in REQUIRED_SCENARIOS},
    }

    errors = validate(record)
    assert "portal_profile must be a non-empty string" in errors
    assert "capabilities must not contain duplicates" in errors
