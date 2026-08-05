import pytest

from jfvb.chart_compat import (
    ChartVersionError,
    active_features,
    feature_active,
    parse_chart_version,
    version_at_least,
    version_before,
)


def test_parse_chart_version():
    assert parse_chart_version("11.5.13") == (11, 5, 13)
    assert parse_chart_version("v11.5.9") == (11, 5, 9)
    assert parse_chart_version("11.5.13-rc.1") == (11, 5, 13)


def test_parse_chart_version_rejects_garbage():
    with pytest.raises(ChartVersionError):
        parse_chart_version("latest")
    with pytest.raises(ChartVersionError):
        parse_chart_version("not-a-version")


def test_version_at_least():
    assert version_at_least("11.5.13", "11.5.13") is True
    assert version_at_least("11.5.14", "11.5.13") is True
    assert version_at_least("11.5.12", "11.5.13") is False
    assert version_at_least(None, "11.5.13") is True
    assert version_at_least("latest", "11.5.13") is True


def test_version_before():
    assert version_before("11.5.12", "11.5.13") is True
    assert version_before("11.5.13", "11.5.13") is False
    assert version_before(None, "11.5.13") is False


def test_airgap_features_flip_at_11_5_13():
    assert feature_active("airgap_extra_system_yaml", "11.5.12") is True
    assert feature_active("airgap_jfconnect_values", "11.5.12") is False
    assert feature_active("airgap_extra_system_yaml", "11.5.13") is False
    assert feature_active("airgap_jfconnect_values", "11.5.13") is True
    assert feature_active("airgap_jfconnect_values", None) is True
    assert feature_active("airgap_extra_system_yaml", None) is False


def test_active_features_lists_expected_for_old_chart():
    ids = {f.id for f in active_features("11.5.9")}
    assert "airgap_extra_system_yaml" in ids
    assert "airgap_jfconnect_values" not in ids


def test_active_features_lists_expected_for_new_chart():
    ids = {f.id for f in active_features("11.5.13")}
    assert "airgap_jfconnect_values" in ids
    assert "airgap_extra_system_yaml" not in ids


def test_unknown_feature_raises():
    with pytest.raises(KeyError, match="unknown chart feature"):
        feature_active("does_not_exist", "11.5.13")
