"""Air-gapped environment configuration.

Confirmed from docs.jfrog.com/installation/docs/configure-jfrog-platform-in-an-air-gapped-environment
and docs.jfrog.com/installation/docs/override-the-default-system-yaml-file-in-helm-installation.

The values.yaml *path* for enabling air-gap is version-gated (see
`jfvb.chart_compat.FEATURES`):

- Charts **before 11.5.13**: `jfconnect.airgap` is not a first-class key on
  the platform chart. Inject via the Artifactory subchart's
  `extraSystemYaml`. CONFIRMED via `helm template` against chart 11.5.9:
  the correct path is the DOUBLE-nested
  `artifactory.artifactory.extraSystemYaml.jfconnect.airgap.enabled`. The
  single-nested `artifactory.extraSystemYaml...` silently drops `airgap`
  (no Helm error).
- Charts **11.5.13+** (and "latest"): the platform chart exposes
  `artifactory.jfconnect.airgap.enabled` directly in values.

Shared across versions:

- Registry mirroring uses `global.imageRegistry` / `global.imagePullSecrets`.
- Entitlements: JFConnect normally fetches these live from MyJFrog. In
  air-gapped mode there is a documented OFFLINE procedure (see "Update
  Entitlements on an Air-gapped JFrog Platform") that does NOT require the
  platform itself to ever reach the internet -- an admin round-trips a
  request/response token through any separate machine that does have
  internet access. Required at least once for ANY air-gapped install, and
  again within the 14-day window before each contract renewal.
"""

from __future__ import annotations

from jfvb.answers import Answers
from jfvb.chart_compat import (
    describe_version_context,
    feature_active,
    get_feature,
)
from jfvb.rules.base import RuleResult, SetupStep

name = "airgap"


def applies(answers: Answers) -> bool:
    return answers.airgapped


def _airgap_values_and_note(answers: Answers) -> tuple[dict, str]:
    """Pick the version-appropriate values patch + rationale note."""

    version = answers.chart_version
    if feature_active("airgap_jfconnect_values", version):
        feature = get_feature("airgap_jfconnect_values")
        values = {
            "artifactory": {
                "jfconnect": {"airgap": {"enabled": True}},
            }
        }
        note = (
            f"Air-gapped mode (chart {describe_version_context(version)}): "
            "set via artifactory.jfconnect.airgap.enabled: true "
            f"({feature.notes} Feature id: `{feature.id}`.)"
        )
        return values, note

    # Pre-11.5.13 (or any version where the first-class key is not active).
    feature = get_feature("airgap_extra_system_yaml")
    values = {
        "artifactory": {
            "artifactory": {
                "extraSystemYaml": {
                    "jfconnect": {"airgap": {"enabled": True}},
                }
            }
        },
    }
    note = (
        f"Air-gapped mode (chart {describe_version_context(version)}): "
        "set via artifactory.artifactory.extraSystemYaml.jfconnect.airgap."
        "enabled: true "
        f"({feature.notes} Feature id: `{feature.id}`.) "
        "Run `jfvb build --validate` to re-confirm nesting for the "
        "chart version you're targeting."
    )
    return values, note


