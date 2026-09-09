"""Load / dump `Answers` as YAML (or JSON) for non-interactive builds.

The interactive wizard remains the default path; pass `--answers FILE` to
`jfvb build` to skip it. Unknown top-level keys are rejected so typos
surface early instead of being silently ignored.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Type, TypeVar, get_args, get_origin, get_type_hints

import yaml

from jfvb.answers import Answers

T = TypeVar("T")


class AnswersLoadError(ValueError):
    """Raised when an answers file is missing, malformed, or invalid."""


def _unwrap_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is type(None):
        return annotation
    args = get_args(annotation)
    if origin is None or not args:
        return annotation
    # Optional[X] / X | None
    non_none = [a for a in args if a is not type(None)]
    if len(non_none) == 1 and type(None) in args:
        return non_none[0]
    return annotation


def _is_optional(annotation: Any) -> bool:
    if annotation is type(None):
        return True
    return type(None) in get_args(annotation)


def _coerce(value: Any, annotation: Any, path: str) -> Any:
    if value is None:
        if _is_optional(annotation):
            return None
        raise AnswersLoadError(f"{path}: null is not allowed here; expected a value")

    annotation = _unwrap_optional(annotation)
    origin = get_origin(annotation)

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except ValueError as exc:
            allowed = ", ".join(m.value for m in annotation)
            raise AnswersLoadError(
                f"{path}: invalid value {value!r}; expected one of: {allowed}"
            ) from exc

    if isinstance(annotation, type) and is_dataclass(annotation):
        if not isinstance(value, dict):
            raise AnswersLoadError(f"{path}: expected a mapping, got {type(value).__name__}")
        return _dataclass_from_dict(annotation, value, path)

    if origin is dict or annotation is dict:
        if not isinstance(value, dict):
            raise AnswersLoadError(f"{path}: expected a mapping, got {type(value).__name__}")
        return value

    if annotation is bool:
        if not isinstance(value, bool):
            raise AnswersLoadError(f"{path}: expected a boolean, got {type(value).__name__}")
        return value

    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise AnswersLoadError(f"{path}: expected an integer, got {type(value).__name__}")
        return value

    if annotation is str:
        if not isinstance(value, str):
            raise AnswersLoadError(f"{path}: expected a string, got {type(value).__name__}")
        return value

    return value


def _dataclass_from_dict(cls: Type[T], data: dict[str, Any], path: str) -> T:
    hints = get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise AnswersLoadError(
            f"{path}: unknown field(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(known))}"
        )

    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        kwargs[f.name] = _coerce(data[f.name], hints[f.name], f"{path}.{f.name}")
    return cls(**kwargs)


def answers_from_dict(data: dict[str, Any]) -> Answers:
    if not isinstance(data, dict):
        raise AnswersLoadError("answers root must be a mapping")
    return _dataclass_from_dict(Answers, data, "answers")


def _to_plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: _to_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    return value


def answers_to_dict(answers: Answers) -> dict[str, Any]:
    return _to_plain(answers)


def load_answers(path: Path) -> Answers:
    """Load Answers from a YAML or JSON file."""

    if not path.exists():
        raise AnswersLoadError(f"answers file not found: {path}")

    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise AnswersLoadError(f"failed to parse {path}: {exc}") from exc

    if data is None:
        raise AnswersLoadError(f"answers file is empty: {path}")
    return answers_from_dict(data)


def dump_answers_yaml(answers: Answers) -> str:
    """Serialize Answers to a commented-friendly YAML string."""

    return yaml.safe_dump(
        answers_to_dict(answers),
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


EXAMPLE_ANSWERS_YAML = """\
# Non-interactive answers for `jfvb build --answers FILE`.
# This file is a 1:1 map of every field the interactive wizard collects --
# set any field here to skip that question. Omit a field to keep its default
# (see Answers in answers.py). Hosts/buckets may be left null to emit
# <PLACEHOLDER> values in customvalues.yaml (same as leaving a wizard
# prompt blank); fill them here when you already know them.

