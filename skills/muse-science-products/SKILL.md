---
name: muse-science-products
description: Plot published MUSE maps and H II region measurements.
version: 1.0.0
author: Tiantian Tong and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: astronomy
    tags:
      - astronomy
      - muse
      - fits
      - nebulae
      - galaxies
      - integral-field-spectroscopy
      - aip
---

# MUSE science products

## Scope and product selection

Use for the public AIP **MUSE Science Data** release: discover, inspect, cache and
plot Orion Nebula physical maps and Antennae emission-line maps and H II region
measurements. These are published reduced/derived products. Reading a temperature
map is not a new temperature determination from spectra; a map cannot recover the
spectral cube from which it was made.

Read [references/products.md](references/products.md) for the supported products,
FITS structures, units, smoothing, velocity frames, measurement conventions and
paper citations before selecting or interpreting data. The reference also explains
how to investigate additional maps in the release. The helper implements two
bounded workflows; it is not an arbitrary FITS-file or URL loader.

Use other workflows for raw MUSE reduction, general ESO archive retrieval, source
extraction from cubes, or new physical inference from emission-line ratios. The
separate MUSE Pipeline Releases page distributes software and test inputs. The
75/110-GiB Orion cubes are unnecessary for the map workflows here.

## Portable Setup

Work outside the installed skill in a fresh project directory. Use isolated
CPython 3.12; do not modify a shared agent environment or inherit system packages.

```bash
(
set -e
if [ -e .venv ] || [ -L .venv ]; then
  printf '%s\n' 'Refusing existing .venv; inspect it and choose a new workspace path.' >&2
  exit 1
fi
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  'astropy==8.0.1' 'numpy==2.5.1' 'matplotlib==3.11.1' 'requests==2.34.2'
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
  'astropy==8.0.1' 'numpy==2.5.1' 'matplotlib==3.11.1' 'requests==2.34.2'
uv pip check --python .venv/bin/python
)
```

`uv` may download Python 3.12; use
`uv venv --no-python-downloads --python 3.12 .venv` if downloads are prohibited.
Both recipes assume `.venv` is a new workspace path. These are direct pins,
not a complete transitive lock; pip and uv may resolve transitive dependencies differently.
Retain a project lock when exact environment reconstruction matters. Workspace
backups may exclude `.venv`, so budget rebuilding after restoration.

## Retrieve and demonstrate

Use [scripts/muse_products.py](scripts/muse_products.py) with the isolated
interpreter. Replace `/path/to/skill` with the actual installed directory.

List the fixed source products and their scientific meanings:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /path/to/skill/scripts/muse_products.py list
```

### Orion: physical conditions across a nebula

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /path/to/skill/scripts/muse_products.py \
  orion --out outputs/muse-orion
```

Plot the published [S III] electron-temperature map, [S II] electron-density map,
and Halpha velocity field with celestial coordinates and individual units. The
temperature and density products are the authors' median-smoothed maps; the helper
does not independently derive or smooth them. Display limits are presentation
choices, recorded separately from validity exclusions. Pixels outside a displayed
colour range are not silently removed from the source data.

### Antennae: emission, gas motion, and H II regions

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /path/to/skill/scripts/muse_products.py \
  antennae --out outputs/muse-antennae
```

Show the central Halpha map and published gas-velocity map, then compare the
central and southern catalogues using the published extinction-corrected Halpha
luminosities. The cumulative distributions are new descriptive summaries of
published measurements. Keep every source-table row in the exported data, with
explicit selection/exclusion reasons. Do not silently substitute observed fluxes,
recompute luminosities from ambiguous unit strings, or equate the two fields'
detection completeness.

### Inspect and replay

Each workflow preserves original FITS bytes and records their URLs, hashes,
retrieval timestamps, helper identity, dependencies, selections and artifact
hashes. Inspect the generated PNGs, summary and CSV outputs, and the self-contained
`report.html` before presenting the result. Confirm source identities, finite and
excluded counts, axes/units, smoothing labels, the velocity convention, and the
distinction between published maps and new table statistics.

Repeat the same command with `--offline` to replay verified cached data without
contacting the archive. Complete verified caches are also preferred by default.
Retrieval timestamps remain those of the original fetch. Use a new output directory
for a fresh snapshot; incomplete, modified or unrelated bundles are refused rather
than silently refreshed. Outputs must be outside the installed skill. The HTML
contains embedded figures and does not require remote image, font or script loads.
The seven source objects are pinned by byte count and SHA256. Changed archive
bytes require a reviewed product-contract update. Replay also verifies the helper
and environment identity; a changed runtime requires a new output bundle.

## Interpretation and extension

- Orion line diagnostics probe different ionization regions; differences between
  maps need not indicate contradictory measurements. Smoothing introduces spatial
  correlation. Map percentiles are descriptive pixel statistics, not independent
  samples or physical uncertainties.
- Antennae luminosity distributions depend on extinction treatment, segmentation,
  depth and selection. A difference in these released samples is not a
  completeness-corrected luminosity function or a causal test of star formation.
- Velocity maps retain their published reference frame and zero point. Do not
  subtract a systemic velocity or combine species without declaring and verifying
  that transformation. The central Antennae velocity map uses the authors' Voronoi
  binning to a target signal-to-noise ratio of about 30; neighbouring pixels in
  a bin are not independent velocity measurements.
- For an additional release product, first inspect its actual FITS HDUs, WCS,
  dimensions, mask, units and paper definition. Some page links omit a bucket path;
  use observed working directory links. A larger array or different unit requires
  an explicit reader/contract update, not disabling validation.
- The helper uses bounded public HTTPS reads, refuses redirects, and ignores
  ambient proxy/netrc credentials. A network requiring a proxy needs a separately
  reviewed access recipe. No credentials or remote writes are required.

## Citation and rights

Credit [MUSE Science Data, DOI 10.17876/data/2023_3](https://doi.org/10.17876/data/2023_3)
and the relevant paper: [Weilbacher et al. 2015, Orion](https://doi.org/10.1051/0004-6361/201526529)
or [Weilbacher et al. 2018, Antennae](https://doi.org/10.1051/0004-6361/201731669).
The release DOI metadata declares CC0 for data. This skill's original instructions
and helper are separately MIT-licensed; archive data are retrieved on demand and
are not bundled into the installed skill. Software checks and replay do not
independently validate the authors' instrument reduction or physical diagnostics.
