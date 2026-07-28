import pytest

from jfvb.wizard import (
    _ask_checkbox,
    _ask_confirm,
    _ask_int,
    _ask_select,
    _ask_text,
    _check_abort,
)


def test_check_abort_passes_through_non_none_values():
    assert _check_abort(False) is False
    assert _check_abort(True) is True
    assert _check_abort("") == ""
    assert _check_abort([]) == []


def test_check_abort_raises_on_none():
    with pytest.raises(KeyboardInterrupt):
        _check_abort(None)


class _FakeQuestion:
    """Stand-in for a questionary Question whose `.ask()` returns a
    pre-set value, so we can simulate both normal answers and a
    Ctrl-C/Ctrl-D abort (which questionary reports as `None`).
    """

    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


@pytest.mark.parametrize(
    "helper_name,helper,extra_args",
    [
        ("text", _ask_text, ("Prompt:", "default")),
        ("confirm", _ask_confirm, ("Prompt?", False)),
        ("select", _ask_select, ("Prompt:", ["a", "b"])),
        ("checkbox", _ask_checkbox, ("Prompt:", ["a", "b"])),
    ],
)
def test_ask_helpers_abort_cleanly_on_ctrl_c(monkeypatch, helper_name, helper, extra_args):
    import jfvb.wizard as wizard_module

    monkeypatch.setattr(
        wizard_module.questionary, "text", lambda *a, **k: _FakeQuestion(None)
    )
    monkeypatch.setattr(
        wizard_module.questionary, "confirm", lambda *a, **k: _FakeQuestion(None)
    )
    monkeypatch.setattr(
        wizard_module.questionary, "select", lambda *a, **k: _FakeQuestion(None)
    )
    monkeypatch.setattr(
        wizard_module.questionary, "checkbox", lambda *a, **k: _FakeQuestion(None)
    )

    with pytest.raises(KeyboardInterrupt):
        helper(*extra_args)


def test_ask_int_falls_back_on_non_numeric_input(monkeypatch, capsys):
    import jfvb.wizard as wizard_module

    monkeypatch.setattr(
        wizard_module.questionary, "text", lambda *a, **k: _FakeQuestion("not-a-number")
    )
    result = _ask_int("How many?", 3)
    assert result == 3
