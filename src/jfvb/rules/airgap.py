"""Air-gapped environment configuration.

Confirmed from docs.jfrog.com/installation/docs/configure-jfrog-platform-in-an-air-gapped-environment
and docs.jfrog.com/installation/docs/override-the-default-system-yaml-file-in-helm-installation:

- The runtime setting is `jfconnect.airgap.enabled: true` in system.yaml.
- It is not a first-class key in the Artifactory chart's values.yaml
  (only `jfconnect.enabled`/`image`/`resources`/etc. are), so for Helm it
  must be injected via `extraSystemYaml` -- the documented mechanism for
  system.yaml keys not otherwise exposed.
- CONFIRMED via `helm template` against chart 11.5.9 (this is exactly what
  the `--validate` step exists to catch): on the jfrog-platform chart the
  correct path is the DOUBLE-nested
  `artifactory.artifactory.extraSystemYaml.jfconnect.airgap.enabled`, not
  the single-nested `artifactory.extraSystemYaml...`. The standalone
  Artifactory chart's own template helper reads
  `.Values.artifactory.extraSystemYaml` -- and since that standalone chart
  is itself mounted under the platform chart's `artifactory:` key, the
  full path gains an extra `artifactory.` level of nesting. A rendered
  system.yaml with only single-nesting shows `jfconnect: {enabled: true}`
  and silently drops `airgap` -- no Helm error, just missing config.
- Registry mirroring for air-gapped clusters uses `global.imageRegistry`
  and `global.imagePullSecrets` (confirmed via JFrog's "Using a Custom
  Image Registry" doc).
- Entitlements: JFConnect normally fetches these live from MyJFrog. In
  air-gapped mode there is a documented OFFLINE procedure (see "Update
  Entitlements on an Air-gapped JFrog Platform" on the page above) that
  does NOT require the platform itself to ever reach the internet --
  instead an admin round-trips a request/response token through any
  separate machine that does have internet access:
    1. On the air-gapped JPD: call jfconnect's `offline/start_register`
       to get an `offline_request_token`.
    2. On a connected machine: POST that token to JFrog's
       `jes.jfrog.io/api/v1/offline_register` to get back an
       `offline_response_token` + `entitlements.json`.
    3. Back on the air-gapped JPD: call `offline/register` with both to
       activate entitlements.
  This is required at least once for ANY air-gapped install (not just
  JAS/Curation/Mission Control -- every product's entitlement and
  expiry date flows through JFConnect), and again within the 14-day
  window before each contract renewal, so previous versions of this note
  claiming JAS/Curation/Mission Control "will not activate" without a
  live/allow-listed path to MyJFrog were simply wrong -- there is no
  live connectivity requirement at all, only this periodic offline
  hand-off.
"""

from __future__ import annotations

from jfvb.answers import Answers
from jfvb.rules.base import RuleResult, SetupStep

name = "airgap"


def applies(answers: Answers) -> bool:
    return answers.airgapped


def apply(answers: Answers) -> RuleResult:
    values: dict = {
        "artifactory": {
            "artifactory": {
                "extraSystemYaml": {
                    "jfconnect": {"airgap": {"enabled": True}},
                }
            }
        },
    }
    notes = [
        "Air-gapped mode: set via "
        "artifactory.artifactory.extraSystemYaml.jfconnect.airgap.enabled: "
        "true (confirmed via live `helm template` against chart 11.5.9 -- "
        "the single-nested artifactory.extraSystemYaml path silently drops "
        "the airgap key with no Helm error). This is injected into "
        "system.yaml, not a first-class values.yaml key -- run "
        "`jfvb build --validate` (default on) to re-confirm this for "
        "whatever chart version you're targeting.",
    ]

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
            title="Prepare the internal image registry and pull secret",
            commands=(
                [
                    "# `jfvb build` already generated images.txt (and mirror-images.sh, if a",
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
