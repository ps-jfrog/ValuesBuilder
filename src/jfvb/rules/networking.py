"""Bundled nginx vs external ingress, plus TLS / custom trusted certificates.

The switch between bundled nginx and an external ingress controller is
explicitly documented in JFrog's own AWS reference architecture notes:
set `artifactory.ingress.enabled: true` and `artifactory.nginx.enabled:
false` (noting the doc's own caveat that on the *platform* chart these
paths may need an extra `artifactory.` nesting level depending on chart
version -- flagged here for the `--validate` dry-run to confirm).
"""

from __future__ import annotations

from jfvb.answers import Answers
from jfvb.rules.base import RuleResult, SetupStep

name = "networking"


def applies(answers: Answers) -> bool:
    return True


def apply(answers: Answers) -> RuleResult:
    net = answers.networking
    notes: list[str] = []
    steps: list[SetupStep] = []
    values: dict = {}

    if net.external_ingress:
        values["artifactory"] = {
            "nginx": {"enabled": False},
            "ingress": {"enabled": True},
        }
        notes.append(
            "External ingress controller selected: set "
            "artifactory.nginx.enabled: false + artifactory.ingress.enabled: "
            "true (per JFrog's reference architecture notes). Verify these "
            "exact key paths for the target chart version via --validate -- "
            "JFrog's own docs note the nesting can shift by one level "
            "between the standalone Artifactory chart and the platform "
            "chart."
        )
        if net.ingress_host:
            values["artifactory"]["ingress"]["hosts"] = [net.ingress_host]
        if net.ingress_class_name:
            values["artifactory"]["ingress"]["className"] = net.ingress_class_name
        else:
            notes.append(
                "No ingress className was set -- fine if the cluster has "
                "exactly one ingress controller marked as the default "
                "IngressClass, but if there are several (e.g. an internal "
                "and an internet-facing controller, or ALB + nginx side by "
                "side) the Ingress may bind to the wrong one or none at "
                "all. Set artifactory.ingress.className explicitly if in "
                "doubt. Controller-specific behavior (e.g. cert-manager "
                "issuer, ALB target-group/scheme) also typically needs "
                "artifactory.ingress.annotations, which jfvb doesn't set."
            )
    else:
        notes.append("Networking: bundled nginx (chart default).")

    if net.custom_tls:
        secret_name = net.tls_secret_name or f"{answers.release_name}-tls"
        if net.external_ingress:
            values.setdefault("artifactory", {}).setdefault("ingress", {})[
                "tls"
            ] = [{"secretName": secret_name, "hosts": [net.ingress_host] if net.ingress_host else []}]
        else:
            values.setdefault("artifactory", {}).setdefault("nginx", {})[
                "tlsSecretName"
            ] = secret_name
            notes.append(
                "Custom TLS on bundled nginx: set via "
                "artifactory.nginx.tlsSecretName (verify exact key for the "
                "target chart version -- some releases expose this as "
                "artifactory.nginx.https.* instead)."
            )
        steps.append(
            SetupStep(
                phase="tls",
                title="Create the TLS certificate secret",
                commands=[
                    f"kubectl -n {answers.namespace} create secret tls {secret_name} \\",
                    "  --cert=<PATH_TO_FULLCHAIN_CERT> \\",
                    "  --key=<PATH_TO_PRIVATE_KEY>",
                ],
                explanation="Referenced by the ingress/nginx TLS settings above for HTTPS termination.",
            )
        )

    if net.custom_trusted_certificates:
        cert_secret = f"{answers.release_name}-trusted-certs"
        values.setdefault("global", {})["customCertificates"] = {
            "enabled": True,
            "certificateSecretName": cert_secret,
        }
        notes.append(
            "Custom trusted certificates enabled: certs in "
            f"'{cert_secret}' are copied into "
            "$JFROG_HOME/*/var/etc/security/keys/trusted for every product "
            "(e.g. to trust an internal CA for LDAP/SSO/proxy TLS)."
        )
        steps.append(
            SetupStep(
                phase="certificates",
                title="Create the trusted-certificates secret",
                commands=[
                    f"kubectl -n {answers.namespace} create secret generic {cert_secret} \\",
                    "  --from-file=ca.crt=<PATH_TO_CA_CERT> \\",
                    "  --from-file=intermediate.crt=<PATH_TO_INTERMEDIATE_CERT>  # add one --from-file per cert",
                ],
                explanation="Every file in this secret is added to the JVM trust store on every product pod.",
            )
        )

    return RuleResult(values=values, notes=notes, steps=steps)
