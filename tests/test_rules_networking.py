from jfvb.answers import Answers
from jfvb.rules import networking


def test_bundled_nginx_default_produces_no_patch():
    answers = Answers()
    result = networking.apply(answers)
    assert result.values == {}
    assert any("bundled nginx" in n.lower() for n in result.notes)


def test_external_ingress_disables_nginx_and_enables_ingress():
    answers = Answers()
    answers.networking.external_ingress = True
    answers.networking.ingress_host = "artifactory.acme.com"
    result = networking.apply(answers)
    ingress = result.values["artifactory"]["ingress"]
    assert result.values["artifactory"]["nginx"]["enabled"] is False
    assert ingress["enabled"] is True
    assert ingress["hosts"] == ["artifactory.acme.com"]


def test_external_ingress_without_class_name_warns():
    answers = Answers()
    answers.networking.external_ingress = True
    result = networking.apply(answers)
    assert "className" not in result.values["artifactory"]["ingress"]
    assert any("classname" in n.lower() for n in result.notes)


def test_external_ingress_with_class_name_sets_it_and_omits_warning():
    answers = Answers()
    answers.networking.external_ingress = True
    answers.networking.ingress_class_name = "alb"
    result = networking.apply(answers)
    assert result.values["artifactory"]["ingress"]["className"] == "alb"
    assert not any("classname" in n.lower() for n in result.notes)


def test_custom_tls_on_external_ingress_sets_ingress_tls_block():
    answers = Answers()
    answers.networking.external_ingress = True
    answers.networking.ingress_host = "artifactory.acme.com"
    answers.networking.custom_tls = True
    answers.networking.tls_secret_name = "my-tls-secret"
    result = networking.apply(answers)
    tls = result.values["artifactory"]["ingress"]["tls"]
    assert tls == [{"secretName": "my-tls-secret", "hosts": ["artifactory.acme.com"]}]
    assert any(
        "kubectl -n" in " ".join(s.commands) and "create secret tls my-tls-secret" in " ".join(s.commands)
        for s in result.steps
    )


def test_custom_tls_on_bundled_nginx_sets_tls_secret_name():
    answers = Answers()
    answers.networking.custom_tls = True
    result = networking.apply(answers)
    tls_secret_name = result.values["artifactory"]["nginx"]["tlsSecretName"]
    assert tls_secret_name == f"{answers.release_name}-tls"


def test_custom_tls_defaults_secret_name_from_release_name():
    answers = Answers()
    answers.release_name = "my-release"
    answers.networking.custom_tls = True
    result = networking.apply(answers)
    assert result.values["artifactory"]["nginx"]["tlsSecretName"] == "my-release-tls"


def test_custom_trusted_certificates_sets_global_block_and_step():
    answers = Answers()
    answers.networking.custom_trusted_certificates = True
    result = networking.apply(answers)
    global_certs = result.values["global"]["customCertificates"]
    assert global_certs["enabled"] is True
    assert global_certs["certificateSecretName"] == f"{answers.release_name}-trusted-certs"
    assert any("trusted-certs" in " ".join(s.commands) for s in result.steps)


def test_no_networking_options_selected_only_default_note():
    answers = Answers()
    result = networking.apply(answers)
    assert result.values == {}
    assert result.steps == []
    assert len(result.notes) == 1
