from jfvb.answers import Answers
from jfvb.rules import modules


def test_curation_without_catalog_auto_enables_catalog():
    answers = Answers()
    answers.products.curation = True
    answers.products.catalog = False
    result = modules.apply(answers)
    assert result.values["catalog"]["enabled"] is True
    assert any("auto-enabled" in note for note in result.notes)


def test_sbom_requires_xray():
    answers = Answers()
    answers.products.xray = False
    answers.products.sbom = True
    result = modules.apply(answers)
    assert "sbom" not in result.values.get("xray", {})
    assert any("WARNING" in note and "SBOM" in note for note in result.notes)


def test_sbom_enabled_when_xray_on():
    answers = Answers()
    answers.products.xray = True
    answers.products.sbom = True
    result = modules.apply(answers)
    assert result.values["xray"]["sbom"]["enabled"] is True


def test_jas_sets_service_account_and_rbac():
    answers = Answers()
    answers.products.xray = True
    answers.products.jas = True
    result = modules.apply(answers)
    assert result.values["xray"]["serviceAccount"]["create"] is True
    assert result.values["xray"]["rbac"]["create"] is True


def test_disabling_xray_also_disables_rabbitmq():
    answers = Answers()
    answers.products.xray = False
    result = modules.apply(answers)
    assert result.values["rabbitmq"]["enabled"] is False


def test_unselected_products_are_omitted_entirely_not_set_false():
    """distribution/catalog/worker default to enabled: false on the chart
    already -- the generated file shouldn't restate that default."""
    answers = Answers()  # distribution/catalog/workers all False by default
    result = modules.apply(answers)
    assert "distribution" not in result.values
    assert "catalog" not in result.values
    assert "worker" not in result.values


def test_selected_products_are_explicitly_enabled():
    answers = Answers()
    answers.products.distribution = True
    answers.products.catalog = True
    answers.products.workers = True
    result = modules.apply(answers)
    assert result.values["distribution"]["enabled"] is True
    assert result.values["catalog"]["enabled"] is True
    assert result.values["worker"]["enabled"] is True


def test_xray_enabled_by_default_omits_explicit_key():
    answers = Answers()  # xray True by default
    result = modules.apply(answers)
    assert "xray" not in result.values


def test_apptrust_sets_both_required_keys():
    answers = Answers()
    answers.products.xray = True
    answers.products.sbom = True
    answers.products.apptrust = True
    result = modules.apply(answers)
    assert result.values["artifactory"]["apptrust"]["enabled"] is True
    assert result.values["artifactory"]["unifiedpolicy"]["enabled"] is True


def test_bridge_alone_sets_digests_default_without_warning():
    answers = Answers()
    answers.products.bridge = True
    result = modules.apply(answers)
    assert result.values["global"]["digests"] == {}
    assert not any("WARNING" in note and "digests" in note for note in result.notes)


def test_bridge_with_worker_or_catalog_warns_about_render_failure():
    answers = Answers()
    answers.products.bridge = True
    answers.products.workers = True
    result = modules.apply(answers)
    assert result.values["global"]["digests"] == {}
    assert any("WARNING" in note and "digests" in note for note in result.notes)
