# CoSEE-Cat DR1 access and interpretation

## Sources and release boundary

- [Catalogue home](https://coseecat.aip.de/)
- [DR1 metadata, download and CC0 declaration](https://coseecat.aip.de/metadata/coseecat_dr1/)
- [Table and plot documentation](https://coseecat.aip.de/cms/documentation/data-description/)
- [Scripted access](https://coseecat.aip.de/cms/documentation/scripted-access/)
- [Warmuth et al. 2025, A&A 701 A20](https://doi.org/10.1051/0004-6361/202554830)
- [Paper text: methods, selection and caveats](https://arxiv.org/html/2509.03250v1)

The September 2026 access check found DR1, 303 main-table events, with event dates
2020-11-17 through 2022-12-30. The release covers observations through the end of
2022; do not mistake a newer poster's accumulated event count for a published DR2.
The API exposes numerical event summaries, not original EPD energy-resolved time
series, RPW spectral arrays, EUI image cubes, or STIX count/event products. Separate
instrument archives and their own calibration/data contracts are needed for those.

## Tables and discovery

Endpoint: `https://coseecat.aip.de/tap`.

| Table | Role | Observed rows / distinct event IDs |
|---|---|---:|
| `coseecat_dr1.main` | EPD, STIX, EUI, RPW summaries; connectivity | 303 / 303 |
| `coseecat_dr1.overview` | Compact selection of event information | Discover/count before use |
| `coseecat_dr1.ip_context` | Solar-wind and magnetic structures near the event | 303 / 303 |
| `coseecat_dr1.cme` | Potential CME associations, including primary/secondary Metis fields | 62 / 62 |

Join on `event_id`, with a **left join** when retaining all electron events. No CME
row does not prove no CME occurred. Check key uniqueness before joins in a future
release; the CME table currently places multiple candidates in suffixed columns.

Anonymous synchronous access worked; the website's token examples are not evidence
that a token is required for these public reads. Do not invent or request credentials
unless a real access failure establishes that requirement. Keep requests small.

```python
import pyvo
service = pyvo.dal.TAPService("https://coseecat.aip.de/tap")
print(list(service.tables.keys()))
for column in service.tables["coseecat_dr1.main"].columns:
    print(column.name, column.unit, column.description)
```

The generic `TAP_SCHEMA.columns` ADQL query failed in the access check with
`Schema tap_schema not found`; `service.tables` succeeded. Use that metadata route
or the website rather than treating a metadata failure as catalogue unavailability.
The service also returned HTTP 500 when a synchronous request included `MAXREC`;
the helper omits it and uses ADQL `TOP`, checking both TAP status and the row cap.
The bundled helper adds response bounds/timeouts; bare PyVO examples here illustrate
custom query syntax, not its full network containment.

## Queries for live reviewer questions

Include `event_id` in any selection that will lead to DataLink discovery. For example,
find a small set of high-confidence STIX-associated events with a primary EUV jet:

```python
query = """
SELECT TOP 5 event_id,event_date,epd_compo,stix_epd_qual,eui_type1
FROM coseecat_dr1.main
WHERE stix_epd_qual=1 AND eui_type1 LIKE '%jet%'
ORDER BY event_id
"""
result = service.run_sync(query, language="ADQL")
print(result.to_table())
```

Projection may affect whether a response contains a usable service descriptor.
For one selected event, a fresh `SELECT TOP 1 *` query is the tested DataLink route.
Keep the ID numeric and validate it as ten decimal digits before interpolating it;
do not paste arbitrary user text into SQL. General free-text criteria need proper
query construction and explicit selected columns.

## DataLink contract and plots

```python
result = service.run_sync(
    "SELECT TOP 1 * FROM coseecat_dr1.main WHERE event_id=2011171841",
    language="ADQL",
)
for links in result.iter_datalinks(preserve_order=True):
    for link in links.bysemantics("#preview-plot"):
        print(link["description"], link["access_url"])
```

Follow the returned service descriptor; the observed endpoint is
`https://coseecat.aip.de/datalink/links`. The human-facing `/datalink/<event_id>/`
page is HTML, not that VOTable API. Select by semantics **and** description, not
position or constructed filenames. The helper matches the observed semantics
directly to avoid an extra vocabulary lookup during offline processing.

| Description | What the published diagnostic shows |
|---|---|
| `Overview` | EPD particle profiles/dispersion, RPW radio spectrum, STIX X-rays, spacecraft context |
| `EUI-STIX` | EUV disc, flare positions, modelled magnetic footpoint, STIX reconstructed source |
| `Anisotropy` | Directional EPT intensities, pitch-angle coverage, anisotropy and uncertainty |
| `RPW-STIX` | Radio dynamic spectra and X-ray profiles with timing estimates |
| `Interplanetary Context` | Solar wind, magnetic field and pressure around particle onset |

PNG rows currently advertise `application/png`, not the conventional `image/png`.
Accept those two types only after checking PNG bytes/decodability. Each may be
absent; a present image does not imply all panels contain usable measurements.
The helper preserves unknown DataLink rows but does not automatically retrieve
unrecognised plot types. Error rows or ambiguous duplicate recognised descriptions
require inspection, not selection of the first one.

`#preview` identifies the HTML event viewer. `#auxiliary` includes external movies;
`content_type=video/mp4` helps distinguish them. EUI links are daily synoptic movies,
not guaranteed event-centred clips. The checked SIDC movie URL redirected into a
year subdirectory on the same host. Movie retrieval, redistribution permissions,
redirect checks, and playback caching need separate consideration. The helper
records links without fetching any movie or third-party resource.

For presentation, use one diagnostic at a time. The checked overview was 630×900,
while EUI–STIX was 1934×1026 and RPW–STIX 1500×875. Preserve originals; do not fabricate
spectral samples from raster pixels or claim super-resolution as measured detail.

## Numeric and scientific conventions

- `epd_tonset` and `epd_tpeak`: UTC particle arrival onset/peak at Solar Orbiter.
  Rise time is their difference; retain zero, report exclusions, do not clip long tails.
- `tsa_itime` and `vda_itime`: inferred release times, **already shifted forwards by
  Sun-to-Solar-Orbiter light propagation time** for comparison with electromagnetic
  observations. Do not shift again, mix with Earth-observed timing without a convention
  check, or identify apparent delay uniquely with delayed acceleration.
- `vda_time_unc`: fit standard deviation in minutes. `vda_lpath`, `vda_path_unc`,
  and `nominal_path`: AU. VDA assumes simultaneous release, first-arrival scatter-free
  propagation and a common effective path; the result is not an imaged field line.
- `stix_tpeak` is the main peak; `stix_tpeak_epd` is the peak chosen for the electron
  association. These may differ. Never substitute one silently. `goes_estim` is a
  STIX-derived estimate, while `goes_class` refers to a direct GOES classification.
- `epd_ipeak` is not background-subtracted; retain `epd_epeak` when comparing peak
  intensities because the reference energy is not necessarily identical for every row.
- `epd_compo` is the associated energetic **ion composition**: impulsive, gradual,
  intermediate, or missing. Do not infer it from electron profile duration.
- Actual `epd_aniso` values are `large`, `medium`, `small`, and blank, although one
  metadata description says `high`. It is a qualitative category, not the numeric
  anisotropy curve plotted in the PNG.
- `stix_epd_qual` actually takes 1/2/3 in DR1; the paper defines high/medium/low.
  The website metadata says 1–4: preserve that discrepancy, not a fabricated fourth
  observed category. Connectivity has its own 1–4 confidence scale.
- Text flags may be `Y`/blank, not Python Booleans. `eui_status` also contains `cor`,
  beyond the documented 1/0 values; keep it as a distinct unmodelled state. Empty
  strings, masked numeric values, zero, and measured non-detection are not equivalent.

For exploratory statistics, record the selected sample, unknowns and denominators.
Rise-time distributions are descriptive; events occur in correlated series and the
catalogue has observing/selection effects. A raw event-count histogram is not an
exposure-normalised rate, and a correlation is not a causal test.

The initial access calculation found 230 impulsive and 57 gradual events with usable
rise times (medians 7 and 19 minutes), plus 5 intermediate and 8 unknown-class events;
3 timestamp pairs were unusable. Treat these as regression evidence for that snapshot,
not values to insert into new outputs. The helper calculates all groups from the data.

## Rights and reproducibility

The DR1 metadata declares CC0 and requests citation of Warmuth et al. 2025. Retain
catalogue and paper attribution on both retrieved and new figures. Do not apply
that catalogue declaration to separately hosted movies without checking the source.
The skill's MIT license covers its code/instructions, not third-party products.

Cache original TAP and DataLink VOTables (units, masks and service descriptors are
lost in a plain CSV), downloaded plot bytes, exact ADQL, and retrieval time. Export
CSV only as an additional convenient analysis view. Verify hashes before offline
replay and keep source and derived-product hashes distinct. Numerical summaries
should agree across environments; direct pins alone do not promise cross-platform
pixel-identical Matplotlib rendering or complete transitive reproducibility.
