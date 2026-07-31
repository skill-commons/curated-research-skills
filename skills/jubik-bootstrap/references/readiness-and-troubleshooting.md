# J-UBIK Core Readiness and Troubleshooting

## Immutable inputs

The bootstrap pins:

- J-UBIK release `v0.3`, peeled Git commit
  `58a1c7c23477d2099774523278c0ed65f27afde3`, and tree
  `675b98f7d0ae9575e5f6081428005063cf942401`;
- the bundled `jubik-0.3-py3-none-any.whl`, SHA-256
  `30953812cbc922aa909e4f7ac5d86cd2c64f69747b8af7e97b00a98f15936b68`;
- NIFTy.re distribution `nifty[re]==9.2.0` and the exact CPython 3.12 resolution;
- public PyPI as the only registry index and registry artifact hashes in `uv.lock`.

J-UBIK is not published on PyPI. The bundled wheel solves two separate problems: it gives the
installation an immutable reviewed artifact, and `--no-build` prevents participant hosts from
running unbounded build requirements. The wheel build provenance links that artifact to the
upstream commit; the installed `direct_url.json` anchors the local wheel path while the wrapper
independently verifies that wheel's hash, installed `RECORD`, and package tree. Installers do not
always repeat the archive hash in `direct_url.json`, so its absence is recorded, not treated as a
failure; a conflicting recorded hash does fail. Do not describe the reviewed upstream commit as
Git metadata observed inside an installed wheel.

Primary sources:

