"""Orchestrates sizing + the rule engine into one build result.

This is the single entry point both the CLI wizard and the test suite use,
so rule modules can be exercised without going through interactive
prompts at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from jfvb.answers import Answers
from jfvb.merge import deep_merge_all
from jfvb.rules import ALL_RULES
from jfvb.rules.base import SetupStep
from jfvb.sizing import build_sizing


@dataclass
class Contribution:
    rule_name: str
    keys: list[str]
    notes: list[str]
    # Kept alongside notes (rather than only in BuildOutput.steps) so
    # SUMMARY.md can point each warning/note straight at the RUNBOOK.md
    # section that has the actual fix-it commands for it.
    steps: list[SetupStep] = field(default_factory=list)


@dataclass
class BuildOutput:
    values: dict
    contributions: list[Contribution] = field(default_factory=list)
    steps: list[SetupStep] = field(default_factory=list)

    @property
    def notes(self) -> list[tuple[str, str]]:
        return [
            (contribution.rule_name, note)
            for contribution in self.contributions
            for note in contribution.notes
        ]

    @property
    def warnings(self) -> list[tuple[str, str]]:
        return [(rule, note) for rule, note in self.notes if note.startswith("WARNING")]


def run_build(answers: Answers, chart_dir: Path | None = None) -> BuildOutput:
    contributions: list[Contribution] = []
    patches: list[dict] = []
    steps: list[SetupStep] = []

    sizing_result = build_sizing(answers, chart_dir)
    contributions.append(
        Contribution(
            rule_name="sizing",
            keys=list(sizing_result.values.keys()),
            notes=sizing_result.notes,
            steps=sizing_result.steps,
        )
    )
    patches.append(sizing_result.values)
    steps.extend(sizing_result.steps)

    for rule_module in ALL_RULES:
        if not rule_module.applies(answers):
            continue
        result = rule_module.apply(answers)
        contributions.append(
            Contribution(
                rule_name=rule_module.name,
                keys=list(result.values.keys()),
                notes=result.notes,
                steps=result.steps,
            )
        )
        patches.append(result.values)
        steps.extend(result.steps)

    values = deep_merge_all(*patches)
    return BuildOutput(values=values, contributions=contributions, steps=steps)
