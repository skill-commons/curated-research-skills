# Library Query Patterns

Use these as focused research prompts, not as a mandatory checklist. Replace placeholders
with the target version, exact API, data shape, and execution environment.

## General

- In `<distribution> <version>`, what is the documented signature and return type of
  `<qualified API>`?
- Which release or migration note changed `<parameter or behavior>`?
- Is `<API>` public and supported, or an internal implementation detail?
- What is the smallest official example that exercises `<behavior>`?
- Which optional dependency or backend is required, and how is its absence reported?
- Does the documentation match the project's installed signature and type hints?

For an interaction between libraries, research both sides of the boundary. Record a
separate source for each library rather than assuming one project's documentation
defines the other's contract.

## Dask

Useful questions include:

- How does `dask.dataframe.read_parquet` apply column and predicate pushdown for the
  selected engine and filesystem?
- Does an operation remain lazy, trigger an eager computation, or construct a large task
  graph?
- Can metadata inference execute the callable while constructing the graph even though
  real partitions remain lazy?
- When is `persist()` appropriate, and can it prevent later optimizer pushdown?
- What partition size and repartitioning strategy fit this workload?
- Does a shuffle require known divisions or a distributed client?
- Does `map_partitions` require explicit `meta` for this callable and output?

Start with the official
[Dask DataFrame best practices](https://docs.dask.org/en/latest/dataframe-best-practices.html)
and [Parquet documentation](https://docs.dask.org/en/latest/dataframe-parquet.html), then
select the version matching the project.

## pandas

Useful questions include:

- Which `read_parquet` engine, filesystem, `filters`, `columns`, `storage_options`, and
  `dtype_backend` behavior applies?
- Are nullable and PyArrow-backed dtypes preserved through this operation?
- What are the documented null, categorical, ordering, and cardinality semantics for
  `groupby`, `merge`, or reshaping?
- Does the operation return a view, a copy, or a new object in the target version?

Use the official [pandas API reference](https://pandas.pydata.org/docs/reference/index.html)
and version-specific release notes.

## Datashader

Useful questions include:

- Which input types does `Canvas.points` support in the target release?
- Which aggregator expresses the scientific quantity: `count`, `sum`, `mean`, category
  count, or another reduction?
- How do range selection, empty bins, transforms, color mapping, and `shade` affect the
  interpretation?
- Which export path is supported for a headless static artifact?
- Where does computation occur when the input is a pandas or Dask DataFrame?

Use the official [Datashader documentation](https://datashader.org/) and verify behavior
with synthetic data whose expected aggregation is known.
