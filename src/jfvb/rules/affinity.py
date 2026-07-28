"""Pod anti-affinity + dedicated node pools, driven by worker-node count.

Rule of thumb: hard podAntiAffinity (one replica per node, guaranteed) is
only safe to request when there are at least as many worker nodes as
replicas -- otherwise pods will not schedule at all. When there are fewer
nodes than the tier's replica count, we fall back to soft anti-affinity
and warn loudly, since that's a real availability gap worth knowing about
(and fixing by adding nodes) before going to production.

nodeSelector key paths were verified with `helm template` against chart
11.5.9: Artifactory and Xray's own StatefulSet/Deployment templates read
`.Values.artifactory.nodeSelector` / `.Values.xray.nodeSelector` from
*inside* their respective standalone charts -- which, mounted as
subcharts of jfrog-platform, means the full paths are DOUBLE-nested:
`artifactory.artifactory.nodeSelector` / `xray.xray.nodeSelector` (the
same "app section nested one level deeper" pattern as `replicaCount` and
`podAntiAffinity`). RabbitMQ/PostgreSQL are plain bitnami subcharts, so
their nodeSelector stays at the conventional bitnami paths
(`rabbitmq.nodeSelector`, `postgresql.primary.nodeSelector`).
"""

from __future__ import annotations

from jfvb.answers import Answers, DatabaseMode, SizeTier
from jfvb.refdata import get_tier
from jfvb.rules.base import RuleResult, SetupStep

name = "affinity"

NODE_POOL_LABEL_KEY = "jfrog.com/node-pool"


def applies(answers: Answers) -> bool:
    return True


def _desired_replicas(answers: Answers) -> dict[str, int]:
    if answers.size_tier == SizeTier.CUSTOM:
        custom = answers.custom_sizing or {}
        return {
            "artifactory": int(custom.get("artifactory_replicas", 1)),
            "xray": int(custom.get("xray_replicas", 1)) if answers.products.xray else 0,
        }
    ref = get_tier(answers.size_tier.value)
    return {
        "artifactory": ref["artifactory"]["replicas"],
        "xray": ref["xray"]["replicas"] if answers.products.xray else 0,
    }


def _anti_affinity_type(component: str, count: int, answers: Answers, notes: list[str]) -> str:
    if count <= 1:
        return "soft"
    if answers.worker_node_count >= count:
        return "hard"
    notes.append(
        f"WARNING: {component} wants {count} replicas but only "
        f"{answers.worker_node_count} worker nodes were reported for this "
        f"cluster -- falling back to SOFT podAntiAffinity for {component} "
        "so pods can still schedule, instead of the HARD anti-affinity the "
        "reference architecture assumes for this tier. Add worker nodes to "
        "get guaranteed one-replica-per-node HA."
    )
    return "soft"


def apply(answers: Answers) -> RuleResult:
    replicas = _desired_replicas(answers)
    notes: list[str] = []
    values: dict = {}
    steps: list[SetupStep] = []

    art_type = _anti_affinity_type("Artifactory", replicas["artifactory"], answers, notes)
    values["artifactory"] = {
        "artifactory": {"podAntiAffinity": {"type": art_type}},
    }

    if answers.products.xray and replicas["xray"]:
        xray_type = _anti_affinity_type("Xray", replicas["xray"], answers, notes)
        values["xray"] = {
            "xray": {
                "podAntiAffinity": {
                    "type": xray_type,
                    "topologyKey": "kubernetes.io/hostname",
                }
            }
        }

    if answers.dedicated_node_pools:
        pool_labels: dict[str, str] = {"artifactory": "artifactory"}
        values["artifactory"]["artifactory"]["nodeSelector"] = {
            NODE_POOL_LABEL_KEY: "artifactory"
        }
        values["artifactory"]["nginx"] = {
            "nodeSelector": {NODE_POOL_LABEL_KEY: "nginx"}
        }
        pool_labels["nginx"] = "nginx"

        if answers.products.xray:
            values.setdefault("xray", {}).setdefault("xray", {})["nodeSelector"] = {
                NODE_POOL_LABEL_KEY: "xray"
            }
            pool_labels["xray"] = "xray"
            values["rabbitmq"] = {"nodeSelector": {NODE_POOL_LABEL_KEY: "rabbitmq"}}
            pool_labels["rabbitmq"] = "rabbitmq"

        if answers.database.mode == DatabaseMode.BUNDLED:
            values["postgresql"] = {
                "primary": {"nodeSelector": {NODE_POOL_LABEL_KEY: "postgresql"}}
            }
            pool_labels["postgresql"] = "postgresql"

        notes.append(
            "Dedicated node pools requested: nodeSelector "
            f"'{NODE_POOL_LABEL_KEY}' was set per component "
            f"({', '.join(sorted(set(pool_labels.values())))}). Verify these "
            "key paths against the rendered manifests for the target chart "
            "version (--validate), and label the corresponding worker "
            "nodes before install (see RUNBOOK.md)."
        )

        label_commands = [
            f"kubectl label nodes <NODE_NAME_FOR_{pool.upper()}> {NODE_POOL_LABEL_KEY}={pool}"
            for pool in sorted(set(pool_labels.values()))
        ]
        steps.append(
            SetupStep(
                phase="node_pools",
                title="Label worker nodes for dedicated component node pools",
                commands=label_commands,
                explanation=(
                    "The generated values.yaml assumes each component below "
                    "is scheduled onto nodes carrying "
                    f"'{NODE_POOL_LABEL_KEY}=<component>'. Replace the "
                    "placeholders with the actual node names for each pool "
                    "before installing, or pods will stay Pending."
                ),
            )
        )

    return RuleResult(values=values, notes=notes, steps=steps)
