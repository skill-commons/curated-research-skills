# Gaia DR3 spectra at AIP

Use this reference for selecting, retrieving, or plotting individual Gaia spectra.
The sampled products need only the main skill's existing Python environment.

## Choose the product

| Product at AIP | Availability in `gaia_source` | Samples and units | Useful demonstration |
|---|---|---|---|
| `gaiadr3.rvs_mean_spectrum` | `has_rvs` | 2401 bins, 846–870 nm, step 0.01 nm; continuum-normalized flux | Ca II triplet and stellar absorption lines |
| `gaiadr3.xp_sampled_mean_spectrum` | `has_xp_sampled` | 343 bins, 336–1020 nm, step 2 nm; flux density in W m⁻² nm⁻¹ | Broad combined BP/RP spectral energy distribution |
| `gaiadr3.xp_continuous_mean_spectrum` | `has_xp_continuous` | Basis coefficients and associated uncertainty information | Reconstruction or custom sampling with GaiaXPy |

These flags describe different released products. An RVS radial velocity is available
for many more stars than a published RVS spectrum. Likewise, continuous XP availability
does not imply sampled XP availability. Coefficient index is not wavelength: use the
[GaiaXPy calibrator](https://gaia-dpci.github.io/GaiaXPy-website/) for physical flux versus
wavelength from continuous XP. Do not introduce GaiaXPy for already sampled spectra.

Keep `source_id` as a decimal string or a signed 64-bit integer throughout retrieval,
caching, and joins. Floating-point conversion can silently change a 19-digit identifier.
Verify the release and counterpart identity before any cross-survey comparison.

## Bounded retrieval and a vetted anchor

The default anchor is Gaia DR3 `5722622022989721600`. On 2026-09-05, its sampled XP
and RVS arrays were retrieved and checked at AIP. It has G about 6.21 and catalog
`rvs_spec_sig_to_noise` about 771. This is a bright demonstration source, not a
representative population sample.

It is also the Gaia counterpart listed by RAVE for observation `20100313_0823m14_113`.
That association supports a later comparison; it does not imply equal radial velocities,
resolution, observing epoch, or wavelength convention between the two surveys.

Check product flags and optional display metadata with one exact-source query:

```sql
SELECT TOP 1 source_id, phot_g_mean_mag, bp_rp,
    has_rvs, has_xp_sampled, has_xp_continuous,
    radial_velocity, radial_velocity_error, rvs_spec_sig_to_noise
FROM gaiadr3.gaia_source
WHERE source_id = 5722622022989721600
```

Retrieve only the required array product:

```python
import pyvo

service = pyvo.dal.TAPService("https://gaia.aip.de/tap/")
query = """SELECT TOP 1 source_id, flux, flux_error
FROM gaiadr3.rvs_mean_spectrum
WHERE source_id = 5722622022989721600"""
table = service.run_sync(query, maxrec=1).to_table()
```

For XP, substitute `gaiadr3.xp_sampled_mean_spectrum`. Keep the array-bearing result
as an Astropy table; flatten the single spectrum into per-pixel rows for CSV export.
Validate one returned source, one-dimensional numeric arrays of the expected length,
and matching flux/error units before interpreting it. An empty result means the
requested product was not returned; do not silently replace the source or product.
AIP's XP unit label `W.m**-2.nm**-1` can arrive as an Astropy `UnrecognizedUnit`.
The helper recognizes that documented spelling as W m⁻² nm⁻¹ without rescaling values;
other missing or incompatible XP units are rejected.

## TAP compatibility and Simple Join Service fallback

AIP's [spectrum-access page](https://gaia.aip.de/cms/services/spectra-access/) warns
that TAP may return array columns as truncated `char(32)` values. The tested anonymous
PyVO calls above returned complete numeric arrays on 2026-09-05. Rely on response
validation, not that behavior remaining unchanged. Never plot a truncated string or
invent the missing samples.

If that check fails, use AIP's
[Simple Join Service](https://gaia.aip.de/cms/services/simple-join-service/) through
the query interface or the
[scripted tutorial](https://gaia.aip.de/cms/services/spectra-download-tutorial/).
Choose the verified spectrum table, the explicit source selection, ECSV or VOTable,
and `INDIVIDUAL` or `COMBINED` output. SJS documents a maximum of 5000 source IDs;
one or a few is sufficient for a demo. The scripted tutorial uses a selection job ID
and an authenticated session; inspect current requirements rather than inventing an
anonymous download URL. Bound foreground polling, abort a timed-out job, and clean up
only jobs created by the current workflow. Validate the downloaded arrays just as for TAP.

The helper intentionally stops with this fallback guidance if TAP does not supply
valid arrays. It does not create SJS jobs automatically.

## Wavelengths, errors, and missing samples

Construct each complete grid before applying any data mask, using zero-based `i`:

```python
rvs_wavelength_nm = 846.0 + 0.01 * i  # i = 0, ..., 2400
xp_wavelength_nm = 336.0 + 2.0 * i    # i = 0, ..., 342
```

Both products supply `flux_error` as an uncertainty in the same units as `flux`.
For visualization, shade `flux ± flux_error`. Do not multiply these errors by flux
as in the RAVE fractional-error recipe. Preserve masked, nonfinite, or invalid-error
samples as gaps; never replace them with zero or shorten and rebuild the wavelength
grid. The anchor RVS spectrum has a masked final bin. Retain all 2401 wavelength
positions even when fewer bins are usable.

RVS is continuum-normalized, already in the stellar rest frame, and on a vacuum
wavelength scale. Do not shift it again using `radial_velocity`. Appropriate Ca II
vacuum markers are 850.035, 854.444, and 866.452 nm. RAVE's air wavelengths require
conversion before an overlay. Merely multiplying Angstrom by 0.1 changes the unit,
not the air/vacuum convention. Resolution matching is also needed for detailed
line-profile comparison. The RVS edges may have fewer contributing CCD spectra and
larger uncertainties; preserve that information in the error panel.

Sampled XP is an externally calibrated, low-resolution BP/RP spectrum. Label its axis
`Wavelength [nm]` and retain physical flux-density units. Do not transfer RVS's
stellar-rest-frame label or continuum normalization to XP. Sample spacing is not
spectral resolution. Avoid interpreting every small wiggle as a resolved absorption
line. Sampled XP uncertainties are correlated; per-bin shading is useful for display,
but a quantitative fit may require the continuous representation and covariance.

## Helper and evidence bundle

From the working project, use its isolated interpreter and the installed helper path:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /path/to/skill/scripts/gaia_spectrum_demo.py \
  --source-id 5722622022989721600 --product rvs --out outputs/gaia-anchor
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /path/to/skill/scripts/gaia_spectrum_demo.py \
  --source-id 5722622022989721600 --product xp --out outputs/gaia-anchor
```

Replace `/path/to/skill` with this skill's installed directory. Separate source/product
folders contain the retrieved array table (ECSV), exact ADQL query, per-pixel CSV,
two-panel spectrum/error PNG, and provenance JSON. The latter records the source,
product, endpoint, retrieval time, grid, units, missing samples, and cache identity.
Re-running uses the validated cache without contacting AIP; `--refresh` retrieves
the requested product again. Inspect the cached table, CSV, and image before claiming
the demo is ready. Retain the original retrieval time when replotting cached data.

## Scientific and archive sources

- [ESA sampled XP data model](https://gea.esac.esa.int/archive/documentation/GDR3/Gaia_archive/chap_datamodel/sec_dm_spectroscopic_tables/ssec_dm_xp_sampled_mean_spectrum.html)
- [ESA mean RVS data model](https://gea.esac.esa.int/archive/documentation/GDR3/Gaia_archive/chap_datamodel/sec_dm_spectroscopic_tables/ssec_dm_rvs_mean_spectrum.html)
- [RVS multi-transit processing](https://gea.esac.esa.int/archive/documentation/GDR3/Data_processing/chap_cu6spe/sec_cu6spe_procsteps/ssec_cu6spe_mta.html)
- [Sartoretti et al., Figure 1: vacuum Ca II wavelengths](https://www.aanda.org/articles/aa/pdf/2023/06/aa43615-22.pdf)
- [XP representation and covariance in fitting, section 2.1](https://academic.oup.com/mnras/article/524/2/1855/7209172)
- [AIP RVS metadata and data DOI](https://gaia.aip.de/metadata/gaiadr3/rvs_mean_spectrum/)
- [AIP sampled XP metadata and data DOI](https://gaia.aip.de/metadata/gaiadr3/xp_sampled_mean_spectrum/)
- [Gaia acknowledgements and citations](https://gaia.aip.de/cms/credit/)

Credit Gaia/ESA/DPAC and AIP when presenting the data. The skill's software license
does not replace the data release's attribution and licensing terms.
