# NIFTy.re 9.2 Validation Reference

## Pinned surface

This skill was reviewed against the NIFTy 9.2.0 release:

- distribution: `nifty[re]==9.2.0`;
- release source commit: `17093190a42cde92a1c3922c57d441bbd8b6f9a1`;
- Python metadata: `>=3.10`;
- PyPI wheel SHA-256: `c9184918df1aa7b1894d77cca74f0a98b8594ebdc8773477168ea3d9143c105c`;
- reviewed CPU resolution on 2026-07-31: Python 3.12.4, JAX/JAXlib 0.11.0,
  jaxbind 1.3.1, ducc0 0.41.0, NumPy 2.5.1, and SciPy 1.18.0.

Primary sources:

- [NIFTy 9.2.0 package](https://pypi.org/project/nifty/9.2.0/)
- [Pinned release tree](https://gitlab.mpcdf.mpg.de/ift/nifty/-/tree/17093190a42cde92a1c3922c57d441bbd8b6f9a1)
- [NIFTy.re inference notebook](https://ift.pages.mpcdf.de/nifty/user/notebooks_re/1_inference.html)
- [`optimize_kl` API](https://ift.pages.mpcdf.de/nifty/mod/nifty.re.optimize_kl.html)
- [Official NIFTy.re introduction](https://gitlab.mpcdf.mpg.de/ift/nifty/-/blob/17093190a42cde92a1c3922c57d441bbd8b6f9a1/demos/re/0_intro.py)
- [Analytic consistency tests](https://gitlab.mpcdf.mpg.de/ift/nifty/-/blob/17093190a42cde92a1c3922c57d441bbd8b6f9a1/test/test_re/test_evi.py)
- [OptimizeVI coverage discussion](https://gitlab.mpcdf.mpg.de/ift/nifty/-/issues/392)

NIFTy package metadata declares GPL-3.0-or-later. Individual NIFTy.re source and demo files use
GPL-2.0-or-later or BSD-2-Clause identifiers. The bundled CRS benchmark is an original small
linear-Gaussian workflow; it does not copy the official demonstration.

## What the bundled benchmark tests

The model is a noisy straight line with slope and intercept, independent Normal priors, and known
Gaussian noise. Linear-Gaussian conjugacy gives an exact posterior mean and covariance. The
benchmark runs NIFTy.re `optimize_kl` in `linear_resample` mode at one fixed certification
configuration: seed 42, one iteration, and 128 requested mirrored sample pairs. It checks:

- a Python 3.12 isolated virtual environment, the complete exact locked package set, CPU backend,
  x64, disabled bytecode writes, and an allowlisted private runtime boundary;
- a registry installation performed by `uv`, absence of direct-URL/editable metadata, and NIFTy
  import origins inside the installed distribution;
- every hashed installed NIFTy `RECORD` entry and the canonical payload fingerprint derived from
  the reviewed PyPI wheel's `RECORD`;
- exact NIFTy version and the unchanged bundled environment asset;
- one completed iteration, zero sample statuses, and successful minimization;
- the expected doubled count from antithetic mirroring;
- variational posterior mean error at most `1e-6` against the analytic result;
- sample-covariance relative Frobenius error below `0.5`;
- improved data RMSE from the initial to posterior position;
- finite values and a deliberately broad reduced-chi-squared range of `0.25` through `4.0`.

The earlier development run at seed 42 and 64 requested pairs produced 128 actual samples, mean
error below `5e-16`, and covariance relative error about `0.091`. Certification uses 128 requested
pairs to make a random covariance miss substantially less likely. Exact floating values remain
evidence, not cross-platform gates; an unexpected statistical miss is investigated, never hidden
by rerunning seeds or relaxing a threshold.

The analytic agreement validates installation, PRNG plumbing, model composition, optimizer,
linear sampling, and summary calculations. It does not validate nonlinear geometry, a telescope
response, research priors, real-data likelihoods, or scientific interpretation.

## Evidence and digest semantics

The frozen package environment lives in the skill's
`assets/nifty-re-certification-environment/pyproject.toml` and `uv.lock`. The lock contains hashes
for every selected package artifact, including the reviewed NIFTy wheel. Synchronize it with
the full fixed `uv sync --frozen --no-build --no-cache --no-config --no-progress` command from the
skill, including the explicit public index, first-index strategy, disabled keyring and Python
downloads, and copy link mode. Install into a new external environment. The benchmark then verifies
what is installed rather than inferring provenance from the version string:

- an isolated virtual environment and the complete exact set of locked distribution versions;
- `uv` as every distribution's installer, absence of direct-URL/origin metadata, and NIFTy
  import-module origins;
- the size and SHA-256 digest of every hashed file named by NIFTy's installed `RECORD`;
- a sorted fingerprint of the immutable wheel payload entries in `RECORD`;
- the bundled lock, environment declaration, and benchmark script digests.

`reviewed_provenance` contains upstream identities established during curation.
`observed_evidence` contains facts read from the running environment. A match is a reproducibility
and integrity check, not proof of who executed the benchmark and not a cryptographic signature.

`canonical_payload_sha256` is computed from canonical JSON after removing that digest field. The
separately printed `report_file_sha256` covers the exact pretty-printed report bytes. Retain the
console summary separately if later file-integrity comparison matters. Nonfinite numerical values
are encoded as one of three explicit `nonfinite:*` strings, counted under `serialization`, and make
the `all_values_finite` gate fail while leaving a valid diagnostic report.

Python warnings and NIFTy log records at warning level or above are retained with fixed count and
message-length caps. The report records how many additional messages were dropped. The script uses
a newly created external mode-0700 JAX cache and sets `odir=None`, so NIFTy checkpoints are not
created. Installation runs in an allowlisted child environment. The benchmark itself requires
`python -I -B`, mode-0700 current-user-owned `HOME` and `TMPDIR` paths outside every Git worktree,
the fixed `C.UTF-8` locale, and no unrelated ambient environment names. Before any scientific
import it removes recognized proxy/certificate variables and `JAX_*`/`XLA_*` controls, records
only their names, and establishes its fixed CPU settings.

The reviewed platform ABI surface is macOS 12-or-newer arm64 and glibc Linux x86_64 with glibc
2.27 or newer. Runtime OS, machine, and libc observations cannot prove that execution was native
rather than virtualized or emulated; record that separately when relevant. The frozen wheel set
does not provide a no-build path for every package on macOS Intel, Linux arm64, or musl, and the
private POSIX mode guarantees do not extend to Windows ACLs.

## API details that prevent common failures

- Import the JAX implementation as `import nifty.re as jft`; `nifty.cl` is a different API.
- Enable JAX x64 before constructing arrays when scientific precision requires it.
- Compose model initialization methods with `|`; a model's domain alone does not carry the same
  standard-normal initialization knowledge.
- Call a model with one position mapping, not expanded keyword arguments.
- Initialize `optimize_kl` with `jft.Vector(likelihood.init(key))`.
- Pass both `noise_cov_inv` and `noise_std_inv` when the likelihood can use them.
- In NIFTy 9.2.0 use `resume=""` when no checkpoint is used; passing Boolean false can reach
  `os.path.isfile` and warn on newer Python versions.
- Posterior mean, residual, and chi-squared diagnostics must be evaluated in the correct target
  space. Count constrained degrees of freedom deliberately.

## Nonlinear imaging decision record

Do not mechanically translate the analytic benchmark to an astronomical image. Before model code
exists, record data units, sky coordinates, latent support, background, PSF, exposure/effective
area, masks, selection effects, likelihood, prior sources, and validation criteria. If any required
response or likelihood component is unknown, stop rather than infer it from array shape.

For each response stage, test shape, dtype, units, finite values, JAX tracing, selected JVP/VJP
derivatives, and finite-difference agreement. Test adjoints for explicitly linear stages and
normalization or flux conservation for convolution/reprojection. Run prior predictive draws before
optimization and simulated-data recovery before real-data interpretation.

NIFTy.re exposes both local-metric linear sampling and nonlinear residual updates. Choose between
MGVI-style `linear_resample` and geoVI-style `nonlinear_resample` from model curvature and bounded
simulation evidence. A nonlinear mode needs reviewed `nonlinearly_update_kwargs` and its sample
statuses and iteration-limit messages retained. Repeat the pilot across seeds, initializations,
sample counts, tolerances, and defensible priors; compare posterior predictive images and
structured residuals, not only posterior means or scalar chi-squared values.

Issue 392 documents incomplete coverage across the wide `OptimizeVI` configuration surface. It
is a reason to remain near documented configurations and build independent validation, not proof
that the release is broken. An older repeated-run memory issue is closed; do not report it as a
current defect.

## Escalation from benchmark to research inference

Before enlarging a run, require a project-local environment lock, immutable data identifiers,
unit and shape tests, a prior predictive check, a simulated-data recovery test, a resource
estimate, and a written diagnostic plan. For nonlinear models, repeat a small pilot across seeds
and defensible prior/tolerance variations. Separate numerical reproducibility from scientific
robustness in every report.
