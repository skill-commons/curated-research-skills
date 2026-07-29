# Reproducible dt4acc Smoke Environment

## Initial CRS Verification Snapshot

The first CRS validation targets these public commits:

| Checkout | Repository | Commit |
|---|---|---|
| `dt4acc` | `https://github.com/dt4acc/dt4acc` | `d8c1774d3c414ec23025b95a8e4de8b451a33422` |
| `dt4acc-lib` | `https://github.com/dt4acc/dt4acc-lib` | `28576ed6548d96ba48574086f8ce6f11f1e9e921` |
| `lat2db` | `https://github.com/hz-b/lat2db` | `7b130297576fb875ab371867606ae5f5760dbf06` |

All three default branches are named `dev/main`. A branch name is mutable; a result is
identified by the full commit IDs, dirty-state flags, Python version, and dependency
snapshot recorded at execution.

CRS exercised this snapshot on macOS arm64 with CPython 3.12.4. The exact resolver output
is recorded in `references/verified-environment-2026-07-29.txt`; it is evidence, not a
cross-platform lock or an instruction to upgrade another project.

## Prepare Deliberately

Clone or update repositories only when the user has authorized that source change. Check
out the approved commits and confirm clean working trees before installing them. Use a
dedicated Python 3.12 environment rather than a reused global interpreter.

A new environment needs the three local projects and the test dependencies declared by
their own metadata. Install from the explicit local paths, inspect the resolver output,
run `pip check`, and save the exact resolved package set next to the experiment record.
Do not automatically upgrade pip, build tools, or packages as part of the smoke test.

`dt4acc-lib` directly supplies the PyAT simulation dependency. `dt4acc` needs its BESSY II
extra or the local `lat2db` installation. EPICS packages such as `softioc` and `p4p` are
not required by this simulation-only path.

## Validate the Interpreter

Before the smoke test:

```bash
uv pip check --python <venv-python>
<venv-python> -m pytest -q <checkout-root>/dt4acc-lib/tests
uv pip freeze --python <venv-python>
```

If the selected environment deliberately includes pip, equivalent `python -m pip`
commands are acceptable. Review the freeze output for local editable paths and unexpected
packages. Confirm that `import uuid` resolves to Python's standard library despite
`lat2db` currently declaring a separate `uuid` distribution. The runner then checks that
imports of `dt4acc`, `dt4acc_lib`, and `lat2db` resolve inside the three supplied
checkouts; a globally installed copy is a hard failure.

## Offline Boundary

The retained workflow loads the lattice JSON and BESSY II translation tables from the
`dt4acc` package and instantiates `SimulatorBackend`. It does not need MongoDB, TANGO,
EPICS, Apptainer, Docker, or a mutable image.

At the verified commits, imports can print a default `MONGODB_URL` warning and a notice
that matplotlib plotting is unavailable. The retained code does not instantiate a MongoDB
client or request a plot. Treat process-level network denial—not the presence or absence
of a warning—as the high-assurance boundary.

For a high-assurance run:

1. unset service URLs and facility credentials;
2. deny outbound sockets at the process or sandbox boundary;
3. inspect that the only new file is the explicitly selected output JSON;
4. compare Git status before and after execution;
5. repeat once and compare normalized measurements within the stated tolerances.

## Updating the Snapshot

When promoting newer commits:

1. review upstream changes and licenses;
2. create a fresh isolated environment and dependency snapshot;
3. run upstream tests and the smoke test twice;
4. verify restore behavior, strict JSON, import origins, and no repository mutations;
5. update this table and the examples in `SKILL.md` in the same reviewed commit.
