"""Image discovery for air-gapped preparation.

Follows the pattern JFrog's own docs confirm for air-gap prep: render the
chart with `helm template` and extract every `image:` reference from the
rendered manifests (Deployments, StatefulSets, Jobs, init containers,
bundled Postgres/RabbitMQ included).

Two chart behaviors make a naive `image:` grep wrong for air-gap prep, both
confirmed by rendering jfrog-platform 11.5.13:

1. `global.imageRegistry` rewrites every rendered image to the *mirror*
   registry. Rendering with an air-gapped customvalues.yaml therefore
   produces the list of images as they will look AFTER mirroring, which is
   useless as a "what do I need to pull" list (and makes mirror-images.sh
   copy the internal registry onto itself). Image discovery strips that
   override so images render with their upstream source registry, while
   keeping every other answer (enabled products, etc.) intact.
2. The JAS images live in a ConfigMap as a separate `registry:` key
   followed by a tag-less `image:` key, e.g.

       contextualAnalysis:
         registry: releases-docker.jfrog.io
         image: jfrog/xray-jas-contextual-analysis

   A plain `image:` match yields `jfrog/xray-jas-contextual-analysis`,
   which resolves to Docker Hub. These pairs are rejoined, and because
   they carry no tag (Xray resolves it at runtime from entitlements) they
   are reported separately so nobody mirrors a bare `:latest`.
"""

from __future__ import annotations

import re
from pathlib import Path
from tempfile import mkdtemp

import yaml

from jfvb import helm_client
from jfvb.helm_client import (
    CHART_NAME,
    JFROG_REPO_NAME,
    JFROG_REPO_URL,
    ProgressCallback,
)

IMAGE_RE = re.compile(r'image:\s*"?([^"\s]+)"?')
# `registry:` immediately followed by `image:` (the JAS ConfigMap shape).
REGISTRY_IMAGE_RE = re.compile(
    r'registry:\s*"?([^"\s]+)"?\s*\n\s*image:\s*"?([^"\s]+)"?'
)

# Keys jfvb's airgap rule injects that rewrite images to the mirror target.
# Stripped before rendering for image discovery -- see module docstring.
MIRROR_OVERRIDE_KEYS = ("imageRegistry", "imagePullSecrets")

JAS_WARNING = (
    "JAS (Advanced Security) images are entitlement-gated and fetched "
    "dynamically once the JPD is connected to MyJFrog with the right "
    "license -- they may be under-represented in helm-template output. "
    "Cross-check this list against the JAS image manifest for the target "
    "Xray version before finalizing an air-gapped image set."
)

UNTAGGED_WARNING = (
    "Some images render without a tag (the JAS images resolve theirs at "
    "runtime from entitlements). They are listed untagged -- pin the tag "
    "that matches your Xray version before mirroring, or you'll mirror "
    ":latest by accident."
)


def is_untagged(image: str) -> bool:
    """True if `image` carries no explicit tag or digest."""

    if "@" in image:
        return False
    last_segment = image.rsplit("/", 1)[-1]
    return ":" not in last_segment


def _uncommented(rendered_manifests: str) -> str:
    """Drop YAML comment lines.

    The chart ships commented-out `#   image: ...` examples inside real
    manifests (confirmed in jfrog-platform 11.5.13, 8 such lines). Matching
    those would list images the deployment never actually pulls.
    """

    return "\n".join(
        line for line in rendered_manifests.splitlines()
        if not line.lstrip().startswith("#")
    )


def extract_images(rendered_manifests: str) -> list[str]:
    """Every image referenced by the rendered manifests, deduped and sorted.

    Handles both the normal `image: <registry>/<repo>:<tag>` form and the
    split `registry:` + `image:` pair used for the JAS images, and ignores
    commented-out example lines.
    """

    text = _uncommented(rendered_manifests)

    paired: dict[str, str] = {}
    for registry, image in REGISTRY_IMAGE_RE.findall(text):
        paired[image] = f"{registry.rstrip('/')}/{image}"

    found: set[str] = set()
    for image in IMAGE_RE.findall(text):
        found.add(paired.get(image, image))
    return sorted(found)


def strip_mirror_overrides(values: dict) -> dict:
    """Return `values` without the mirror-registry overrides.

    Mutates a shallow copy only; the caller's dict is left untouched.
    """

    sanitized = dict(values)
    global_section = sanitized.get("global")
    if not isinstance(global_section, dict):
        return sanitized

    remaining = {
        key: value
        for key, value in global_section.items()
        if key not in MIRROR_OVERRIDE_KEYS
    }
    if remaining:
        sanitized["global"] = remaining
    else:
        sanitized.pop("global", None)
    return sanitized


