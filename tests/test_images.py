from jfvb.images import extract_images, render_mirror_script


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


def test_extract_images_dedupes_and_sorts():
    images = extract_images(SAMPLE_MANIFEST)
    assert images == sorted(set(images))
    assert "releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0" in images
    assert images.count("releases-docker.jfrog.io/jfrog/artifactory-pro:7.90.0") == 1
    assert len(images) == 3


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