- [Pinned J-UBIK source](https://github.com/NIFTy-PPL/J-UBIK/tree/58a1c7c23477d2099774523278c0ed65f27afde3)
- [Package requirements](https://github.com/NIFTy-PPL/J-UBIK/blob/58a1c7c23477d2099774523278c0ed65f27afde3/pyproject.toml)
- [Installation and instrument requirements](https://github.com/NIFTy-PPL/J-UBIK/blob/58a1c7c23477d2099774523278c0ed65f27afde3/README.md)
- [Official SkyModel test](https://github.com/NIFTy-PPL/J-UBIK/blob/58a1c7c23477d2099774523278c0ed65f27afde3/test/test_sky_model.py)
- [SkyModel implementation](https://github.com/NIFTy-PPL/J-UBIK/blob/58a1c7c23477d2099774523278c0ed65f27afde3/jubik/sky_models.py)
- [JAX platform support](https://docs.jax.dev/en/latest/installation.html)
- [JAX cache security guidance](https://docs.jax.dev/en/latest/persistent_compilation_cache.html)

Upstream's `LICENSE` and source SPDX headers say BSD-2-Clause, while its `pyproject.toml` contains
a GPLv3 classifier. The inconsistency is preserved rather than silently resolved. The wheel
contains the upstream license and the skill redistributes the same text beside it. The CRS wrapper
and smoke are original or small API-level adaptations; do not copy substantial upstream code.

## Readiness ladder

| Level | Evidence required | What it does not imply |
|---|---|---|
| Host supported | Doctor accepts host, CPython 3.12, `uv`, executables, assets, and disk | Network or package availability |
| Plan confirmed | User reviewed one content-bound, persisted plan and confirmed its digest | Installation or runtime behavior |
| Environment installed | Frozen no-build sync, offline frozen check, and `uv pip check` pass | J-UBIK runtime behavior |
| `core_ready` | Reviewed-wheel/installed-RECORD integrity and bounded SkyModel smoke pass | Any instrument or scientific model is ready |
| Adapter ready | Separate packages, external software, calibration, data, response, and adapter smoke pass | A particular analysis is scientifically valid |
| Research ready | Domain model, likelihood, priors, data, inference, and posterior checks pass | General validity outside recorded scope |

The bundled report intentionally sets `scientific_research_ready` and all three adapter readiness
fields to false.

## What the core smoke exercises

The smoke builds J-UBIK's central `SkyModel` with one diffuse spatial correlated field, one
spectral-index correlated field, an 8×8 spatial grid, two energy bins, and a fixed NIFTy.re latent
position. It JIT-compiles and evaluates the model twice. A pass requires:

- the exact reviewed J-UBIK 0.3 wheel and NIFTy 9.2.0 lock resolution;
- matching direct-wheel metadata, installed `RECORD` hashes, and package tree;
- CPython 3.12, CPU backend, JAX x64, and disabled bytecode writes. The smoke interpreter is
  invoked with `-I -B`; `-B` remains necessary because `-I` ignores `PYTHONDONTWRITEBYTECODE`;
- output shape `(2, 8, 8)`;
- finite, strictly positive, nonconstant values;
- exact repeatability for the same latent position.

The exact-release curation run on 2026-07-31 used macOS arm64, CPython 3.12.4, JAX/JAXlib 0.11.0,
NumPy 2.5.1, SciPy 1.18.0, Astropy 8.0.1, ducc0 0.41.0, and Matplotlib 3.11.1. The smoke passed;
the complete upstream suite produced 123 passes, four skips, and nine warnings. Two skips required
CIAO and two plotting tests were marked for a different branch. That is core evidence only. The
glibc Linux x86_64 path is admitted only with its binary-wheel and integration evidence; do not
generalize either result to another OS or architecture.

## Secrets and transport configuration

Public GitHub and PyPI need no application secret. The wrapper constructs a small child
environment and rejects `PIP_*`, `UV_*`, Git credential helpers, arbitrary package indexes, and a
keyring. It may pass standard HTTP(S) proxy, no-proxy, and TLS certificate variables to the public
artifact-fetch command because some institutions require them. It does not pass them to the smoke
process, and `--no-build` prevents dependency build hooks from receiving them.

If a proxy credential is needed and no approved variable exists, ask the user to inject one into
the agent process environment outside the skill. Never echo its value, serialize it, paste it into
a URL or command, or persist it in a plan, state, report, or diagnostic. Report only the variable
name. A proxy `407` is not permission to weaken TLS, add an insecure host, switch indexes, or expose
credentials.

## Failure routing

Every failure is bounded and redacted. If an environment was created, preserve the reported exact
partial-environment path. Never improvise a repair in place, run an arbitrary interpreter as a
verification shortcut, or automatically delete the destination.

### Doctor or planning fails

- **No supported Python:** install or select reviewed CPython 3.12; do not let `uv` download an
  interpreter during this workflow.
- **Old or missing `uv`:** obtain `uv` through an approved system or organization package channel,
  then rerun `doctor`. The skill does not execute remote installer scripts.
- **Unsupported platform:** use Apple-silicon macOS 12 or newer or glibc 2.27-or-newer Linux on
  x86_64. Do not fall back to a source build or infer support from another architecture's wheel.
- **Unsafe executable:** choose a current-user/root-owned regular executable that is not
  group/world writable. A plan binds its path, metadata, and SHA-256.
- **Unsafe parent or Git worktree:** choose current-user-owned, non-group/world-writable parents
  outside every Git worktree. Both plan and environment must be new absolute paths.
- **Low disk:** select a private parent with at least 2 GiB free. A verified environment occupied
  about 0.5 GiB before user data.

### Frozen sync fails

- **Proxy authentication / HTTP 407:** have the user update the appropriate proxy environment
  variable outside chat and create a new plan. Never print the value.
- **TLS/certificate:** verify the organization's approved CA injection; never disable certificate
  checks.
- **Network/DNS:** verify access to the planned public PyPI hosts. Do not add a mirror without a
  newly reviewed lock and plan.
- **No compatible wheel:** confirm OS, libc, architecture, and CPython 3.12. Do not remove
  `--no-build`, improvise compiler flags, or loosen dependency ranges.
- **Partial destination:** preserve it for inspection. A retry requires explicit approval to remove
  that exact directory and then a new absent destination and fresh plan.

### Lock or dependency check fails

An offline `uv sync --check --frozen` failure means the installed environment no longer matches the
planned resolution. A `uv pip check` failure means installed dependency metadata is inconsistent.
Preserve the bounded evidence and state. Do not repair one package in place; resolve the cause and
recreate at a new path from a newly confirmed plan.

### Smoke fails

- Missing-JWST or missing-Shapely notices alone are expected optional-adapter notices.
- A wheel URL/hash failure means the installed artifact identity is unacceptable even if imports
  work.
- A `RECORD`, package-tree, or exact-version failure means installed bytes drifted from the
  reviewed artifact or lock.
- Backend/x64/bytecode failure means the controlled process configuration was not established.
- Shape, finite, positivity, variance, or repeatability failure is a core incompatibility.
- First import may populate the wrapper's new private external Matplotlib/XDG/JAX caches. They are
  not reused as scientific evidence and are never placed in the candidate environment.

Retain the exact environment path, bounded warning/failure evidence, plan, state, and any report;
then stop. The wrapper has no `verify` command because executing an arbitrary candidate Python and
accepting its self-report would not establish trusted provenance.

## Instrument boundaries

- **JWST:** requires separately reviewed `jwst`, `gwcs`, `stpsf`, Shapely-related behavior, PSF
  inputs, calibration context, observation data, and an adapter-specific smoke.
- **Chandra:** requires CIAO 4.16 or newer, MARX, environment configuration, calibration products,
  observation files, and an adapter-specific smoke.
- **eROSITA:** requires the supported Docker/eSASS path, CALDB, observation data, response inputs,
  and an adapter-specific smoke.

Do not install these as a troubleshooting experiment. Each expands software, data, network,
licensing, compute, and scientific scope and needs a separate skill and review.
