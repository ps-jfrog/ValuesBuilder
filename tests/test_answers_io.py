from pathlib import Path
from dataclasses import fields, is_dataclass

import pytest
import yaml

from jfvb.answers import Answers, DatabaseMode, FilestoreType, SizeTier
from jfvb.answers_io import (
    EXAMPLE_ANSWERS_YAML,
    AnswersLoadError,
    answers_from_dict,
    answers_to_dict,
    dump_answers_yaml,
    load_answers,
)
from jfvb.builder import run_build


def _dataclass_field_paths(cls, prefix: str = "") -> set[str]:
    from typing import get_args, get_origin, get_type_hints

    paths: set[str] = set()
    hints = get_type_hints(cls)
    for f in fields(cls):
        path = f"{prefix}.{f.name}" if prefix else f.name
        paths.add(path)
        annotation = hints[f.name]
        origin = get_origin(annotation)
        args = get_args(annotation)
        # Optional[Nested] / Nested | None
        candidates = args if origin is not None and args else (annotation,)
        for candidate in candidates:
            if isinstance(candidate, type) and is_dataclass(candidate):
                paths |= _dataclass_field_paths(candidate, path)
    return paths


def _mapping_key_paths(data: dict, prefix: str = "") -> set[str]:
    """Collect dotted key paths; stop at free-form dict fields."""

    freeform = {"custom_sizing", "per_product_host", "per_product_secret_name"}
    paths: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.add(path)
        if isinstance(value, dict) and key not in freeform:
            paths |= _mapping_key_paths(value, path)
    return paths


def test_example_answers_covers_every_answers_field():
    """The starter answers file must expose every wizard/Answers field."""

    data = yaml.safe_load(EXAMPLE_ANSWERS_YAML)
    example_paths = _mapping_key_paths(data)
    schema_paths = _dataclass_field_paths(Answers)
    missing = sorted(schema_paths - example_paths)
    assert missing == [], f"example answers missing fields: {missing}"


def test_example_answers_yaml_loads_and_builds(tmp_path: Path):
    path = tmp_path / "answers.yaml"
    path.write_text(EXAMPLE_ANSWERS_YAML, encoding="utf-8")
    answers = load_answers(path)
    assert answers.size_tier == SizeTier.SMALL
    assert answers.database.mode == DatabaseMode.EXTERNAL
    assert answers.database.shared_host is None
    assert answers.filestore.type == FilestoreType.LOCAL
    out = run_build(answers, chart_dir=None)
    assert out.values["global"]["database"]["host"] == "<DB_HOST>"


def test_answers_file_can_supply_database_host(tmp_path: Path):
    path = tmp_path / "answers.yaml"
    path.write_text(
        "database:\n  mode: external\n  shared_host: pg.acme.com\n",
        encoding="utf-8",
    )
    answers = load_answers(path)
    out = run_build(answers, chart_dir=None)
    assert out.values["global"]["database"]["host"] == "pg.acme.com"


def test_load_answers_json(tmp_path: Path):
    path = tmp_path / "answers.json"
    path.write_text('{"size_tier": "medium", "namespace": "jf"}', encoding="utf-8")
    answers = load_answers(path)
    assert answers.size_tier == SizeTier.MEDIUM
    assert answers.namespace == "jf"


def test_unknown_field_rejected():
    with pytest.raises(AnswersLoadError, match="unknown field"):
        answers_from_dict({"not_a_real_field": True})


def test_invalid_enum_rejected():
    with pytest.raises(AnswersLoadError, match="invalid value"):
        answers_from_dict({"size_tier": "tiny"})


def test_round_trip_dump_load(tmp_path: Path):
    answers = answers_from_dict(
        {
            "size_tier": "large",
            "products": {"xray": False, "distribution": True},
            "database": {"mode": "bundled"},
            "filestore": {"type": "s3", "mode": "direct"},
        }
    )
    path = tmp_path / "roundtrip.yaml"
    path.write_text(dump_answers_yaml(answers), encoding="utf-8")
    loaded = load_answers(path)
    assert loaded.size_tier == SizeTier.LARGE
    assert loaded.products.xray is False
    assert loaded.products.distribution is True
    assert loaded.database.mode == DatabaseMode.BUNDLED
    assert loaded.filestore.type == FilestoreType.S3
    assert answers_to_dict(loaded)["size_tier"] == "large"


def test_nested_unknown_field_rejected():
    with pytest.raises(AnswersLoadError, match="database"):
        answers_from_dict({"database": {"mode": "external", "typo_host": "x"}})


def test_example_answers_is_valid_yaml():
    data = yaml.safe_load(EXAMPLE_ANSWERS_YAML)
    assert isinstance(data, dict)
    assert data["size_tier"] == "small"
