from jfvb.answers import Answers, SizeTier
from jfvb.sizing import _prune_irrelevant_baseline_sections, build_sizing


def test_medium_tier_sets_reference_architecture_replicas():
    answers = Answers(size_tier=SizeTier.MEDIUM)
    result = build_sizing(answers, chart_dir=None)
    assert result.values["artifactory"]["artifactory"]["replicaCount"] == 2
    assert result.values["artifactory"]["nginx"]["replicaCount"] == 2
    assert result.values["xray"]["replicaCount"] == 2
    assert result.values["rabbitmq"]["replicaCount"] == 3


def test_bundled_postgres_corrected_to_reference_architecture_not_chart_half_values():
    """Regression test for the confirmed discrepancy: the chart's own
    sizing/platform-large.yaml sets Artifactory's bundled Postgres to
    cpu=16/memory=64Gi, but the reference architecture's db.m8g.8xlarge for
    "Large" is 32 vCPU / 128GiB. jfvb must apply the reference-architecture
    number, not the chart's under-sized one."""

    answers = Answers(size_tier=SizeTier.LARGE)
    result = build_sizing(answers, chart_dir=None)

    pg = result.values["artifactory"]["postgresql"]["primary"]["resources"]
    assert pg["requests"]["cpu"] == "32"
    assert pg["requests"]["memory"] == "128Gi"

    xray_pg = result.values["xray"]["postgresql"]["primary"]["resources"]
    assert xray_pg["requests"]["cpu"] == "16"
    assert xray_pg["requests"]["memory"] == "64Gi"


def test_max_connections_set_per_tier():
    answers = Answers(size_tier=SizeTier.SMALL)
    result = build_sizing(answers, chart_dir=None)
    config = result.values["artifactory"]["postgresql"]["primary"]["extendedConfiguration"]
    assert "max_connections = 100" in config


def test_xray_disabled_skips_xray_and_rabbitmq_sizing():
    answers = Answers(size_tier=SizeTier.SMALL)
    answers.products.xray = False
    result = build_sizing(answers, chart_dir=None)
    assert "xray" not in result.values
    assert "rabbitmq" not in result.values


FULL_CHART_BASELINE = {
    "artifactory": {
        "artifactory": {"replicaCount": 2},
        "nginx": {"replicaCount": 2, "resources": {"requests": {"cpu": "100m"}}},
    },
    "xray": {"replicaCount": 2},
    "rabbitmq": {"replicaCount": 3},
    "distribution": {"replicaCount": 1},
}


def test_prune_drops_xray_and_rabbitmq_when_xray_disabled():
    answers = Answers()
    answers.products.xray = False
    pruned = _prune_irrelevant_baseline_sections(FULL_CHART_BASELINE, answers)
    assert "xray" not in pruned
    assert "rabbitmq" not in pruned
    assert "artifactory" in pruned  # untouched


def test_prune_drops_distribution_when_not_enabled():
    answers = Answers()
    answers.products.distribution = False
    pruned = _prune_irrelevant_baseline_sections(FULL_CHART_BASELINE, answers)
    assert "distribution" not in pruned


def test_prune_drops_nginx_when_external_ingress_selected():
    answers = Answers()
    answers.networking.external_ingress = True
    pruned = _prune_irrelevant_baseline_sections(FULL_CHART_BASELINE, answers)
    assert "nginx" not in pruned["artifactory"]
    assert pruned["artifactory"]["artifactory"]["replicaCount"] == 2  # untouched


def test_prune_does_not_mutate_input():
    answers = Answers()
    answers.products.xray = False
    _prune_irrelevant_baseline_sections(FULL_CHART_BASELINE, answers)
    assert "xray" in FULL_CHART_BASELINE


def test_build_sizing_omits_nginx_resource_noise_when_external_ingress(tmp_path):
    sizing_dir = tmp_path / "sizing"
    sizing_dir.mkdir()
    (sizing_dir / "platform-medium.yaml").write_text(
        """
artifactory:
  artifactory:
    replicaCount: 2
  nginx:
    replicaCount: 2
    disableProxyBuffering: true
    resources:
      requests:
        cpu: 100m
        memory: 100Mi
xray:
  replicaCount: 2
rabbitmq:
  replicaCount: 3
distribution:
  replicaCount: 1
"""
    )
    answers = Answers(size_tier=SizeTier.MEDIUM)
    answers.networking.external_ingress = True
    result = build_sizing(answers, chart_dir=tmp_path)
    assert "nginx" not in result.values["artifactory"]


def test_custom_tier_uses_manual_replica_counts_only():
    answers = Answers(size_tier=SizeTier.CUSTOM)
    answers.custom_sizing = {"artifactory_replicas": 5, "xray_replicas": 2}
    result = build_sizing(answers, chart_dir=None)
    assert result.values["artifactory"]["artifactory"]["replicaCount"] == 5
    assert result.values["xray"]["replicaCount"] == 2
    assert "postgresql" not in result.values.get("artifactory", {})
