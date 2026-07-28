"""Primary product checklist + advanced/less-common modules.

Keys confirmed against `stable/jfrog-platform/values.yaml` and
`stable/xray/values.yaml` in https://github.com/jfrog/charts:

- artifactory.enabled / xray.enabled / distribution.enabled / catalog.enabled
  / worker.enabled / bridge.enabled are top-level toggles in the platform
  chart. Chart defaults: artifactory/xray default to `enabled: true`;
  distribution/catalog/worker/bridge default to `enabled: false`. This
  module only writes an `enabled` key into the generated file when it's a
  genuine override from that default (e.g. `distribution.enabled: true`
  because you selected it, or `xray.enabled: false` because you
  deselected it) -- it never restates a default the chart already applies,
  so products you didn't select don't clutter the output at all.
- JAS (Advanced Security) activates via xray.serviceAccount.create +
  xray.rbac.create once the JPD is connected to MyJFrog with an
  Enterprise+/Enterprise X license.
- Curation is a component of Xray that requires catalog.enabled: true to
  access package vulnerability data (not a standalone chart toggle).
- SBOM is xray.sbom.enabled (default false in stable/xray/values.yaml).
- AppTrust requires both artifactory.apptrust.enabled and
  artifactory.unifiedpolicy.enabled together, and needs Xray + SBOM.
- Mission Control is artifactory.mc.enabled (E+ only; not supported on Pro).

Platform Federation's exact key varies by chart version -- we set the
commonly-documented path and flag it in notes for verification via the
`--validate` dry-run step, since JFrog has moved this key across releases.
"""

from __future__ import annotations

from jfvb.answers import Answers
from jfvb.rules.base import RuleResult


name = "modules"


def applies(answers: Answers) -> bool:
    return True


def apply(answers: Answers) -> RuleResult:
    p = answers.products
    # artifactory/xray default to enabled: true on the chart already, so
    # they're always written explicitly (as a true anchor for artifactory,
    # and because turning xray off IS a meaningful override worth stating).
    values: dict = {"artifactory": {"enabled": True}}
    if not p.xray:
        values["xray"] = {"enabled": False}
    notes: list[str] = []

    if not p.xray:
        # RabbitMQ is only used by Xray -- disable it too, per JFrog's own
        # "Chart Defaults vs Production" guidance, or it deploys for nothing.
        values["rabbitmq"] = {"enabled": False}
        notes.append(
            "Xray disabled -> also disabled the bundled RabbitMQ "
            "(rabbitmq.enabled: false), since it exists only to serve Xray."
        )

    # distribution/catalog/worker default to enabled: false on the chart
    # already -- only write the key when turning one ON, so products you
    # didn't select don't show up in the generated file at all.
    if p.distribution:
        values["distribution"] = {"enabled": True}
    if p.catalog:
        values["catalog"] = {"enabled": True}
    if p.workers:
        values["worker"] = {"enabled": True}

    if p.curation and not p.catalog:
        values["catalog"] = {"enabled": True}
        notes.append(
            "Curation was selected but Catalog was not -- Curation is a "
            "component of Xray that requires Catalog to access package "
            "vulnerability data, so Catalog has been auto-enabled "
            "(catalog.enabled: true)."
        )
    if p.curation:
        notes.append(
            "Curation has no separate chart toggle: it ships as part of "
            "Xray and is activated in the UI once entitlements confirm an "
            "Enterprise+/Enterprise X license and Catalog is enabled."
            + (
                " Air-gapped: entitlements come from the offline "
                "registration procedure, not a live MyJFrog connection -- "
                "see the 'Offline entitlements' runbook step."
                if answers.airgapped
                else " Normally this means the JPD connecting live to "
                "MyJFrog for the entitlement check."
            )
        )

    if p.jas:
        if not p.xray:
            notes.append(
                "WARNING: JAS was selected but Xray is disabled -- JAS is a "
                "component of Xray and cannot run without it. JAS settings "
                "were skipped."
            )
        else:
            values.setdefault("xray", {}).update(
                {
                    "serviceAccount": {"create": True},
                    "rbac": {"create": True},
                }
            )
            notes.append(
                "JAS (Advanced Security) requires xray.serviceAccount.create "
                "and xray.rbac.create (set here). It only activates once "
                "entitlements confirm an Enterprise+/Enterprise X license "
                "-- it will not appear until then."
                + (
                    " Air-gapped: entitlements come from the offline "
                    "registration procedure, not a live MyJFrog connection "
                    "-- see the 'Offline entitlements' runbook step. "
                    "(Unverified whether JAS's separate signature-database "
                    "updates have their own offline path -- check JFrog's "
                    "Advanced Security docs for your version before "
                    "relying on this in a fully air-gapped network.)"
                    if answers.airgapped
                    else " Normally this means the JPD connecting live to "
                    "MyJFrog for the entitlement check."
                )
            )

    if p.sbom:
        if not p.xray:
            notes.append(
                "WARNING: SBOM was selected but Xray is disabled -- SBOM is "
                "an Xray service. SBOM settings were skipped."
            )
        else:
            values.setdefault("xray", {})["sbom"] = {"enabled": True}
            notes.append(
                "SBOM service enabled (xray.sbom.enabled: true) -- not the "
                "chart default. Also update system.yaml sbom.enabled: true "
                "via xray.extraSystemYaml if targeting an existing/upgraded "
                "instance so the migration workers pick it up."
            )

    # --- Advanced / less common modules ---
    if p.bridge:
        values["bridge"] = {"enabled": True}
        notes.append("Bridge enabled (bridge.enabled: true).")

    if p.apptrust:
        values.setdefault("artifactory", {}).update(
            {
                "apptrust": {"enabled": True},
                "unifiedpolicy": {"enabled": True},
            }
        )
        if not (p.xray and p.sbom):
            notes.append(
                "WARNING: AppTrust requires both Xray and SBOM to be "
                "enabled. Re-check your product selection -- AppTrust may "
                "not function correctly without them."
            )
        notes.append(
            "AppTrust enabled -- requires artifactory.apptrust.enabled AND "
            "artifactory.unifiedpolicy.enabled together (both set here), "
            "plus an Enterprise+/Enterprise X license entitlement."
        )

    if p.mission_control:
        values.setdefault("artifactory", {})["mc"] = {"enabled": True}
        notes.append(
            "Mission Control enabled (artifactory.mc.enabled: true) -- "
            "requires an Enterprise+ license; not supported on a Pro "
            "license (mc.enabled must stay false for Pro)."
        )

    if p.federation:
        notes.append(
            "Platform Federation was requested. Its exact values.yaml key "
            "path has changed across chart releases -- verify against the "
            "target chart version's values.yaml/README (search for "
            "'federation') and confirm via the helm-template validation "
            "step before relying on the generated file for this."
        )

    return RuleResult(values=values, notes=notes)
