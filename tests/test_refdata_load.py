import pytest

from jfvb.refdata import (
    format_tier_choice_label,
    get_tier_load,
    recommend_tier,
)


def test_every_named_tier_has_cluster_wide_load_criteria():
    for tier in ("small", "medium", "large", "xlarge", "2xlarge"):
        load = get_tier_load(tier)
        assert load is not None
        assert load["peak_rpm"] > 0
        assert load["peak_concurrent_connections"] > 0
        assert load["use_case"]


def test_small_load_criteria_match_reference_architecture():
    load = get_tier_load("small")
    assert load["peak_rpm"] == 6000
    assert load["peak_concurrent_connections"] == 100


def test_2xlarge_load_criteria_match_reference_architecture():
    load = get_tier_load("2xlarge")
    assert load["peak_rpm"] == 500000
    assert load["peak_concurrent_connections"] == 6000


def test_format_tier_choice_label_includes_rpm_and_connections():
    label = format_tier_choice_label("medium")
    assert "medium" in label
    assert "50,000" in label
    assert "500" in label
    assert "cluster-wide" in label


def test_recommend_tier_by_rpm():
    assert recommend_tier(peak_rpm=1000) == "small"
    assert recommend_tier(peak_rpm=6000) == "small"
    assert recommend_tier(peak_rpm=6001) == "medium"
    assert recommend_tier(peak_rpm=100000) == "large"
    assert recommend_tier(peak_rpm=200000) == "xlarge"
    assert recommend_tier(peak_rpm=500000) == "2xlarge"


def test_recommend_tier_by_concurrent_connections():
    assert recommend_tier(peak_concurrent_connections=100) == "small"
    assert recommend_tier(peak_concurrent_connections=501) == "large"
    assert recommend_tier(peak_concurrent_connections=3000) == "xlarge"


def test_recommend_tier_uses_stricter_of_both_metrics():
    # RPM fits small, but concurrent needs medium
    assert recommend_tier(peak_rpm=1000, peak_concurrent_connections=200) == "medium"


def test_recommend_tier_none_when_no_metrics():
    assert recommend_tier() is None


def test_recommend_tier_rejects_overload():
    with pytest.raises(ValueError, match="exceeds"):
        recommend_tier(peak_rpm=500_001)
