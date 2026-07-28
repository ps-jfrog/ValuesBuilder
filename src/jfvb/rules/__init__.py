"""Scenario rule engine: one module per deployment use case.

Adding support for a new scenario means adding a new module here (with
`applies(answers)` and `apply(answers) -> RuleResult`) and registering it
in `ALL_RULES` -- never touching a monolithic conditional block.
"""

from __future__ import annotations

from jfvb.answers import Answers
from jfvb.rules import (
    affinity,
    airgap,
    database,
    filestore,
    ha,
    modules,
    networking,
    openshift,
    security,
)
from jfvb.rules.base import RuleResult

ALL_RULES = [
    modules,
    airgap,
    affinity,
    database,
    filestore,
    networking,
    ha,
    security,
    openshift,
]


def run_all(answers: Answers) -> list[RuleResult]:
    results = []
    for rule_module in ALL_RULES:
        if rule_module.applies(answers):
            results.append(rule_module.apply(answers))
    return results
