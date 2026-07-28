from jfvb.answers import Answers
from jfvb.rules import airgap


def test_airgap_not_applied_when_not_airgapped():
    answers = Answers(airgapped=False)
    assert airgap.applies(answers) is False


def test_airgap_sets_jfconnect_extra_system_yaml():
    answers = Answers(airgapped=True, custom_image_registry="registry.internal")
    result = airgap.apply(answers)
    assert (
        result.values["artifactory"]["artifactory"]["extraSystemYaml"]["jfconnect"]["airgap"][
            "enabled"
        ]
        is True
    )
    assert result.values["global"]["imageRegistry"] == "registry.internal"


def test_airgap_warns_when_no_registry_provided():
    answers = Answers(airgapped=True, custom_image_registry=None)
    result = airgap.apply(answers)
    assert any("no custom image registry" in note for note in result.notes)


def test_airgap_notes_offline_entitlement_registration_for_every_deployment():
    # Entitlement registration applies to any air-gapped install -- not
    # just when JAS/Curation/Mission Control are enabled -- and must not
    # claim a live/allow-listed MyJFrog path is required, since JFrog's
    # documented offline procedure needs no such thing.
    answers = Answers(airgapped=True)
    result = airgap.apply(answers)
    assert any("entitlements" in note.lower() for note in result.notes)
    assert not any("allow-listed path to myjfrog" in note.lower() for note in result.notes)


def test_airgap_includes_entitlements_runbook_step():
    answers = Answers(airgapped=True)
    result = airgap.apply(answers)
    entitlement_steps = [s for s in result.steps if s.phase == "entitlements"]
    assert len(entitlement_steps) == 1
    commands = "\n".join(entitlement_steps[0].commands)
    assert "offline/start_register" in commands
    assert "jes.jfrog.io/api/v1/offline_register" in commands
    assert "offline/register" in commands
