"""Interactive question flow. Produces a fully populated `Answers` object.

Kept separate from `cli.py` so the flow can be driven programmatically in
tests (by monkeypatching `questionary`) without going through Click.
"""

from __future__ import annotations

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from jfvb.answers import (
    Answers,
    DatabaseMode,
    FilestoreMode,
    FilestoreType,
    KeySource,
    NginxTlsMode,
    RabbitMQMode,
    SizeTier,
)
from jfvb.chart_compat import feature_active
from jfvb.refdata import format_tier_choice_label

console = Console()

_TOTAL_SECTIONS = 9
_QMARK_STYLE = questionary.Style(
    [
        ("qmark", "fg:#00afff bold"),
        ("question", "bold"),
        ("answer", "fg:#00d787 bold"),
        ("pointer", "fg:#00afff bold"),
        ("highlighted", "fg:#00afff bold"),
        ("selected", "fg:#00d787"),
        ("instruction", "fg:#808080"),
    ]
)


def _check_abort(result):
    """questionary's `.ask()` swallows Ctrl-C/Ctrl-D and returns None rather
    than raising, which would otherwise leave the wizard silently limping
    along on default values instead of actually stopping. Treat a None
    result as an explicit abort so it surfaces as a clean cancellation
    instead of confusing partial answers.
    """
    if result is None:
        raise KeyboardInterrupt
    return result


def _ask_text(prompt: str, default: str = "") -> str:
    result = questionary.text(prompt, default=default, style=_QMARK_STYLE).ask()
    return _check_abort(result) or default


def _ask_int(prompt: str, default: int) -> int:
    raw = _check_abort(questionary.text(prompt, default=str(default), style=_QMARK_STYLE).ask())
    try:
        return int(raw)
    except (TypeError, ValueError):
        console.print(f"[yellow]![/yellow] '{raw}' isn't a number, using default ({default}).")
        return default


def _ask_confirm(prompt: str, default: bool = False) -> bool:
    result = questionary.confirm(prompt, default=default, style=_QMARK_STYLE).ask()
    return _check_abort(result)


def _ask_select(prompt: str, choices, default=None):
    result = questionary.select(prompt, choices=choices, default=default, style=_QMARK_STYLE).ask()
    return _check_abort(result)


def _ask_checkbox(prompt: str, choices):
    result = questionary.checkbox(prompt, choices=choices, style=_QMARK_STYLE).ask()
    return _check_abort(result) or []


def _section(index: int, title: str, hint: str = "") -> None:
    console.print()
    heading = f"[bold cyan]Step {index}/{_TOTAL_SECTIONS} · {title}[/bold cyan]"
    console.rule(heading, style="cyan")
    if hint:
        console.print(f"[dim]{hint}[/dim]")


