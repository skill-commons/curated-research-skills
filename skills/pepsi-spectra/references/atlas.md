# PEPSI Paper II atlas contract

## Discover the actual release

The [Paper II release page](https://pepsi.aip.de/?page_id=552) describes 48 bright-star
atlases with nominal coverage 383–912 nm and average resolving power about 220,000.
Actual coverage and resolution vary. These ground-based spectra include **Gaia
benchmark stars**; that label does not make them Gaia mission data.

The page's [embedded table](https://pepsi.aip.de/library/paperII/table_v2.html) loads:

- [Dwarf catalog](https://pepsi.aip.de/library/paperII/dwarfs.csv)
- [Giant catalog](https://pepsi.aip.de/library/paperII/giants.csv)
- [Link-construction script](https://pepsi.aip.de/library/paperII/fits2table.js)

Each catalog has `name,simbad,type,snr,basename`. The script constructs refined,
continuum-normalized download links under `https://pepsi.aip.de/library/paperII/cont_v2/`
by appending `.awl.all6` to a **catalog-provided** basename. These are uncompressed
FITS files despite the unusual extension. The unrefined-product links are commented
out in the inspected script; do not promise those products or guess their URLs.

Use exact catalog labels or basenames, not fuzzy crossmatches. Preserve the chosen
row and original CSV. The helper fetches two small indexes (at most 256 KiB each)
and one product (at most 32 MiB), rejects redirects, and uses no archive credentials.
Schema/link changes should cause reinspection, not automatic expansion of URL access.

## Verified FITS structure

Inspected on 2026-09-05 for 18 Sco and Arcturus (`α Boo` in the catalog): an empty
primary HDU followed by a single `DataVector` binary table. There are scalar native-grid
rows, not six separate order extensions. The checked columns are:

| Column | FITS format | Interpretation |
|---|---|---|
| `Arg` | `1D` | Stellar-rest-frame air wavelength in Angstrom |
| `Fun` | `1D` | Continuum-normalized intensity |
| `Var` | `1D` | Variance of normalized intensity |
| `Mask` | `1L`, described as byte | Raw release mask, polarity undocumented |

The inspected files omit `TUNIT`. The wavelength convention comes from the release
and [Paper II](https://doi.org/10.1051/0004-6361/201731633), not from guessing the
scale of the numbers. The helper is deliberately release-specific and rejects
unfamiliar units, scaling, columns, and mask formats. Do not use it as a generic
FITS reader for unrelated PEPSI products.

Verify `INSTRUME=PEPSI` and that `FILE0` starts with the selected catalog basename.
Retain `OBJECT` verbatim: Greek catalog labels can be spelled out in the header
(`α Boo` versus `alpha Boo`), so naive string equality is not identity verification.

### Mask storage needs special handling

Both inspected products store numeric byte **0x01** throughout the `Mask` column,
although `TFORM=1L` denotes FITS logical storage. Ordinary Astropy Boolean decoding
turns those bytes into `False`, which is not a documented PEPSI quality meaning.
Use the [Astropy 8 raw-logical-byte option](https://docs.astropy.org/en/stable/whatsnew/8.0.html):

```python
from astropy.io import fits
import numpy as np

with fits.open(local_path, memmap=False, logical_as_bytes=True, checksum=False) as hdus:
    raw_mask = np.asarray(hdus[1].data["Mask"]).view(np.uint8)
```

No polarity definition was found in the release, paper, headers, or
[official viewer](https://pepsi.aip.de/spview_v2/spviewer.js); the viewer uses `Arg`
and `Fun` without consulting `Mask`. Therefore **do not assert 1 means good or bad**.
The helper preserves the observed uniform `0x01` bytes, applies no mask-based
exclusion, and states that limitation on the plot and in provenance. Other/mixed
bytes are outside the tested contract and stop the helper. A documented mask
mapping from the data producer would be needed to extend this policy.

Numeric validity is separate: retain native rows; use gaps for nonfinite intensity,
nonfinite variance, or negative variance. Standard uncertainty is `sqrt(Var)`;
the flux does not multiply it. Zero variance is retained, not silently changed to
an arbitrary error floor. The propagated pixel variance is not a full covariance
model and does not include all continuum/calibration systematics.

### Wavelength, gaps, and velocity

Read every wavelength from `Arg`. Sampling is nonuniform. Do not sort malformed
arrays, rebuild a linear grid, interpolate across missing intervals, or infer resolving
power from one pixel step. For plotting, the helper splits at steps above ten times
the full-grid median. This is a documented display heuristic, not an instrument
quality criterion; all rows remain in the CSV and original FITS.

The release describes barycentric, catalog stellar-RV, and residual cross-correlation
corrections as already removed. Retain `RADVEL`, `CCFOFF`, `CCFERR`, and `HISTORY`
as provenance, not instructions to shift the wavelength again. For Gaia RVS comparison,
convert air/vacuum convention explicitly, then match units and resolution as needed.

The primary header retains observing/setup fields from combined inputs. Preserve
date/time/exposure strings without turning them into a single observation epoch;
Paper II section 4.6 cautions that averaged combined-header dates have no physical
meaning. Do not interpret `CROSDIS` as the full combined wavelength coverage.
The inspected extension's `CHECKSUM` is described as Adler32, not a normal FITS
checksum; retain the raw file and use SHA256 for cache-integrity verification.

## Anchor and demonstration choices

The catalog's **18 Sco** entry has type G2 V, catalog S/N 748, and basename
`pepsib.20150524.161.sxt`. Its published product contained 433,381 rows spanning
3822.810912–9119.066637 Angstrom, with 2,906 rows in 5160–5190 Angstrom. This window
is the Mg I triplet region also offered by the
[official viewer's zoom definitions](https://pepsi.aip.de/spview_v2/buttons.json).
The 10,857,600-byte file has SHA256
`2a1a90f2f70fbf56f36b9001106cb4ca81f9e7837dedbe37f34a5e917f255260` at inspection time.
That hash is a dated observation, not permission to ignore later product changes.

Arcturus is an alternative giant: catalog name `α Boo`, basename
`pepsib.20150409.026.sxt`, 435,550 rows in the inspected product. Use a separate
workspace bundle for each target. Neither these stars nor their high catalog S/N
are a representative population sample. Catalog S/N is not necessarily the S/N of
the chosen zoom and was not an `SNR` keyword in the inspected FITS headers.

Other useful regions include H-alpha and the Ca II infrared triplet; change the
window only after checking actual coverage. Telluric absorption, continuum errors,
instrumental ripples, and stellar variability can affect interpretation. This helper
does not fit abundances, infer stellar parameters, or automatically identify every dip.

## Evidence bundle and rights

The helper writes `catalog.csv`, unmodified `spectrum.fits`, `cache.json`, a full
native-grid `spectrum.csv`, a selected-window `spectrum.png`, and `provenance.json`.
CSV columns retain original intensity and variance, derived sigma, raw mask byte,
and numeric-validity status. Provenance records the chosen row, URLs, timestamps,
SHA256 hashes, selected header fields, conventions, limitations, and software versions.
Verified cache replay is offline and retains the original retrieval time. A corrupt
or mismatched cache is refused; use a new output directory rather than overwriting
unrelated files. `--refresh` deliberately replaces only a recognized verified bundle's
release files and derived outputs; it is not a general overwrite switch.

Cite Strassmeier, Ilyin & Weber (2018), **PEPSI deep spectra. II. Gaia benchmark stars
and other M-K standards**, A&A 612, A45,
[doi:10.1051/0004-6361/201731633](https://doi.org/10.1051/0004-6361/201731633), and
credit the PEPSI/AIP archive when presenting these data. The
[author-posted paper](https://arxiv.org/html/1712.06967) describes public data use.
No dataset-specific open license was identified in the inspected release/header;
the skill's MIT license covers its code and instructions, **not the downloaded
spectra**. Do not bundle archive data into the skill or claim additional redistribution
rights from the software license.
