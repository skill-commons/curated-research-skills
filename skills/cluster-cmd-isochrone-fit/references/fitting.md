# Fitting contract and interpretation

`../scripts/fit_cmd.py` performs an offline **robust geometric fit to stellar-evolution
isochrones**. It searches every supplied age, metallicity, extinction and requested
true distance modulus. It profiles over each star's position on the retained model
curve. It does not assign stellar masses from a main-sequence lifetime formula.

This is a practical cluster-locus estimator, **not** a normalized population
likelihood, Bayesian posterior, binary decomposition, or precision age pipeline.
A model mismatch or a contaminated sample can bias its answer. A successful command
means the numerical search completed; the CMD and sensitivity checks determine
whether the scientific inference is useful.

## Input artifacts

Every CSV has a same-stem JSON sidecar, for example `stars.csv` / `stars.json`.
Both require `schema_version: 1` and an explicit `photometric_system`. The supplied
adapters also record `output_csv_sha256`; the fitter verifies it if present.
For other adapters include this digest and document source URLs, downloaded-byte
hashes, column units, transformations, selections and model settings in the sidecar.
The report preserves the complete sidecars and hashes both input CSVs and sidecars.

Stars use `photometric_system: "Gaia DR3"` or `"Gaia EDR3"` and these columns:

| Column | Meaning |
| --- | --- |
| `source_id` | Unique source identifier, retained as text |
| `g,bp,rp` | Observed Gaia mean magnitudes; do not pre-deredden |
| `g_error,bp_error,rp_error` | Positive formal magnitude uncertainties |
| `member_score` | Membership-selection score in [0,1]; never used as a likelihood weight |
| `selected` | `true/false` or `1/0`, from an independently specified quality/membership selection |
| `exclusion_reason` | Required for excluded sources; retain all input rows |

Selected sources require finite photometry, errors and membership scores. Excluded
sources may have missing measurements. The fitter adds the predeclared observed
`--g-range` and optional `--color-range` to this selection. Every changed selection
is exported. At least five stars are required computationally; this does not prove
that an age-sensitive turnoff or subgiant population is present.

Models use `photometric_system: "Gaia EDR3 (Vega)"`, compatible with Gaia DR3 mean
G/BP/RP photometry. Gaia DR2, Johnson bands, AB magnitudes and arbitrary renamed
columns are not accepted. Model provenance must identify the evolutionary library,
version, bolometric corrections, extinction prescription and requested grid.

| Column | Meaning |
| --- | --- |
| `model_id` | One contiguous CSV block per isochrone |
| `log_age` | Base-10 age in years |
| `mh` | Model [M/H], not automatically interchangeable with measured [Fe/H] |
| `av` | Model A_V in magnitudes |
| `mini` | Initial mass in solar masses, nondecreasing within a model |
| `label` | Integer PARSEC evolutionary-phase label |
| `g_abs,bp_abs,rp_abs` | Model absolute magnitudes **already including this model's extinction** |

Keep every original model row in its original order, including excluded phases and
repeated initial masses. Do not thin or reorder phases before fitting. The code joins
only adjacent retained rows, skips gaps in phase labels, and never reconnects across
a removed row. Optional `row_index` can preserve original adjacency in an external
adapter. Duplicate masses are allowed; zero-length CMD segments are ignored.
Distinct models must have distinct `(log_age,mh,av)` coordinates. At least three ages
must remain after optional `--log-age`, `--mh`, and `--av` filtering.

## Objective and geometry

For star i the fitting coordinates are `(BP−RP,G)` with diagonal metric scales

```
s_color,i² = sigma_BP,i² + sigma_RP,i² + floor_color²
s_G,i²     = sigma_G,i² + floor_G²
```

The default floors are 0.02 mag in color and 0.03 mag in G. They limit domination by
very small formal errors and set the relative tolerance to color/magnitude mismatch.
They are **not** measured additional errors or calibrated intrinsic scatter. Change
both as a sensitivity test. Cross-band error covariance is assumed absent because
the supplied catalogue has no covariance between its mean band fluxes.

For each grid point, add only the true distance modulus to the model G magnitude.
The model bands already contain extinction; shifting color or adding A_G again
would double-count it. The nearest point on the retained piecewise-linear model
curve defines the standardized two-dimensional radius r_i. The objective is

