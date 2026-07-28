from jfvb.answers import Answers, SizeTier
from jfvb.rules import affinity


def test_hard_anti_affinity_when_enough_nodes():
    answers = Answers(size_tier=SizeTier.MEDIUM, worker_node_count=5)
    result = affinity.apply(answers)
    assert result.values["artifactory"]["artifactory"]["podAntiAffinity"]["type"] == "hard"


def test_soft_anti_affinity_and_warning_when_not_enough_nodes():
    answers = Answers(size_tier=SizeTier.LARGE, worker_node_count=1)
    result = affinity.apply(answers)
    assert result.values["artifactory"]["artifactory"]["podAntiAffinity"]["type"] == "soft"
    assert any("WARNING" in note for note in result.notes)


def test_dedicated_node_pools_adds_node_selectors():
    answers = Answers(size_tier=SizeTier.MEDIUM, worker_node_count=5, dedicated_node_pools=True)
    result = affinity.apply(answers)
    assert (
        result.values["artifactory"]["artifactory"]["nodeSelector"]["jfrog.com/node-pool"]
        == "artifactory"
    )
    assert result.values["artifactory"]["nginx"]["nodeSelector"]["jfrog.com/node-pool"] == "nginx"
    assert result.values["xray"]["xray"]["nodeSelector"]["jfrog.com/node-pool"] == "xray"
    assert len(result.steps) == 1
    assert "kubectl label nodes" in result.steps[0].commands[0]


def test_no_node_pools_when_not_requested():
    answers = Answers(size_tier=SizeTier.MEDIUM, dedicated_node_pools=False)
    result = affinity.apply(answers)
    assert "nodeSelector" not in result.values["artifactory"]["artifactory"]
    assert result.steps == []
