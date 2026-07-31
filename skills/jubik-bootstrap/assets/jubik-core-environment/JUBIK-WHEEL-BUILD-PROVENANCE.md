# J-UBIK 0.3 wheel provenance

- Artifact: `wheels/jubik-0.3-py3-none-any.whl`
- Artifact SHA-256: `30953812cbc922aa909e4f7ac5d86cd2c64f69747b8af7e97b00a98f15936b68`
- Upstream repository: `https://github.com/NIFTy-PPL/J-UBIK`
- Upstream release: `v0.3`
- Upstream commit: `58a1c7c23477d2099774523278c0ed65f27afde3`
- Upstream tree: `675b98f7d0ae9575e5f6081428005063cf942401`
- Upstream commit timestamp: `1776764398` (Unix epoch seconds)
- Upstream `pyproject.toml` SHA-256: `cb62cdf9e845a065476b976efb997515051531441f9d9077f35294590493fd03`
- Upstream `LICENSE` SHA-256: `fdd9d3c04f3f3df5ff24e6da099a7bf5c6717842cb2b89d83dfdb0b57de99e13`
- Wheel tag: `py3-none-any`
- Wheel metadata generator: `setuptools 83.0.0`
- Reproduction tool: `uv 0.10.12`
- Reproduction build interpreter selected by uv: CPython `3.11.15`
- License carried by the wheel: BSD-2-Clause; reproduced in
  `JUBIK-WHEEL-LICENSE.txt`

The wheel's complete `jubik/` package tree was compared byte-for-byte with the checkout at the
commit above; it matched after excluding source-tree `__pycache__` artifacts. The wheel's outer
SHA-256 is the installation identity used by this skill. A clean curation rerun reproduced that
SHA-256 with:

```text
SOURCE_DATE_EPOCH=1776764398 uv build --wheel --out-dir <new-private-directory> <exact-checkout>
```

The build imported package code and initialized JAX. The runtime bootstrap therefore installs
this reviewed wheel under `--no-build`; it does not execute a package build on a participant host.
