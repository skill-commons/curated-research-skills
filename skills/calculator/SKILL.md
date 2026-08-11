---
name: calculator
description: Perform exact symbolic and numerical calculations.
version: 1.0.2
author: Arman Khalatyan and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: general
    tags:
      - mathematics
      - sympy
      - mpmath
      - units
      - scientific-computing
---

# Calculator — sympy/mpmath for honest math

## When to Use
EVERY time a task involves a formula, a derivation, a unit conversion, or any
arithmetic beyond a single trivial operation. The rule from your SOUL: **never
hand-evaluate formulas** — mental algebra and mental arithmetic both fabricate
precision. Derive symbolically, evaluate numerically, and quote exactly what
the code printed.

Create an isolated workspace environment instead of assuming that an agent runtime
preinstalls the libraries:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install \
  sympy==1.14.0 mpmath==1.3.0 astropy==8.0.1
.venv/bin/python -m pip check
```

When `uv` is available, install the same direct pins with:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  sympy==1.14.0 mpmath==1.3.0 astropy==8.0.1
uv pip check --python .venv/bin/python
```

`uv` may download Python 3.12 when no compatible interpreter is installed; use
`uv venv --no-python-downloads --python 3.12 .venv` when downloads are not permitted.
Both recipes assume `.venv` is a new workspace path. If it already exists, inspect it and
choose another path rather than replacing or modifying it.
These are tested direct pins, not a complete transitive lock; pip and uv may resolve
transitive dependencies differently. Retain a project lock when exact reproduction
matters.

Run the examples with `.venv/bin/python`. These versions were exercised with CPython
3.12 on macOS ARM64; do not modify a system Python implicitly.

## Core recipe
```python
import sympy as sp

# 1. Declare symbols and the formula SYMBOLICALLY
M, t = sp.symbols("M t", positive=True)
t_ms = 10 * (M) ** sp.Rational(-5, 2)   # main-sequence lifetime in Gyr, M in Msun

# 2. Manipulate symbolically if needed (solve, diff, integrate, simplify)
M_of_t = sp.solve(sp.Eq(t, t_ms), M)[0]

# 3. Substitute numbers LAST, evaluate with evalf — quote THIS output
print(t_ms.subs(M, 1.1).evalf())        # -> 7.879856... Gyr (NOT a guessed 3.6!)
print(M_of_t.subs(t, 4.0).evalf())      # mass whose t_MS = 4 Gyr
```

## Error propagation (do this instead of hand-waving uncertainties)
```python
import sympy as sp
x, y, sx, sy = sp.symbols("x y sigma_x sigma_y", positive=True)
f = x * y**2
sigma_f = sp.sqrt((sp.diff(f, x) * sx) ** 2 + (sp.diff(f, y) * sy) ** 2)
print(sp.simplify(sigma_f / f))          # relative error, exact
print(sigma_f.subs({x: 3.2, y: 1.7, sx: 0.1, sy: 0.05}).evalf())
```

## Units & constants — astropy, not memory
```python
from astropy import units as u, constants as c
E = (c.G * u.Msun**2 / u.Rsun).to(u.erg)       # gravitational self-energy scale
v = (500 * u.km / u.s).to(u.pc / u.Myr)        # unit conversion
print(E, v)
```
Never recall constants from memory when `astropy.constants` has them.

## High precision / special functions — mpmath
```python
import mpmath as mp
mp.mp.dps = 50                       # 50 significant digits
print(mp.quad(lambda x: mp.exp(-x**2), [0, mp.inf]))   # sqrt(pi)/2 to 50 digits
```

## Honesty contract
- The number you report MUST be a number your code printed in this session.
- If symbolic and numeric paths disagree, or a result surprises you, show both
  and say so — do not silently pick the one that matches expectations.
- Keep the derivation script in the task's folder (e.g. `analysis/derive_age.py`)
  so the user can rerun it.
