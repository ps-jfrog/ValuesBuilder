from jfvb.answers import Answers, KeySource
from jfvb.rules import security


def test_default_uses_secret_name_refs_not_literals():
    answers = Answers()
    result = security.apply(answers)
    assert result.values["global"]["masterKeySecretName"] == "master-key"
    assert result.values["global"]["joinKeySecretName"] == "join-key"
    assert "masterKey" not in result.values["global"]
    assert "joinKey" not in result.values["global"]
    assert not any(n.startswith("WARNING") for n in result.notes)


def test_literal_keys_write_placeholders_and_warn():
    answers = Answers()
    answers.security.master_key_source = KeySource.LITERAL
    answers.security.join_key_source = KeySource.LITERAL
    result = security.apply(answers)
    assert result.values["global"]["masterKey"] == "<MASTER_KEY>"
    assert result.values["global"]["joinKey"] == "<JOIN_KEY>"
    assert "masterKeySecretName" not in result.values["global"]
    warnings = [n for n in result.notes if n.startswith("WARNING")]
    assert len(warnings) == 2


def test_custom_secret_names_are_used():
    answers = Answers()
    answers.security.master_key_secret_name = "custom-master-key"
    answers.security.join_key_secret_name = "custom-join-key"
    result = security.apply(answers)
    assert result.values["global"]["masterKeySecretName"] == "custom-master-key"
    assert result.values["global"]["joinKeySecretName"] == "custom-join-key"


def test_fresh_deployment_generates_new_keys():
    answers = Answers()
    answers.fresh_deployment = True
    result = security.apply(answers)
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.phase == "keys"
    step_text = " ".join(step.commands)
    assert "openssl rand -hex 32" in step_text
    assert "openssl rand -hex 16" in step_text
    assert "create secret generic master-key" in step_text
    assert "create secret generic join-key" in step_text


def test_upgrade_deployment_retrieves_existing_keys_without_regenerating():
    answers = Answers()
    answers.fresh_deployment = False
    result = security.apply(answers)
    step = result.steps[0]
    step_text = " ".join(step.commands)
    assert "openssl rand" not in step_text
    assert "kubectl -n" in step_text and "get secret master-key" in step_text
    assert "get secret join-key" in step_text
    assert "Retrieve existing master key" in step.title


def test_fresh_vs_upgrade_use_custom_secret_names_in_commands():
    answers = Answers()
    answers.security.master_key_secret_name = "custom-master-key"
    answers.security.join_key_secret_name = "custom-join-key"
    answers.fresh_deployment = False
    result = security.apply(answers)
    step_text = " ".join(result.steps[0].commands)
    assert "custom-master-key" in step_text
    assert "custom-join-key" in step_text


def test_applies_always_true():
    assert security.applies(Answers()) is True
