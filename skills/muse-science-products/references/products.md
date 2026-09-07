# Verified MUSE science-products contract

Verified 2026-09-06 by bounded HTTPS downloads, FITS inspection, and the two release papers. These are published derived products. Replotting them does not constitute a new MUSE reduction, temperature/density inference, line fit, or extinction measurement.

## Sources and citation

- [AIP release](https://data.aip.de/projects/musescience.html), dataset [DOI 10.17876/data/2023_3](https://doi.org/10.17876/data/2023_3). DataCite metadata declares **Creative Commons CC0 for data**; this does not replace attribution to the authors and scientific method.
- Orion: Weilbacher et al. (2015), *A MUSE map of the central Orion Nebula (M 42)*, A&A 582, A114, [paper](https://arxiv.org/html/1507.00006), especially Sections 3.4, 4, 5.4, 5.5 and Figures 25, 26, 28.
- Antennae: Weilbacher et al. (2018), *On the Origin of Diffuse Ionized Gas in the Antennae Galaxy*, A&A 611, A95, [paper](https://arxiv.org/html/1712.04450), especially Sections 3, 4.1, 5.1–5.3 and Figures 4, 5, 10.

## Pinned products

Use HTTPS with certificate verification. All paths below are relative to `https://s3.data.aip.de:9000/data.aip.de/musesc/`. Some landing-page links omit the `data.aip.de` bucket; the corrected paths below were downloaded successfully with normal TLS verification. The helper pins the corresponding byte count and SHA256 rather than trusting a filename alone.

| Product path | Bytes | HDU and shape | Scientific quantity |
|---|---:|---|---|
| `orion/orion_m42/tem_siii_a_median55.fits` | 10,434,240 | PRIMARY, 1476×1766 | Published [S III] electron temperature, K; 5×5-pixel median filter |
| `orion/orion_m42/den_sii_median33.fits` | 10,431,360 | PRIMARY, 1476×1766 | Published [S II] electron density, cm⁻³; 3×3-pixel median filter |
| `orion/orion_m42/emhalpha_velos.fits` | 20,856,960 | PRIMARY, 1476×1766 | Hα Gaussian-centroid velocity, km s⁻¹, solar-system barycentric |
| `antennae/antennae_diffuse_data/Antennae_Center_Halpha_gaussfit.fits` | 7,398,720 | PRIMARY, 973×950 | Hα flux from single-Gaussian fits; native values ×10⁻²⁰ erg s⁻¹ cm⁻² per spaxel |
| `antennae/antennae_diffuse_data/Antennae_Center_Halpha_velo_SN30.fits` | 3,700,800 | PRIMARY, 973×950 | Hα Gaussian-centroid barycentric velocity, km s⁻¹; spatially binned to S/N≈30 |
| `antennae/antennae_diffuse_data/Antennae_Center_measurements.fits` | 250,560 | HII_REGIONS, 551 rows | Central merger H II region measurements; 52 columns |
| `antennae/antennae_diffuse_data/Antennae_South_measurements.fits` | 43,200 | HII_REGIONS, 55 rows | Southern tidal-tail H II region measurements; same schema |

Image shapes are NumPy `(rows, columns)`, not FITS `(NAXIS1, NAXIS2)`. All listed image files contain a single primary image; tables have an empty primary HDU and the named binary-table extension.

```text
tem_siii_a_median55.fits 5496565ec1b2e104066899fcf26deb40810243624c03a85547ba46b1d7aff67b
den_sii_median33.fits 48c67f1c9e9a2e67962ae8bdaac045c6b6592a52a71790efb54aa34ed5c873c5
emhalpha_velos.fits 554a18418be0ba26e7a83bf1bb3fc1e605fdd1d3ca45090e2f70c49cabb50f3f
Antennae_Center_Halpha_gaussfit.fits 3c2c68c010bf3e7538d0120682de6352b62915ef951201fc87394e4dbf9125ea
Antennae_Center_Halpha_velo_SN30.fits 5ee1a4f25d5dececafa3632089f5f5e936e41f6569434060f7f45cbc4475e5c5
Antennae_Center_measurements.fits b8b276553d04861b25a2d7248b642fa5c59f5e1bea80aca497028b92eee4f6d1
Antennae_South_measurements.fits 5a38340d75b73bb99f35ff63b39476adf41bf51c35eac0b1875c265230136b03
```

## Coordinates and map rendering

All listed images use celestial `RA---TAN`, `DEC--TAN` WCS in degrees. The CD matrix is diagonal with approximately `(-5.55555555555556e-5, +5.55555555555556e-5)` degrees per pixel: 0.2 arcsec sampling, increasing RA toward the left. Preserve the celestial WCS with `origin="lower"`; do not add a manual RA reversal on top of the WCS. These headers do not explicitly declare `RADESYS`/`EQUINOX`; label the axes RA and Dec without inventing a reference-frame assertion.

Orion: `CRPIX=(1606.5,724.5)`, `CRVAL=(83.780566,-5.396172)`. The three maps share this grid. Antennae central: `CRPIX≈(743.368759115,487.157972265)`, `CRVAL≈(180.463933342,-18.8808681183)`. Tiny rounding differences between its image headers are not a real astrometric displacement; compare numerically with an appropriate tolerance. Never assume a southern map uses the central WCS.

For the Gaussian-fit Antennae Hα map, physical surface brightness is `native_value * 1e-20 / pixel_area_arcsec2`; here the projected pixel area is approximately 0.04 arcsec². Label the map explicitly as Hα surface brightness in erg s⁻¹ cm⁻² arcsec⁻², or retain the native per-spaxel flux units and say so. The map is not corrected for internal extinction. Gaussian fits can overestimate flux at very low S/N; apparent faint structure and map sums need the paper's validation and background treatment.

Use a transparent/bad-pixel colour for masked pixels. Finite positive physical-map pixels may be displayed with robust percentile limits, but those limits are a visual stretch, not quality cuts. Record the limits and any additional masking. Do not infer that saturating the colour bar removes outliers from the underlying data.

## Orion masks and interpretation

- Temperature has **no `BUNIT`**; K is established by the release and paper. The pinned smoothed map has 46,582 exact zeros and no NaNs. Mask nonfinite and nonpositive values. Its median including the zero fill is 8569.7 K.
- Density has **no `BUNIT`**; cm⁻³ is established by the release and paper. The pinned map has 47,217 negative pixels, including the `-1000` fill value, and no NaNs. Mask nonfinite and nonpositive values. Its median including fill is 1220.8 cm⁻³.
- Velocity has `BUNIT=km/s`, 36,274 exact zero pixels, and no NaNs. The zeros are predominantly footprint fill (the last image row and last column are entirely zero); mask exact zeros for this pinned visualization, record the rule, and do not generalize it to arbitrary velocity products. **Retain negative velocities**: blueshifted gas is meaningful. The native finite range is approximately −457 to +459 km s⁻¹; extreme pixels can be contaminated/failed fits. A robust display stretch is not validation of those extremes.
- Use the release's smoothing as provided and label it; do not silently smooth again. The nominal smoothing widths are 1 arcsec for temperature and 0.6 arcsec for density, not a claim about the effective PSF or independent resolution.
- The temperature and density were derived by PyNeb cross-iteration of extinction-corrected line ratios. [S III] temperature uses 6312/9069, cross-iterated with [S II]; [S II] density uses 6731/6716. They are model-dependent luminosity-weighted gas diagnostics, not stellar effective temperatures or a direct count of particles.
- The authors did not separate stellar continua or mask all stars in these physical maps. Strong compact features near stars, Trapezium artifacts, the Dark Bay temperature excess, and mosaic-grid structure require caution. Do not claim every sharp feature is a new shock or hot knot.
- Hα velocities are barycentric single-line centroids, not transverse speed, systemic-relative speed, dispersion, or line width. The paper finds red-wavelength systematic deviations generally below about 3 km s⁻¹; the modest MUSE resolving power does not imply every centroid is uncertain by the full instrumental width.

## Antennae table contract and ECDF

`HII_REGIONS` has 52 columns. `ID`, `XPEAK`, `YPEAK`, `NPIX`, and `NCluster` are 64-bit integers (`K`); other columns are double precision (`D`). `XPEAK`/`YPEAK` have unit `pixel`, not degrees. This release check did not establish their indexing origin, so do not overlay them as RA/Dec or assume zero-/one-based indexing without a separate image-alignment check. `ID` is a within-field identifier; retain the field when combining tables.

Useful groups:

| Columns | Meaning and caveat |
|---|---|
| `HALPHA`, `HBETA`, `OIII5007`, `OI6300`, `SII6716`, `SII6731`, `SIII9068`, plus each `E_...` | Measured fluxes and quoted fit errors. FITS TUNIT is bare `erg/s/cm**2`, but numerical values require the missing ×10⁻²⁰ factor; ratios are unaffected. |
| `HA_HB`, `E_HA_HB`, `C_HBETA` | Balmer decrement, its error, and inferred logarithmic extinction. Zero extinction is valid; do not discard it. |
| `F_H1r_6563A`, `F_H1r_4861A`, `F_O3_5007A`, `F_O1_6300A`, `F_S2_6716A`, `F_S2_6731A`, `F_S3_9069A`, plus each `E_...` | Published corrected flux fields. Their quoted error fields must not be assumed to include full extinction uncertainty. |
| `OIII_SII`, `SIII_SII`, `OIII_OI`, plus each `E_...` | Published corrected diagnostic ratios; inspect errors and detections before interpreting. |
| `LHa_obs`, `LHa_cor` | Observed and internally extinction-corrected Hα luminosity, already in erg s⁻¹. These are **linear**, not logarithmic values. |
| `Q_H0`, `E_Q_H0`, `NCluster`, `NLyC_GALEV`, `fesc_GALEV`, `E_fesc_GALEV`, `NLyC_SB99`, `fesc_SB99`, `E_fesc_SB99` | Ionizing-photon and population-model results; absent matches can have zero counts/rates and NaN escape fractions. Do not call these direct observations of escaping photons. |

The luminosities assume **22 Mpc** and correction via the Balmer decrement. Direct inspection confirms, for every row in both fields, `LHa_obs / (4*pi*(22 Mpc in cm)^2*HALPHA) ≈ 1e-20`, and the same relation for `LHa_cor/F_H1r_6563A`. Thus the missing raw-flux scale is empirically reconciled against the documented distance and published luminosities. Prefer the published luminosity columns; do not apply the ×10⁻²⁰ factor to `LHa_cor` itself.

In all 606 rows, `E_H1r_6563A == E_HALPHA`, even when the corrected flux is increased. There is no `E_LHa_cor` column. Do not construct a purported full corrected-luminosity uncertainty from that unchanged error or `E_Q_H0`.

For a descriptive comparison, plot an ECDF of `LHa_cor` with a logarithmic luminosity axis in erg s⁻¹ and y-axis cumulative fraction, as the helper does. Equivalently, transform to `log10(LHa_cor)` and label a linear axis `log10[L(Hα)corrected / (erg s⁻¹)]`; do not apply both logarithms. A conservative demo selection requires finite, positive `LHa_cor`, `HALPHA`, `E_HALPHA`, `HBETA`, `E_HBETA` and both Balmer-line flux/error ratios ≥3. The pinned release retains **549/551 central** and **52/55 southern** rows. Report both source and retained counts. This is an additional, explicit visualization quality selection, not the authors' original sample definition or a completeness correction. Comparing all finite positive published luminosities instead retains 551 and 55; state that choice explicitly if used.

The detected regions are dendrogram leaves, after artifact/contaminant screening. Different area, background, surface-brightness sensitivity, seeing, blending, and selection affect the two fields. The ECDF compares the released selected samples; it alone does not establish a complete population luminosity function, star-formation rate, age, or significance of an environmental effect. The paper's population conclusion uses its own sampling experiment. The source maxima provide a useful sanity check: corrected luminosity approximately 3.89×10⁴⁰ erg s⁻¹ in the central field and 1.56×10³⁸ erg s⁻¹ in the south.

The central velocity file is **Voronoi-binned to Hα S/N≈30**, not an image selected by a hard per-pixel S/N≥30 threshold. It has 254,828 NaNs and 669,522 finite velocities (about 1388–1982 km s⁻¹); use its finite footprint. Bins repeat a fitted value over multiple pixels, so pixels are not independent velocity measurements. The paper's 1705 km s⁻¹ systemic value was a fitting first guess, not a subtraction already applied to this map.

## Useful extensions and exceptions

The release includes unsmoothed Orion physical maps, [N II]/[Cl III] diagnostics, extinction, individual emission-line fluxes, line ratios, and relative-velocity maps. Select by the scientific question and inspect that product's units, mask, WCS, dereddening, and any sky correction before use. In particular, [O I] has alternative partial sky corrections; a velocity difference is not an absolute barycentric velocity; `c_Hβ` is dimensionless logarithmic extinction, not a magnitude in an arbitrary band. Do not reuse the default product's numerical sentinel rule without validation.

The Antennae southern flux maps are separate fields and cannot be aligned using the central WCS. **The narrow-band `Antennae_Center_Halpha_filter.fits` normalization remains unresolved here**: despite the same `10**(-20)*erg/s/cm**2` BUNIT, its sum times 10⁻²⁰ is approximately 9.59×10⁻¹³, while the Gaussian map gives approximately 9.80×10⁻¹² erg s⁻¹ cm⁻² and the paper's integrated estimates are of order 9×10⁻¹². These methods need not agree exactly, but this order-of-magnitude mismatch prevents treating the narrow-band values as calibrated integrated flux without checking filter normalization. Use the verified Gaussian-fit map for the default physical-unit figure; do not silently rescale the narrow-band map to force agreement. The narrow-band snapshot was 3,703,680 bytes, SHA256 `9b043b4e67c9e5bebfca009c845e285f0d2fbd41c974a3e84c13a7b35cfc2dfb`.

Full Orion cubes are 75/110 GiB and the complete map ZIP is about 577 MiB. The default workflows do not need them. Requests for fresh line fits, variance propagation, cube extraction, stellar-continuum subtraction, gas diagnostics, or new physical inference need a separately scoped analysis and appropriate cube/software resources.