def apply(answers: Answers) -> RuleResult:
    values, path_note = _airgap_values_and_note(answers)
    notes = [path_note]

    if answers.custom_image_registry:
        values["global"] = {"imageRegistry": answers.custom_image_registry}
        notes.append(
            f"global.imageRegistry set to '{answers.custom_image_registry}' "
            "so every subchart pulls from your mirrored registry instead "
            "of releases-docker.jfrog.io."
        )
    else:
        notes.append(
            "WARNING: airgapped was selected but no custom image registry "
            "was provided. Set global.imageRegistry to your internal "
            "mirror before install, or images will fail to pull. Run "
            "`jfvb images` to get the full list to mirror first."
        )

    if answers.image_pull_secret_name:
        values.setdefault("global", {})["imagePullSecrets"] = [
            {"name": answers.image_pull_secret_name}
        ]

    notes.append(
        "WARNING: entitlements must be registered offline before the "
        "platform is usable -- JFConnect can't reach MyJFrog from an "
        "air-gapped network, but there IS a documented no-internet-"
        "required procedure for this (see the 'Offline entitlements' "
        "runbook step below). This applies to every product, not just "
        "JAS/Curation/Mission Control, and must be repeated within the "
        "14-day window before each contract renewal or entitlements "
        "will lapse."
    )

    steps = [
        SetupStep(
            phase="registry",
            title="Download the chart package and prepare the internal image registry",
            commands=(
                [
                    "# 1. On a machine WITH internet: download the chart package and",
                    "#    transfer jfrog-platform-<VERSION>.tgz into the air-gap.",
                    "#    (Full copy-pasteable steps are also at the top of images.txt.)",
                    "helm repo add jfrog https://charts.jfrog.io",
                    "helm repo update jfrog",
                    "helm pull jfrog/jfrog-platform --version <CHART_VERSION>",
                    "",
                    "# 2. `jfvb build` already generated images.txt (and mirror-images.sh, if a",
                    "# target registry was set) next to customvalues.yaml when it had helm/network",
                    "# access -- check the output directory before re-running this manually:",
                    "jfvb images --version <CHART_VERSION> --target-registry "
                    f"{answers.custom_image_registry or '<INTERNAL_REGISTRY>'} "
                    "-o images.txt",
                    "# Review images.txt, then mirror every image into the internal registry",
                    "# (see mirror-images.sh for ready-to-run skopeo/docker commands)",
                ]
                + (
                    [
                        "kubectl create namespace "
                        f"{answers.namespace} --dry-run=client -o yaml | kubectl apply -f -",
                        "kubectl -n "
                        f"{answers.namespace} create secret docker-registry "
                        f"{answers.image_pull_secret_name or '<PULL_SECRET_NAME>'} \\\n"
                        f"  --docker-server={answers.custom_image_registry or '<INTERNAL_REGISTRY>'} \\\n"
                        "  --docker-username=<REGISTRY_USER> \\\n"
                        "  --docker-password=<REGISTRY_PASSWORD>",
                    ]
                    if answers.image_pull_secret_name
                    else []
                )
            ),
            explanation=(
                "Air-gapped clusters cannot reach releases-docker.jfrog.io, "
                "so every image referenced by the chart must be mirrored "
                "into a registry the cluster can reach before installing."
            ),
        ),
        SetupStep(
            phase="entitlements",
            title="Register entitlements offline (no internet access needed on the JPD)",
            commands=[
                "# 1. Generate an admin-scoped access token from the JPD "
                "(Platform UI > Administration > User Management > Access "
                "Tokens, or the Access REST API).",
                "ADMIN_TOKEN=<ADMIN_SCOPED_TOKEN>",
                "",
                "# 2. On the air-gapped JPD: request an offline registration token.",
                "curl -H \"Authorization: Bearer $ADMIN_TOKEN\" -X GET \\\n"
                "  'http://<JFROG_PLATFORM_URL>:8082/jfconnect/api/v1/offline/start_register'",
                "# -> {\"offline_request_token\": \"<OFFLINE_REQUEST_TOKEN>\"}",
                "",
                "# 3. On a SEPARATE machine WITH internet access: exchange it for entitlements.",
                "curl -v -L 'https://jes.jfrog.io/api/v1/offline_register' \\\n"
                "  -H 'Content-Type: application/json' \\\n"
                "  --data '{\"offline_request_token\": \"<OFFLINE_REQUEST_TOKEN>\"}' \\\n"
                "  > entitlements.json",
                "# Keep the `offline_response_token` from the response header, plus entitlements.json.",
                "# Do NOT modify entitlements.json.",
                "",
                "# 4. Back on the air-gapped JPD: submit both to activate entitlements.",
                "curl -L -H \"Authorization: Bearer $ADMIN_TOKEN\" -X POST \\\n"
                "  'http://<JFROG_PLATFORM_URL>:8082/jfconnect/api/v1/offline/register' \\\n"
                "  --form '[email protected];type=application/json' \\\n"
                "  --form 'offline_response_token=\"<OFFLINE_RESPONSE_TOKEN>\"'",
            ],
            explanation=(
                "Entitlements set which services can run and the platform's "
                "license expiry date -- required once up front, and again "
                "within the 14-day window before each contract renewal "
                "(two rounds if the new contract reduces features/capacity: "
                "once before expiry to extend the old terms, once after "
                "renewal to apply the new ones). Full procedure: "
                "docs.jfrog.com/installation/docs/"
                "configure-jfrog-platform-in-an-air-gapped-environment"
            ),
        ),
    ]

    return RuleResult(values=values, notes=notes, steps=steps)
