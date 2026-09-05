---
name: rave-dr6
description: Query and plot public RAVE DR6 spectra and catalogs.
version: 2.1.0
author: Arman Khalatyan, Tiantian Tong, and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: astronomy
    tags:
      - astronomy
      - rave-dr6
      - tap
      - pyvo
      - spectra
      - fits
      - stellar-parameters
      - crossmatch
---

# RAVE DR6

## When to Use

Use this skill for RAVE DR6 table discovery, 1-D spectrum retrieval, stellar-parameter
queries, observation metadata, Gaia crossmatches, and RAVE-to-StarHorse distance joins.
A fixed sample size, ordering, or plot style is a query/visualization choice—not a
separate skill.

Use `tap-pyvo-adql-access` for generic TAP mechanics and
`astro-catalog-plotting-cache` after the query has produced a local table.
For spectrum selection, FITS interpretation, and the bundled plotting workflow, read
[`references/spectra.md`](references/spectra.md).

## Portable Setup

Use CPython 3.12 in an isolated environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  'astropy==8.0.1' 'pyvo==1.9.1' 'pandas==3.0.5' 'pyarrow==25.0.0' \
  'matplotlib==3.11.1' 'seaborn==0.13.2' 'numpy==2.5.1'
.venv/bin/python -m pip check
```

When `uv` is available, install the same direct pins with:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  'astropy==8.0.1' 'pyvo==1.9.1' 'pandas==3.0.5' 'pyarrow==25.0.0' \
  'matplotlib==3.11.1' 'seaborn==0.13.2' 'numpy==2.5.1'
uv pip check --python .venv/bin/python
```

`uv` may download Python 3.12 when no compatible interpreter is installed; use
`uv venv --no-python-downloads --python 3.12 .venv` when downloads are not permitted.
Both recipes assume `.venv` is a new workspace path. If it already exists, inspect it and
choose another path rather than replacing or modifying it.
These are tested direct pins, not a complete transitive lock; pip and uv may resolve
transitive dependencies differently. Retain a project lock when exact reproduction
matters.

Run examples with `.venv/bin/python`. The public service requires network access but no
credentials. Start with metadata and a tiny query before requesting a larger result.

## Query Workflow

### 1. Connect and inspect the live schema

```python
from pyvo.dal import TAPService

tap = TAPService("https://www.rave-survey.org/tap/")
for table in tap.tables:
    print(table.name)
```

Inspect columns before constructing joins:

```python
table = tap.tables["ravedr6.dr6_x_gaiaedr3"]
for column in table.columns:
    print(column.name, column.datatype, column.unit)
```

Do not assume that an example table, column, or row count remains unchanged.

### 2. Run a bounded synchronous query

```python
query = """
SELECT TOP 100
    rave_obs_id, source_id, ra, dec, l, b,
    parallax, parallax_error, phot_g_mean_mag, bp_rp
FROM ravedr6.dr6_x_gaiaedr3
WHERE parallax > 0
ORDER BY parallax DESC
"""
result = tap.run_sync(query)
df = result.to_table().to_pandas()
```

The RAVE service has been reliable with `run_sync()`. Do not assume its asynchronous
endpoint behaves like another TAP provider. Use `TOP N`, explicit columns, and selective
`WHERE` clauses.

### 3. Cache the exact result and query

```python
from pathlib import Path

out = Path("outputs/rave-dr6")
out.mkdir(parents=True, exist_ok=True)
df.to_parquet(out / "subset.parquet", index=False)
(out / "query.adql").write_text(query)
```

Record the endpoint, query, retrieval time, row count, and column units with the cache.

## Useful Tables

Discover these from the live service before relying on them:

| Table | Typical role |
|---|---|
| `ravedr6.dr6_sparv` | Master parameters, classifications, and diagnostics |
| `ravedr6.dr6_obsdata` | Observation identifiers, input coordinates, and dates |
| `ravedr6.dr6_cnn` | CNN products and a Gaia source identifier |
| `ravedr6.dr6_x_gaiaedr3` | Gaia EDR3 crossmatch with astrometry and photometry |
| `ravedr6.dr6_x_gaiadr2` | Gaia DR2 crossmatch |
| `ravedr6.dr6_spectra` | Observation-level DOI, preview, and public FITS links |
| `ravedr6.dr6_orbits` | Orbital parameters |
| `ravedr6.dr6_seismic` | Seismic products |

## Individual 1-D Spectra

The TAP spectrum table is an index: it returns observation metadata and public FITS/PNG
URLs, not flux samples. Query the table by `rave_obs_id`, then download and validate the
FITS product. Do not synthesize a URL from an identifier because not every possible
identifier has a released spectrum.

For a bounded end-to-end check using a vetted normal-star observation:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/rave_spectrum_demo.py \
  --rave-obs-id 20100313_0823m14_113 \
  --out outputs/rave-dr6/spectrum-demo
```

The helper writes the exact ADQL query, cached FITS file, wavelength/flux/error CSV,
figure, and provenance JSON. Read [`references/spectra.md`](references/spectra.md) before
changing target-selection cuts or interpreting the arrays scientifically.

## Distance Choices

- A positive parallax supports a simple exploratory ordering, but
  `1000 / parallax_mas` is not a precision distance estimator.
- Use a documented posterior distance product when the scientific task needs distances
  or Galactocentric coordinates.
- For an external StarHorse/SHboost product, follow
  [`references/starhorse-crossmatch.md`](references/starhorse-crossmatch.md). Validate
  source-ID release semantics before joining.

## Plotting

Pass local catalog results to `astro-catalog-plotting-cache`. That skill covers RA/Dec
maps, Galactic projections, CMDs, density rendering, publication style, talk style, and
figure provenance. Use the spectrum reference and helper for FITS spectra. Choose sample
size and style from the scientific question, not from a hard-coded “nearest 100” recipe.

## Pitfalls

- Use ADQL `TOP N`, not SQL `LIMIT`.
- Inspect the schema instead of guessing joins or Gaia release semantics.
- Do not launch an unbounded query against the full survey.
- Keep synchronous requests in a bounded foreground process.
- Filter missing or non-physical parallaxes before exploratory distance calculations.
- Anchor cluster or stream selections to literature values rather than maximizing the
  number of selected stars.
- Deduplicate by the scientifically appropriate identifier after a crossmatch; one
  source can have multiple observations.
- Treat `rave_obs_id` as the spectrum identifier and `raveid` as the target identifier;
  repeated observations of one star are scientifically distinct spectra.
- Reconstruct each wavelength array from its FITS header. RAVE grids and lengths vary.
- RAVE spectra are continuum-normalized; they are not absolute spectrophotometry.

## Verification

- [ ] A `TOP 1` query succeeds and the expected columns exist.
- [ ] The scientific query has explicit columns and a bounded result.
- [ ] The exact query and result cache are saved.
- [ ] Units and Gaia/source-ID release semantics were checked.
- [ ] Crossmatch duplicates and unmatched rows were measured.
- [ ] Any plot is produced from the cache with recorded provenance.
- [ ] If a spectrum was requested, the FITS origin, observation ID, HDUs, wavelength
      calibration, error array, cached derivative, and figure were verified.
