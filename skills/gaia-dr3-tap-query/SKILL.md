---
name: gaia-dr3-tap-query
description: Query Gaia DR3 catalogs and spectra at AIP.
version: 3.1.1
author: Hermi (sorgenfresser), AIP, and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: astronomy
    tags:
      - astronomy
      - gaia-dr3
      - tap
      - adql
      - pyvo
      - daiquiri
      - spectra
      - rvs
      - xp
---

# Gaia DR3 @AIP — TAP / pyvo (default)

## When to Use
Use this for Gaia DR3 catalog and 1-D spectrum access at AIP. Start with one bounded
`run_sync()` call against `https://gaia.aip.de/tap/`. For full-table aggregation, exceptional long-running
jobs, or TAP outages, read [`references/daiquiri-rest.md`](references/daiquiri-rest.md)
and use the same skill's REST fallback.
For sampled BP/RP (XP) or RVS spectra, read
[`references/spectra.md`](references/spectra.md) for product selection, array validation,
wavelength grids, and error interpretation. Continuous XP needs reconstruction and is
not interchangeable with the sampled product.

## Portable Setup

Dependencies are not bundled with the skill. From the working project, create a fresh,
isolated Python 3.12 environment with the tested direct pins below. Keep the environment
and outputs outside the installed skill directory. In Ori, do not install these pins into
the agent's shared `_base` environment or inherit its system site-packages: it has a
different dependency contract. These recipes test the isolated environment, not `_base`.

```bash
(
set -e
if [ -e .venv ] || [ -L .venv ]; then
  printf '%s\n' 'Refusing existing .venv; inspect it and choose a new workspace path.' >&2
  exit 1
fi
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  "astropy==8.0.1" "matplotlib==3.11.1" "numpy==2.5.1" \
  "pandas==3.0.5" "pyarrow==25.0.0" "pyvo==1.9.1" \
  "requests==2.34.2" "scipy==1.18.0" "seaborn==0.13.2"
.venv/bin/python -m pip check
)
```

When `uv` is available, install the same direct pins with:

```bash
(
set -e
if [ -e .venv ] || [ -L .venv ]; then
  printf '%s\n' 'Refusing existing .venv; inspect it and choose a new workspace path.' >&2
  exit 1
fi
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  "astropy==8.0.1" "matplotlib==3.11.1" "numpy==2.5.1" \
  "pandas==3.0.5" "pyarrow==25.0.0" "pyvo==1.9.1" \
  "requests==2.34.2" "scipy==1.18.0" "seaborn==0.13.2"
uv pip check --python .venv/bin/python
)
```

`uv` may download Python 3.12 when no compatible interpreter is installed; use
`uv venv --no-python-downloads --python 3.12 .venv` when downloads are not permitted.
Both recipes assume `.venv` is a new workspace path. If it already exists, inspect it and
choose another path rather than replacing or modifying it.
These are tested direct pins, not a complete transitive lock; pip and uv may resolve
transitive dependencies differently. Retain a project lock when exact reproduction
matters.
Ori workspace backups may omit `.venv`; recreate it from these pins after a restore.
First use therefore needs package-index access, installation time, and disk space.

Run the examples with `.venv/bin/python`. They send queries to `gaia.aip.de` and write
Parquet or image files beneath the current workspace. Begin with the five-row
verification query before increasing result sizes.

## Procedure

### 1. Connect
```python
import pyvo, warnings
warnings.filterwarnings("ignore")
service = pyvo.dal.TAPService("https://gaia.aip.de/tap/")
```

### 2a. Uniform random subsample — the RIGHT way to get N representative stars, FAST
`random_index` is a precomputed shuffle column; filtering on it returns a uniform
sample without sorting the 1.8-billion-row table. ~200k stars come back in ~1 s.
```python
q = """SELECT ra, dec, phot_g_mean_mag, parallax, bp_rp
       FROM gaiadr3.gaia_source
       WHERE random_index < 200000"""
df = service.run_sync(q, maxrec=300000).to_table().to_pandas()
```

### 2b. Nearest stars / targeted selection
```python
q = """SELECT TOP 100 source_id, ra, dec, l, b, parallax, phot_g_mean_mag, bp_rp
       FROM gaiadr3.gaia_source
       WHERE parallax > 10
       ORDER BY parallax DESC"""        # a parallax floor bounds the sort -> fast
df = service.run_sync(q).to_table().to_pandas()
```

### 3. Cache to a workspace topic folder
```python
import os
out = "gaia-dr3-allsky"                 # descriptive topic folder under the workspace
os.makedirs(out, exist_ok=True)
df.to_parquet(f"{out}/gaia_sample.parquet", index=False)
```

### 4. Discover tables / columns (optional)
```python
print([t.name for t in service.tables if "gaiadr3" in t.name][:10])
```

## Individual 1-D Spectra

At AIP, `gaiadr3.rvs_mean_spectrum` and `gaiadr3.xp_sampled_mean_spectrum` contain
`flux` and `flux_error` arrays. Check `has_rvs` or `has_xp_sampled` in
`gaiadr3.gaia_source`, then retrieve the requested product by exact DR3 `source_id`.
A catalog radial velocity alone does not imply a released RVS spectrum.

