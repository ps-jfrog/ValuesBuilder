"""Master key / join key handling, and the fresh-install vs upgrade branch.

Confirmed keys from `stable/jfrog-platform/values.yaml`:

- Literal (not recommended beyond quick eval): `global.masterKey` /
  `global.joinKey`.
- Existing-secret refs (recommended): `global.masterKeySecretName` /
  `global.joinKeySecretName` -- propagates to every subchart so
  Artifactory, Xray, Distribution, and Catalog all share the same keys.

This module is also where the "fresh deployment vs existing/upgrade"
branch that drives RUNBOOK.md's key-handling instructions lives: a fresh
install generates new keys, an upgrade/existing install must retrieve the
currently-running keys instead (mismatched keys break startup).
"""

from __future__ import annotations

from jfvb.answers import Answers, KeySource
from jfvb.rules.base import RuleResult, SetupStep

name = "security"


def applies(answers: Answers) -> bool:
    return True


def apply(answers: Answers) -> RuleResult:
    sec = answers.security
    notes: list[str] = []
    values: dict = {"global": {}}

    if sec.master_key_source == KeySource.LITERAL:
        values["global"]["masterKey"] = "<MASTER_KEY>"
        notes.append(
            "WARNING: literal master key requested -- this will be stored "
            "in plaintext in customvalues.yaml. Fine for a disposable "
            "eval; use an existing-secret ref for anything else."
        )
    else:
        values["global"]["masterKeySecretName"] = sec.master_key_secret_name

    if sec.join_key_source == KeySource.LITERAL:
        values["global"]["joinKey"] = "<JOIN_KEY>"
        notes.append(
            "WARNING: literal join key requested -- same plaintext caveat "
            "as the master key above."
        )
    else:
        values["global"]["joinKeySecretName"] = sec.join_key_secret_name

    if answers.fresh_deployment:
        commands = [
            "export MASTER_KEY=$(openssl rand -hex 32)",
            "export JOIN_KEY=$(openssl rand -hex 16)",
            f"kubectl create namespace {answers.namespace} --dry-run=client -o yaml | kubectl apply -f -",
            f"kubectl -n {answers.namespace} create secret generic {sec.master_key_secret_name} "
            "--from-literal=master-key=$MASTER_KEY",
            f"kubectl -n {answers.namespace} create secret generic {sec.join_key_secret_name} "
            "--from-literal=join-key=$JOIN_KEY",
            "# Save $MASTER_KEY and $JOIN_KEY in your secrets manager now -- "
            "you need them for every future upgrade and for disaster recovery.",
        ]
        title = "Generate master key + join key (fresh deployment)"
        explanation = (
            "This is a brand-new deployment, so new keys are generated "
            "and stored as Kubernetes secrets that the generated "
            "values.yaml references by name."
        )
    else:
        commands = [
            "# This is an upgrade/existing deployment -- reuse the CURRENT keys, do not regenerate them.",
            f"kubectl -n {answers.namespace} get secret {sec.master_key_secret_name} "
            "-o jsonpath='{.data.master-key}' | base64 -d; echo",
            f"kubectl -n {answers.namespace} get secret {sec.join_key_secret_name} "
            "-o jsonpath='{.data.join-key}' | base64 -d; echo",
            "# If these secrets don't exist yet but the databases were already initialized with a master key, "
            "recreate the secrets with that exact value instead of generating new ones.",
        ]
        title = "Retrieve existing master key + join key (upgrade)"
        explanation = (
            "Regenerating keys on an existing deployment causes a 'Master "
            "key mismatch' startup failure, since the databases were "
            "already initialized with the current key."
        )

    steps = [SetupStep(phase="keys", title=title, commands=commands, explanation=explanation)]
    return RuleResult(values=values, notes=notes, steps=steps)
