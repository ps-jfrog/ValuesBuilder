"""Reconciles JFrog's reference-architecture sizing with the chart's own
bundled sizing profiles into a single values.yaml patch.

Precedence rule (per explicit product decision): reference-architecture
numbers win wherever they overlap with the chart's `sizing/platform-*.yaml`
profiles (replica counts, nginx replicas, and -- most notably -- bundled
PostgreSQL CPU/memory, which the chart's profiles under-size by roughly
half versus the equivalent managed RDS instance at every tier). Anything
the reference architecture does not specify (JVM heap percentages, Tomcat
thread pools, HPA min/max bounds, per-microservice resource requests) is
taken from the chart's live-pulled sizing profile as a baseline, so we
don't have to reinvent tuning JFrog has already published.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from jfvb.answers import Answers, SizeTier
from jfvb.merge import deep_merge
from jfvb.refdata import chart_sizing_profile_path, get_tier
from jfvb.rules.base import RuleResult


def _get_path(node: dict[str, Any], path: list[str]) -> Any:
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _with_max_connections(existing_config: str | None, max_connections: int) -> str:
    existing = existing_config or ""
    if "max_connections" in existing:
        return re.sub(
            r"max_connections\s*=\s*\d+",
            f"max_connections = {max_connections}",
            existing,
        )
    if existing.strip():
        return existing.rstrip("\n") + f"\nmax_connections = {max_connections}\n"
    return f"max_connections = {max_connections}\n"


def _aws_annotation(component: dict[str, Any]) -> str:
    return (
        f"{component.get('aws_instance_type', '?')} "
        f"({component['vcpu']} vCPU / {component['memory_gb']}GiB)"
    )


def load_chart_baseline(tier_name: str, chart_dir: Path | None) -> dict[str, Any]:
    """Load the chart's own sizing/platform-<tier>.yaml, if we have a
    live-pulled chart available. Returns {} if unavailable."""

    if not chart_dir:
        return {}
    rel_path = chart_sizing_profile_path(tier_name)
    if not rel_path:
        return {}
    profile_path = chart_dir / rel_path
    if not profile_path.exists():
        return {}
    return yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}


def _prune_irrelevant_baseline_sections(
    chart_baseline: dict[str, Any], answers: Answers
) -> dict[str, Any]:
    """The chart's sizing/platform-*.yaml files unconditionally bundle
    tuning for artifactory, xray, rabbitmq, AND distribution -- regardless
    of which products were actually enabled, or whether bundled nginx is
    even in use. Left as-is, that means disabling Xray or choosing an
    external ingress controller would still leave a fully-populated (but
    meaningless) xray/rabbitmq/nginx resource block in the generated
    file. Strip those sections out before merging so the output only
    contains sizing for what's actually being deployed.
    """

    baseline = deepcopy(chart_baseline)

    if not answers.products.xray:
        baseline.pop("xray", None)
        baseline.pop("rabbitmq", None)
    if not answers.products.distribution:
        baseline.pop("distribution", None)
    if not answers.products.catalog:
        baseline.pop("catalog", None)
    if not answers.products.workers:
        baseline.pop("worker", None)

    if answers.networking.external_ingress and "artifactory" in baseline:
        baseline["artifactory"].pop("nginx", None)

    return baseline


def _reference_overrides(
    ref: dict[str, Any], chart_baseline: dict[str, Any], answers: Answers
) -> dict[str, Any]:
    art = ref["artifactory"]
    patch: dict[str, Any] = {
        "artifactory": {
            "artifactory": {"replicaCount": art["replicas"]},
            "postgresql": {
                "primary": {
                    "resources": {
                        "requests": {
                            "cpu": str(art["postgres"]["vcpu"]),
                            "memory": f"{art['postgres']['memory_gb']}Gi",
                        },
                        "limits": {"memory": f"{art['postgres']['memory_gb']}Gi"},
                    },
                    "extendedConfiguration": _with_max_connections(
                        _get_path(
                            chart_baseline,
                            [
                                "artifactory",
                                "postgresql",
                                "primary",
                                "extendedConfiguration",
                            ],
                        ),
                        art["postgres"]["max_connections"],
                    ),
                }
            },
        },
    }

    if not answers.networking.external_ingress:
        patch["artifactory"]["nginx"] = {"replicaCount": art["nginx_replicas"]}

    if answers.products.xray:
        xr = ref["xray"]
        patch["xray"] = {
            "replicaCount": xr["replicas"],
            "autoscaling": {"minReplicas": xr["replicas"]},
            "postgresql": {
                "primary": {
                    "resources": {
                        "requests": {
                            "cpu": str(xr["postgres"]["vcpu"]),
                            "memory": f"{xr['postgres']['memory_gb']}Gi",
                        },
                        "limits": {"memory": f"{xr['postgres']['memory_gb']}Gi"},
                    },
                    "extendedConfiguration": _with_max_connections(
                        _get_path(
                            chart_baseline,
                            ["xray", "postgresql", "primary", "extendedConfiguration"],
                        ),
                        xr["postgres"]["max_connections"],
                    ),
                }
            },
        }
        patch["rabbitmq"] = {"replicaCount": xr["rabbitmq_replicas"]}

    return patch


def _custom_sizing(answers: Answers) -> RuleResult:
    custom = answers.custom_sizing or {}
    art_replicas = int(custom.get("artifactory_replicas", 1))
    xray_replicas = int(custom.get("xray_replicas", 1))

    patch: dict[str, Any] = {
        "artifactory": {"artifactory": {"replicaCount": art_replicas}},
    }
    if answers.products.xray:
        patch["xray"] = {
            "replicaCount": xray_replicas,
            "autoscaling": {"minReplicas": xray_replicas},
        }

    notes = [
        "Custom sizing tier: no reference-architecture table applies. Only "
        f"replica counts were set (artifactory={art_replicas}, "
        f"xray={xray_replicas}) -- resource requests/limits, HPA bounds, "
        "and DB sizing are left at chart defaults. Review manually or "
        "provide vCPU/memory numbers explicitly before going to "
        "production.",
    ]
    return RuleResult(values=patch, notes=notes)


def build_sizing(answers: Answers, chart_dir: Path | None = None) -> RuleResult:
    if answers.size_tier == SizeTier.CUSTOM:
        return _custom_sizing(answers)

    tier_name = answers.size_tier.value
    ref = get_tier(tier_name)
    raw_chart_baseline = load_chart_baseline(tier_name, chart_dir)
    chart_baseline = _prune_irrelevant_baseline_sections(raw_chart_baseline, answers)

    notes: list[str] = []
    if chart_baseline:
        notes.append(
            f"Loaded the chart's own sizing/platform-{tier_name}.yaml as the "
            "baseline for JVM heap %, Tomcat thread pools, HPA bounds, and "
            "per-microservice resource requests not covered by the "
            "reference architecture."
        )
    else:
        notes.append(
            "Chart sizing profile unavailable (helm/network not used for "
            "this run, or the file wasn't found for this chart version) -- "
            "JVM/Tomcat/HPA tuning left at plain chart defaults; only "
            "reference-architecture-driven values (replicas, nginx "
            "replicas, PostgreSQL sizing) were applied."
        )

    pruned_sections = sorted(set(raw_chart_baseline) - set(chart_baseline))
    if raw_chart_baseline.get("artifactory", {}).get("nginx") and not chart_baseline.get(
        "artifactory", {}
    ).get("nginx"):
        pruned_sections.append("artifactory.nginx")
    if pruned_sections:
        notes.append(
            "The chart's sizing profile bundles tuning for "
            f"{', '.join(pruned_sections)} unconditionally, regardless of "
            "which products/networking mode are actually selected -- those "
            "sections were dropped before merging since they don't apply "
            "here (e.g. an unused nginx replicaCount/resources block when "
            "an external ingress controller is selected instead)."
        )

    overrides = _reference_overrides(ref, chart_baseline, answers)
    patch = deep_merge(chart_baseline, overrides)

    notes.append(
        f"Sizing tier '{tier_name}': replica counts, nginx replicas, and "
        "PostgreSQL max_connections match between the chart's bundled "
        "profile and the reference architecture at this tier. The bundled "
        "PostgreSQL CPU/memory requests were corrected to the reference "
        "architecture's recommended value ("
        f"{_aws_annotation(ref['artifactory']['postgres'])} for "
        "Artifactory's DB"
        + (
            f", {_aws_annotation(ref['xray']['postgres'])} for Xray's DB"
            if answers.products.xray
            else ""
        )
        + "), since the chart's own sizing profile sets roughly half of "
        "that."
    )
    notes.append(
        "Reference architecture AWS node pool sizing for this tier "
        "(informational only -- not written into values.yaml, since "
        "sizing is computed cloud-agnostically): Artifactory pool "
        f"{_aws_annotation(ref['artifactory']['node_pool'])}, Nginx pool "
        f"{_aws_annotation(ref['artifactory']['nginx_node_pool'])}"
        + (
            f", Xray pool {_aws_annotation(ref['xray']['node_pool'])}, "
            f"RabbitMQ pool {_aws_annotation(ref['xray']['rabbitmq_node_pool'])}, "
            f"JAS pool {_aws_annotation(ref['xray']['jas_node_pool'])}"
            if answers.products.xray
            else ""
        )
        + ". Azure/GCP instance-type equivalents are not yet mapped in "
        "this tool -- vCPU/memory numbers above apply regardless of cloud."
    )

    return RuleResult(values=patch, notes=notes)
