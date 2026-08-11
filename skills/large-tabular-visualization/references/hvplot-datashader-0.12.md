# hvPlot and Datashader Compatibility Notes

This reference was exercised with CPython 3.12.4, `dask[dataframe]` 2026.7.1, hvPlot
0.12.2, Datashader 0.19.1, PyArrow 25.0.0, pandas 3.0.5, NumPy 2.4.6, and Bokeh 3.9.2.
Treat that as a compatibility target, not a command to upgrade an existing project.
Preserve the project's lockfile or create and commit a lock for a new environment.

On 2026-08-11, the direct pins installed cleanly with `uv` 0.10.12 on macOS ARM64 and
`uv pip check` reported a compatible environment. The bundled synthetic smoke exercised
Dask Parquet loading, finite-value filtering, `rasterize=True`, a known count aggregate,
and self-contained inline HTML export. This is an environment and rendering check, not
scientific validation of a research dataset or visualization choice.

## Choose pandas or Dask

Dask's own
[DataFrame best practices](https://docs.dask.org/en/latest/dataframe-best-practices.html)
recommend reducing data and switching to pandas when the result fits in memory. Dask is
useful when reading, filtering, grouping, or aggregating still requires partitioned
execution.

For Parquet:

- pass `columns=` and supported `filters=` to `read_parquet`;
- inspect partition sizes rather than relying on file count;
- target roughly 100–300 MiB in-memory partitions as a starting point, then measure;
- avoid `persist()` until after column and predicate pushdown, because early persistence
  can prevent later optimizer improvements;
- compute only a reduced result known to fit.

On the exercised stack, prefer `replace([np.inf, -np.inf], np.nan).dropna(...)` over
combining separately constructed Dask boolean masks for finite-value filtering. Reused
partition indexes can make the latter masks misalign after plot construction. Import
`hvplot.pandas` before plotting a pandas sample computed from the Dask frame.

See the official [Dask Parquet guide](https://docs.dask.org/en/latest/dataframe-parquet.html)
for version-specific reader behavior.

## `rasterize` versus `datashade`

Both operations aggregate points into screen-space pixels:

- `rasterize=True` retains a numeric aggregate represented by a HoloViews element. Use it
  when the plotting backend should apply the colormap or expose aggregate values through
  hover and a colorbar.
- `datashade=True` applies a Datashader transfer function and returns an RGB
  representation. Use it when the final shaded pixels are the desired result.

Choose an aggregator that matches the meaning of the plot. `count` answers how many rows
land in each pixel; it does not show a mean or weighted quantity. `cnorm="eq_hist"` can
reveal low-density structure, but it changes visual emphasis and must be disclosed.

Review the version-matched
[hvPlot resampling guide](https://hvplot.holoviz.org/en/docs/latest/ref/plotting_options/resampling.html)
for supported options.

## Dynamic and standalone output

Dynamic rasterization can recompute the aggregate when the viewport changes and therefore
needs a live Python process. For a standalone HTML snapshot, use explicit ranges and
`dynamic=False`, then save with inline resources:

```python
import hvplot

static_plot = clean.hvplot.scatter(
    x=x,
    y=y,
    rasterize=True,
    dynamic=False,
    xlim=(x_min, x_max),
    ylim=(y_min, y_max),
    width=900,
    height=600,
)
hvplot.save(static_plot, "density.html", resources="inline")
```

Open the saved file in a clean browser context and verify its content. PNG export with the
Bokeh backend can require a compatible headless browser stack; do not claim a PNG was
produced until the file has been read back. The official
[hvPlot viewing guide](https://hvplot.holoviz.org/en/docs/latest/user_guide/Viewing.html)
documents the current save behavior.

## Reproducibility record

For a reusable result, retain:

- input identifier, immutable revision or checksum, and access date;
- projected columns and pushed-down filters;
- post-read finite-value filters and transformations;
- aggregator, ranges, canvas dimensions, color normalization, and colormap;
- exact environment lock and backend;
- whether the artifact is dynamic, standalone, or a static snapshot.
