from jfvb.answers import Answers, DatabaseMode, SizeTier
from jfvb.builder import run_build
from jfvb.render import render_summary_md, render_values_yaml
from jfvb.runbook import render_runbook_md


def test_run_build_merges_all_rule_outputs():
    answers = Answers(size_tier=SizeTier.SMALL)
    answers.database.mode = DatabaseMode.EXTERNAL
    answers.database.shared_host = "pg.acme.com"
    out = run_build(answers, chart_dir=None)
    assert "artifactory" in out.values
    assert out.values["postgresql"]["enabled"] is False
    assert len(out.contributions) >= 5


def test_render_values_yaml_is_valid_yaml():
    import yaml as pyyaml

    answers = Answers(size_tier=SizeTier.SMALL)
    out = run_build(answers, chart_dir=None)
    text = render_values_yaml(out)
    parsed = pyyaml.safe_load(text)
    assert parsed["artifactory"]["enabled"] is True


def test_render_values_yaml_puts_global_section_first():
    answers = Answers(size_tier=SizeTier.SMALL, airgapped=True)
    answers.custom_image_registry = "registry.internal.corp"
    out = run_build(answers, chart_dir=None)
    text = render_values_yaml(out)
    top_level_keys = [
        line.split(":", 1)[0]
        for line in text.splitlines()
        if line and not line.startswith(" ") and not line.startswith("#")
    ]
    assert top_level_keys[0] == "global"


def test_render_summary_lists_warnings_separately():
    answers = Answers(size_tier=SizeTier.SMALL, airgapped=True)
    out = run_build(answers, chart_dir=None)
    summary = render_summary_md(answers, out, "11.5.9")
    assert "## Warnings" in summary


def test_render_runbook_omits_irrelevant_phases():
    answers = Answers(size_tier=SizeTier.SMALL)
    answers.database.mode = DatabaseMode.BUNDLED  # no DB runbook steps expected
    answers.networking.custom_tls = False
    answers.networking.custom_trusted_certificates = False
    out = run_build(answers, chart_dir=None)
    runbook = render_runbook_md(answers, out, "11.5.9")
    assert "TLS" not in runbook.split("## ")[0]
    assert "3. Trusted certificates" not in runbook
    assert "5. TLS" not in runbook


def test_fresh_vs_upgrade_key_instructions_differ():
    fresh = Answers(fresh_deployment=True)
    upgrade = Answers(fresh_deployment=False)
    fresh_out = run_build(fresh, chart_dir=None)
    upgrade_out = run_build(upgrade, chart_dir=None)
    fresh_runbook = render_runbook_md(fresh, fresh_out, "11.5.9")
    upgrade_runbook = render_runbook_md(upgrade, upgrade_out, "11.5.9")
    assert "openssl rand" in fresh_runbook
    assert "openssl rand" not in upgrade_runbook
    assert "Retrieve existing master key" in upgrade_runbook