def run_wizard() -> Answers:
    answers = Answers()

    console.print(
        Panel.fit(
            "[bold]JFrog Platform Values Builder[/bold]\n"
            "[dim]Answer a few questions about your deployment to get a "
            "tailored customvalues.yaml, a SUMMARY.md, and a RUNBOOK.md "
            "with copy-pasteable setup commands.[/dim]",
            border_style="cyan",
            padding=(1, 3),
        )
    )

    _section(1, "Basics")
    version_input = _ask_text(
        "Target chart version (leave blank for latest; pins version-gated values paths):"
    )
    answers.chart_version = version_input or None
    answers.release_name = _ask_text("Helm release name:", answers.release_name)
    answers.namespace = _ask_text("Kubernetes namespace:", answers.namespace)
    answers.fresh_deployment = _ask_confirm(
        "Is this a FRESH install (as opposed to upgrading an existing one)?",
        default=True,
    )

    _section(2, "Environment", "Air-gap, OpenShift, and image registry setup.")
    answers.airgapped = _ask_confirm(
        "Are you deploying into an air-gapped environment?", default=False
    )
    if answers.airgapped:
        registry = _ask_text(
            "Internal image registry to mirror into (e.g. registry.internal.corp):"
        )
        answers.custom_image_registry = registry or None
        if _ask_confirm("Use an imagePullSecret for this registry?", default=True):
            answers.image_pull_secret_name = _ask_text(
                "Pull secret name:", "jfrog-registry-creds"
            )
    else:
        if _ask_confirm("Pulling images from a custom/mirrored registry?", default=False):
            answers.custom_image_registry = _ask_text("Registry URL:") or None

    answers.openshift = _ask_confirm("Is this targeting OpenShift?", default=False)

    _section(3, "Sizing", "Based on JFrog's reference architecture.")
    console.print(
        "[dim]T-shirt sizes map to cluster-wide Artifactory load: "
        "peak Requests Per Minute (RPM) and peak concurrent HTTP "
        "connections across the whole cluster (not per node).[/dim]"
    )
    tier_choice = _ask_select(
        "Deployment size tier:",
        choices=[
            questionary.Choice(format_tier_choice_label("small"), value="small"),
            questionary.Choice(format_tier_choice_label("medium"), value="medium"),
            questionary.Choice(format_tier_choice_label("large"), value="large"),
            questionary.Choice(format_tier_choice_label("xlarge"), value="xlarge"),
            questionary.Choice(format_tier_choice_label("2xlarge"), value="2xlarge"),
            questionary.Choice(
                "custom  (set replica counts yourself)", value="custom"
            ),
        ],
        default="small",
    )
    answers.size_tier = SizeTier(tier_choice)
    if answers.size_tier == SizeTier.CUSTOM:
        answers.custom_sizing["artifactory_replicas"] = _ask_int(
            "Artifactory replica count:", 1
        )
        answers.custom_sizing["xray_replicas"] = _ask_int("Xray replica count:", 1)

    answers.worker_node_count = _ask_int("How many worker nodes do you have?", 3)
    answers.dedicated_node_pools = _ask_confirm(
        "Will you use dedicated node pools per component "
        "(Artifactory/Nginx/Xray/RabbitMQ/Postgres)?",
        default=False,
    )

    _section(4, "Products")
    console.print("[dim]Artifactory is always enabled (base product).[/dim]")
    chosen = _ask_checkbox(
        "Select the other products you need:",
        choices=[
            questionary.Choice("Xray", value="xray", checked=True),
            questionary.Choice("Distribution", value="distribution"),
            questionary.Choice("Catalog", value="catalog"),
            questionary.Choice("Workers", value="workers"),
            questionary.Choice("JAS", value="jas"),
            questionary.Choice("Curation", value="curation"),
        ],
    )
    p = answers.products
    p.xray = "xray" in chosen
    p.distribution = "distribution" in chosen
    p.catalog = "catalog" in chosen
    p.workers = "workers" in chosen
    p.jas = "jas" in chosen
    p.curation = "curation" in chosen

    if p.xray:
        p.sbom = _ask_confirm("Enable the SBOM service (xray.sbom.enabled)?", default=False)

    if _ask_confirm(
        "Configure any advanced/less-common modules (Bridge, AppTrust, "
        "Mission Control, Federation)?",
        default=False,
    ):
        advanced = _ask_checkbox(
            "Select advanced modules:",
            choices=[
                questionary.Choice("Bridge", value="bridge"),
                questionary.Choice("AppTrust", value="apptrust"),
                questionary.Choice("Mission Control", value="mission_control"),
                questionary.Choice("Platform Federation", value="federation"),
            ],
        )
        p.bridge = "bridge" in advanced
        p.apptrust = "apptrust" in advanced
        p.mission_control = "mission_control" in advanced
        p.federation = "federation" in advanced

    _section(
        5,
        "Database",
        "Leave a host blank to emit a <DB_HOST> placeholder (passwords always "
        "go in a Secret via RUNBOOK.md, never into customvalues.yaml).",
    )
    db_choice = _ask_select(
        "PostgreSQL:",
        choices=[
            questionary.Choice("External (recommended for production)", value="external"),
            questionary.Choice("Bundled (eval/dev only)", value="bundled"),
        ],
    )
    answers.database.mode = DatabaseMode(db_choice)
    if answers.database.mode == DatabaseMode.EXTERNAL:
        if _ask_confirm(
            "Use one shared external Postgres host for every product?", default=True
        ):
            answers.database.shared_host = (
                _ask_text("External Postgres host (blank for <DB_HOST>):") or None
            )
            answers.database.per_product_host = {}
        else:
            answers.database.shared_host = None
            for product in answers.products.enabled_db_products():
                host = _ask_text(
                    f"Postgres host for '{product}' (blank for <DB_HOST>):"
                )
                if host:
                    answers.database.per_product_host[product] = host
        answers.database.port = _ask_int("Postgres port:", 5432)
        answers.database.ssl_mode = _ask_select(
            "SSL mode:", choices=["require", "disable", "verify-full"], default="require"
        )

    _section(
        6,
        "Filestore",
        "Artifactory only -- other products don't expose this. "
        "Leave bucket/IP/account blank to emit placeholders.",
    )
    fs_choice = _ask_select(
        "Filestore backend:",
        choices=[
            questionary.Choice("Local PVC (file-system, default)", value="local"),
            questionary.Choice("NFS", value="nfs"),
            questionary.Choice("S3 (AWS or S3-compatible, e.g. MinIO/Ceph)", value="s3"),
            questionary.Choice("Google Cloud Storage", value="gcs"),
            questionary.Choice("Azure Blob Storage", value="azure_blob"),
            questionary.Choice("Store binaries in the database (Full-DB)", value="full_db"),
            questionary.Choice("Other / custom binarystore.xml (e.g. sharding)", value="custom"),
        ],
    )
    answers.filestore.type = FilestoreType(fs_choice)
    fs = answers.filestore
    if fs.type == FilestoreType.LOCAL:
        fs.local_cache_enabled = _ask_confirm(
            "Is the underlying disk slow (e.g. a mounted network volume)? "
            "Add a local LRU read cache in front of it?",
            default=False,
        )
    elif fs.type == FilestoreType.NFS:
        fs.nfs_ip = _ask_text("NFS server IP (blank for <NFS_SERVER_IP>):") or None
        fs.nfs_capacity = _ask_text("NFS capacity:", fs.nfs_capacity)
    elif fs.type in (FilestoreType.S3, FilestoreType.GCS, FilestoreType.AZURE_BLOB):
        cloud_name = {
            FilestoreType.S3: "S3",
            FilestoreType.GCS: "GCS",
            FilestoreType.AZURE_BLOB: "Azure Blob",
        }[fs.type]
        mode_choices = [
            questionary.Choice(
                "Direct (recommended -- single-node & HA, no shared mount)",
                value="direct",
            ),
            questionary.Choice(
                "Eventual (buffers locally first; needed for slow/flaky "
                "uplinks; HA needs a shared mount)",
                value="eventual",
            ),
            questionary.Choice(
                "Cluster (HA without a shared mount, via per-node "
                "buffering -- JFrog recommends Direct over this where possible)",
                value="cluster",
            ),
        ]
        if fs.type == FilestoreType.S3:
            mode_choices.append(
                questionary.Choice(
                    "Archive (s3-storage-v3-archive -- newer/advanced, low "
                    "documentation confidence, verify before production use)",
                    value="archive",
                )
            )
        mode_choice = _ask_select(
            f"{cloud_name} upload mode:", choices=mode_choices, default="direct"
        )
        fs.mode = FilestoreMode(mode_choice)

        if fs.type == FilestoreType.S3:
            fs.s3_bucket = _ask_text("S3 bucket name (blank for <S3_BUCKET_NAME>):") or None
            fs.s3_compatible_non_aws = _ask_confirm(
                "Is this an S3-compatible provider that ISN'T AWS (MinIO, "
                "Ceph, Wasabi, Dell ECS, NetApp StorageGRID, etc.)?",
                default=False,
            )
            if fs.s3_compatible_non_aws:
                fs.s3_endpoint = (
                    _ask_text(
                        "S3-compatible endpoint (blank for <S3_ENDPOINT_URL>, "
                        "e.g. https://minio.example.com):"
                    )
                    or None
                )
                fs.s3_use_http = _ask_confirm(
                    "Does the endpoint use plain HTTP (no TLS)?", default=False
                )
                port_str = _ask_text(
                    "Custom port (leave blank to use the endpoint's default, "
                    "e.g. 9000 for MinIO):"
                )
                fs.s3_port = int(port_str) if port_str.strip().isdigit() else None
                fs.s3_region = (
                    _ask_text(
                        "Region (many non-AWS providers ignore the value but "
                        "still require *something* for request signing -- "
                        "'us-east-1' is a safe default if unsure):",
                        "us-east-1",
                    )
                    or None
                )
                fs.s3_use_iam_role = False  # no EC2/EKS instance metadata outside AWS
            else:
                fs.s3_region = _ask_text("AWS region (blank for <AWS_REGION>):") or None
                fs.s3_use_iam_role = _ask_confirm(
                    "Use IAM role-based access (recommended) instead of static keys?",
                    default=True,
                )
        elif fs.type == FilestoreType.GCS:
            fs.gcs_bucket = _ask_text("GCS bucket name (blank for <GCS_BUCKET_NAME>):") or None
        elif fs.type == FilestoreType.AZURE_BLOB:
            fs.azure_account_name = (
                _ask_text("Azure storage account name (blank for <AZURE_ACCOUNT_NAME>):")
                or None
            )
            fs.azure_container = (
                _ask_text("Azure container name (blank for <AZURE_CONTAINER_NAME>):")
                or None
            )
            if _ask_confirm(
                "Does this account use a non-default endpoint (Azure "
                "Government/China cloud, Azure Stack, or another "
                "sovereign/private cloud) instead of blob.core.windows.net?",
                default=False,
            ):
                fs.azure_endpoint = (
                    _ask_text(
                        "Azure Blob endpoint (blank for <AZURE_BLOB_ENDPOINT>, e.g. "
                        "https://<account>.blob.core.usgovcloudapi.net):"
                    )
                    or "<AZURE_BLOB_ENDPOINT>"
                )
            else:
                fs.azure_endpoint = None

        if fs.type in (FilestoreType.S3, FilestoreType.GCS):
            fs.direct_cloud_download = _ask_confirm(
                "Enable Direct Cloud Storage Download (redirect large-binary "
                "downloads, e.g. Docker layers, straight to "
                f"{cloud_name} instead of proxying through Artifactory)?",
                default=False,
            )
            if fs.direct_cloud_download:
                fs.signed_url_expiry_seconds = _ask_int(
                    "Signed URL expiry (seconds):", fs.signed_url_expiry_seconds
                )
    elif fs.type == FilestoreType.FULL_DB:
        fs.full_db_use_cache = _ask_confirm(
            "Add a local filesystem cache in front of the database reads "
            "(Full-DB, recommended) instead of reading every binary "
            "straight from the database (Full-DB-Direct)?",
            default=True,
        )

    _section(7, "Networking / TLS")
    answers.networking.external_ingress = _ask_confirm(
        "Use an external ingress controller instead of bundled nginx?",
        default=False,
    )
    if answers.networking.external_ingress:
        answers.networking.ingress_host = _ask_text("Ingress hostname:") or None
        answers.networking.ingress_class_name = (
            _ask_text(
                "Ingress class name (leave blank to rely on the cluster's "
                "default IngressClass, e.g. 'nginx', 'alb', 'traefik'):"
            )
            or None
        )
    answers.networking.custom_tls = _ask_confirm(
        "Configure a custom TLS certificate?", default=False
    )
    if answers.networking.custom_tls:
        answers.networking.tls_secret_name = _ask_text(
            "TLS secret name:", f"{answers.release_name}-tls"
        )
    elif not answers.networking.external_ingress and feature_active(
        "nginx_tls_required", answers.chart_version
    ):
        # Chart >= 11.6.0 refuses to render bundled nginx without this.
        console.print(
            "[dim]This chart version requires an explicit HTTPS decision for "
            "the bundled nginx -- without one it fails to render.[/dim]"
        )
        tls_mode = _ask_select(
            "Bundled nginx HTTPS:",
            choices=[
                questionary.Choice(
                    "Use my own certificate from a Kubernetes secret (production)",
                    value="existing_secret",
                ),
                questionary.Choice(
                    "Let the chart generate a self-signed certificate (dev/test only)",
                    value="self_signed",
                ),
                questionary.Choice(
                    "Disable HTTPS -- HTTP only (TLS terminated upstream)",
                    value="disabled",
                ),
            ],
            default="existing_secret",
        )
        answers.networking.nginx_tls_mode = NginxTlsMode(tls_mode)
        if answers.networking.nginx_tls_mode == NginxTlsMode.EXISTING_SECRET:
            answers.networking.tls_secret_name = _ask_text(
                "TLS secret name:", f"{answers.release_name}-tls"
            )
    answers.networking.custom_trusted_certificates = _ask_confirm(
        "Add custom trusted CA certificates (internal CA for LDAP/SSO/proxy)?",
        default=False,
    )

    if answers.products.xray:
        _section(8, "RabbitMQ (Xray)")
        rmq_choice = _ask_select(
            "RabbitMQ mode:",
            choices=[
                questionary.Choice("Bundled, classic queues (default)", value="bundled_classic"),
                questionary.Choice("Bundled, quorum queues (HA)", value="bundled_quorum"),
                questionary.Choice("External RabbitMQ", value="external"),
            ],
        )
        answers.rabbitmq_mode = RabbitMQMode(rmq_choice)

    _section(9, "Security keys")
    key_choice = _ask_select(
        "Master/join key handling:",
        choices=[
            questionary.Choice("Kubernetes secret (recommended)", value="secret"),
            questionary.Choice("Literal value (eval only, plaintext)", value="literal"),
        ],
    )
    if key_choice == "secret":
        source = KeySource.GENERATE if answers.fresh_deployment else KeySource.EXISTING_SECRET
        answers.security.master_key_source = source
        answers.security.join_key_source = source
        answers.security.master_key_secret_name = _ask_text(
            "Master key secret name:", answers.security.master_key_secret_name
        )
        answers.security.join_key_secret_name = _ask_text(
            "Join key secret name:", answers.security.join_key_secret_name
        )
    else:
        answers.security.master_key_source = KeySource.LITERAL
        answers.security.join_key_source = KeySource.LITERAL

    _print_review(answers)
    return answers


