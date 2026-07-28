"""OpenShift support.

The chart ships a ready-made `openshift-values.yaml` overlay (disables all
security contexts, since OpenShift manages pod security automatically) and
JFrog's docs are explicit that it "must be applied last" via its own `-f`
flag. Rather than guess/replicate its (version-varying) contents inside
our single merged file, we leave it as a separate chart-provided overlay
and just make sure the install command in RUNBOOK.md layers it correctly:

    helm upgrade --install ... -f customvalues.yaml -f openshift-values.yaml

This is deliberately the one place jfvb does NOT try to inline everything
into a single file, because JFrog already publishes and maintains the
correct overlay per chart version, and duplicating it here would go stale.
"""

from __future__ import annotations

from jfvb.answers import Answers
from jfvb.rules.base import RuleResult

name = "openshift"


def applies(answers: Answers) -> bool:
    return answers.openshift


def apply(answers: Answers) -> RuleResult:
    notes = [
        "OpenShift target detected: layer the chart's own "
        "openshift-values.yaml LAST, after customvalues.yaml, e.g. "
        "`-f customvalues.yaml -f openshift-values.yaml` -- it disables "
        "security contexts that OpenShift manages itself. This file is "
        "not reproduced inside customvalues.yaml since JFrog maintains it "
        "per chart version; see the install command in RUNBOOK.md.",
    ]
    return RuleResult(values={}, notes=notes)
