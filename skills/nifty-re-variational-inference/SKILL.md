---
name: nifty-re-variational-inference
description: Run bounded Bayesian variational inference with NIFTy.re. Build and validate workflows with the JAX-based NIFTy.re API. Use when a researcher needs to formulate priors, a response and likelihood, prove a NIFTy.re installation against an analytic posterior, run a small CPU pilot, inspect optimizer and sampling evidence, or prepare a reproducible inference handoff. Do not use this skill to bootstrap J-UBIK, configure telescope instruments, certify an arbitrary scientific model from one successful run, or launch unbounded production inference.
version: 1.0.1
author: AIP AstroAgent team and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: scientific-computing
    tags:
      - nifty
      - nifty-re
      - jax
      - bayesian-inference
      - variational-inference
      - reproducibility
      - validation
---

# NIFTy.re Variational Inference

## Scientific Boundary

Use this skill for a bounded local inference workflow with explicit evidence. The supported
reference surface is `nifty[re]==9.2.0`; do not silently translate another NIFTy release to
this API. Read [`references/nifty-re-validation.md`](references/nifty-re-validation.md)
before adapting the bundled benchmark.

The reviewed certification path is CPU-only and Python 3.12-only on a runtime reporting macOS
12-or-newer arm64 or glibc Linux x86_64 (glibc 2.27 or newer). Windows, older macOS, macOS Intel,
Linux arm64, musl/Alpine, and other dependency resolutions are outside this certification. The
report verifies this observed platform ABI; it cannot prove whether execution was native,
virtualized, or emulated. Record that separately when it matters for performance or provenance.

- Do not claim that a successful import, optimizer exit, or analytic benchmark validates a
  research model.
- Do not infer a noise model, selection function, response, units, mask, or prior from a
  filename or array shape.
- Do not start a large domain, GPU run, parameter sweep, repeated optimization loop, or
  production job without a separate resource plan and user authorization.
- Do not overwrite data, results, environments, caches, or reports. Keep generated evidence
  outside source repositories in a new path.
- Do not install into system Python, use `--user`, use `--break-system-packages`, or alter an
  existing environment.
- Do not treat reduced chi-squared near one as sufficient evidence of model validity.

Route J-UBIK installation and core diagnostics to `jubik-bootstrap`. Treat telescope response,
calibration, and instrument data as separate downstream capabilities.

## Workflow

### 1. State the inference problem before coding

Record the observed data and units, latent quantities, priors and hyperpriors, forward response,
likelihood, masks and selection effects, expected output, and scientific validation criteria.
Identify every assumption that comes from the researcher rather than the data. Stop if the
response or likelihood is underspecified.

Estimate domain size, latent degrees of freedom, sample count, iterations, device, memory, and
expected runtime. Begin with a synthetic or downsampled CPU pilot. A pilot that is structurally
too small to exercise the real response must be labeled a software smoke test, not a scientific
validation.

### 2. Establish the frozen certification environment

The certification report is valid only for the bundled Python 3.12 package environment. Read the
exact direct requirements in
[`assets/nifty-re-certification-environment/pyproject.toml`](assets/nifty-re-certification-environment/pyproject.toml)
and use the artifact hashes and full transitive resolution in
[`assets/nifty-re-certification-environment/uv.lock`](assets/nifty-re-certification-environment/uv.lock).
Do not regenerate the lock during certification.

Select an already installed CPython 3.12 interpreter; do not let `uv` download an unreviewed
interpreter. Choose a new absolute environment path outside every Git worktree. Create a new
mode-0700 runtime directory outside those worktrees for `HOME` and temporary files. For the usual
credential-free public download, run `uv` by absolute path in an allowlisted child environment:

```bash
env -i HOME=<new-private-runtime-directory> TMPDIR=<new-private-runtime-directory> \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  UV_PROJECT_ENVIRONMENT=<new-absolute-environment> \
  <absolute-uv> sync --frozen --no-build --no-cache --no-config --no-progress \
  --default-index https://pypi.org/simple \
  --index-strategy first-index --keyring-provider disabled \
  --no-python-downloads --link-mode copy \
  --project <absolute-skill-root>/assets/nifty-re-certification-environment \
  --python <absolute-python-3.12>
```