The bundled [`scripts/gaia_spectrum_demo.py`](scripts/gaia_spectrum_demo.py) retrieves,
validates, caches, and plots one source. Run from the working project with its isolated
interpreter; replace `/path/to/skill` with this skill's installed directory:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /path/to/skill/scripts/gaia_spectrum_demo.py \
  --source-id 5722622022989721600 --product rvs --out outputs/gaia-anchor
```

Use `--product xp` for the externally calibrated BP/RP spectrum of the same source.
The helper saves the retrieved array table as ECSV, the query, a per-pixel CSV, a PNG,
and provenance in separate source/product folders. Validated cache reuse works offline;
`--refresh` requests fresh data. Read the spectrum reference before scientific comparison
or changing product assumptions.

## Quick-Look Plot Recipes

Use these only to inspect a query result. For reusable CMDs, sky maps, density rendering,
cache provenance, or presentation figures, use `astro-catalog-plotting-cache`.

### RA/Dec sky scatter
```python
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.figure(figsize=(12, 5))
sc = plt.scatter(df["ra"], df["dec"], c=df.get("parallax"), cmap="plasma", s=4)
plt.xlabel("RA [deg]"); plt.ylabel("Dec [deg]"); plt.colorbar(sc, label="parallax [mas]")
plt.title("Gaia DR3 sample"); plt.savefig(f"{out}/gaia_ra_dec.png", dpi=150, bbox_inches="tight")
```

### All-sky density (Galactic, Mollweide) — for large samples
```python
import numpy as np
from astropy.coordinates import SkyCoord
import astropy.units as u
from matplotlib.colors import LogNorm
g = SkyCoord(ra=df.ra.values*u.deg, dec=df.dec.values*u.deg).galactic
l = -g.l.wrap_at(180*u.deg).radian; b = g.b.radian          # l increases left (convention)
H, xe, ye = np.histogram2d(l, b, bins=[np.linspace(-np.pi, np.pi, 361),
                                       np.linspace(-np.pi/2, np.pi/2, 181)])
fig = plt.figure(figsize=(13, 7)); ax = fig.add_subplot(111, projection="mollweide")
pcm = ax.pcolormesh(*np.meshgrid(xe, ye), H.T, cmap="magma",
                    norm=LogNorm(vmin=1, vmax=H.max()), shading="auto")
ax.grid(True, alpha=0.2); fig.colorbar(pcm, ax=ax, orientation="horizontal", shrink=0.6, label="stars/bin")
fig.savefig(f"{out}/allsky_density.png", dpi=130, bbox_inches="tight")
```

## Pitfalls
- Use `TOP N` or `WHERE random_index < N` — **not `LIMIT`** (unsupported by this service).
- **Never `ORDER BY random()`** over the full table — it forces a full scan + sort (minutes → stuck).
- For nearest-stars queries add a parallax floor (`WHERE parallax > 10`) so the sort is bounded.
- Samples above the sync row cap: pass `maxrec=`; for >1M rows use `service.submit_job(q)` (async).
- Save all outputs under the workspace (a topic folder); do not rely on
  client-specific home directories or temporary storage.
- **Keep queries in the foreground.** Run the query in a single foreground script — never launch it as a background process (terminal `background=true` / `notify_on_complete`). A detached query job keeps running after you stop and spawns stray completion nudges. A `run_sync` of a few hundred k rows returns in ~1 s; for a genuinely large scan use `service.submit_job(q)` and poll it *within* the foreground script. If you think you need millions of rows, subsample instead (`WHERE random_index < N`).
- **Anchor selections to literature values.** When isolating a known object's members (e.g. an open cluster), set your parallax / proper-motion / distance cuts from its published values — not from whatever maximizes the star count.
- **This service also speaks PostgreSQL (not only ADQL).** gaia.aip.de is a Daiquiri service: `service.submit_job(qstr, language='postgresql')` runs native PostgreSQL — the recipe many published notebooks for AIP-hosted tables use (e.g. StarHorse's `get_one_query`; see the `starhorse-access` skill). If a notebook/paper gives you a PostgreSQL query for an AIP table, run it VERBATIM with `language='postgresql'` — do NOT rewrite it into ADQL. Both languages work; rewriting is where errors creep in.
- Use the Daiquiri REST fallback only when TAP is unsuitable. It creates a server-side
  job and local cookie state and therefore requires bounded polling and cleanup.
- Preserve Gaia source IDs as 64-bit integers or decimal strings, never floating point.
- Verify complete numeric spectrum arrays; AIP documents legacy TAP array truncation.
  If validation fails, follow the Simple Join Service guidance in the spectrum reference.
- Gaia `flux_error` is in the same units as `flux`; the RAVE fractional-error convention
  does not apply. Keep missing samples as gaps at their original wavelengths.
- RVS spectra are already in the stellar rest frame and use vacuum wavelengths. Use
  vacuum line markers and do not apply the catalog radial velocity again.

## Verification
- `service.run_sync("SELECT TOP 5 ra, dec FROM gaiadr3.gaia_source")` returns 5 rows.
- A `random_index < N` query returns ~N real rows in ~1 s.
- Output Parquet / PNG land in the workspace topic folder.
- For a spectrum: validate source/product identity, numeric array lengths, masks, units,
  the fixed wavelength grid, the cached table, and the rendered figure.
