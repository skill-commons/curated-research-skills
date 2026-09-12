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

Literature discovery, evidence synthesis, monitoring, and scientific calculation.

| Skill | Version | Description |
|---|---:|---|
| [`arxiv`](skills/arxiv/) | `2.0.0` | Search, read, cite, and monitor papers through arXiv. |
| [`calculator`](skills/calculator/) | `1.0.2` | Perform exact symbolic and numerical calculations. |
| [`research-paper-evidence-workflow`](skills/research-paper-evidence-workflow/) | `1.0.1` | Map research-paper claims to evidence; audit draft scope. Map claims to supplied evidence, synthesize completed results, construct an evidence-backed outline, and audit a draft for traceability, numeric fidelity, scope, and overclaiming. Use when notes, tables, figures, result files, or a manuscript need a claim-evidence matrix, results narrative, outline, or evidence-focused review. Do not use to design or run experiments, retrieve citations, format or compile LaTeX, manage projects, submit or promote papers, or perform external writes. |
| [`rss-feed-monitor`](skills/rss-feed-monitor/) | `2.0.1` | Monitor RSS and Atom feeds in an isolated local database. Scan for new articles and manage read state with explicit mutation safeguards. |

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
| [`astro-catalog-plotting-cache`](skills/astro-catalog-plotting-cache/) | `2.0.1` | Create cached, publication-ready astronomy catalog plots. |
| [`cluster-cmd-isochrone-fit`](skills/cluster-cmd-isochrone-fit/) | `1.0.1` | Fit cluster CMD ages with stellar-evolution isochrones. |
| [`coseecat`](skills/coseecat/) | `1.0.0` | Query solar electron events and plots from CoSEE-Cat. |
| [`gaia-dr3-tap-query`](skills/gaia-dr3-tap-query/) | `3.1.1` | Query Gaia DR3 catalogs and spectra at AIP. |
| [`muse-science-products`](skills/muse-science-products/) | `1.0.0` | Plot published MUSE maps and H II region measurements. |
| [`pepsi-spectra`](skills/pepsi-spectra/) | `1.0.1` | Retrieve and plot public PEPSI stellar spectra. |
| [`rave-dr6`](skills/rave-dr6/) | `2.1.1` | Query and plot public RAVE DR6 spectra and catalogs. |
| [`starhorse-access`](skills/starhorse-access/) | `2.0.3` | Access StarHorse SHboost and SH21 catalog products. |
| [`tap-pyvo-adql-access`](skills/tap-pyvo-adql-access/) | `1.0.1` | Query astronomy TAP services with PyVO and ADQL. |

### Data

Reproducible access to large research datasets and object storage.

| Skill | Version | Description |
|---|---:|---|
| [`data-aip-de-s3`](skills/data-aip-de-s3/) | `2.0.1` | Access and cache research data from S3-compatible stores. |
| [`drphub-cards`](skills/drphub-cards/) | `2.1.3` | Manage and publish DRP Hub research products via REST. Supports full CRUD, clone, maturity, publish, audit, lineage, human-review, bookmarks, likes, sharing, and SSE event streaming against the production API at drp-term.kube.aip.de/api/v1/. |
| [`drphub-products`](skills/drphub-products/) | `1.0.1` | Inspect Digital Research Product Hub products and health. Report API capabilities, product summaries, maturity, and lineage through a bounded read-only REST client. Use when a user needs to diagnose a DRP Hub endpoint, search or inspect products, verify immutable Git and image identities, or review maturity and clone relationships without creating, changing, publishing, sharing, reviewing, or deleting remote data. |

### Visualization

General-purpose publication and report graphics.

| Skill | Version | Description |
|---|---:|---|
| [`large-tabular-visualization`](skills/large-tabular-visualization/) | `2.0.2` | Visualize large tabular data with hvPlot and Datashader. Build interpretable interactive or static plots for datasets too dense or too large for ordinary point plotting. |
| [`seaborn-paper-plots`](skills/seaborn-paper-plots/) | `1.0.2` | Create reproducible publication plots with Seaborn. |

