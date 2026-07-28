"""Shared contract every scenario rule module implements.

Each rule module in `jfvb.rules` is self-contained: it decides for itself
whether it is relevant to a given `Answers` object (`applies`), and if so
produces three things (`apply`):

1. `values`   -- a partial `customvalues.yaml` patch to be deep-merged in
2. `notes`    -- human-readable warnings/rationale for SUMMARY.md
3. `steps`    -- ordered, copy-pasteable setup commands for RUNBOOK.md

This is deliberately "one file per use case" so that adding support for a
new deployment scenario never requires touching a monolithic conditional
block -- you add a new module and register it in `rules/__init__.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from jfvb.answers import Answers

# Runbook phases, in the order they should be presented to the reader.
PHASE_ORDER = [
    "keys",
    "database",
    "certificates",
    "filestore",
    "tls",
    "registry",
    "entitlements",
    "node_pools",
    "sizing_note",
    "install",
    "verify",
]


@dataclass
class SetupStep:
    phase: str
    title: str
    commands: list[str] = field(default_factory=list)
    explanation: str = ""

    def __post_init__(self) -> None:
        if self.phase not in PHASE_ORDER:
            raise ValueError(
                f"Unknown runbook phase '{self.phase}'. Must be one of {PHASE_ORDER}."
            )


@dataclass
class RuleResult:
    values: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    steps: list[SetupStep] = field(default_factory=list)


class Rule(Protocol):
    name: str

    def applies(self, answers: "Answers") -> bool: ...

    def apply(self, answers: "Answers") -> RuleResult: ...