def sanitize_values_files(values_files: list[Path], dest_dir: Path | None = None) -> list[Path]:
    """Copy each values file with the mirror-registry overrides removed.

    Files that don't set those keys are passed through unchanged.
    """

    dest_dir = dest_dir or Path(mkdtemp(prefix="jfvb-images-"))
    dest_dir.mkdir(parents=True, exist_ok=True)

    sanitized_paths: list[Path] = []
    for index, values_file in enumerate(values_files):
        loaded = yaml.safe_load(values_file.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            sanitized_paths.append(values_file)
            continue
        sanitized = strip_mirror_overrides(loaded)
        if sanitized == loaded:
            sanitized_paths.append(values_file)
            continue
        out_path = dest_dir / f"{index}-{values_file.name}"
        out_path.write_text(
            yaml.safe_dump(sanitized, sort_keys=False), encoding="utf-8"
        )
        sanitized_paths.append(out_path)
    return sanitized_paths


def list_images(
    version: str,
    values_files: list[Path] | None = None,
    namespace: str = "jfrog-platform",
    release_name: str = "jfrog-platform",
    on_progress: ProgressCallback | None = None,
    strip_registry_override: bool = True,
) -> list[str]:
    """Render the chart and list every image it references.

    `strip_registry_override` keeps the *source* registry in the output even
    when the values file points images at an internal mirror -- which is
    what you need in order to know what to pull. Set it False to see the
    post-mirror image names instead.
    """

    files = list(values_files or [])
    if strip_registry_override and files:
        files = sanitize_values_files(files)

    rendered = helm_client.template(
        version=version,
        release_name=release_name,
        namespace=namespace,
        values_files=files or None,
        on_progress=on_progress,
    )
    return extract_images(rendered)


def render_chart_download_instructions(chart_version: str) -> str:
    """Copy-pasteable steps to fetch the chart package for air-gapped install.

    Air-gapped clusters cannot reach charts.jfrog.io, so the chart itself
    (not just the container images) must be pulled on a connected machine
    and transferred in as a `.tgz`.
    """

    version = chart_version.strip() or "<CHART_VERSION>"
    package = f"{CHART_NAME}-{version}.tgz"
    return "\n".join(
        [
            f"# Chart version: {version}",
            "#",
            "# --- Download the Helm chart (on a machine WITH internet) ---",
            f"# helm repo add {JFROG_REPO_NAME} {JFROG_REPO_URL}",
            f"# helm repo update {JFROG_REPO_NAME}",
            f"# helm pull {JFROG_REPO_NAME}/{CHART_NAME} --version {version}",
            f"# # produces {package} -- copy this file into the air-gapped environment",
            "#",
            "# Optional: also fetch the chart's OpenShift overlay if you need it:",
            f"# tar -xzf {package} {CHART_NAME}/openshift-values.yaml",
            "#",
            "# --- Install from the local chart package (inside the air-gap) ---",
            f"# helm upgrade --install <RELEASE_NAME> ./{package} \\",
            "#   -n <NAMESPACE> --create-namespace \\",
            "#   -f customvalues.yaml",
            "# # OpenShift: append ` -f openshift-values.yaml` last",
            "#",
            "# --- Container images to mirror (one per line) ---",
        ]
    )


def render_image_list(
    images: list[str],
    chart_version: str | None = None,
) -> str:
    """Render images.txt contents, optionally with chart-download instructions."""

    lines: list[str] = [
        "# Generated by jfvb -- air-gapped prep (Helm chart + container images)",
    ]
    if chart_version:
        lines.append(render_chart_download_instructions(chart_version))
    else:
        lines.append("# (no chart version provided -- image list only)")
        lines.append("#")
        lines.append("# --- Container images to mirror (one per line) ---")
    lines.extend(images)
    return "\n".join(lines) + "\n"


def write_image_list(
    images: list[str],
    output_path: Path,
    chart_version: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_image_list(images, chart_version=chart_version), encoding="utf-8"
    )
    return output_path


def _strip_registry_host(image: str) -> str:
    first_segment, _, rest = image.partition("/")
    if rest and ("." in first_segment or ":" in first_segment or first_segment == "localhost"):
        return rest
    return image


def render_mirror_script(images: list[str], target_registry: str) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by jfvb -- mirrors every referenced image into an internal registry.",
        "set -euo pipefail",
        "",
        f'TARGET_REGISTRY="{target_registry}"',
        "",
    ]
    for image in images:
        path = _strip_registry_host(image)
        if is_untagged(image):
            lines.append(
                f"# NOTE: {image} renders without a tag -- set the tag matching "
                "your Xray version before running this line."
            )
        lines.append(f'skopeo copy "docker://{image}" "docker://$TARGET_REGISTRY/{path}"')
    return "\n".join(lines) + "\n"


def write_mirror_script(images: list[str], target_registry: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_mirror_script(images, target_registry), encoding="utf-8")
    output_path.chmod(0o755)
    return output_path