### Scientific Computing

Simulation, validation, and reproducible scientific software workflows.

| Skill | Version | Description |
|---|---:|---|
| [`dt4acc-host-smoke-test`](skills/dt4acc-host-smoke-test/) | `2.0.1` | Smoke-test local dt4acc checkouts without any containers. Run a bounded, simulation-only host check for dt4acc, dt4acc-lib, and lat2db without facility services. |
| [`dt4acc-operations`](skills/dt4acc-operations/) | `1.0.1` | Operate local dt4acc simulations from digest-pinned SIFs. Preflight, plan, start, inspect, and stop a simulation packaged as an already-built Apptainer SIF. Use when an operator needs a bounded simulation IOC lifecycle with exact source/build provenance, a clean child environment, no host or facility network, content-bound start/stop confirmation, and exact owned-process cleanup. This CRS skill never builds or pulls images, connects to facility services, accesses live PVs, imports MongoDB data, or performs PV writes. |
| [`jubik-bootstrap`](skills/jubik-bootstrap/) | `1.0.1` | Bootstrap a pinned J-UBIK CPU core and verify readiness. Preflight, plan, create, and diagnose its environment with an isolated lock-backed, wheel-only workflow and a genuine synthetic SkyModel smoke test. Use when a researcher is blocked on J-UBIK installation, JAX/NIFTy compatibility, environment configuration, artifact provenance, or core readiness. This skill proves only the CPU core; it never claims JWST, Chandra, or eROSITA adapter readiness, downloads calibration or observation data, invokes instrument software, or runs research inference. |
| [`nifty-re-variational-inference`](skills/nifty-re-variational-inference/) | `1.0.1` | Run bounded Bayesian variational inference with NIFTy.re. Build and validate workflows with the JAX-based NIFTy.re API. Use when a researcher needs to formulate priors, a response and likelihood, prove a NIFTy.re installation against an analytic posterior, run a small CPU pilot, inspect optimizer and sampling evidence, or prepare a reproducible inference handoff. Do not use this skill to bootstrap J-UBIK, configure telescope instruments, certify an arbitrary scientific model from one successful run, or launch unbounded production inference. |
| [`reana-operator`](skills/reana-operator/) | `1.0.1` | Inspect remote REANA workflows using read-only commands. Use a fixed allowlist against an authenticated service to report connectivity, cluster information, workflow inventory, status, redacted logs, workspace files, and disk usage. Use when a user wants to diagnose or review remote REANA state after a local workflow has been authored. This CRS skill never uploads, creates, starts, stops, deletes, downloads, shares, or otherwise mutates a workflow. |
| [`reana-workflow-authoring`](skills/reana-workflow-authoring/) | `1.0.1` | Author and validate local REANA Serial workflow projects. Scaffold and edit provider-neutral projects while checking reana.yaml structure, declared inputs and outputs, runtime-image reproducibility, path containment, symlinks, and accidental secrets. Use when a user asks to create or review a local REANA workflow definition before operational handoff. This skill never authenticates, contacts a REANA server or registry, uploads, submits, starts, monitors, downloads, or mutates a remote workflow. |

### Software Development

Documentation-grounded software development and library workflows.

| Skill | Version | Description |
|---|---:|---|
| [`python-library-docs-first`](skills/python-library-docs-first/) | `2.0.1` | Verify Python APIs against version-matched documentation. Check version-sensitive third-party APIs against authoritative documentation before writing, reviewing, fixing, or explaining code. |

Descriptions may retain detailed trigger and boundary prose, but their opening sentence should stand alone within Hermes' 57-character selection surface. The validator mirrors Hermes' 60-character prompt truncation and emits a non-blocking warning for longer leads.

Attribution and consolidation history are recorded in [`PROVENANCE.md`](PROVENANCE.md).

The inventory is generated from Hermes `SKILL.md` metadata. After changing a skill, run:

```bash
uv run python scripts/validate_skills.py
```
