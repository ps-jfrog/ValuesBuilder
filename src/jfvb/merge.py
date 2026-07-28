"""Deep-merge helpers shared by the sizing engine and the rule engine."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` into `base`, returning a new dict.

    Dicts are merged key-by-key; any other type (including lists) in
    `override` replaces the corresponding value in `base` outright. This
    matches Helm's own values-merging semantics closely enough for our
    purposes (Helm does not merge lists either).
    """

    result = deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def deep_merge_all(*patches: dict[str, Any]) -> dict[str, Any]:
    """Fold a sequence of patches together in order, left-to-right."""

    result: dict[str, Any] = {}
    for patch in patches:
        if patch:
            result = deep_merge(result, patch)
    return result
