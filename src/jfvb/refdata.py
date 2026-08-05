"""Loader for the bundled reference-architecture sizing data."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any, Optional

import yaml

# Ordered from smallest to largest for recommend_tier().
_TIER_ORDER = ("small", "medium", "large", "xlarge", "2xlarge")


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


def get_tier_load(tier_name: str) -> dict[str, Any] | None:
    """Return the cluster-wide load criteria for a named tier, or None."""

    try:
        return get_tier(tier_name).get("load")
    except KeyError:
        return None


def format_tier_choice_label(tier_name: str) -> str:
    """Human-readable select-menu label including RPM / concurrent connections."""

    load = get_tier_load(tier_name)
    if not load:
        return tier_name
    rpm = load["peak_rpm"]
    conc = load["peak_concurrent_connections"]
    use_case = load.get("use_case", "")
    rpm_label = f"{rpm:,}" if isinstance(rpm, int) else str(rpm)
    conc_label = f"{conc:,}" if isinstance(conc, int) else str(conc)
    suffix = f" — {use_case}" if use_case else ""
    return (
        f"{tier_name}  (≤ {rpm_label} RPM / ≤ {conc_label} concurrent"
        f" connections, cluster-wide){suffix}"
    )


def recommend_tier(
    peak_rpm: Optional[int] = None,
    peak_concurrent_connections: Optional[int] = None,
) -> Optional[str]:
    """Pick the smallest tier that covers the given cluster-wide load.

    Returns None if neither metric is provided. Raises ValueError if the
    load exceeds every published template.
    """

    if peak_rpm is None and peak_concurrent_connections is None:
        return None

    for name in _TIER_ORDER:
        load = get_tier_load(name) or {}
        rpm_ok = peak_rpm is None or peak_rpm <= load.get("peak_rpm", 0)
        conc_ok = (
            peak_concurrent_connections is None
            or peak_concurrent_connections
            <= load.get("peak_concurrent_connections", 0)
        )
        if rpm_ok and conc_ok:
            return name

    raise ValueError(
        "load exceeds the largest published template (2xlarge: "
        "≤ 500,000 RPM / ≤ 6,000 concurrent connections); contact "
        "JFrog Support for custom sizing"
    )


def chart_sizing_profile_path(tier_name: str) -> str | None:
    paths = load_reference_architecture().get("chart_sizing_profile_files", {})
    return paths.get(tier_name)
