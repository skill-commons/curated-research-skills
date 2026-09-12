# PARSEC model contract and retrieval

This adapter obtains real stellar-evolution isochrones from the official
[CMD 3.9 service](https://stev.oapd.inaf.it/cgi-bin/cmd_3.9), using nonrotating
PARSEC 1.2S and the service's Gaia EDR3 Vega G, BP and RP passbands. Gaia DR3
retains the EDR3 mean photometry, so these are appropriate for DR3 mean-flux CMDs;
they are not Gaia DR2 passbands or XP synthetic photometry. See the
[Gaia DR3 summary](https://www.cosmos.esa.int/web/gaia/dr3) and
[photometric-system list](https://stev.oapd.inaf.it/cmd_3.9/photsys.html).

## Exact model choice

The serialized query in `model.json` is authoritative. Fixed settings include:

| Parameter | Setting |
| --- | --- |
| CMD / tracks | `cmd_version=3.9`, `track_parsec=parsec_CAF09_v1.2S` |
| Composition | Scaled solar; initial `MH=[M/H]` supplied to the service |
| Subsequent tracks | `track_colibri=no`, `track_postagb=no` |
| Photometry | `photsys_file=YBC_tab_mag_odfnew/tab_mag_gaiaEDR3.dat` |
| Bolometric corrections | `photsys_version=odfnew` = **OBC**, despite the filter path's `YBC_` prefix |
| Dust / RGB mass loss | No circumstellar dust; Reimers efficiency `eta_reimers=0.2` |
| Interstellar extinction | Supplied `A_V`; constant solar coefficients, Cardelli+O'Donnell law, `R_V=3.1` |
| Output | Isochrone table, preserving all emitted mass rows and phase labels |

On 2026-09-09, the live service rejected the advertised Gaia EDR3 selection
with both `YBC` and `YBCnewVega` bolometric corrections. OBC produced valid
tables. This adapter deliberately requests OBC and checks the returned header;
it does not silently substitute another model or label these data as YBC.
The [current form](https://stev.oapd.inaf.it/cgi-bin/cmd) explains that OBC
uses constant extinction coefficients, whereas YBC can use stellar parameters.
PARSEC 2.0 is a different model family/version with a different output layout and
its service extinction feature is currently unavailable. A future adapter must
validate that choice separately.

**Use the returned magnitudes, with no second extinction correction.**
`g_abs`, `bp_abs` and `rp_abs` already contain each model's `A_V`; only the
distance modulus is added when comparing to apparent photometry. In a live
check, changing `A_V` from 0 to 0.20 left all intrinsic stellar rows identical
and changed each band's magnitudes by a nearly constant amount, with 0.001 mag
rounding. The illustrative G2V coefficients displayed in the output HTML
did not reproduce these shifts. They are not the applied correction contract.

Constant solar extinction is a limitation for broad Gaia bands, especially
across large temperature ranges or appreciable extinction. A fit must state
this approximation, examine sensitivity to `A_V`, and avoid presenting its
formal age range as including extinction-law or stellar-atmosphere systematics.

## Retrieval and replay

After the skill's environment setup, from the analysis workspace:

```bash
.venv/bin/python /absolute/path/to/cluster-cmd-isochrone-fit/scripts/fetch_parsec.py \
  --out "$PWD/parsec-grid"
```

The default grid covers log10(age/yr)=9.00–10.00 in 0.05 dex steps,
initial `[M/H]`=−0.20…+0.20 in 0.10 dex steps, and
`A_V`={0,0.05,0.10,0.1271,0.15,0.20}mag: **630 model curves**.
The 0.1271 value supports the independently constrained M67 demonstration;
it does not constrain age or serve as a universal cluster reddening.
Specify other finite regular age/metallicity grids and explicit extinction
values when required:

```bash
.venv/bin/python /absolute/path/to/cluster-cmd-isochrone-fit/scripts/fetch_parsec.py \
  --out "$PWD/other-grid" \
  --log-age-min 8.5 --log-age-max 9.5 --log-age-step 0.05 \
  --mh-min -0.5 --mh-max 0.0 --mh-step 0.1 --av 0,0.1,0.2
```

The adapter caps a query at 600 age/metallicity pairs, the bundle at 3000
models, and extinction at 16 distinct values within 0–1 mag. The service's form
advertises a higher 10,000-isochrone maximum. Each response is limited to 32 MiB;
the combined bundle is limited to 256 MiB. Requests run sequentially. The helper
does not submit an unbounded survey grid or repeatedly poll the service.

CMD can omit the final requested age through endpoint arithmetic. The helper
pads the submitted upper limit by half a grid step, within service bounds,
then requires exactly the desired output coordinates. It rejects missing,
extra or ambiguous ages/metallicities, invalid columns, nonfinite data,
decreasing initial masses, unexpected phase labels, and a missing final
terminator. The intended grid and actual submitted endpoints are both saved.
The service also accumulates small coordinate-rounding errors: a live refined
grid printed ages 9.68001 through 9.75001 for nominal 9.68 through 9.75.
The adapter matches coordinates within 0.0000201 dex, records that tolerance,
and writes the nominal requested coordinate in the normalized CSV. Original
printed coordinates remain in the raw data. This small tolerance never
permits a missing, extra, or ambiguously matched grid curve.

The runtime uses Python's standard library and `curl`, with TLS verification,
bounded timeouts, no redirects, no curl configuration, no proxy, and no
credential files. On macOS it selects `/usr/bin/curl` to use system trust;
elsewhere it selects `curl` from PATH. A certificate failure is an error,
never a reason to disable verification or switch to HTTP.

CMD's generated output link expires after two hours, according to the live
response page. The helper immediately saves raw bytes, response HTML, query,
retrieval time and SHA256. It normalizes data into `model.csv` with companion
`model.json`. Data remain outside the installed skill. Use a new or empty
output directory; an existing bundle must pass integrity checks.

Replay using the same grid arguments and `--offline`:

```bash
.venv/bin/python /absolute/path/to/cluster-cmd-isochrone-fit/scripts/fetch_parsec.py \
  --out "$PWD/parsec-grid" --offline
```

Replay makes no network requests or writes: it verifies the complete file
inventory and hashes, reparses all raw sources, and requires the resulting
CSV bytes to match. The old temporary download URL is provenance, not a
durable archive URL. Fresh service requests can differ as upstream models
change; preserve the reviewed bundle for reproducibility.

## Columns and interpretation

Every raw source has these 14 whitespace-separated columns:

```text
Zini MH logAge Mini int_IMF Mass logL logTe logg label mbolmag Gmag G_BPmag G_RPmag
```

`Zini` is initial metal mass fraction; `MH` is initial model `[M/H]` in dex;
`logAge` is base 10 age in years. `Mini` and `Mass` are initial/current masses
in solar masses; `logL` is log10 luminosity in solar units; `logTe` is log10
effective temperature in kelvin; `logg` uses cgs gravity. Magnitudes are
absolute Vega magnitudes at the requested extinction. Do not treat row
density or the cumulative `int_IMF` column as observed membership likelihoods.
The [CMD help](https://stev.oapd.inaf.it/cmd_3.9/help.html) links the underlying
table documentation and describes phase labels.

The normalized schema is:

```text
model_id,log_age,mh,av,mini,label,g_abs,bp_abs,rp_abs
```

There is one `model_id` per age/metallicity/extinction combination. The adapter
retains all rows, source order and approximate phase labels: 0 PMS, 1 main
sequence, 2 subgiant branch, 3 red giant branch, 4–6 core-helium burning,
7 early AGB. Initial masses can repeat exactly. Fitting must respect phase
boundaries and ignore zero-length CMD segments, without bridging over removed
rows. Repeated masses with distinct CMD coordinates must remain. No white dwarfs,
blue stragglers, interacting binaries or empirical age
formula are added to the models. The service's
[FAQ](https://stev.oapd.inaf.it/cmd_3.9/faq.html) explicitly cautions that
phase labels are approximate and can contain misclassifications.

PARSEC's `[M/H]=0` produces initial `Zini≈0.01471`, not exactly the current
solar metal fraction. Keep the model's helium enrichment relation and
initial-metallicity convention distinct from measured surface `[Fe/H]`;
do not replace one by the other without stating the scaled-solar assumption.

## References and data rights

Preserve the raw source headers and cite the actual model/photometry choices:

- [Bressan et al. 2012](https://doi.org/10.1111/j.1365-2966.2012.21948.x), PARSEC.
- [Chen et al. 2014](https://doi.org/10.1093/mnras/stu1605), low-mass models;
  [Chen et al. 2015](https://doi.org/10.1093/mnras/stv1281) and
  [Tang et al. 2014](https://doi.org/10.1093/mnras/stu2029), additional tracks.
- [Marigo et al. 2008](https://doi.org/10.1051/0004-6361:20078467) and
  [Girardi et al. 2008](https://doi.org/10.1086/588526), OBC framework/extinction.
- [Riello et al. 2021](https://doi.org/10.1051/0004-6361/202039587), Gaia EDR3 photometry.

The service provides public research output. No explicit redistribution
license was found on the inspected CMD form, help, FAQ or photometry pages.
The skill's MIT license covers its code and instructions; it does not
relicense downloaded model grids. No model tables are bundled in the skill.
