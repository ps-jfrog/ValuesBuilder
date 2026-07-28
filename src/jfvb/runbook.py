"""Assembles the ordered, personalized RUNBOOK.md from every rule module's
setup steps, plus the always-present install/verify steps at the end.
"""

from __future__ import annotations

from pathlib import Path

from jfvb.answers import Answers
from jfvb.builder import BuildOutput
from jfvb.helm_client import JFROG_REPO_NAME, JFROG_REPO_URL
from jfvb.rules.base import PHASE_ORDER, SetupStep

PHASE_TITLES = {
    "keys": "1. Master key / join key",
    "database": "2. External database",
    "certificates": "3. Trusted certificates",
    "filestore": "4. Filestore credentials",
    "tls": "5. TLS",
    "registry": "6. Image registry / air-gap prep",
    "entitlements": "7. Offline entitlements (air-gapped)",
    "node_pools": "8. Dedicated node pools",
    "sizing_note": "9. Sizing",
    "install": "10. Install / upgrade",
    "verify": "11. Post-install verification",
}


def _install_step(answers: Answers, chart_version: str | None) -> SetupStep:
    version = chart_version or "<CHART_VERSION>"
    commands = [
        f"helm repo add {JFROG_REPO_NAME} {JFROG_REPO_URL}",
        f"helm repo update {JFROG_REPO_NAME}",
        f"kubectl create namespace {answers.namespace} --dry-run=client -o yaml | kubectl apply -f -",
    ]
    install_cmd = (
        f"helm upgrade --install {answers.release_name} {JFROG_REPO_NAME}/jfrog-platform \\\n"
        f"  --namespace {answers.namespace} \\\n"
        f"  --version {version} \\\n"
        "  -f customvalues.yaml"
    )
    if answers.openshift:
        install_cmd += " \\\n  -f openshift-values.yaml  # chart-provided overlay, must be applied LAST"
    commands.append(install_cmd)
    return SetupStep(
        phase="install",
        title="Install / upgrade the JFrog Platform",
        commands=commands,
        explanation=(
            "customvalues.yaml already has sizing, product toggles, "
            "database, filestore, networking, and security settings "
            "merged in -- no other sizing `-f` file is needed."
            + (
                " OpenShift needs the chart's own openshift-values.yaml "
                "layered last to disable security contexts it manages "
                "itself."
                if answers.openshift
                else ""
            )
        ),
    )


def _verify_steps(answers: Answers) -> list[SetupStep]:
    return [
        SetupStep(
            phase="verify",
            title="Check platform health",
            commands=[
                "curl -f http://<JFROG_URL>/router/api/v1/system/health",
                "# Expect \"status\": \"HEALTHY\" for every service. If unhealthy, wait 3-5 minutes and retry.",
            ],
            explanation="Services can take a few minutes to initialize after first install.",
        ),
        SetupStep(
            phase="verify",
            title="Complete first-login setup",
            commands=[
                "# Open http://<JFROG_URL> in a browser",
                "# Log in with admin / password, change the password immediately",
                "# Complete the Onboarding Wizard",
            ],
        ),
    ]


def render_runbook_md(
    answers: Answers, build: BuildOutput, chart_version: str | None
) -> str:
    steps_by_phase: dict[str, list[SetupStep]] = {phase: [] for phase in PHASE_ORDER}
    for step in build.steps:
        steps_by_phase[step.phase].append(step)
    steps_by_phase["install"].append(_install_step(answers, chart_version))
    steps_by_phase["verify"].extend(_verify_steps(answers))

    lines: list[str] = [
        "# RUNBOOK",
        "",
        "Ordered, copy-pasteable setup commands for this deployment. Steps "
        "for scenarios that don't apply to your answers are omitted "
        "entirely. Replace `<PLACEHOLDERS>` with real values before "
        "running anything.",
        "",
    ]

    for phase in PHASE_ORDER:
        steps = steps_by_phase[phase]
        if not steps:
            continue
        lines.append(f"## {PHASE_TITLES[phase]}")
        lines.append("")
        for step in steps:
            lines.append(f"**{step.title}**")
            lines.append("")
            if step.explanation:
                lines.append(step.explanation)
                lines.append("")
            if step.commands:
                lines.append("```bash")
                lines.extend(step.commands)
                lines.append("```")
                lines.append("")

    return "\n".join(lines) + "\n"


def write_runbook(
    answers: Answers, build: BuildOutput, output_dir: Path, chart_version: str | None
) -> Path:
    path = output_dir / "RUNBOOK.md"
    path.write_text(render_runbook_md(answers, build, chart_version), encoding="utf-8")
    return path
