# jfvb -- JFrog Platform Values Builder

`jfvb` interviews you about your deployment environment and generates a
tailored `customvalues.yaml` for the `jfrog-platform` Helm chart, plus a
`SUMMARY.md` (rationale/warnings) and a `RUNBOOK.md` of ordered,
copy-pasteable setup commands (keys, database, certificates, filestore,
install, verification). It's just as useful whether you're a JFrog SE
scoping a deployment or a platform team setting one up for yourselves.

It exists to catch the scenarios that don't fit the chart's plain
defaults: air-gapped environments, undersized/oversized infrastructure,
which products to enable, external vs. bundled databases, which filestore
backend, dedicated node pools, OpenShift, and more -- and to reconcile
sizing against [JFrog's reference architecture](https://jfrog.com/reference-architecture/self-managed/deployment/sizing/aws/),
which is stricter than the chart's own bundled sizing profiles for
PostgreSQL resources.

## Why this exists

The chart ships sensible defaults and six bundled sizing overlays, but a
lot of real deployments diverge from them in predictable ways. Instead of
everyone hand-editing `values.yaml` from scratch and re-discovering the
same gotchas (air-gap needs a `system.yaml` override two levels deep in
the values tree; the bundled Postgres sizing profile under-sizes CPU/RAM
by half versus the reference architecture; filestore settings only apply
to Artifactory; etc.), `jfvb` encodes that knowledge once, as small
independent rule modules, and asks the right questions to apply it
correctly for your specific deployment.

## Install

Requires Python 3.10+ and a local `helm` 3.x binary on `PATH` (used to
fetch chart data live -- nothing is vendored, so results always reflect
the actual chart version you're targeting).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

### `jfvb build` -- interactive wizard

```bash
jfvb build -o ./output
```

Walks through: chart version, environment (air-gap/OpenShift/registry),
size tier (with cluster-wide RPM / concurrent-connection criteria shown),
worker-node count and node pools, product checklist
(Artifactory/Xray/Distribution/Catalog/Workers/JAS/Curation/SBOM, plus an
advanced sub-list for Bridge/AppTrust/Mission Control/Federation),
database (including host URLs -- leave blank for `<DB_HOST>` placeholders),
filestore (including bucket/IP/account -- blank for placeholders),
networking/TLS, RabbitMQ mode, and security keys. Then writes:

- `output/customvalues.yaml` -- single merged file, with a `--- set by:
  <rules> ---` comment above every top-level key so you can trace where
  each setting came from
- `output/SUMMARY.md` -- chosen tier + load criteria, enabled products,
  warnings (e.g. "only 2 worker nodes but 3 replicas requested"), and the
  rationale behind every section
- `output/RUNBOOK.md` -- ordered `kubectl`/`openssl`/`psql` commands
  scoped to exactly what you answered (steps for scenarios you didn't
  select are omitted entirely)

By default it also live-pulls the target chart version and runs `helm
template` against the generated file as a dry-run sanity check before
declaring success (`--no-validate` to skip, `--offline` to skip both the
chart-baseline sizing fetch and validation entirely, e.g. if `helm`/network
aren't available).

### `jfvb build --answers` -- non-interactive

```bash
jfvb example-answers -o answers.yaml   # starter file you can edit
jfvb build --answers answers.yaml -o ./output --offline
```

The answers file is a **1:1 map of every wizard field** -- anything the
interactive interview asks can be set here instead. Omit a field to keep
its default; unknown fields and invalid enum values are rejected with a
clear error. Hosts, buckets, and secrets can be left null/omitted so the
output uses placeholders (same as leaving a wizard prompt blank), or
filled in when you already know them.

Set `chart_version` in the answers file (or at the wizard prompt) so
version-gated use cases pick the right values.yaml paths for that chart
release. Leaving it blank resolves to latest (when helm/network are
available) and uses the newest path for each feature.

### Version-gated use cases

Some chart keys move between releases. `jfvb` keeps a small registry in
`src/jfvb/chart_compat.py` (`FEATURES`) and rules branch with
`feature_active("…", answers.chart_version)`. Today:

| Feature id | Chart versions | Values path |
| --- | --- | --- |
| `airgap_extra_system_yaml` | &lt; 11.5.13 | `artifactory.artifactory.extraSystemYaml.jfconnect.airgap.enabled` |
| `airgap_jfconnect_values` | ≥ 11.5.13 (and latest) | `artifactory.jfconnect.airgap.enabled` |
| `nginx_tls_required` | ≥ 11.6.0 (and latest) | `artifactory.nginx.tlsSecretName` / `.generateSelfSignedCert` / `.https.enabled` |

`SUMMARY.md` lists which features were active for the targeted version.
To add another use case: register a `ChartFeature` with `since`/`until`,
then branch in the relevant rule module.

#### Bundled nginx HTTPS on chart ≥ 11.6.0

From 11.6.0 the chart **refuses to render** when bundled nginx is enabled
without an explicit HTTPS decision, so a default build against latest
would fail validation and never write `images.txt`. The wizard now asks,
and `networking.nginx_tls_mode` covers it non-interactively:

| Mode | Values written | When to use |
| --- | --- | --- |
| `existing_secret` (default) | `artifactory.nginx.tlsSecretName` | Production -- bring your own cert; `RUNBOOK.md` gets the `kubectl create secret tls` step |
| `self_signed` | `artifactory.nginx.generateSelfSignedCert: true` | Dev/test only -- clients reject it unless they trust it |
| `disabled` | `artifactory.nginx.https.enabled: false` | TLS terminated upstream (mesh / external LB); plaintext otherwise |

Leaving `nginx_tls_mode` null falls back to `existing_secret` (the only
production-safe option) and records a warning in `SUMMARY.md`. Setting
`custom_tls: true` already satisfies the requirement. An external ingress
is unaffected -- nginx is disabled there, so the guard never fires.

### T-shirt size criteria

Tiers map to **cluster-wide** Artifactory load from [JFrog's reference
architecture](https://jfrog.com/reference-architecture/self-managed/deployment/sizing/):

| Tier | Peak RPM (cluster-wide) | Peak concurrent connections (cluster-wide) | Typical use |
| --- | --- | --- | --- |
| small | ≤ 6,000 | ≤ 100 | Dev/test (not production) |
| medium | ≤ 50,000 | ≤ 500 | Small production |
| large | ≤ 100,000 | ≤ 1,200 | Medium enterprise |
| xlarge | ≤ 200,000 | ≤ 3,000 | Large enterprise |
| 2xlarge | ≤ 500,000 | ≤ 6,000 | Large enterprise |

RPM = total HTTP requests/minute across all Artifactory nodes.
Concurrent connections = active HTTP threads serving requests across the
whole cluster at peak. These select the template; they are distinct from
Postgres `max_connections` (a DB-side setting also sized per tier).

### `jfvb images` -- air-gapped image list

```bash
jfvb images --version 11.5.9 -f output/customvalues.yaml \
  --target-registry registry.internal.corp -o images.txt
```

Renders the chart with `helm template`, extracts every `image:` reference
(including init containers and bundled Postgres/RabbitMQ), dedupes, and
writes the list. The top of `images.txt` also includes copy-pasteable
instructions to `helm pull` the chart `.tgz` on a connected machine and
install from that package inside the air-gap. With `--target-registry`,
also writes `mirror-images.sh` (ready-to-run `skopeo copy` commands).
Passing your generated `customvalues.yaml` via `-f` narrows the list to
only the products/modules you actually enabled.

Two chart behaviors are handled specially, both confirmed against chart
11.5.13:

- **Source registries are preserved.** An air-gapped `customvalues.yaml`
  sets `global.imageRegistry` to your internal mirror, which would
  otherwise make every rendered image point at the mirror itself (and
  make `mirror-images.sh` copy that registry onto itself). Image
  discovery strips that override so you get the images to *pull*. Use
  `--keep-registry-override` for the post-mirror names instead.
- **JAS images are rejoined.** They render as a separate `registry:` +
  tag-less `image:` pair in a ConfigMap; a naive `image:` grep yields
  `jfrog/xray-jas-…`, which resolves to Docker Hub. These are recombined
  and flagged as untagged, since Xray resolves the tag at runtime.

JAS images are also entitlement-gated and may be under-represented --
`jfvb` prints a warning to double check them against the target Xray
version.

## Design

```
Answers (dataclass)
  |
  +--> sizing.py -- blends reference-architecture tables with the
  |                 chart's own live-pulled sizing/platform-*.yaml,
  |                 reference architecture wins on conflicts
  |
  +--> rules/*.py -- one small module per deployment scenario:
  |      airgap, modules (products), affinity, database, filestore,
  |      networking, ha (RabbitMQ), security, openshift
  |      each returns (values_patch, notes, runbook_steps)
  |
  +--> builder.py -- runs sizing + every applicable rule, deep-merges
         all patches, tracks which rule touched which top-level key
                |
                +--> render.py   -> customvalues.yaml + SUMMARY.md
                +--> runbook.py  -> RUNBOOK.md
```

Adding support for a new deployment scenario means adding one file to
`src/jfvb/rules/` with an `applies(answers)` and `apply(answers)`
function, and registering it in `src/jfvb/rules/__init__.py` -- never
touching a monolithic conditional block.

### Sizing reconciliation

JFrog's AWS reference architecture and the chart's bundled
`sizing/platform-*.yaml` profiles agree on replica counts, nginx
replicas, and Postgres `max_connections` -- but the chart's bundled
PostgreSQL CPU/memory requests are roughly **half** of what the reference
architecture recommends for the equivalent managed RDS instance, at every
tier. `jfvb` treats the reference architecture as the source of truth for
infrastructure sizing (replicas, resource requests where specified, DB
sizing) and takes JVM/Tomcat/HPA tuning from the chart's own profile where
the reference architecture doesn't cover it. Sizing math is cloud-agnostic
(vCPU/memory/replicas/connections); AWS instance-type names are surfaced
only as informational annotations in `SUMMARY.md`.

### A note on values.yaml key nesting

Several JFrog Helm values live one level deeper than you'd expect on the
`jfrog-platform` chart, because it mounts the *standalone* Artifactory/Xray
charts as subcharts under its own `artifactory:`/`xray:` keys -- and those
standalone charts, in turn, nest some of their own settings under an inner
`artifactory:`/`xray:` "app section" (e.g. `replicaCount`,
`podAntiAffinity`, `nodeSelector`, `persistence`, `extraSystemYaml`), while
others stay at a single level (`nginx`, `ingress`, `postgresql`, `mc`,
`apptrust`). Getting this wrong doesn't raise a Helm error -- it silently
drops the setting. Every such key path in this tool was verified with a
live `helm template` render against chart 11.5.9 rather than assumed from
docs alone; `jfvb build`'s `--validate` step re-confirms this for whatever
version you actually target.

## Tests

```bash
pytest
```

Unit tests cover the merge engine, sizing reconciliation (including a
regression test for the Postgres under-sizing discrepancy above), and
every rule module, using constructed `Answers` objects -- no `helm`/network
required. The behaviors above that depend on exact chart-version key
paths were additionally verified against the live chart during
development (see rule module docstrings for what was confirmed and how).

## Project layout

```
src/jfvb/
  answers.py        -- Answers dataclass tree (wizard / answers-file input)
  answers_io.py     -- YAML/JSON load + example answers for --answers
  chart_compat.py   -- chart version compare + FEATURES registry
  wizard.py          -- questionary-based interactive prompts
  cli.py             -- `jfvb build` / `jfvb example-answers` / `jfvb images`
  builder.py         -- orchestrates sizing + rules -> merged values
  sizing.py          -- reference-architecture <-> chart sizing reconciliation
  refdata.py         -- loader for data/reference_architecture.yaml
  helm_client.py     -- subprocess wrapper around the local helm binary
  images.py          -- image extraction + mirror script generation
  merge.py           -- deep-merge helpers
  render.py          -- customvalues.yaml + SUMMARY.md rendering
  runbook.py         -- RUNBOOK.md rendering
  rules/             -- one module per deployment scenario (see above)
  data/
    reference_architecture.yaml
tests/
```
