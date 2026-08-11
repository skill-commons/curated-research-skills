---
name: seaborn-paper-plots
description: Create reproducible publication plots with Seaborn.
version: 1.0.2
author: Arman Khalatyan and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: visualization
    tags:
      - seaborn
      - matplotlib
      - visualization
      - publication
      - reports
---

# Seaborn Paper Plots

## When to Use
Use this skill when a plot should be clean, reproducible, and suitable for paper drafting.

## Setup

Create a workspace-local environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  seaborn==0.13.2 matplotlib==3.11.1 pandas==3.0.5
.venv/bin/python -m pip check
```

When `uv` is available, install the same direct pins with:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  seaborn==0.13.2 matplotlib==3.11.1 pandas==3.0.5
uv pip check --python .venv/bin/python
```

`uv` may download Python 3.12 when no compatible interpreter is installed; use
`uv venv --no-python-downloads --python 3.12 .venv` when downloads are not permitted.
Both recipes assume `.venv` is a new workspace path. If it already exists, inspect it and
choose another path rather than replacing or modifying it.
These are tested direct pins, not a complete transitive lock; pip and uv may resolve
transitive dependencies differently. Retain a project lock when exact reproduction
matters.

These versions were exercised with CPython 3.12 on macOS ARM64. Run plotting scripts
with `.venv/bin/python`; do not depend on notebook or agent-runtime state.

## Procedure
1. Build the figure from explicit data frames.
2. Set the seaborn theme deliberately.
3. Use readable labels, legends, and output DPI.
4. Export deterministic filenames.

## Pitfalls
- Avoid relying on notebook state.
- Do not hide transformations that affect interpretation.

## Verification
- Run the plot from a standalone script in a clean process.
- Confirm that the saved output is non-empty and has the requested labels,
  dimensions, and resolution.
