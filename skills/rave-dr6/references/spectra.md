# RAVE DR6 1-D spectra

Read this reference when the task needs an individual RAVE spectrum, a spectrum-quality
selection, FITS interpretation, uncertainty display, or spectral-line annotation. For
catalog-only work, the main skill is sufficient.

## Access contract

RAVE DR6 is hosted by AIP at `https://www.rave-survey.org/`. The public TAP endpoint is:

```text
https://www.rave-survey.org/tap/
```

`ravedr6.dr6_spectra` is an observation-level index. It contains `rave_obs_id`, DOI,
preview-plot URL, FITS URL, and field identifier. It does **not** contain wavelength or
flux arrays. Query the table for a released URL rather than constructing a path: spectrum
identifiers are not continuous, and only observations that passed SPARV have a released
file.

The two identifiers have different meanings:

- `rave_obs_id` identifies one observation and one released spectrum;
- `raveid` identifies a target, which can have multiple observations.

Official documentation:

- [RAVE DR6 overview](https://www.rave-survey.org/)
- [`dr6_spectra` metadata](https://www.rave-survey.org/metadata/ravedr6/dr6_spectra/)
- [Spectrum access](https://www.rave-survey.org/cms/documentation/spectra/)
- [DR6 survey, spectra, and radial-velocity paper](https://doi.org/10.3847/1538-3881/ab9ab9)

## Select a demonstration-quality observation

Quality cuts are part of the scientific or presentation choice, not universal RAVE
requirements. For a conventional, visually clean stellar spectrum, start with a bounded
query like this:

```sql
SELECT TOP 20
    o.rave_obs_id,
    o.ra_input,
    o.dec_input,
    s.hrv_sparv,
    s.hrv_error_sparv,
    s.snr_med_sparv,
    s.correlation_coeff_sparv,
    c.flag1_class,
    c.w1_class,
    f.spectrum_fits,
    f.spectrum_png
FROM ravedr6.dr6_obsdata AS o
JOIN ravedr6.dr6_sparv AS s
  ON o.rave_obs_id = s.rave_obs_id
JOIN ravedr6.dr6_classification AS c
  ON o.rave_obs_id = c.rave_obs_id
JOIN ravedr6.dr6_spectra AS f
  ON o.rave_obs_id = f.rave_obs_id
WHERE s.snr_med_sparv > 60
  AND s.hrv_error_sparv < 2
  AND s.correlation_coeff_sparv > 20
  AND c.flag1_class = 'n'
  AND c.w1_class > 0.9
ORDER BY s.snr_med_sparv DESC
```

`snr_med_sparv` is the a posteriori S/N estimate and is preferable to the a priori
`snr_sparv` value for this selection. The classification cuts favor spectra whose dominant
automated class is normal (`n`); `w1_class` is a classification weight, not a calibrated
probability. These are pragmatic demonstration cuts, not a general science-quality
definition. If atmospheric parameters are part of the story, join `dr6_madera` separately
and require `algo_conv_madera = 0`; a value of `1` means that pipeline did not converge.

The bundled helper defaults to the vetted observation `20100313_0823m14_113`. It has a
normal classification with weight 1, median S/N about 123, a Tonry-Davis correlation
coefficient about 99, and a released FITS product. Use it for smoke testing; discover and
justify a different object when the scientific story requires one.

## FITS structure and wavelength

Released files are small FITS image products, not binary tables. The verified structure
is:

- an empty primary HDU carrying `ROI` (the RAVE observation ID), DOI, and license;
- a one-dimensional float image extension named `SPECTRUM`;
- a matching one-dimensional float image extension named `ERROR`.

Validate the structure instead of assuming fixed HDU numbers. Require both arrays to be
one-dimensional, equal in length, finite where plotted, and associated with the requested
`ROI`.

Construct the wavelength at zero-based pixel index `i` from the spectrum-extension header:

```python
wavelength = CRVAL1 + (i + 1 - CRPIX1) * CDELT1
```

The released products use Angstrom and a linear grid, but their starting wavelength,
spacing, and number of samples vary slightly. Do not hard-code 8410–8795 Angstrom or a
fixed array length; those values describe the nominal survey coverage rather than every
file.

The DR6 processing description says SPARV places stellar features in the zero-velocity
frame, and line positions in released products are consistent with a stellar-rest air-
wavelength scale. The FITS headers do not declare `SPECSYS`, so retain that provenance
caveat. Do not apply `hrv_sparv` a second time; retain it as measurement metadata. When
comparing to a product in vacuum wavelengths, convert one wavelength convention
explicitly before overlaying line positions.

## Flux, errors, and line markers

The spectrum is continuum-normalized, not absolutely flux-calibrated. The DR6 paper calls
the `ERROR` extension a relative error spectrum: a value of `0.01` represents an expected
approximately Gaussian 1% flux error at that wavelength bin. It is not inverse variance.
Plot the relative error directly, or convert it to normalized-flux uncertainty with
`sigma_flux = abs(normalized_flux) * relative_error` before drawing an uncertainty band.
The reported errors include several instrumental contributions and are smoothed over
three pixels, so neighboring values are correlated. Do not relabel them as physical flux
density.

Useful Ca II triplet rest wavelengths in air are approximately:

```text
8498.018 Angstrom
8542.089 Angstrom
8662.140 Angstrom
```

Label lines only when they support the requested explanation. RAVE also contains Paschen
lines, metal lines, diffuse interstellar absorption, residual sky features, and unusual
stellar spectra; not every dip is Ca II.

## Cache and provenance

For a repeatable demo, cache the smallest complete evidence bundle:

```text
query.adql
<rave_obs_id>.fits
<rave_obs_id>.csv
<rave_obs_id>.png
provenance.json
```

Record the TAP endpoint, exact query, FITS URL and DOI, retrieval time, observation ID,
HDU names, wavelength-header values, sample count, normalization, error interpretation,
software versions, and target-selection criteria. Read back the FITS, CSV, and image
before reporting success. Keep a vetted cache for live demonstrations, while allowing a
fresh bounded retrieval when network conditions permit.

## Operational and scientific caveats

- Validate that the FITS URL remains on the expected HTTPS RAVE origin before downloading.
- Use a size bound and an atomic local write; never upload or modify archive objects.
- Residual fringing, imperfect sky subtraction, continuum artifacts, emission, binaries,
  and hot-star Paschen lines can complicate interpretation.
- Spectra from edge fibers 1–2 and 145–150 are more often affected by instrumental
  problems; this is a selection consideration, not an automatic rejection rule.
- Do not infer a population from a hand-picked high-S/N demonstration source.
- Verify the live schema and product because row counts and service behavior can change.

## Bounded helper

From the installed skill directory, run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/rave_spectrum_demo.py \
  --rave-obs-id 20100313_0823m14_113 \
  --out outputs/rave-dr6/spectrum-demo
```

The helper queries only the requested observation, permits downloads only from its exact
official FITS path, enforces a 10 MiB response bound, validates the FITS structure and
wavelength metadata, and writes the evidence bundle above. Re-running uses the validated
local FITS cache; pass `--refresh` to retrieve it again deliberately.
