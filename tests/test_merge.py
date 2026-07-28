from jfvb.merge import deep_merge, deep_merge_all


def test_deep_merge_combines_nested_dicts():
    base = {"a": {"b": 1, "c": 2}}
    override = {"a": {"c": 3, "d": 4}}
    result = deep_merge(base, override)
    assert result == {"a": {"b": 1, "c": 3, "d": 4}}


def test_deep_merge_does_not_mutate_inputs():
    base = {"a": {"b": 1}}
    override = {"a": {"b": 2}}
    deep_merge(base, override)
    assert base == {"a": {"b": 1}}
    assert override == {"a": {"b": 2}}


def test_deep_merge_replaces_lists_outright():
    base = {"a": [1, 2, 3]}
    override = {"a": [4]}
    assert deep_merge(base, override) == {"a": [4]}


def test_deep_merge_all_folds_left_to_right():
    result = deep_merge_all({"a": 1}, {"a": 2, "b": 1}, {"b": 2})
    assert result == {"a": 2, "b": 2}


def test_deep_merge_all_skips_empty_patches():
    result = deep_merge_all({}, {"a": 1}, None or {})
    assert result == {"a": 1}
