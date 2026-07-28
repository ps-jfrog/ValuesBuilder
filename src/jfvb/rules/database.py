"""External vs bundled PostgreSQL, per enabled product.

External Postgres is treated as the default/expected production path (the
chart's own README explicitly calls the bundled `bitnami/postgresql`
"not suitable for production"). Covers, per enabled product in
{artifactory, xray, distribution, catalog}:

- Connection string format: Artifactory/Distribution/Catalog use JDBC
  (`jdbc:postgresql://...`), Xray uses libpq (`postgres://...`) --
  confirmed from JFrog's Quick Start customvalues.yaml sample.
- Shared `global.database.host` (auto-generates per-product URLs, database
  names must equal the product's chart name) vs explicit per-product
  `<product>.database.url` when hosts/SSL differ per product.
- CONFIRMED via live `helm template` (chart 11.5.3): `global.database.
  initDBCreation: false` is REQUIRED whenever `postgresql.enabled: false`
  (i.e. any external-DB setup), regardless of whether host/port/sslMode
  are even used -- the chart's own `keys-warnings.yaml` template hard
  -fails with "Bundled postgres is disabled but database initialisation
  is enabled" if it's missing. This is NOT the chart default (it defaults
  to initialisation being enabled, for the bundled-postgres happy path),
  so unlike host/port/sslMode it must always be written, even when every
  product has its own explicit `<product>.database.url` and the rest of
  global.database would otherwise be unused.
- Credentials via existing K8s Secret refs, never plaintext in the
  generated file. NOTE: the exact `database.secrets.*` key layout is
  confirmed for the platform-level admin bootstrap secret
  (`global.database.secrets.adminPassword`) but the per-product
  credential-secret field name can vary by chart/subchart version --
  flagged in notes for verification via the `--validate` dry-run.
- SQL to create the database/user/grants, and a connectivity check, are
  generated for RUNBOOK.md so whoever owns the database has copy-pasteable
  commands instead of just a description.
"""

from __future__ import annotations

from jfvb.answers import Answers, DatabaseMode
from jfvb.rules.base import RuleResult, SetupStep

name = "database"


def applies(answers: Answers) -> bool:
    return True


def _connection_string(product: str, host: str, port: int, ssl_mode: str) -> str:
    if product == "xray":
        return f"postgres://{host}:{port}/{product}?sslmode={ssl_mode}"
    return f"jdbc:postgresql://{host}:{port}/{product}?sslmode={ssl_mode}"


def _sql_for_product(product: str) -> str:
    return (
        f"-- {product}\n"
        f"CREATE DATABASE {product};\n"
        f"CREATE USER {product} WITH ENCRYPTED PASSWORD "
        f"'<{product.upper()}_DB_PASSWORD>';\n"
        f"GRANT ALL PRIVILEGES ON DATABASE {product} TO {product};\n"
    )


