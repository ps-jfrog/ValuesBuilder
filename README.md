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
size tier, worker-node count and node pools, product checklist
(Artifactory/Xray/Distribution/Catalog/Workers/JAS/Curation/SBOM, plus an
advanced sub-list for Bridge/AppTrust/Mission Control/Federation),
database, filestore, networking/TLS, RabbitMQ mode, and security keys.
Then writes:

- `output/customvalues.yaml` -- single merged file, with a `--- set by:
  <rules> ---` comment above every top-level key so you can trace where
  each setting came from
- `output/SUMMARY.md` -- chosen tier, enabled products, warnings (e.g.
  "only 2 worker nodes but 3 replicas requested"), and the rationale
  behind every section
- `output/RUNBOOK.md` -- ordered `kubectl`/`openssl`/`psql` commands
  scoped to exactly what you answered (steps for scenarios you didn't
  select are omitted entirely)

By default it also live-pulls the target chart version and runs `helm
template` against the generated file as a dry-run sanity check before
declaring success (`--no-validate` to skip, `--offline` to skip both the
chart-baseline sizing fetch and validation entirely, e.g. if `helm`/network
aren't available).

### `jfvb images` -- air-gapped image list

```bash
jfvb images --version 11.5.9 -f output/customvalues.yaml \
  --target-registry registry.internal.corp -o images.txt
```

Renders the chart with `helm template`, extracts every `image:` reference
(including init containers and bundled Postgres/RabbitMQ), dedupes, and
writes the list. With `--target-registry`, also writes
`mirror-images.sh` (ready-to-run `skopeo copy` commands). Passing your
generated `customvalues.yaml` via `-f` narrows the list to only the
products/modules you actually enabled. JAS images are entitlement-gated
and may be under-represented -- `jfvb` prints a warning to double check
these against the target Xray version.

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
  answers.py        -- Answers dataclass tree (wizard output)
  wizard.py          -- questionary-based interactive prompts
  cli.py             -- `jfvb build` / `jfvb images` entry points
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
