"""Shared pytest fixtures for EnergyKey."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load this custom integration."""


@pytest.fixture
def load_fixture() -> Any:
    """Load a synthetic, sanitized EnergyKey JSON response."""

    def load(name: str) -> Any:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return load
