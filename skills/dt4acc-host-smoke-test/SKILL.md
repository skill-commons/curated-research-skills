---
name: dt4acc-host-smoke-test
description: Run a bounded, simulation-only host smoke test for local dt4acc, dt4acc-lib, and lat2db checkouts without facility services or containers.
version: 2.0.0
author: AIP AstroAgent team and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: scientific-computing
    tags:
      - dt4acc
      - digital-twin
      - accelerator-physics
      - pyat
      - simulation
      - smoke-test
---

# dt4acc Host Smoke Test

## When to Use

Use this skill to separate core dt4acc simulation health from deployment problems. It
checks local `dt4acc`, `dt4acc-lib`, and `lat2db` checkouts; builds the packaged BESSY II
lattice; reads tune, track, and Twiss data; translates one bounded quadrupole perturbation;
and verifies restoration.

This is not a facility integration test. It deliberately excludes EPICS, TANGO, MongoDB,
containers, control-system endpoints, and physical accelerator operations.

## Prerequisites

Read [the reproducible environment guide](references/reproducible-environment.md) before
running the test. The workflow requires:

- three explicit Git checkouts under one root;
- a Python environment in which those exact local checkouts are installed;
- an output JSON path outside all three source repositories;
- known commit IDs and clean working trees unless dirty execution is explicitly justified.

The package does not clone repositories, install dependencies, start services, or select
mutable runtime images. The
[initial resolver snapshot](references/verified-environment-2026-07-29.txt) records the
environment used for the first CRS execution.

## Procedure

### 1. Inspect inputs

Expected layout:

```text
<checkout-root>/
├── dt4acc/
├── dt4acc-lib/
└── lat2db/
```

Record each `HEAD`, review `git status`, inspect the environment lock, and confirm that
the packaged BESSY II input and translation tables are present. Do not proceed from an
unknown or contaminated environment and call the result reproducible.

### 2. Run upstream library tests

Use the selected environment's interpreter and the exact local checkout:

```bash
<venv-python> -m pytest -q <checkout-root>/dt4acc-lib/tests
```

Treat failures as library regressions or environment incompatibilities before testing the
higher-level translation path.

### 3. Run the simulation-only smoke test

The wrapper resolves its Python runner relative to the skill, so it works from any current
directory:

```bash
DT4ACC_SMOKE_PYTHON=<venv-python> \
  scripts/run_dt4acc_host_smoke_test.sh \
  <checkout-root> \
  <output-directory>/dt4acc-smoke.json \
  --expected-dt4acc-commit d8c1774d3c414ec23025b95a8e4de8b451a33422 \
  --expected-dt4acc-lib-commit 28576ed6548d96ba48574086f8ce6f11f1e9e921 \
  --expected-lat2db-commit 7b130297576fb875ab371867606ae5f5760dbf06
```

Those commits are the initial CRS verification snapshot, not an instruction to replace a
project's approved pins. Pass the commits actually under review. Use the underlying
[Python runner](scripts/dt4acc_host_smoke_test.py) directly when shell is unavailable.

The default perturbation is `+0.01 A` on the uniquely translated `set_current` command
for `Q1M2D1R`. Use `--device-id` if translation yields more than one candidate. Any
non-default perturbation must remain bounded by `--max-current-delta`.

### 4. Inspect the strict JSON result

A pass requires all of the following:

- imported modules originate inside the supplied checkouts;
- lattice, track, and Twiss results exceed the configured minimum sizes;
- command translation produces at least one inverse lattice target;
- the bounded perturbation changes lattice strength and at least one tune;
- every inverse target, the named quadrupole, and both tunes return within tolerance;
- all reported floating-point values are finite.

The JSON records Python and distribution versions, checkout commits and dirty state,
parameters, measurements, checks, and final status. It omits absolute checkout paths so
the report can be shared more safely.

## Interpret Results

- **Upstream tests fail:** investigate the library or dependency environment first.
- **Import origin fails:** the interpreter loaded a global or stale installation instead
  of one of the supplied checkouts.
- **Lattice construction fails:** inspect `lat2db` compatibility and the packaged BESSY II
  JSON.
- **No translated device:** inspect the liaison and translation tables.
- **No tune change:** inspect command conversion, simulator updates, and optics
  recalculation.
- **Restore fails:** treat the run as failed even if the perturbation behaved as expected.
- **This passes but deployment fails:** investigate the separate EPICS, TANGO, MongoDB, or
  container layer without claiming that this test validated it.

## Safety

- Run only against the in-memory `SimulatorBackend`.
- Never adapt this first-line test to a facility backend or live control endpoint.
- Keep service credentials and facility environment variables unset.
- Run with outbound network denied when the execution environment supports it.
- The runner refuses output inside the checkout root and refuses overwrite by default.
- Do not use `--allow-dirty`, `--force`, or relaxed tolerances without documenting why.

## Included Files

- [`scripts/run_dt4acc_host_smoke_test.sh`](scripts/run_dt4acc_host_smoke_test.sh) — thin
  wrapper around an already prepared Python environment.
- [`scripts/dt4acc_host_smoke_test.py`](scripts/dt4acc_host_smoke_test.py) — validated
  simulation, translation, restoration, and result reporting.
