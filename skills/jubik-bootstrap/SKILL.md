---
name: jubik-bootstrap
description: Bootstrap a pinned J-UBIK CPU core and verify readiness. Preflight, plan, create, and diagnose its environment with an isolated lock-backed, wheel-only workflow and a genuine synthetic SkyModel smoke test. Use when a researcher is blocked on J-UBIK installation, JAX/NIFTy compatibility, environment configuration, artifact provenance, or core readiness. This skill proves only the CPU core; it never claims JWST, Chandra, or eROSITA adapter readiness, downloads calibration or observation data, invokes instrument software, or runs research inference.
version: 1.0.1
author: AIP AstroAgent team and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: scientific-computing
    tags:
      - jubik
      - nifty
      - jax
      - bayesian-imaging
      - installation
      - diagnostics
      - reproducibility
---

# J-UBIK Bootstrap

## Core-Only Boundary

Use this skill to get a researcher from an unproven host to one evidence-backed J-UBIK core
environment. Read
[`references/readiness-and-troubleshooting.md`](references/readiness-and-troubleshooting.md)
before installation or diagnosis.

`core_ready` means that the reviewed J-UBIK 0.3 wheel and exact locked dependencies are installed
consistently and that a small central `jubik.SkyModel` built and evaluated twice on CPU. It does
not mean that an instrument adapter, calibration, observation, response, inference model, or
scientific result is ready.

- Do not install to system Python, an active environment, an existing path, or any Git worktree.
- Do not use `--user`, `--system`, `--break-system-packages`, source builds, mutable Git refs, or
  dependency overrides.
- Do not install instrument extras or invoke CIAO, MARX, Docker, eSASS, JWST pipelines, or
  calibration downloads.
- Do not delete a failed or partial environment automatically. Preserve its exact path and remove
  it only after the user explicitly approves that exact path.
- Do not reuse a Matplotlib, XDG, or JAX cache writable by another user.
- Do not interpret optional JWST or Shapely import notices as a core failure.

Route subsequent inference formulation and validation to `nifty-re-variational-inference`.
Instrument onboarding belongs in separately reviewed future skills.

## Supported Bootstrap Surface

The controlled surface is CPython 3.12, `uv` 0.10 or newer, at least 2 GiB free, and either:

- Apple-silicon macOS 12 or newer; or
- glibc 2.27-or-newer Linux on x86_64.

Linux aarch64, macOS Intel, musl/Alpine, Windows, GPUs, source builds, other Python versions, and
other architectures are outside this first bootstrap. Upstream projects may support more; this
skill reports only the wheel-backed surface that it checks.

The package sources are public. J-UBIK needs no application secret, and this skill stores none.
The wrapper rejects package-index and Git credential configuration, pins public PyPI as its only
index, disables keyrings and interactive Git prompts, and forwards only standard proxy and TLS
certificate environment variables during the public artifact fetch. It reports their names, never
their values. If an organization needs proxy authentication and no suitable variable is present,
ask the user to inject it into the agent process environment. Never put a secret in chat, a command
argument, the skill body, the plan, the lock, or a report.

## Reviewed Support Assets

The wrapper is
[`scripts/jubik_bootstrap.py`](scripts/jubik_bootstrap.py), and its fresh-process scientific probe
is [`scripts/jubik_core_smoke.py`](scripts/jubik_core_smoke.py). The controlled environment is
defined by all of these reviewed assets:

- [`assets/jubik-core-environment/pyproject.toml`](assets/jubik-core-environment/pyproject.toml);
- [`assets/jubik-core-environment/uv.lock`](assets/jubik-core-environment/uv.lock);
- [`assets/jubik-core-environment/wheels/jubik-0.3-py3-none-any.whl`](assets/jubik-core-environment/wheels/jubik-0.3-py3-none-any.whl);
- [`assets/jubik-core-environment/JUBIK-WHEEL-BUILD-PROVENANCE.md`](assets/jubik-core-environment/JUBIK-WHEEL-BUILD-PROVENANCE.md);
- [`assets/jubik-core-environment/JUBIK-WHEEL-LICENSE.txt`](assets/jubik-core-environment/JUBIK-WHEEL-LICENSE.txt).

The lock is CPython-3.12-only. The bundled pure-Python wheel was reproducibly built from J-UBIK
release `v0.3`, commit `58a1c7c23477d2099774523278c0ed65f27afde3`, and is identified by
SHA-256 `30953812cbc922aa909e4f7ac5d86cd2c64f69747b8af7e97b00a98f15936b68`. The runtime
install uses `--no-build`; it does not execute a package build hook.

