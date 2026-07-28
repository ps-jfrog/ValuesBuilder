"""Loader for the bundled reference-architecture sizing data."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

import yaml


@lru_cache(maxsize=1)
def load_reference_architecture() -> dict[str, Any]:
    data_text = resources.files("jfvb.data").joinpath(
        "reference_architecture.yaml"
    ).read_text(encoding="utf-8")
    return yaml.safe_load(data_text)


def get_tier(tier_name: str) -> dict[str, Any]:
    """Return the reference-architecture entry for a tier name.

    Raises KeyError with a helpful message if the tier isn't in the table
    (e.g. "custom", which the caller is expected to handle separately).
    """

    tiers = load_reference_architecture()["tiers"]
    if tier_name not in tiers:
        raise KeyError(
            f"'{tier_name}' has no reference-architecture entry "
            f"(available: {sorted(tiers)})"
        )
    return tiers[tier_name]


def chart_sizing_profile_path(tier_name: str) -> str | None:
    paths = load_reference_architecture().get("chart_sizing_profile_files", {})
    return paths.get(tier_name)
