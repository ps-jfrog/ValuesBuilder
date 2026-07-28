from jfvb.answers import Answers, RabbitMQMode
from jfvb.rules import ha


def test_does_not_apply_when_xray_disabled():
    answers = Answers()
    answers.products.xray = False
    assert ha.applies(answers) is False


def test_applies_when_xray_enabled():
    answers = Answers()
    answers.products.xray = True
    assert ha.applies(answers) is True


def test_bundled_classic_is_default_and_produces_no_patch():
    answers = Answers()
    answers.rabbitmq_mode = RabbitMQMode.BUNDLED_CLASSIC
    result = ha.apply(answers)
    assert result.values == {}
    assert any("classic" in n.lower() for n in result.notes)


def test_bundled_quorum_sets_ha_quorum_flag():
    answers = Answers()
    answers.rabbitmq_mode = RabbitMQMode.BUNDLED_QUORUM
    result = ha.apply(answers)
    assert (
        result.values["global"]["rabbitmq"]["xray"]["rabbitmq"]["haQuorum"]["enabled"]
        is True
    )
    assert any("quorum" in n.lower() for n in result.notes)


def test_external_rabbitmq_disables_bundled_and_wires_secret_refs():
    answers = Answers()
    answers.rabbitmq_mode = RabbitMQMode.EXTERNAL
    result = ha.apply(answers)
    assert result.values["rabbitmq"]["enabled"] is False
    xray_rmq = result.values["xray"]["rabbitmq"]
    assert xray_rmq["enabled"] is False
    secret_name = f"{answers.release_name}-external-rabbitmq-creds"
    assert xray_rmq["external"]["secrets"]["username"]["name"] == secret_name
    assert xray_rmq["external"]["secrets"]["password"]["name"] == secret_name
    assert xray_rmq["external"]["secrets"]["url"]["name"] == secret_name


def test_external_rabbitmq_generates_credentials_secret_step():
    answers = Answers()
    answers.rabbitmq_mode = RabbitMQMode.EXTERNAL
    result = ha.apply(answers)
    assert len(result.steps) == 1
    step_text = " ".join(result.steps[0].commands)
    assert "create secret generic" in step_text
    assert "--from-literal=username=" in step_text
    assert "--from-literal=password=" in step_text
    assert "--from-literal=url=" in step_text


def test_external_rabbitmq_secret_name_uses_release_name():
    answers = Answers()
    answers.release_name = "my-release"
    answers.rabbitmq_mode = RabbitMQMode.EXTERNAL
    result = ha.apply(answers)
    step_text = " ".join(result.steps[0].commands)
    assert "my-release-external-rabbitmq-creds" in step_text