```
Q = sum_i rho(r_i)
rho(r) = 0.5*r²                       if r <= delta
         delta*(r - 0.5*delta)        otherwise
```

The default Huber transition `delta` is 2. Outliers still contribute: the linear tail
reduces their influence but does not identify or remove binaries or blue stragglers.
The `residuals.csv` flags stars in this tail. A high fraction requires examining the
selection, model mismatch and astrophysical contaminants rather than tuning the
fit toward a known literature age.

The search uses exact nearest-point projections onto line segments, not distances
to sampled model rows. Subdividing a segment leaves its geometry and objective
unchanged. For speed, segments are split to at most 0.5 in the floor-scaled metric;
a KD-tree indexes their midpoints. A provisional exact projection supplies an upper
bound. The candidate radius is this bound times the largest star-error/floor ratio,
plus the maximum half-segment length. The triangle inequality guarantees that every
potentially closer segment is checked, including with heteroscedastic errors.
There is no nearest-neighbor approximation in the final projection. The physical
isochrone itself remains a linear interpolation of the supplied grid rows; age,
metallicity, extinction and distance are not interpolated between grid values.

## Grid, uncertainty and diagnostics

A single value or inclusive `min,max,step` specifies `--distance-modulus`. Optional
model filters use the same syntax. The maximum must lie on the requested step, and
values absent from the input model grid are not manufactured. A typical conditional
M67 fit fixes distance, metallicity and extinction from independent evidence, then
searches a broad age grid. Separate runs should vary those assumptions.

`score-grid.csv` contains every tested grid point and its objective. `age-profile.csv`
contains the minimum across supplied nuisance parameters at each age. These scores
are **not chi-square**; do not draw delta-chi-square confidence contours or turn
`exp(-Q)` into posterior probabilities. A flat profile, multiple competing minima or
a boundary optimum signals an unresolved inference. Boundary warnings are emitted;
the flat-profile threshold is only a heuristic diagnostic, not a statistical test.
Tied grid optima are resolved by input order; inspect their scores before reporting
a unique age.

`star-costs.npz` stores the per-star costs in score-grid row order, with source IDs.
The optional bootstrap resamples selected stars with replacement and reselects the
best grid point using those same costs. `fit.json` reports 16/50/84-percentile
**conditional star-bootstrap quantiles** and a fixed seed. If nuisance dimensions
are searched, they are reprofiled in each replicate. If fixed, they stay fixed.
These quantiles exclude stellar-evolution and atmosphere uncertainties, extinction
law/calibration systematics, membership errors, catalogue-wide correlations, and
nuisance assumptions outside the grid. Equal quantiles on a coarse grid are not
zero age uncertainty. Use a finer grid near a stable interior optimum only after
exploring broad ages; retain the broad search as provenance.

`cmd.png` shows selected/excluded sources and the best model. Its dashed equal-mass
binary curve shifts G by −2.5 log10(2), preserving color for identical components;
it is a diagnostic and is not included in the fitting objective. Other mass ratios
require flux combination in each band and a population model. `age-profile.png`
shows the robust score profile. `stars.csv` preserves final selections and
`residuals.csv` records observed-minus-model color/G residuals for fitted sources.
No output directory may already exist.

A defensible report gives the model/version/passbands, source catalogue and quality
cuts, age grid and resolution, age-sensitive population used, nuisance assumptions,
fit tolerances, best grid value, boundary/contamination diagnostics, conditional
bootstrap behavior, and nuisance/selection/model sensitivity. Report a broad range
or an underconstrained result when these do not support a precise age. Do not average
away sensitivity or substitute a cited cluster age for the fitted result.

## Methodological references

[Naylor & Jeffries (2006)](https://arxiv.org/abs/astro-ph/0609764) develop a
normalized CMD population likelihood incorporating measurement errors and binary
populations. It is a guide to a more complete statistical extension, not a
justification for interpreting this helper's geometric objective as their statistic.
[Hernandez & Valls-Gabaud (2008)](https://arxiv.org/abs/0801.1081) discuss the
information in stellar density along an isochrone, which the geometric helper
does not model.