## Workflow

Run the wrapper with Python 3.11 or newer as the controller. The selected environment interpreter
must still be CPython 3.12. J-UBIK's process-global x64 setting is contained in the separate smoke
process.

### 1. Doctor the host without network or mutation

```bash
python scripts/jubik_bootstrap.py doctor
```

`doctor` selects CPython 3.12 when available, probes Python and `uv` with fixed commands, checks
the precise host surface and every bundled asset, and reports proxy or certificate variable names
without values. It neither contacts the network nor creates an environment. If auto-selection is
wrong, pass an explicit reviewed `--python` or `--uv` executable path.

Stop when `ready_to_plan` is false. Use the structured check and reason fields; do not bypass an
unsupported version, platform, missing asset, or unsafe executable.

### 2. Persist and review the complete installation plan

Choose a new absolute environment path whose parent already exists, is current-user-owned,
private, writable, and outside every Git worktree. Choose a new absolute plan path with the same
trust properties:

```bash
python scripts/jubik_bootstrap.py plan \
  --environment <new-absolute-environment> \
  --output <new-absolute-plan.json>
```

Optional `--python` and `--uv` select explicit reviewed executables. The saved plan binds the
canonical destination; host; Python and `uv` paths, versions, ownership, modes, and executable
digests; exact source identity; public network hosts; the full sync/check/smoke commands; minimum
disk space; secret boundary; and SHA-256 values for the wrapper and every support asset listed
above. It states that instrument adapters and scientific inference are out of scope.

Show the complete saved JSON plan to the user and obtain fresh explicit confirmation of that
exact `plan_sha256`. Planning is not installation and does not prove public network access.

### 3. Create only from the confirmed plan file

```bash
python scripts/jubik_bootstrap.py create \
  --plan <absolute-plan.json> \
  --confirm <plan_sha256>
```

`create` reads the bounded plan without following a symlink, validates its canonical digest,
rechecks the destination and host, and refuses if the plan, executable identity, code, or any asset
changed. It copies the verified project, lock, wheel, license, provenance note, and smoke script
into a new private staging directory. The bytes executed are therefore the bytes the user
confirmed.

The planned sync is frozen, wheel-only, public-PyPI-only, noninteractive, and configuration-free.
It uses `--no-build`, `--no-config`, `--no-python-downloads`, and no shared cache. Proxy or TLS
variables are visible only to the downloader for this step; wheel installation executes no build
code. After the fetch and install, the wrapper repeats a frozen `uv sync --check` without network,
runs `uv pip check`, and then runs the staged 8×8/two-bin CPU SkyModel smoke with the environment
Python flags `-I -B` and new private caches outside the environment. `-I` isolates imports and
`-B` is required explicitly because isolated mode ignores `PYTHONDONTWRITEBYTECODE`.

On success the environment contains:

- `crs-jubik-core-report.json`, with wheel, installed `RECORD`, package, device, model, warnings,
  and readiness evidence;
- `crs-jubik-bootstrap-state.json`, with the complete confirmed plan, command outcomes, hashes,
  report digest, and all adapter states set false.

On failure, preserve the exact partial environment and show only the structured category, path,
and bounded redacted diagnostic. A proxy-authentication failure has its own category. Do not retry
in that path or broaden the install; this skill deliberately has no independent `verify` or
deletion command. Resolve the cause, obtain approval for any cleanup, and start with a new absent
destination and a new plan.

### 4. Hand off readiness precisely

Require `core_ready: true`, every core check true, successful offline lock and dependency checks,
wheel/installed-`RECORD` integrity evidence, the complete saved plan, and false adapter readiness.
Provide the environment path, plan/report digests, complete report, support boundary, and bounded
warning record. Never shorten the result to “J-UBIK is ready.” Say: “J-UBIK core is ready on this
recorded CPU environment; instrument and scientific readiness are unverified.”

## Verification Checklist

- The destination was absent before the confirmed create operation and is outside a Git worktree.
- Plan, support assets, Python, `uv`, public index, and supported host still match the saved state.
- Frozen wheel-only sync, offline frozen check, and `uv pip check` passed.
- Installed package versions match the lock; J-UBIK files and `RECORD` match the reviewed wheel.
- The smoke is CPU-only, x64, finite, positive, nonconstant, correctly shaped, and exactly
  repeatable at its fixed latent position.
- JWST, Chandra, and eROSITA readiness and `scientific_research_ready` remain false.
- No credential value, existing environment, system Python, source repository, instrument
  software, calibration, observation, or untrusted cache was modified.