Keep the environment path quoted in a real shell command when it contains whitespace. Record the
absolute interpreter path and `uv --version`. An existing environment may be inspected, but do not
alter it and do not call it certified merely because its top-level NIFTy version matches.

This fixed command excludes ambient `UV_*`, index, credential, JAX, and XLA configuration;
disables keyring lookup and Python downloads; refuses source builds; uses the first public index
only; avoids a persistent package cache; and copies rather than links installed artifacts. After
sync, run `uv sync --check --offline` for the same project/environment and `uv pip check --python
<new-absolute-environment>/bin/python --no-config` in the same allowlisted environment.

Package installation contacts public PyPI and normally needs no credential. If an organization
requires an authenticated proxy or custom CA, ask the user to inject only the standard proxy or
certificate variables into the install subprocess's environment mapping. Do not add secret-bearing
shell assignments to the `env` command: they can be exposed through process arguments. Never
request the values in chat, place them in a command, forward them to the benchmark, persist them in
the skill, or print them. Record only the variable names that were used.

The lock is the reviewed installation recipe; the benchmark independently records the resolved
versions, interpreter, OS and architecture, devices, import origins, installer metadata, and
installed NIFTy `RECORD` verification. The report distinguishes those observations from the
reviewed release commit and PyPI wheel hash. Neither an embedded digest nor a passing benchmark
authenticates who ran it.

### 3. Prove the inference machinery against an analytic answer

Choose an unused absolute report path and a new private runtime directory outside every Git
worktree. Use the runtime directory for `HOME`, `TMPDIR`, and the new JAX cache. Run the original
bundled
[`scripts/nifty_re_linear_gaussian.py`](scripts/nifty_re_linear_gaussian.py) in a fresh process:

```bash
env -i HOME=<new-private-runtime-directory> TMPDIR=<new-private-runtime-directory> \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  <new-absolute-environment>/bin/python -I -B \
  <absolute-skill-root>/scripts/nifty_re_linear_gaussian.py \
  --output <new-absolute-report.json> \
  --cache-dir <new-private-runtime-directory>/jax-cache
```

`-I` disables user-site and path injection, while `-B` prevents the benchmark from writing
bytecode into the frozen environment. The script requires mode-0700, current-user-owned `HOME`
and `TMPDIR` paths outside every Git worktree, the fixed locale above, and an allowlisted `env -i`
runtime. It rejects unrelated ambient variable names. Recognized proxy/certificate and
`JAX_*`/`XLA_*` controls, if explicitly supplied, are removed before scientific imports and only
their names are recorded. The script then fixes the backend to CPU, enables x64, uses seed 42 and
exactly 128 requested antithetic sample pairs, performs one linear-resampling VI iteration, and
compares the inferred two-parameter posterior with the exact Gaussian posterior.
Those certification parameters are deliberately not CLI options. It creates the cache with mode
0700, writes one new mode-0600 JSON report, captures warning-or-higher messages with fixed count
and length limits, and never writes NIFTy checkpoints.

Require every `checks` value and `validation_passed` to be true. Preserve the report and its
`canonical_payload_sha256`, plus the separately printed `report_file_sha256`. Verify the embedded
payload digest without importing NIFTy:

```bash
<new-absolute-environment>/bin/python -I -B \
  <absolute-skill-root>/scripts/nifty_re_linear_gaussian.py \
  --verify-report <absolute-report.json>
```

The canonical digest covers the parsed report with its own digest field removed; it is not the
byte-for-byte file digest. On failure, diagnose the exact failed gate before writing research code.
Do not change the fixed seed, sample count, or threshold merely to obtain a pass. A digest detects
accidental change only when it is compared with a separately retained value; it is not a signature.

### 4. Implement the smallest faithful research model

Use documented NIFTy.re models and compose their initialization domains explicitly. Keep these
layers separate and testable:

