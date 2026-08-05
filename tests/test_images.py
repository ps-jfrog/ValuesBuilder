from pathlib import Path

import yaml

from jfvb.images import (
    extract_images,
    is_untagged,
    render_chart_download_instructions,
    render_image_list,
    render_mirror_script,
    sanitize_values_files,
    strip_mirror_overrides,
)


SAMPLE_MANIFEST = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: artifactory
spec:
  template:
    spec:
      containers:
        - name: artifactory
          image: releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0
      initContainers:
        - name: init
          image: "releases-docker.jfrog.io/bitnami/kubectl:1.31.2"
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgresql
spec:
  template:
    spec:
      containers:
        - name: postgresql
          image: releases-docker.jfrog.io/bitnami/postgresql:17.6.0-debian-12-r2
        - name: artifactory
          image: releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0
"""

# The JAS shape confirmed in a real jfrog-platform 11.5.13 render: registry
# and image are separate keys, and the image carries no tag.
JAS_CONFIGMAP_MANIFEST = """
apiVersion: v1
kind: ConfigMap
data:
  jas: |
    contextualAnalysis:
      registry: releases-docker.jfrog.io
      image: jfrog/xray-jas-contextual-analysis
    exposures:
      container:
        registry: releases-docker.jfrog.io
        image: jfrog/xray-jas-exposures
"""


def test_extract_images_dedupes_and_sorts():
    images = extract_images(SAMPLE_MANIFEST)
    assert images == sorted(set(images))
    assert "releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0" in images
    assert images.count("releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0") == 1
    assert len(images) == 3


def test_extract_images_ignores_commented_out_examples():
    # The chart ships commented-out image examples inside real manifests;
    # listing them would mean mirroring images nothing actually pulls.
    manifest = """
spec:
  template:
    spec:
      containers:
        - name: app
          image: releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0
      # initContainers:
      #   - name: example
      #     image: releases-docker.jfrog.io/ubi9/ubi-minimal:9.9.9
"""
    images = extract_images(manifest)
    assert images == ["releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0"]


def test_extract_images_joins_split_registry_and_image():
    images = extract_images(JAS_CONFIGMAP_MANIFEST)
    assert "releases-docker.jfrog.io/jfrog/xray-jas-contextual-analysis" in images
    assert "releases-docker.jfrog.io/jfrog/xray-jas-exposures" in images
    # The bare, registry-less form must not leak through -- it would resolve
    # to Docker Hub.
    assert "jfrog/xray-jas-contextual-analysis" not in images


def test_is_untagged():
    assert is_untagged("releases-docker.jfrog.io/jfrog/xray-jas-exposures") is True
    assert is_untagged("releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0") is False
    assert is_untagged("registry:5000/team/app") is True
    assert is_untagged("registry:5000/team/app:1.2.3") is False
    assert is_untagged("repo/app@sha256:abc123") is False


def test_strip_mirror_overrides_removes_registry_and_pull_secrets():
    values = {
        "global": {
            "imageRegistry": "registry.internal.corp",
            "imagePullSecrets": [{"name": "creds"}],
            "database": {"host": "db"},
        },
        "artifactory": {"enabled": True},
    }
    sanitized = strip_mirror_overrides(values)
    assert "imageRegistry" not in sanitized["global"]
    assert "imagePullSecrets" not in sanitized["global"]
    assert sanitized["global"]["database"] == {"host": "db"}
    assert sanitized["artifactory"] == {"enabled": True}
    # original untouched
    assert values["global"]["imageRegistry"] == "registry.internal.corp"


def test_strip_mirror_overrides_drops_empty_global():
    values = {"global": {"imageRegistry": "r"}, "xray": {"enabled": True}}
    assert strip_mirror_overrides(values) == {"xray": {"enabled": True}}


def test_sanitize_values_files_writes_copy_without_override(tmp_path: Path):
    src = tmp_path / "customvalues.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "global": {"imageRegistry": "registry.internal.corp"},
                "artifactory": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )
    out = sanitize_values_files([src], tmp_path / "sanitized")
    assert out[0] != src
    loaded = yaml.safe_load(out[0].read_text(encoding="utf-8"))
    assert "global" not in loaded
    assert loaded["artifactory"] == {"enabled": True}


def test_sanitize_values_files_passes_through_untouched_file(tmp_path: Path):
    src = tmp_path / "plain.yaml"
    src.write_text(yaml.safe_dump({"artifactory": {"enabled": True}}), encoding="utf-8")
    assert sanitize_values_files([src], tmp_path / "sanitized") == [src]


def test_render_chart_download_instructions_include_pull_and_offline_install():
    text = render_chart_download_instructions("11.5.13")
    assert "helm repo add jfrog https://charts.jfrog.io" in text
    assert "helm pull jfrog/jfrog-platform --version 11.5.13" in text
    assert "jfrog-platform-11.5.13.tgz" in text
    assert "helm upgrade --install <RELEASE_NAME> ./jfrog-platform-11.5.13.tgz" in text


def test_render_image_list_prefixes_chart_instructions():
    text = render_image_list(
        ["releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0"],
        chart_version="11.5.13",
    )
    assert text.startswith("# Generated by jfvb")
    assert "helm pull jfrog/jfrog-platform --version 11.5.13" in text
    # Image lines stay uncommented so tools can still parse them line-by-line
    # after stripping comments.
    assert text.strip().endswith(
        "releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0"
    )
    assert not text.splitlines()[-1].startswith("#")


def test_render_mirror_script_strips_source_registry():
    script = render_mirror_script(
        ["releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0"],
        target_registry="registry.internal.corp",
    )
    assert (
        'skopeo copy "docker://releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0" '
        '"docker://$TARGET_REGISTRY/jfrog/artifactory-pro:7.90.0"'
        in script
    )
    assert 'TARGET_REGISTRY="registry.internal.corp"' in script


def test_render_mirror_script_flags_untagged_images():
    script = render_mirror_script(
        ["releases-docker.jfrog.io/jfrog/xray-jas-exposures"],
        target_registry="registry.internal.corp",
    )
    assert "# NOTE:" in script
    assert "renders without a tag" in script