def _yes_no(value: bool) -> str:
    return "[green]Yes[/green]" if value else "[dim]No[/dim]"


def _print_review(answers: Answers) -> None:
    console.print()
    console.rule("[bold cyan]Review[/bold cyan]", style="cyan")

    table = Table(box=None, show_header=False, padding=(0, 2, 0, 0))
    table.add_column("Setting", style="dim")
    table.add_column("Value", style="bold")
    table.add_row("Chart version", answers.chart_version or "latest")
    table.add_row("Release / namespace", f"{answers.release_name} / {answers.namespace}")
    table.add_row("Fresh install", _yes_no(answers.fresh_deployment))
    table.add_row("Air-gapped", _yes_no(answers.airgapped))
    table.add_row("OpenShift", _yes_no(answers.openshift))
    table.add_row("Size tier", answers.size_tier.value)
    table.add_row("Worker nodes", str(answers.worker_node_count))
    table.add_row("Database", answers.database.mode.value)
    filestore_value = answers.filestore.type.value
    if answers.filestore.type in (
        FilestoreType.S3,
        FilestoreType.GCS,
        FilestoreType.AZURE_BLOB,
    ):
        filestore_value += f" ({answers.filestore.mode.value})"
        if answers.filestore.type == FilestoreType.S3 and answers.filestore.s3_compatible_non_aws:
            filestore_value += ", S3-compatible non-AWS"
    elif answers.filestore.type == FilestoreType.FULL_DB:
        filestore_value += " (cached)" if answers.filestore.full_db_use_cache else " (direct)"
    table.add_row("Filestore", filestore_value)
    console.print(table)
    console.print()
