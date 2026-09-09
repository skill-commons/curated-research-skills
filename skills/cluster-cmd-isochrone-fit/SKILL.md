---
name: cluster-cmd-isochrone-fit
description: Fit cluster CMD ages with stellar-evolution isochrones.
version: 1.0.0
author: Tiantian Tong and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: astronomy
    tags:
      - astronomy
      - stellar-evolution
      - isochrones
      - gaia
      - star-clusters
      - parsec
---

# Cluster CMD isochrone fitting

Estimate a star cluster's age by comparing measured photometry with an actual
stellar-evolution **age grid**, using matched photometric passbands. The supplied
workflow fits Gaia G versus BP−RP with PARSEC isochrones and includes a public M67
example. It supports a robust geometric fit and conditional sample bootstrap;
these are not a normalized population likelihood or a Bayesian age posterior.

Use for CMD turnoff/sequence fitting, not gyrochronology or raw-image photometry.
PERICLES rotation tables lack the luminosities needed here. A single isochrone
chosen at a literature age cannot estimate an age; neither can a main-sequence
lifetime power law substituted for a missing model grid.

## Choose and inspect the inputs

1. Obtain membership evidence independently of the age being fitted. Preserve
   source identifiers, photometry, uncertainties, all excluded rows and reasons.
   A clustering membership score is not automatically a calibrated probability.
2. Match the model's named filters and magnitude system to the observations.
   Gaia DR3 uses EDR3 passbands; Gaia DR2 is a different system. Keep apparent and
   absolute magnitudes distinct. Use the model's already-extincted magnitudes and
   add the true distance modulus once; do not deredden the data again.
3. Inspect the observed CMD before choosing the fit window. Use the same stars
   for every trial age. Keep lower-main-sequence stars visible, but test whether
   they overwhelm the sparse age-sensitive turnoff/subgiants. Do not adjust cuts
   to approach a published age.
4. Declare independent distance, metallicity and reddening constraints. Fit or
   vary nuisance parameters explicitly. A fixed solar metallicity or uniform
   extinction screen is an assumption, not information learned from the CMD.
5. Read [references/fitting.md](references/fitting.md) for the input schemas,
   metric, evolutionary phases, bootstrap interpretation and failure checks.

## Portable Setup

Work outside the installed skill in a fresh project directory. Use isolated
CPython 3.12 and exact direct pins:

```bash
(
set -e
if [ -e .venv ] || [ -L .venv ]; then
  printf '%s\n' 'Refusing existing .venv; inspect it and choose a new workspace path.' >&2
  exit 1
fi
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  'numpy==2.5.1' 'scipy==1.18.0' 'matplotlib==3.11.1'
.venv/bin/python -m pip check
)
```

Or use uv with the same direct pins:

```bash
(
set -e
if [ -e .venv ] || [ -L .venv ]; then
  printf '%s\n' 'Refusing existing .venv; inspect it and choose a new workspace path.' >&2
  exit 1
fi
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  'numpy==2.5.1' 'scipy==1.18.0' 'matplotlib==3.11.1'
uv pip check --python .venv/bin/python
)
```

`uv` may download Python 3.12; use
`uv venv --no-python-downloads --python 3.12 .venv` when downloads are prohibited.
Both recipes assume `.venv` is a new workspace path. These are direct pins,
not a complete transitive lock; pip and uv may resolve transitive dependencies differently.
Retain a project lock when exact reconstruction matters. Workspace backups may
exclude `.venv`, so expect to rebuild it after restoration. The download helpers
use the Python standard library and verified HTTPS; the PARSEC helper also uses
the system `curl` client where documented in its reference.

## Retrieve, fit and inspect

- [scripts/fetch_parsec.py](scripts/fetch_parsec.py) retrieves and validates a
  bounded PARSEC grid. Read [references/parsec.md](references/parsec.md) first for
  service settings, filter identity, extinction limitations and citations. Retain
  original responses and request metadata: the service can omit a requested age
  endpoint, and a successful HTTP response can contain an error page.
- [scripts/fetch_m67.py](scripts/fetch_m67.py) obtains the M67 Gaia DR3 member
  sample and exports quality selections. Read [references/m67.md](references/m67.md)
  for its exact data contract and independent nuisance constraints. This adapter
  is optional; other clusters can supply the same documented input schema.
- [scripts/fit_cmd.py](scripts/fit_cmd.py) fits local input tables without network
  access, saves every trial score, and produces the CMD, age profile and fit
  summary. Use its `--help` and the worked commands in the references. Inputs and
  outputs belong in the project workspace, outside the installed skill.

Start with a broad age grid. Refine around an interior minimum before quoting
more precision than its spacing permits. Check the full CMD and residuals, the
retained phases, the age profile, boundary warnings and whether turnoff/subgiant
stars actually distinguish ages. A flat or boundary minimum is an unresolved
fit, not an age measurement.

Rerun with plausible distance, extinction and metallicity changes, a different
faint limit, and defensible quality/membership thresholds. Report the changes
alongside the fitted age. A robust loss limits outlier influence; it does not
identify binaries, remove blue stragglers, or correct completeness. An equal-mass
binary locus is only a diagnostic, not a population model.

## Deliver an auditable result

Keep the raw sources, queries, model settings and versions, CSV/metadata hashes,
selection reasons, runtime versions, numerical score grid and generated figures.
Inspect rendered plots before presenting them. Distinguish a newly fitted age
from a literature comparison and from any catalogue-derived ages. State whether
the result is conditional on fixed nuisance parameters and on one stellar-model
family. Bootstrap quantiles describe sample sensitivity; they omit uncertainties
in stellar physics, binaries, calibration and selection. Do not call the robust
score chi-square, convert it into posterior probabilities, or quote its profile
width as a formal confidence interval.

For publication-grade inference requiring calibrated uncertainties, model the
population density along isochrones, binaries, field contamination, measurement
errors and the observation-window selection. The geometric helper does not
implement that extension; see the methodological sources in the fitting reference.

The skill's original code and instructions are MIT-licensed. Model and catalogue
data are retrieved on demand under their own terms; do not relicense them as MIT.
Cite PARSEC, the selected bolometric corrections/passbands, the member catalogue,
CDS/VizieR and Gaia/DPAC as specified in the references.
