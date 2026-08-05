"""Chart-version comparison and version-gated feature registry.

Rules use this to pick the right values.yaml path (or question/behavior)
for the chart version the user is targeting. Adding a new use case is:

1. Register a `ChartFeature` in `FEATURES` with `since` / `until`.
2. Branch in the relevant rule with `feature_active("my_feature", answers.chart_version)`.

Version strings are JFrog chart versions (`11.5.9`, `11.5.13`, …). Pre-release
suffixes after `-` / `+` are ignored for comparison. `None` / `"latest"` means
"current published chart" and is treated as satisfying every `since` gate
(and no `until` gate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class ChartVersionError(ValueError):
    """Raised when a chart version string cannot be parsed."""


def parse_chart_version(version: str) -> tuple[int, ...]:
    """Parse a chart version into a comparable integer tuple.

    Accepts `11.5.13`, `v11.5.13`, and ignores a trailing pre-release /
    build metadata suffix (`11.5.13-rc.1`, `11.5.13+meta`).
    """

    raw = version.strip()
    if raw.startswith(("v", "V")):
        raw = raw[1:]
    for sep in ("-", "+"):
        if sep in raw:
            raw = raw.split(sep, 1)[0]
    if not raw or raw.lower() == "latest":
        raise ChartVersionError(f"not a concrete chart version: {version!r}")

    parts = raw.split(".")
    try:
        numbers = tuple(int(p) for p in parts)
    except ValueError as exc:
        raise ChartVersionError(f"invalid chart version: {version!r}") from exc
    if not numbers:
        raise ChartVersionError(f"invalid chart version: {version!r}")
    return numbers


def is_concrete_version(version: Optional[str]) -> bool:
    if version is None:
        return False
    try:
        parse_chart_version(version)
        return True
    except ChartVersionError:
        return version.strip().lower() != "latest"


def version_at_least(version: Optional[str], minimum: str) -> bool:
    """True if `version` is >= `minimum`, or version is unset/latest."""

    if version is None or str(version).strip().lower() == "latest":
        return True
    return parse_chart_version(version) >= parse_chart_version(minimum)


def version_before(version: Optional[str], exclusive_upper: str) -> bool:
    """True if `version` is < `exclusive_upper`. Unset/latest is never before."""

    if version is None or str(version).strip().lower() == "latest":
        return False
    return parse_chart_version(version) < parse_chart_version(exclusive_upper)


@dataclass(frozen=True)
class ChartFeature:
    """A documented behavior that depends on chart version.

    - `since`: first chart version where the feature is available (inclusive).
      None means available from the earliest supported chart.
    - `until`: first chart version where the feature is *no longer* the
      preferred path (exclusive). None means still current.
    """

    id: str
    description: str
    since: Optional[str] = None
    until: Optional[str] = None
    # Human-readable note shown in SUMMARY when this feature drives output.
    notes: str = ""


# Registry of known version-gated use cases. Rules look these up by id.
FEATURES: dict[str, ChartFeature] = {
    "airgap_extra_system_yaml": ChartFeature(
        id="airgap_extra_system_yaml",
        description=(
            "Air-gap flag via artifactory.artifactory.extraSystemYaml."
            "jfconnect.airgap.enabled (pre-first-class values key)"
        ),
        until="11.5.13",
        notes=(
            "On charts before 11.5.13, jfconnect.airgap is not a first-class "
            "values key on the platform chart; inject it through "
            "extraSystemYaml (double-nested under artifactory.artifactory)."
        ),
    ),
    "nginx_tls_required": ChartFeature(
        id="nginx_tls_required",
        description=(
            "Bundled nginx requires an explicit HTTPS decision "
            "(tlsSecretName / generateSelfSignedCert / https.enabled=false)"
        ),
        since="11.6.0",
        notes=(
            "From chart 11.6.0 the platform chart fails to render when "
            "bundled nginx is enabled without one of: "
            "artifactory.nginx.tlsSecretName, "
            "artifactory.nginx.generateSelfSignedCert, or "
            "artifactory.nginx.https.enabled=false. Confirmed by rendering "
            "11.5.13 (no requirement) against 11.6.0 (hard fail)."
        ),
    ),
    "airgap_jfconnect_values": ChartFeature(
        id="airgap_jfconnect_values",
        description=(
            "Air-gap flag via first-class artifactory.jfconnect.airgap.enabled "
            "on the jfrog-platform chart"
        ),
        since="11.5.13",
        notes=(
            "From chart 11.5.13, the platform chart exposes "
            "artifactory.jfconnect.airgap.enabled directly in values."
        ),
    ),
}


def get_feature(feature_id: str) -> ChartFeature:
    try:
        return FEATURES[feature_id]
    except KeyError as exc:
        known = ", ".join(sorted(FEATURES))
        raise KeyError(f"unknown chart feature {feature_id!r} (known: {known})") from exc


def feature_active(feature_id: str, chart_version: Optional[str]) -> bool:
    """Whether `feature_id` applies for the given chart version."""

    feature = get_feature(feature_id)
    if feature.since is not None and not version_at_least(chart_version, feature.since):
        return False
    if feature.until is not None and not version_before(chart_version, feature.until):
        return False
    return True


def active_features(chart_version: Optional[str]) -> list[ChartFeature]:
    """All registered features active for `chart_version`, stable id order."""

    return [
        feature
        for feature_id, feature in sorted(FEATURES.items())
        if feature_active(feature_id, chart_version)
    ]


def describe_version_context(chart_version: Optional[str]) -> str:
    if chart_version is None or str(chart_version).strip().lower() == "latest":
        return "latest (version-gated features use the newest path)"
    return str(chart_version)