1. latent prior model;
2. deterministic response from latent space to data space;
3. likelihood with explicit covariance or inverse covariance;
4. initialization and fixed PRNG-key derivation;
5. variational configuration;
6. summaries and posterior predictive checks.

Check shapes, dtypes, finite values, units, masks, response adjoint or consistency properties
where applicable, and a prior draw before optimization. Use `likelihood.init(key)` wrapped in
`jft.Vector` for the documented `optimize_kl` pattern. Keep the first run CPU-only and bounded.

For a nonlinear astronomical imaging model, turn the researcher's specification into an original
small model rather than editing the certification benchmark in place. The minimal composition is:

```python
sky_prior = ...  # researcher-specified standardized NIFTy.re prior


def response(signal):
    """Apply the researcher-specified, unit-aware response in data space."""
    ...


imaging_model = jft.Model(
    lambda position: response(sky_prior(position)),
    init=sky_prior.init,
)
likelihood = ...  # explicit Gaussian, Poisson, or other justified data model
likelihood = likelihood.amend(imaging_model)
initial = jft.Vector(likelihood.init(initial_key))
```

Before inference, separately test the sky prior and every response stage. For an imaging response,
that normally includes coordinate orientation, PSF/kernel normalization, flux conservation,
exposure and effective-area units, background treatment, mask ordering, boundary conditions, and
positivity where the likelihood requires it. Exercise `jax.jit`, a JVP and VJP, compare selected
derivatives with finite differences, and test the adjoint identity for every explicitly linear
stage. A small model that omits the real convolution, projection, mask, or exposure structure is a
software smoke test only.

Do not copy `sample_mode="linear_resample"` from the certification without a written choice.
Linear sampling is the local-metric MGVI approximation; NIFTy.re's
`sample_mode="nonlinear_resample"` adds a nonlinear residual update and requires explicit, bounded
`nonlinearly_update_kwargs`. Neither is universally superior. Compare justified linear and
nonlinear pilots on simulated truth when model curvature matters, retain every sample status, and
explain the selected approximation and stopping criteria. Never interpret an iteration-limit
warning as convergence.

### 5. Validate the pilot scientifically

Software completion is only the first gate. Inspect and retain:

- optimizer and per-sample status, iteration count, convergence history, and any warnings;
- normalized residuals in data space, including spatial or spectral structure;
- posterior predictive behavior rather than only a posterior mean;
- sensitivity to initialization, sample count, minimizer tolerances, and defensible priors;
- prior-to-posterior movement, degeneracies, identifiability, and boundary effects;
- comparison with a simulation truth, analytic limit, or independent implementation where one
  exists;
- domain-specific calibration, held-out, or coverage checks agreed with the researcher.

Unexpectedly good residual statistics can indicate leakage, an over-flexible model, or an
incorrect degrees-of-freedom calculation. Report failed and ambiguous checks alongside passed
ones.

### 6. Produce a reproducible handoff

Deliver the model and response definitions, immutable input identifiers or checksums, complete
configuration, PRNG derivation, environment lock or freeze, device and x64 state, raw diagnostic
evidence, result checksums, known failure modes, and the exact claims the evidence does and does
not support. A production-scale or GPU handoff needs a new resource review; the bundled benchmark
does not authorize it.

## Verification

- The analytic benchmark passes unchanged in the selected environment.
- The report records a private, allowlisted runtime boundary, disabled bytecode writes, and the
  observed supported platform ABI without claiming to prove native execution.
- The isolated virtual environment, complete installed package set, installers, import origins,
  exact locked versions, installed NIFTy `RECORD`, reviewed wheel payload fingerprint, and frozen
  environment asset all pass independently.
- Every model input, prior, response, likelihood, unit, and mask has an explicit source.
- The pilot has fixed seeds, recorded package versions, bounded resources, and immutable inputs.
- Optimizer success, sample status, residuals, posterior predictive behavior, and sensitivity are
  all reviewed.
- The result distinguishes software readiness, numerical validation, and scientific validity.
- No secret value, system environment, existing result, or source repository was modified.
