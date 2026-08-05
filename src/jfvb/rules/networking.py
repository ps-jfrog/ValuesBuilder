"""Bundled nginx vs external ingress, plus TLS / custom trusted certificates.

The switch between bundled nginx and an external ingress controller is
explicitly documented in JFrog's own AWS reference architecture notes:
set `artifactory.ingress.enabled: true` and `artifactory.nginx.enabled:
false` (noting the doc's own caveat that on the *platform* chart these
paths may need an extra `artifactory.` nesting level depending on chart
version -- flagged here for the `--validate` dry-run to confirm).

Version-gated: from chart 11.6.0, bundled nginx requires an explicit HTTPS
decision or the chart refuses to render at all (confirmed by rendering
11.5.13 vs 11.6.0). See `NginxTlsMode` and the `nginx_tls_required`
feature. An external ingress is unaffected -- nginx is disabled there, so
the chart's guard never fires.
"""

from __future__ import annotations

from jfvb.answers import Answers, NginxTlsMode
from jfvb.chart_compat import feature_active
from jfvb.rules.base import RuleResult, SetupStep

name = "networking"


def _tls_secret_step(answers: Answers, secret_name: str) -> SetupStep:
    return SetupStep(
        phase="tls",
        title="Create the TLS certificate secret",
        commands=[
            f"kubectl -n {answers.namespace} create secret tls {secret_name} \\",
            "  --cert=<PATH_TO_FULLCHAIN_CERT> \\",
            "  --key=<PATH_TO_PRIVATE_KEY>",
        ],
        explanation="Referenced by the ingress/nginx TLS settings above for HTTPS termination.",
    )


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
        steps.append(_tls_secret_step(answers, secret_name))

    # Charts >= 11.6.0 refuse to render bundled nginx without an explicit
    # HTTPS decision. custom_tls already satisfies that via tlsSecretName.
    needs_https_decision = (
        not net.external_ingress
        and not net.custom_tls
        and feature_active("nginx_tls_required", answers.chart_version)
    )
    if needs_https_decision:
        mode = net.nginx_tls_mode or NginxTlsMode.EXISTING_SECRET
        nginx = values.setdefault("artifactory", {}).setdefault("nginx", {})

        if mode == NginxTlsMode.SELF_SIGNED:
            nginx["generateSelfSignedCert"] = True
            notes.append(
                "WARNING: bundled nginx is using a chart-generated "
                "self-signed certificate "
                "(artifactory.nginx.generateSelfSignedCert: true). It is "
                "unique per install but not issued by a trusted CA -- "
                "clients will reject it unless they explicitly trust it. "
                "Dev/test only; switch to a real certificate before "
                "go-live."
            )
        elif mode == NginxTlsMode.DISABLED:
            nginx["https"] = {"enabled": False}
            notes.append(
                "WARNING: HTTPS is disabled on bundled nginx "
                "(artifactory.nginx.https.enabled: false) -- all platform "
                "traffic, including credentials and access tokens, is sent "
                "in plaintext. Only acceptable when TLS is terminated "
                "upstream (service mesh / external load balancer)."
            )
        else:
            secret_name = net.tls_secret_name or f"{answers.release_name}-tls"
            nginx["tlsSecretName"] = secret_name
            steps.append(_tls_secret_step(answers, secret_name))
            chosen = net.nginx_tls_mode is not None
            notes.append(
                (
                    "Bundled nginx HTTPS uses your own certificate from the "
                    f"'{secret_name}' secret (artifactory.nginx.tlsSecretName)."
                    if chosen
                    else "WARNING: this chart version (>= 11.6.0) requires an "
                    "explicit HTTPS decision for bundled nginx and none was "
                    "given, so jfvb defaulted to the only production-safe "
                    f"option: your own certificate in the '{secret_name}' "
                    "secret (artifactory.nginx.tlsSecretName). Create that "
                    "secret before installing (see RUNBOOK.md), or set "
                    "networking.nginx_tls_mode to self_signed / disabled."
                )
                + " Without one of tlsSecretName, generateSelfSignedCert, or "
                "https.enabled=false, the chart fails to render at all."
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
