---
name: coseecat
description: Query solar electron events and plots from CoSEE-Cat.
version: 1.0.0
author: Tiantian Tong and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: astronomy
    tags:
      - astronomy
      - solar
      - solar-orbiter
      - energetic-electrons
      - tap
      - datalink
      - aip
---

# CoSEE-Cat solar electron events

## Scope

Use for the public **CoSEE-Cat DR1** catalogue at AIP: find Solar Orbiter solar
energetic electron events, inspect associated flares/radio bursts/eruptions,
retrieve published diagnostic plots, and analyse tabulated event parameters.
This is not a raw instrument archive, real-time space-weather service, or a
replacement for EPD/STIX/EUI/RPW data reduction. A published plot is not its
underlying time series or spectral array.

Read [references/catalogue.md](references/catalogue.md) before constructing
queries or interpreting classifications, instrument status, timing, or missing
measurements. It includes table relationships, DataLink selection, examples,
the original sources, and the distinction between observed and inferred times.

## Portable Setup

Work in a fresh project directory, outside the installed skill. Use isolated
CPython 3.12 with every directly imported dependency pinned. Do not modify Ori's
shared `_base`, install into the skill directory, or inherit system site-packages.

```bash
(
set -e
if [ -e .venv ] || [ -L .venv ]; then
  printf '%s\n' 'Refusing existing .venv; inspect it and choose a new workspace path.' >&2
  exit 1
fi
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  'pyvo==1.9.1' 'requests==2.34.2' 'astropy==8.0.1' \
  'numpy==2.5.1' 'matplotlib==3.11.1'
.venv/bin/python -m pip check
)
```

Or use the matching uv recipe:

```bash
(
set -e
if [ -e .venv ] || [ -L .venv ]; then
  printf '%s\n' 'Refusing existing .venv; inspect it and choose a new workspace path.' >&2
  exit 1
fi
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  'pyvo==1.9.1' 'requests==2.34.2' 'astropy==8.0.1' \
  'numpy==2.5.1' 'matplotlib==3.11.1'
uv pip check --python .venv/bin/python
)
```

`uv` may download Python 3.12; use
`uv venv --no-python-downloads --python 3.12 .venv` if downloads are prohibited.
Both recipes assume `.venv` is a new workspace path. Inspect existing environments
and choose a new path instead of replacing them. These are direct pins,
not a complete transitive lock; pip and uv may resolve transitive dependencies differently.
Record a project lock when needed. Ori backups exclude workspace `.venv` folders:
after restoration, rebuild the environment and budget the necessary disk/time/network.

## Two reproducible demos

Use [scripts/coseecat_demo.py](scripts/coseecat_demo.py) with the isolated interpreter;
replace `/path/to/skill` by the actual installed skill directory.

### Event story: from solar observations to spacecraft electrons

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /path/to/skill/scripts/coseecat_demo.py \
  event --event-id 2011171841 --out outputs/coseecat-2011171841
```

The helper queries the event, follows the TAP response's DataLink descriptor with
PyVO, and downloads available recognised `#preview-plot` PNG products. It preserves
the original response and PNG bytes, creates a new catalogue-timing CSV/figure,
and writes a local `report.html` gallery. Published diagnostics and the new timeline
are labelled separately. Missing plots/times remain explicit. All DataLinks are
recorded, including external movies, but movies are **not downloaded or embedded**.

The default anchor has a high-confidence STIX association and both TSA/VDA estimates.
`2011181438` is a useful second example: it has a missing catalogue radio onset and
different TSA/VDA estimates; do not fill that missing value by guessing from its PNG.

### New analysis: rise times by published ion composition

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /path/to/skill/scripts/coseecat_demo.py \
  rise-times --out outputs/coseecat-rise-times
```

This computes `epd_tpeak - epd_tonset` in minutes and plots empirical cumulative
distributions for impulsive, gradual, intermediate, and unknown ion composition.
The CSV retains every selected row, including missing/invalid/negative-time
exclusions with reasons. Zero rise times and long tails are retained. Counts and
medians are calculated, not hard-coded; classes are not derived from rise times.
The current 303-row DR1 is small. The helper refuses overflow or a selection reaching
its 1001-row safety cap rather than presenting a truncated catalogue as complete.

### Offline replay and verification

Repeat either command with the same output directory and `--offline` to regenerate
the derived products from hash-verified cached source bytes, without contacting the
archive. Without that flag, a complete cache is also preferred automatically.
Original retrieval timestamps are preserved. For a new snapshot, choose a new
output directory; there is no overwrite/refresh switch. An incomplete or corrupt
bundle is refused, not silently replaced.

Inspect `report.html` and the generated PNG, CSV, `summary.json`, and
`provenance.json` before declaring success. Verify the selected event, row counts,
exclusions, uncertainty labels, source hashes, and package versions. Cache hashes
detect changes relative to the manifest, not an independently signed archive product.

## Scientific and operational boundaries

- TSA/VDA release times already include the Sun-to-Solar-Orbiter light-travel shift.
  Do not apply another correction. EPD onset is particle arrival at the spacecraft,
  not solar release. VDA timing bars represent the published fit standard deviation,
  not all systematic/model uncertainty.
- Observational association is not proof of causation. Preserve association confidence,
  instrument availability, and missing values. An estimated GOES class is not a direct
  GOES measurement. Ion-composition classes are not electron spectral classifications.
- Downloaded diagnostic images must be credited as the catalogue team's work. New
  catalogue plots should cite [Warmuth et al. 2025](https://doi.org/10.1051/0004-6361/202554830)
  and [DR1](https://doi.org/10.17876/coseecat/dr.1). The catalogue declares CC0; do not
  extend that declaration to separately hosted instrument movies or the skill's MIT code.
- The helper makes bounded public query/read requests only to fixed AIP TAP/DataLink
  endpoints and event-specific plot paths. It refuses redirects, caps each response
  at 8 MiB, uses connect/read timeouts, and neither reads ambient netrc credentials
  nor uses environment proxies. Networks requiring a proxy need a separately reviewed
  access recipe; do not disable TLS verification to compensate.
- Outputs and caches belong in the working project, never in the installed skill.
  No login, uploads, publication, instrument control, or live Ori/image changes occur.

For custom reviewer questions, use the bounded PyVO examples in the reference, inspect
actual metadata and category values, then select a small set of events before following
DataLinks. Do not download plots for the entire catalogue merely to filter it.
