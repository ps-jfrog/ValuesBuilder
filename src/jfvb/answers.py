"""Structured representation of everything the wizard asks the user.

This is the single object every sizing/rule module reads from. Keeping it
as one plain dataclass tree (instead of scattering dict lookups everywhere)
makes it easy to unit test rule modules by constructing an `Answers` object
directly, without going through the interactive wizard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SizeTier(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    XLARGE = "xlarge"
    TWOXLARGE = "2xlarge"
    CUSTOM = "custom"


class DatabaseMode(str, Enum):
    EXTERNAL = "external"
    BUNDLED = "bundled"


class FilestoreType(str, Enum):
    LOCAL = "local"
    NFS = "nfs"
    S3 = "s3"
    GCS = "gcs"
    AZURE_BLOB = "azure_blob"
    FULL_DB = "full_db"
    CUSTOM = "custom"


class FilestoreMode(str, Enum):
    """Upload mechanism for cloud-backed filestores (S3/GCS/Azure Blob).

    Confirmed against the actual `jfrog-platform` chart's Artifactory
    subchart -- both its `artifactory.persistence.type` enum (commented in
    values.yaml) and the `binarystore.xml`-generating template logic that
    switches on it -- plus docs.jfrog.com/installation/docs/s3-binary-storage-templates,
    configure-artifactory-to-use-s3-storage, and azure-blob-storage-v2-binary-provider.

    All three clouds follow the same three-way split, though the exact
    `persistence.type` string per mode differs per cloud (see
    `jfvb.rules.filestore._CLOUD_PERSISTENCE_TYPES`) -- most notably Azure,
    where only DIRECT gets the newer V2 binary provider; EVENTUAL/CLUSTER
    fall back to the older V1 provider because the chart's binarystore.xml
    template never wires up a V2 eventual/cluster chain:

    - DIRECT   -- uploads straight to the cloud bucket/container, bypassing
                  local buffering entirely. Works for single-node AND HA
                  with no shared mount required. This is JFrog's documented
                  recommendation in every case except the ones EVENTUAL
                  exists for.
    - EVENTUAL -- buffers locally first, then asynchronously uploads. Only
                  useful when upload bandwidth to the cloud is slow/flaky
                  (e.g. self-hosted, not co-located with the bucket) or you
                  need writes to succeed while the cloud endpoint is down.
                  For HA, this REQUIRES a shared mount across nodes (plain
                  local disk isn't enough), unlike DIRECT/CLUSTER.
    - CLUSTER  -- HA-only: same local buffering as EVENTUAL, but each node
                  syncs independently (eventual-cluster) instead of relying
                  on a shared mount. JFrog explicitly recommends DIRECT over
                  CLUSTER wherever possible; CLUSTER mainly exists for
                  environments already built around that model.
    - ARCHIVE  -- S3 ONLY (`s3-storage-v3-archive`). A newer, thinly
                  documented provider layered on top of s3-storage-v3.
                  jfvb exposes it because it's a real, chart-supported
                  `persistence.type`, but flags it as low-confidence since
                  JFrog's public docs don't fully spell out its behavior
                  beyond the binarystore.xml chain shape -- verify with
                  JFrog Support/docs before relying on it in production.
    """

    DIRECT = "direct"
    EVENTUAL = "eventual"
    CLUSTER = "cluster"
    ARCHIVE = "archive"


class RabbitMQMode(str, Enum):
    BUNDLED_CLASSIC = "bundled_classic"
    BUNDLED_QUORUM = "bundled_quorum"
    EXTERNAL = "external"


class NginxTlsMode(str, Enum):
    """How the bundled nginx terminates HTTPS.

    From chart 11.6.0 the platform chart hard-fails at render time unless one
    of these is chosen explicitly whenever bundled nginx is enabled (the
    chart prints the three options itself). Older charts default to a
    built-in certificate and need no decision -- see
    `jfvb.chart_compat.FEATURES["nginx_tls_required"]`.
    """

    # artifactory.nginx.tlsSecretName -- bring your own cert (production).
    EXISTING_SECRET = "existing_secret"
    # artifactory.nginx.generateSelfSignedCert -- chart generates one (dev/test).
    SELF_SIGNED = "self_signed"
    # artifactory.nginx.https.enabled: false -- HTTP only.
    DISABLED = "disabled"


class KeySource(str, Enum):
    """How the master/join key material is supplied."""

    GENERATE = "generate"
    EXISTING_SECRET = "existing_secret"
    LITERAL = "literal"


PRODUCT_DB_NAMES = ("artifactory", "xray", "distribution", "catalog")


@dataclass
class ProductSelection:
    """The primary product checklist, plus the advanced/less-common modules."""

    artifactory: bool = True  # always on; kept explicit for clarity in output
    xray: bool = True
    distribution: bool = False
    catalog: bool = False
    workers: bool = False
    jas: bool = False
    curation: bool = False
    sbom: bool = False

    # Advanced / less common modules (secondary sub-step in the wizard)
    bridge: bool = False
    apptrust: bool = False
    mission_control: bool = False
    federation: bool = False

    def enabled_db_products(self) -> list[str]:
        """Products that each need their own Postgres database."""
        result = ["artifactory"]
        if self.xray:
            result.append("xray")
        if self.distribution:
            result.append("distribution")
        if self.catalog:
            result.append("catalog")
        return result


@dataclass
class DatabaseAnswers:
    mode: DatabaseMode = DatabaseMode.EXTERNAL
    shared_host: Optional[str] = None
    port: int = 5432
    ssl_mode: str = "require"
    # Per-product host override, e.g. {"xray": "xray-db.internal"}. Empty means
    # "use shared_host for everyone".
    per_product_host: dict[str, str] = field(default_factory=dict)
    # Per-product existing-secret name overrides. Empty means the tool
    # generates a conventional name: "<release>-<product>-db-creds".
    per_product_secret_name: dict[str, str] = field(default_factory=dict)


@dataclass
class FilestoreAnswers:
    type: FilestoreType = FilestoreType.LOCAL

    # Upload mechanism for S3/GCS/Azure Blob only -- see FilestoreMode.
    mode: FilestoreMode = FilestoreMode.DIRECT

    # NFS
    nfs_ip: Optional[str] = None
    nfs_capacity: str = "200Gi"

    # S3
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None
    s3_endpoint: Optional[str] = None
    s3_path: str = "artifactory/filestore"
    s3_use_iam_role: bool = True
    # True for MinIO/Ceph/Wasabi/Dell ECS/NetApp StorageGRID/etc -- anything
    # that isn't real AWS S3. Forces enablePathStyleAccess and rules out
    # useInstanceCredentials (there's no EC2/EKS instance metadata service
    # to fetch IAM credentials from outside AWS). See FilestoreMode/
    # rules/filestore.py for the full rationale.
    s3_compatible_non_aws: bool = False
    s3_use_http: bool = False
    s3_port: Optional[int] = None

    # GCS
    gcs_bucket: Optional[str] = None
    gcs_path: str = "artifactory/filestore"

    # Azure Blob
    azure_account_name: Optional[str] = None
    azure_container: Optional[str] = None
    azure_endpoint: Optional[str] = None

    # Direct Cloud Storage Download -- S3/GCS only; the chart never templates
    # an equivalent field for Azure, so it's a no-op there (see filestore.py).
    direct_cloud_download: bool = False
    signed_url_expiry_seconds: int = 30

    # Local disk cache (cache-fs) in front of a single-node file-system
    # filestore -- off by default (matches the chart default), useful for
    # slow disks/mounted volumes. HA already gets cache-fs automatically
    # via cluster-file-system.
    local_cache_enabled: bool = False

    # Full-DB / Full-DB-Direct (binaries stored as blobs in the database)
    full_db_use_cache: bool = True


@dataclass
class NetworkingAnswers:
    external_ingress: bool = False
    ingress_host: Optional[str] = None
    # Needed whenever there's no cluster-default IngressClass (or more than
    # one ingress controller installed, e.g. ALB + internal nginx side by
    # side) -- without it the Ingress resource may bind to the wrong
    # controller, or none at all. Chart field: artifactory.ingress.className.
    ingress_class_name: Optional[str] = None
    custom_tls: bool = False
    tls_secret_name: Optional[str] = None
    custom_trusted_certificates: bool = False
    # Only consulted for bundled nginx on charts that require an explicit
    # HTTPS decision (>= 11.6.0). None means "not chosen" -- the networking
    # rule then falls back to a bring-your-own-cert secret, which is the
    # only production-safe default of the three.
    nginx_tls_mode: Optional[NginxTlsMode] = None


@dataclass
class SecurityAnswers:
    master_key_source: KeySource = KeySource.GENERATE
    join_key_source: KeySource = KeySource.GENERATE
    master_key_secret_name: str = "master-key"
    join_key_secret_name: str = "join-key"


@dataclass
class Answers:
    # Meta / environment
    chart_version: Optional[str] = None  # None => latest
    namespace: str = "jfrog-platform"
    release_name: str = "jfrog-platform"
    fresh_deployment: bool = True
    airgapped: bool = False
    openshift: bool = False
    custom_image_registry: Optional[str] = None
    image_pull_secret_name: Optional[str] = None

    # Sizing
    size_tier: SizeTier = SizeTier.SMALL
    worker_node_count: int = 3
    dedicated_node_pools: bool = False
    # Only consulted when size_tier == SizeTier.CUSTOM, e.g.
    # {"artifactory_replicas": 2, "xray_replicas": 1}
    custom_sizing: dict = field(default_factory=dict)

    # Products
    products: ProductSelection = field(default_factory=ProductSelection)

    # Data layer
    database: DatabaseAnswers = field(default_factory=DatabaseAnswers)
    filestore: FilestoreAnswers = field(default_factory=FilestoreAnswers)
    rabbitmq_mode: RabbitMQMode = RabbitMQMode.BUNDLED_CLASSIC

    # Networking / security
    networking: NetworkingAnswers = field(default_factory=NetworkingAnswers)
    security: SecurityAnswers = field(default_factory=SecurityAnswers)
