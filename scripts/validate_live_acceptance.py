#!/usr/bin/env python3
"""Validate a privacy-safe EnergyKey live-acceptance record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_SCENARIOS = frozenset(
    {
        "setup",
        "initial_background_load",
        "heartbeat",
        "restart",
        "authentication_rejection",
        "reauthentication",
        "delayed_data",
        "revised_data",
        "statistics_backfill",
        "removal",
    }
)
ALLOWED_RESULTS = frozenset({"pass", "not_applicable"})
FORBIDDEN_KEYS = frozenset(
    {
        "address",
        "cookie",
        "cookies",
        "customer_id",
        "item_id",
        "meter_id",
        "meter_number",
        "name",
        "session_id",
        "token",
        "value",
    }
)


def validate(record: Any) -> list[str]:
    """Return validation errors without echoing record contents."""
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record must be a JSON object"]
    if record.get("format_version") != 1:
        errors.append("format_version must be 1")
    for key in ("release", "home_assistant_version", "tested_at"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            errors.append(f"{key} must be a non-empty string")
    if (
        not isinstance(record.get("portal_profile"), str)
        or not record["portal_profile"].strip()
    ):
        errors.append("portal_profile must be a non-empty string")
    capabilities = record.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(isinstance(item, str) and item.strip() for item in capabilities)
    ):
        errors.append("capabilities must be a non-empty list of non-empty strings")
    elif len(capabilities) != len(set(capabilities)):
        errors.append("capabilities must not contain duplicates")

    scenarios = record.get("scenarios")
    if not isinstance(scenarios, dict):
        errors.append("scenarios must be an object")
    else:
        missing = REQUIRED_SCENARIOS - scenarios.keys()
        unexpected = scenarios.keys() - REQUIRED_SCENARIOS
        if missing:
            errors.append("missing required scenarios: " + ", ".join(sorted(missing)))
        if unexpected:
            errors.append("unknown scenarios: " + ", ".join(sorted(unexpected)))
        for scenario, result in scenarios.items():
            if result not in ALLOWED_RESULTS:
                errors.append(f"scenario {scenario} must be pass or not_applicable")

    _find_forbidden_keys(record, "$", errors)
    return errors


def _find_forbidden_keys(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in FORBIDDEN_KEYS:
                errors.append(f"forbidden sensitive field at {path}.{key}")
            _find_forbidden_keys(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _find_forbidden_keys(child, f"{path}[{index}]", errors)


def main() -> int:
    """Validate one record and print only a safe status message."""
    parser = argparse.ArgumentParser()
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        print(f"Invalid acceptance record: {type(err).__name__}")
        return 1
    errors = validate(record)
    if errors:
        print("Acceptance record failed validation:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Acceptance record is complete and contains no forbidden field names.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