# --- Basics ---
chart_version: null          # null = latest; set e.g. "11.5.9" to pin.
# Chart version also drives version-gated values paths (chart_compat.FEATURES).
# Example: air-gap uses extraSystemYaml before 11.5.13, and
# artifactory.jfconnect.airgap.enabled from 11.5.13 onward.
namespace: jfrog-platform
release_name: jfrog-platform
fresh_deployment: true

# --- Environment ---
airgapped: false
openshift: false
custom_image_registry: null  # e.g. registry.internal.corp
image_pull_secret_name: null # e.g. jfrog-registry-creds

# --- Sizing ---
# T-shirt size. Criteria (cluster-wide Artifactory load):
#   small    <= 6,000 RPM / <= 100 concurrent connections   (dev/test)
#   medium   <= 50,000 RPM / <= 500 concurrent connections  (small prod)
#   large    <= 100,000 RPM / <= 1,200 concurrent connections
#   xlarge   <= 200,000 RPM / <= 3,000 concurrent connections
#   2xlarge  <= 500,000 RPM / <= 6,000 concurrent connections
#   custom   -- set custom_sizing.artifactory_replicas / xray_replicas
size_tier: small
worker_node_count: 3
dedicated_node_pools: false
custom_sizing: {}
# custom_sizing example when size_tier is custom:
# custom_sizing:
#   artifactory_replicas: 2
#   xray_replicas: 1

# --- Products ---
products:
  artifactory: true          # always on
  xray: true
  distribution: false
  catalog: false
  workers: false
  jas: false
  curation: false
  sbom: false                # only meaningful when xray is true
  bridge: false
  apptrust: false
  mission_control: false
  federation: false

# --- Database ---
# mode: external (recommended) | bundled (eval/dev only)
# Set shared_host, or per_product_host for each enabled DB product.
# Leave null/{} to emit <DB_HOST> placeholders.
database:
  mode: external
  shared_host: null          # e.g. pg.acme.com
  port: 5432
  ssl_mode: require          # require | disable | verify-full
  per_product_host: {}
  # per_product_host example:
  #   artifactory: art-pg.acme.com
  #   xray: xray-pg.acme.com
  per_product_secret_name: {}
  # per_product_secret_name example (defaults to <release>-<product>-db-creds):
  #   artifactory: my-artifactory-db-creds

# --- Filestore (Artifactory only) ---
# type: local | nfs | s3 | gcs | azure_blob | full_db | custom
# mode (cloud only): direct | eventual | cluster | archive (S3 only)
# Leave bucket/IP/account null for placeholders.
filestore:
  type: local
  mode: direct
  nfs_ip: null
  nfs_capacity: 200Gi
  s3_bucket: null
  s3_region: null
  s3_endpoint: null
  s3_path: artifactory/filestore
  s3_use_iam_role: true
  s3_compatible_non_aws: false
  s3_use_http: false
  s3_port: null
  gcs_bucket: null
  gcs_path: artifactory/filestore
  azure_account_name: null
  azure_container: null
  azure_endpoint: null
  direct_cloud_download: false
  signed_url_expiry_seconds: 30
  local_cache_enabled: false
  full_db_use_cache: true

# --- RabbitMQ (only used when products.xray is true) ---
rabbitmq_mode: bundled_classic   # bundled_classic | bundled_quorum | external

# --- Networking / TLS ---
networking:
  external_ingress: false   # bundled nginx is used whenever this is false
  ingress_host: null
  ingress_class_name: null   # e.g. nginx, alb, traefik
  custom_tls: false
  tls_secret_name: null
  custom_trusted_certificates: false
  # Bundled nginx HTTPS. Required from chart 11.6.0 (older charts ignore it):
  # existing_secret (production, uses tls_secret_name) | self_signed (dev/test)
  # | disabled (HTTP only). null defaults to existing_secret with a warning.
  nginx_tls_mode: null

# --- Security keys ---
# master_key_source / join_key_source: generate | existing_secret | literal
security:
  master_key_source: generate
  join_key_source: generate
  master_key_secret_name: master-key
  join_key_secret_name: join-key
"""
