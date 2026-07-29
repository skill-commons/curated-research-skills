# Curated Research Skills

Canonical Hermes skills consolidated and maintained by [Skill Commons](https://github.com/skill-commons). This repository is a content source; the federated discovery index lives in [`skill-commons/skill-commons`](https://github.com/skill-commons/skill-commons).

Each directory under [`skills/`](skills/) is a complete installable unit. Supporting `references/`, `scripts/`, `templates/`, and assets stay with its `SKILL.md`.

## Use with Hermes

Subscribe to the complete tap:

```bash
hermes skills tap add skill-commons/curated-research-skills
hermes skills search astronomy
hermes skills install skill-commons/curated-research-skills/tap-pyvo-adql-access
```

Or install one skill directly without subscribing:

```bash
hermes skills install skill-commons/curated-research-skills/skills/tap-pyvo-adql-access
```

## Skills

### General

Literature discovery, monitoring, and scientific calculation.

| Skill | Version | Description |
|---|---:|---|
| [`arxiv`](skills/arxiv/) | `2.0.0` | Search, read, cite, and monitor papers through arXiv. |
| [`calculator`](skills/calculator/) | `1.0.1` | Perform exact symbolic and numerical calculations. |
| [`rss-feed-monitor`](skills/rss-feed-monitor/) | `2.0.0` | Track public RSS or Atom feeds in an isolated local database, scan for new articles, and manage read state with explicit mutation safeguards. |

### LaTeX

Research-manuscript authoring, revision, compilation, and submission packaging.

| Skill | Version | Description |
|---|---:|---|
| [`latex-journal-submission-package`](skills/latex-journal-submission-package/) | `2.0.0` | Build and verify portable LaTeX journal submissions. |
| [`latex-research-paper`](skills/latex-research-paper/) | `1.0.0` | Draft, revise, and verify LaTeX research manuscripts. |

### Astronomy

Astronomy catalog access, survey workflows, and scientific visualization.

| Skill | Version | Description |
|---|---:|---|
| [`astro-catalog-plotting-cache`](skills/astro-catalog-plotting-cache/) | `2.0.0` | Create cached, publication-ready astronomy catalog plots. |
| [`gaia-dr3-tap-query`](skills/gaia-dr3-tap-query/) | `3.0.0` | Query Gaia DR3 through AIP TAP and Daiquiri services. |
| [`rave-dr6`](skills/rave-dr6/) | `2.0.0` | Query, cache, and crossmatch public RAVE DR6 data. |
| [`starhorse-access`](skills/starhorse-access/) | `2.0.2` | Access StarHorse SHboost and SH21 catalog products. |
| [`tap-pyvo-adql-access`](skills/tap-pyvo-adql-access/) | `1.0.0` | Query astronomy TAP services with PyVO and ADQL. |

### Data

Reproducible access to large research datasets and object storage.

| Skill | Version | Description |
|---|---:|---|
| [`data-aip-de-s3`](skills/data-aip-de-s3/) | `2.0.0` | Access and cache research data from S3-compatible stores. |

### Visualization

General-purpose publication and report graphics.

| Skill | Version | Description |
|---|---:|---|
| [`large-tabular-visualization`](skills/large-tabular-visualization/) | `2.0.0` | Build interpretable interactive or static visualizations from tabular data that is too dense or too large for ordinary point plotting. |
| [`seaborn-paper-plots`](skills/seaborn-paper-plots/) | `1.0.1` | Create reproducible publication plots with Seaborn. |

### Scientific Computing

Simulation, validation, and reproducible scientific software workflows.

| Skill | Version | Description |
|---|---:|---|
| [`dt4acc-host-smoke-test`](skills/dt4acc-host-smoke-test/) | `2.0.0` | Run a bounded, simulation-only host smoke test for local dt4acc, dt4acc-lib, and lat2db checkouts without facility services or containers. |

### Software Development

Documentation-grounded software development and library workflows.

| Skill | Version | Description |
|---|---:|---|
| [`python-library-docs-first`](skills/python-library-docs-first/) | `2.0.0` | Verify version-sensitive third-party Python APIs against authoritative documentation before writing, reviewing, fixing, or explaining code. |

Attribution and consolidation history are recorded in [`PROVENANCE.md`](PROVENANCE.md).

The inventory is generated from Hermes `SKILL.md` metadata. After changing a skill, run:

```bash
uv run python scripts/validate_skills.py
```
