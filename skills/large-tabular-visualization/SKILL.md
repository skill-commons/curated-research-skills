---
name: large-tabular-visualization
description: Build interpretable interactive or static visualizations from tabular data that is too dense or too large for ordinary point plotting.
version: 2.0.1
author: AIP AstroAgent team and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: visualization
    tags:
      - dask
      - hvplot
      - datashader
      - parquet
      - large-data
      - visualization
---

# Large Tabular Visualization

## Portable Setup

Use CPython 3.12 in an isolated environment. The following direct versions were
exercised together for this workflow:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  'numpy==2.4.6' 'pandas==3.0.5' 'dask[dataframe]==2026.7.1' \
  'hvplot==0.12.2' 'datashader==0.19.1' 'pyarrow==25.0.0' 'bokeh==3.9.2'
.venv/bin/python -m pip check
```

When `uv` is available, install the same direct pins with:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  'numpy==2.4.6' 'pandas==3.0.5' 'dask[dataframe]==2026.7.1' \
  'hvplot==0.12.2' 'datashader==0.19.1' 'pyarrow==25.0.0' 'bokeh==3.9.2'
uv pip check --python .venv/bin/python
```

`uv` may download Python 3.12 when no compatible interpreter is installed; use
`uv venv --no-python-downloads --python 3.12 .venv` when downloads are not permitted.
Both recipes assume `.venv` is a new workspace path. If it already exists, inspect it and
choose another path rather than replacing or modifying it.
These are tested direct pins, not a complete transitive lock; pip and uv may resolve
transitive dependencies differently. Retain a project lock when exact reproduction
matters. Run examples with `.venv/bin/python` and do not modify a system interpreter.

Keep `.venv` in the user workspace, not inside the installed skill directory. Resolve the
skill's installed path, then verify the late-loading Datashader path before using research
data:

```bash
SKILL_DIR=/path/to/installed/large-tabular-visualization
PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python "$SKILL_DIR/scripts/hvplot_datashader_smoke.py"
```

The bounded smoke uses synthetic data in a private temporary directory, checks a known
count aggregation, exports inline HTML, reads it back, and leaves no persistent output.

## When to Use

Use this skill when a dense scatter plot overplots important structure, or when a tabular
dataset should not be materialized in memory before filtering and aggregation. It covers
local or authorized Parquet inputs, Dask DataFrames, hvPlot exploration, and Datashader
aggregation.

If the projected and filtered data fits comfortably in memory, use pandas and ordinary
hvPlot instead. For astronomy-specific catalog acquisition and plotting conventions, use
the relevant catalog-access skill first and then apply this general workflow.

## Workflow

### 1. Define the scientific view

Before choosing a backend, decide:

- the exact x, y, color, and grouping columns;
- units, transforms, finite-value rules, and physically meaningful ranges;
- whether the pixel value represents count, sum, mean, category count, or another
  reduction;
- the required interactive behavior and final artifact format.

Do not use density as a substitute for an unspoken scientific quantity. Label the
aggregation and color normalization in the result.

### 2. Reduce at the source

Project only required columns and apply predicate filters while reading Parquet. For
TAP, database, or object-store data, push selection into the query or reader where
possible. Cache locally only when reuse, offline operation, or reproducibility justifies
the extra copy.

Use pandas when the reduced result fits in memory. Use Dask when partitioned execution is
still needed. Aim for partitions that are large enough to avoid scheduler overhead but
small enough for several working partitions to fit in memory; do not assume a fixed total
RAM budget. See [scaling and export](references/hvplot-datashader-0.12.md) for the
curated compatibility notes.

### 3. Construct a finite, lazy input

```python
import dask.dataframe as dd
import hvplot.dask  # registers the Dask .hvplot accessor
import hvplot.pandas  # registers .hvplot after computing a pandas sample
import numpy as np

x = "x_column"
y = "y_column"
frame = dd.read_parquet("data/partitioned-table", columns=[x, y])
clean = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=[x, y])
```

Add reader-level `filters=` whenever the Parquet engine and predicate support them. Do
not call `compute()` on the complete frame. Computing a reduced table is appropriate once
its size is known to fit.

### 4. Choose the rendering path

For a moderate result, start with a raw hvPlot scatter. For a dense or large result:

```python
plot = clean.hvplot.scatter(
    x=x,
    y=y,
    rasterize=True,
    aggregator="count",
    cnorm="eq_hist",
    width=900,
    height=600,
    colorbar=True,
)
```

Prefer `rasterize=True` when hover, colorbars, or access to aggregate values matters.
Use `datashade=True` when a final RGB representation is sufficient. Supply explicit
ranges for scientific comparability across panels or runs.

### 5. Verify and record provenance

Verify more than successful object construction:

1. render or save the intended artifact and read it back;
2. check dimensions, labels, units, ranges, and non-empty output;
3. compare a small synthetic or sampled case with a known aggregation;
4. inspect NaN, infinity, outlier, and empty-selection behavior;
5. record the input fingerprint or query, filters, columns, aggregation, ranges, color
   normalization, dependency lock, and output path.

An interactive plot is not a standalone artifact unless its dynamic behavior works in the
delivery environment. Use a fixed-range, non-dynamic render for self-contained export.

## Safety

- Treat remote reads, credential use, and cache placement as separate authorization
  decisions; this skill does not grant access to a dataset.
- Never print object-store credentials, signed URLs, or private table paths into logs or
  public metadata.
- Do not upload private data to a hosted plotting service merely to make an export.
- Ask before overwriting an artifact or creating a persistent cache.