def apply(answers: Answers) -> RuleResult:
    db = answers.database
    notes: list[str] = []
    steps: list[SetupStep] = []
    values: dict = {}

    if db.mode == DatabaseMode.BUNDLED:
        values["postgresql"] = {"enabled": True}
        notes.append(
            "WARNING: bundled PostgreSQL (bitnami/postgresql) selected. "
            "JFrog's own chart README explicitly recommends an external "
            "managed PostgreSQL (e.g. AWS RDS) for production -- bundled "
            "Postgres is meant for evaluation/dev only. Revisit this "
            "before go-live."
        )
        return RuleResult(values=values, notes=notes, steps=steps)

    products = answers.products.enabled_db_products()
    # global.database.host/port/sslMode is only a *fallback* the chart uses
    # to auto-generate a connection URL for products that don't have their
    # own explicit <product>.database.url. If every enabled product has an
    # explicit per-product host, that fallback is never consulted at all --
    # writing it anyway just adds a confusing, unused placeholder block.
    # initDBCreation is different: it's REQUIRED by the chart whenever
    # bundled postgres is disabled (see module docstring), so it's always
    # written even when host/port/sslMode aren't needed.
    products_using_global_fallback = [p for p in products if p not in db.per_product_host]

    values["postgresql"] = {"enabled": False}
    values["global"] = {"database": {"initDBCreation": False}}

    if products_using_global_fallback:
        values["global"]["database"].update(
            {
                "host": db.shared_host or "<DB_HOST>",
                "port": db.port,
                "sslMode": db.ssl_mode,
            }
        )
        if not db.shared_host:
            notes.append(
                "WARNING: "
                + ", ".join(products_using_global_fallback)
                + " have no per-product host set, so they'd fall back to "
                "global.database.host -- currently left as the placeholder "
                "'<DB_HOST>'. Either fill that in, or give each of those "
                "products its own host so this fallback isn't needed."
            )
    else:
        notes.append(
            "global.database.host/port/sslMode omitted -- every enabled "
            "product (" + ", ".join(products) + ") has its own explicit "
            "<product>.database.url below, so that fallback is never "
            "consulted. global.database.initDBCreation: false is kept "
            "regardless -- the chart requires it whenever bundled "
            "postgres is disabled, host fallback or not."
        )

    sql_blocks: list[str] = []
    for product in products:
        host = db.per_product_host.get(product, db.shared_host)
        secret_name = db.per_product_secret_name.get(
            product, f"{answers.release_name}-{product}-db-creds"
        )

        product_patch = {
            "database": {
                "user": product,
                "waitForDatabase": True,
                "secrets": {
                    "password": {"name": secret_name, "key": "db-password"}
                },
            }
        }
        if product in db.per_product_host:
            product_patch["database"]["url"] = _connection_string(
                product, host or "<DB_HOST>", db.port, db.ssl_mode
            )
        values[product] = product_patch

        sql_blocks.append(_sql_for_product(product))
        steps.append(
            SetupStep(
                phase="database",
                title=f"Create the '{product}' external database + credentials secret",
                commands=[
                    f"# Run on the external PostgreSQL server (psql -h {host or '<DB_HOST>'} -U <ADMIN_USER>):",
                    _sql_for_product(product).strip(),
                    "",
                    f"kubectl -n {answers.namespace} create secret generic {secret_name} \\",
                    f"  --from-literal=db-password='<{product.upper()}_DB_PASSWORD>'",
                ],
                explanation=(
                    f"{product.capitalize()} connects to its own database "
                    f"('{product}') and reads its password from the "
                    f"'{secret_name}' secret referenced in the generated "
                    "customvalues.yaml -- never plaintext in the file "
                    "itself."
                ),
            )
        )

    external_db_note = "External database selected for: " + ", ".join(products) + "."
    if products_using_global_fallback:
        external_db_note += (
            " Database names must exactly match each product's chart name "
            "(artifactory/xray/distribution/catalog) for "
            + ", ".join(products_using_global_fallback)
            + ", since those rely on the chart auto-generating their "
            "connection URL from the shared global.database.host."
        )
    notes.append(external_db_note)
    notes.append(
        "The per-product credential secret key path "
        "('<product>.database.secrets.password.name/key') was set "
        "following the platform chart's documented secret-reference "
        "pattern, but confirm it against the specific subchart version's "
        "values.yaml/README via the --validate step -- some product "
        "chart versions expose this slightly differently."
    )

    # One connectivity check per distinct real host actually in play --
    # avoids a check against a "<DB_HOST>" placeholder for a shared host
    # nobody set when every product already has its own explicit host.
    distinct_hosts = dict.fromkeys(db.per_product_host.values())  # preserves order, dedupes
    if products_using_global_fallback:
        distinct_hosts = {db.shared_host or "<DB_HOST>": None, **distinct_hosts}
    check_commands = [
        f"kubectl -n {answers.namespace} run db-check --rm -it --restart=Never "
        f"--image=postgres:16 -- psql -h {host} -p {db.port} -U <ADMIN_USER> -c '\\l'"
        for host in distinct_hosts
    ]
    steps.insert(
        0,
        SetupStep(
            phase="database",
            title="Verify external PostgreSQL reachability from the cluster",
            commands=check_commands,
            explanation=(
                "Confirms the cluster can reach every external Postgres "
                "host in use before creating per-product databases."
            ),
        ),
    )

    return RuleResult(values=values, notes=notes, steps=steps)
