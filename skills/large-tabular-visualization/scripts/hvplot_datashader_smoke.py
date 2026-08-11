#!/usr/bin/env python3
"""Exercise the documented Dask, hvPlot, and Datashader rendering path locally."""

from __future__ import annotations

import json
import re
import tempfile
from importlib.metadata import version
from pathlib import Path

import dask.dataframe as dd
import hvplot
import hvplot.dask  # noqa: F401 - registers the Dask accessor
import numpy as np
import pandas as pd


def main() -> int:
    source = pd.DataFrame(
        {
            "x": [-1.0, 0.0, 1.0, np.nan, -1.0, 0.0, 1.0, np.inf],
            "y": [-1.0, 0.0, 1.0, 0.0, -1.0, 0.0, 1.0, 0.0],
        }
    )

    with tempfile.TemporaryDirectory(prefix="large-tabular-smoke-") as temporary:
        root = Path(temporary)
        dataset = root / "points.parquet"
        dd.from_pandas(source, npartitions=2).to_parquet(dataset, write_index=False)
        frame = dd.read_parquet(dataset, columns=["x", "y"])
        if frame.npartitions != 2:
            raise RuntimeError(f"unexpected partition count: {frame.npartitions}")
        clean = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["x", "y"])
        plot = clean.hvplot.scatter(
            x="x",
            y="y",
            rasterize=True,
            aggregator="count",
            cnorm="eq_hist",
            dynamic=False,
            xlim=(-2, 2),
            ylim=(-2, 2),
            width=300,
            height=200,
            colorbar=True,
        )

        counts = plot.dimension_values("Count", flat=False)
        if counts.shape != (200, 300):
            raise RuntimeError(f"unexpected aggregate shape: {counts.shape}")
        finite_counts = np.nan_to_num(counts, nan=0)
        nonzero_counts = np.sort(finite_counts[finite_counts != 0])
        if not np.array_equal(nonzero_counts, np.array([2.0, 2.0, 2.0])):
            raise RuntimeError(f"unexpected nonzero aggregate counts: {nonzero_counts}")
        aggregate = {
            "count": float(finite_counts.sum()),
            "maximum_bin": float(finite_counts.max()),
            "nonempty_bins": int(np.count_nonzero(finite_counts)),
        }

        output = root / "density.html"
        hvplot.save(plot, output, resources="inline")
        html = output.read_text(encoding="utf-8")
        if len(html) < 100_000 or "Bokeh" not in html:
            raise RuntimeError("inline HTML export is incomplete")
        script_sources = re.findall(r'<script\b[^>]*\bsrc=["\']([^"\']+)', html, re.IGNORECASE)
        if any(not source.startswith("data:") for source in script_sources):
            raise RuntimeError("inline HTML export depends on external JavaScript")
        serialized = re.sub(r"\s+", "", html)
        required_markers = ('"name":"Image"', '"field":"image"', '"type":"ndarray"')
        if not all(marker in serialized for marker in required_markers):
            raise RuntimeError("inline HTML does not contain a serialized raster image")

    packages = ("bokeh", "dask", "datashader", "hvplot", "numpy", "pandas", "pyarrow")
    print(
        json.dumps(
            {
                "aggregate": aggregate,
                "packages": {package: version(package) for package in packages},
                "status": "pass",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
