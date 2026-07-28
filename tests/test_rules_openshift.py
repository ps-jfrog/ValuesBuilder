from jfvb.answers import Answers
from jfvb.rules import openshift


def test_does_not_apply_when_not_openshift():
    answers = Answers()
    answers.openshift = False
    assert openshift.applies(answers) is False


def test_applies_when_openshift():
    answers = Answers()
    answers.openshift = True
    assert openshift.applies(answers) is True


def test_apply_never_touches_values_only_notes():
    # Deliberately left as a chart-provided overlay (openshift-values.yaml)
    # rather than reproduced inline -- see the module docstring.
    answers = Answers()
    answers.openshift = True
    result = openshift.apply(answers)
    assert result.values == {}
    assert len(result.notes) == 1
    assert "openshift-values.yaml" in result.notes[0]


def test_apply_note_mentions_layering_order():
    answers = Answers()
    answers.openshift = True
    result = openshift.apply(answers)
    assert "-f customvalues.yaml -f openshift-values.yaml" in result.notes[0]
