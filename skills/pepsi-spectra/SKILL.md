---
name: pepsi-spectra
description: Retrieve and plot public PEPSI stellar spectra.
version: 1.0.0
author: Tiantian Tong and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: astronomy
    tags:
      - astronomy
      - pepsi
      - spectra
      - fits
      - stellar-atlas
      - aip
---

# PEPSI stellar spectra

## When to Use

Use this skill to discover, download, inspect, cache, or plot the public PEPSI
**Paper II stellar atlas** at AIP: Gaia benchmark stars, solar analogs, and other
bright spectral standards. These are high-resolution, continuum-normalized 1-D
spectra, not Gaia mission spectra, absolute spectrophotometry, or raw detector data.

The bundled helper supports the published `cont_v2` products only. It does not
control the instrument, reduce raw echelle observations, retrieve private data,
or assume other PEPSI solar/time-series/polarimetric releases have the same format.
Read [`references/atlas.md`](references/atlas.md) before interpreting FITS columns,
masks, wavelength conventions, gaps, uncertainties, or cross-survey comparisons.

## Portable Setup

From the working project, create an isolated CPython 3.12 environment. Keep it and
all outputs outside the installed skill directory. Do not install these pins into
Ori's shared `_base` environment or inherit system site-packages; that environment
has a different dependency contract. Astropy 8's raw logical-byte reading is needed
for this atlas's nonstandard mask storage.

```bash
(
set -e
if [ -e .venv ] || [ -L .venv ]; then
  printf '%s\n' 'Refusing existing .venv; inspect it and choose a new workspace path.' >&2
  exit 1
fi
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  'astropy==8.0.1' 'numpy==2.5.1' 'matplotlib==3.11.1'
.venv/bin/python -m pip check
)
```

When `uv` is available, use the same direct pins:

```bash
(
set -e
if [ -e .venv ] || [ -L .venv ]; then
  printf '%s\n' 'Refusing existing .venv; inspect it and choose a new workspace path.' >&2
  exit 1
fi
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  'astropy==8.0.1' 'numpy==2.5.1' 'matplotlib==3.11.1'
uv pip check --python .venv/bin/python
)
```

`uv` may download Python 3.12; use
`uv venv --no-python-downloads --python 3.12 .venv` when downloads are not permitted.
Both recipes assume `.venv` is a new workspace path. Inspect an existing environment
and choose another path instead of replacing or modifying it. These are tested direct
pins, not a complete transitive lock; pip and uv may resolve transitive dependencies
differently. Keep a project lock if exact reproduction matters. Ori backups exclude
workspace `.venv` directories, so budget installation time, package-index access,
and disk space again after a restore.

## Workflow

1. Consult the [official atlas page](https://pepsi.aip.de/?page_id=552) and its current
   giant/dwarf indexes. Select an actual catalog entry; do not invent a download URL
   from a star name. A spectrum's filename need not end in `.fits`.
2. Retrieve one complete published product, validate its identity and format, then
   choose a wavelength window for display. Downloading a narrow wavelength interval
   alone is not an advertised archive API.
3. Keep the original FITS bytes, source/index metadata, hashes, native-grid CSV, and
   provenance. Replot a verified cache for a repeatable demonstration.
4. Inspect both the raw product and rendered figure before reporting success. Label
   wavelength convention, normalization, and uncertainty interpretation explicitly.

Use [`scripts/pepsi_spectrum_demo.py`](scripts/pepsi_spectrum_demo.py) from the working
project with its isolated interpreter. Replace `/path/to/skill` with the installed
skill directory. List targets first when the requested name is not known:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /path/to/skill/scripts/pepsi_spectrum_demo.py --list
```

For a solar-like anchor, retrieve **18 Sco** and show the Mg I b region:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /path/to/skill/scripts/pepsi_spectrum_demo.py \
  --target '18 Sco' --range 5160 5190 --out outputs/pepsi-18sco
```

Use the exact displayed name, SIMBAD label, or catalog basename for another target.
The default range is 5160–5190 Angstrom; change `--range` for another feature. Use a
separate output directory per target. Re-running a valid bundle is offline; `--refresh`
deliberately retrieves the current index and product again. The helper performs only
bounded public HTTPS reads from `pepsi.aip.de` and workspace writes; it needs no archive
credentials and never publishes, uploads, or writes into an installed skill.

## Interpretation and Verification

- Read wavelength from `Arg`; do not reconstruct a uniform grid from header endpoints.
- `Fun` is continuum-normalized intensity; `Var` is variance, so the plotted standard
  uncertainty is `sqrt(Var)`, not `Var` or `abs(Fun) * Var`.
- Preserve raw mask bytes and nonfinite samples. Mask polarity is undocumented; the
  helper plots the observed uniform `0x01` state **without mask-based exclusion** and
  annotates that limitation. Other or mixed bytes stop the helper. Numeric validity is
  not a quality flag; do not interpret Astropy's Boolean view as a bad-pixel mask.
- Keep discontinuities as gaps. Do not join or interpolate across missing ranges just
  to make a smooth plot. A presentation zoom does not change the cached full spectrum.
- These atlas wavelengths are stellar-rest-frame **air Angstrom**. Do not apply a
  catalog radial velocity again. Gaia RVS uses vacuum wavelengths; conversion is not
  the same operation as Angstrom-to-nm scaling. Match resolution for detailed overlays.
- Confirm the selected catalog row, FITS identity, column shapes, wavelength ordering,
  mask values, variance, raw-file hash, CSV count, and figure. Retain the original
  retrieval timestamp on cache replay and cite the archive and Paper II with the demo.
- High S/N and fine sampling do not establish abundance precision. Continuum placement,
  telluric absorption, correlated errors, and instrumental systematics still matter.
