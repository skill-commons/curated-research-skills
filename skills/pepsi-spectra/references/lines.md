# Identifying absorption lines in PEPSI atlas spectra

A normalized stellar spectrum invites naming its lines, and that is the step where
recalled wavelengths go wrong. This file supplies sourced identification references
for this skill. It is deliberately short: it states the group the atlas demo plots, and
otherwise gives a rule rather than a list.

## The rule

1. **Do not identify a feature from memory.** Identify only against the table below
   or against a line list you actually queried in that session.
2. **Report every other feature by measured wavelength and say it is unidentified.**
   "An unidentified line at 5165.41 Å" is a result. A guessed species is not.
3. **Match a reference wavelength to a line CORE**, never to the nearest grid sample
   (see *Quoting agreement* below).
4. **State the wavelength frame.** Atlas wavelengths are stellar-rest-frame air
   Angstrom; a reference list in vacuum wavelengths is not comparable without
   conversion.

## The Mg I b group (air wavelengths)

The strongest features between roughly 5160 and 5190 Å.

| Fraunhofer | Air wavelength (Å) | Species | Transition | Source |
|---|---|---|---|---|
| b1 | 5183.604 | Mg I | 3s3p ³P°₂ – 3s4s ³S₁ | [NIST Mg I](https://physics.nist.gov/PhysRefData/Handbook/Tables/magnesiumtable3_a.htm) |
| b2 | 5172.684 | Mg I | 3s3p ³P°₁ – 3s4s ³S₁ | [NIST Mg I](https://physics.nist.gov/PhysRefData/Handbook/Tables/magnesiumtable3_a.htm) |
| b3 | 5168.901 (historical) | **Fe I** | not magnesium — see below | [Moore, NBS Technical Note 36, p. 48](https://nvlpubs.nist.gov/nistpubs/Legacy/TN/nbstechnicalnote36.pdf) |
| b4 | 5167.322 | Mg I | 3s3p ³P°₀ – 3s4s ³S₁ | [NIST Mg I](https://physics.nist.gov/PhysRefData/Handbook/Tables/magnesiumtable3_a.htm) |

Sources checked 2026-09-07. The adopted Mg wavelengths and level assignments come
from the NIST Handbook's persistent-line table (wavelength reference KM91a, Kaufman
and Martin 1991). The Fe I value is retained from Moore's historical multiplet table
for identification, not as a modern precision-velocity zero point. Printed decimal
places are not uncertainty estimates. For quantitative wavelength offsets, retain
the actual database record, its observed-versus-Ritz designation, air/vacuum
convention, uncertainty where available, and retrieval date; do not silently mix
compilations. If reference uncertainty is unavailable, say so.

Four statements about this group are made wrongly often enough to be worth writing
down:

- **The numbering runs from the red end downward.** b1 is the *longest* wavelength.
  Calling 5167 Å "b1" inverts the series.
- **b3 is an Fe I line**, historically grouped with the others by Fraunhofer. The
  magnesium triplet is **b1, b2 and b4** — three Mg lines, not "b1, b2, b3".
- **These are not ground-state transitions.** The lower level is the excited
  3s3p ³P° term; the ground state of Mg I is 3s² ¹S₀.
- **The Na I D lines are not here.** D₂ is 5889.95 Å and D₁ is 5895.92 Å, some 700 Å
  redward ([NIST Na](https://physics.nist.gov/PhysRefData/Handbook/Tables/sodiumtable2.htm)).
  Neither can appear in this stellar-rest-frame b-region window. The familiar Ca II
  resonance lines are **K = 3933.66 Å; H = 3968.47 Å**, also outside this window
  ([NIST Ca II wavelengths](https://physics.nist.gov/PhysRefData/Handbook/Tables/calciumtable4.htm);
  [H/K identifications](https://arxiv.org/abs/1205.3503)). Their absence here is not
  evidence that every calcium transition is absent; any other identification still
  requires a queried line list.

## Quoting agreement

The atlas is finely sampled, but inspect the actual local spacing in `Arg` and
preserve gaps. Between adjacent samples separated by Δλ, any reference wavelength
inside that interval is within Δλ/2 of a grid point, whether or not an absorption
feature exists there. Thus, for a local spacing of 0.01 Å, a nearest-grid-point
distance of at most 0.005 Å supplies no identification evidence; this is not a
universal spacing or tolerance for every atlas product.

Report the difference between the reference wavelength and the measured or fitted
line core, with the method and justified uncertainty. Distinguish the wavelength
of a minimum-flux sample from a fitted centre. **A fit can achieve sub-pixel
precision**; sample spacing is not a hard precision floor (see
[Teague and Foreman-Mackey 2018](https://arxiv.org/abs/1809.10295)). Account for noise,
sampling, profile/blend assumptions, wavelength calibration and reference-wavelength
uncertainty; a small formal fit error alone does not establish absolute accuracy.
If those uncertainties are not assessed, report a descriptive offset without a
precision or physical-shift claim. Before interpreting an offset, verify the adopted
reference and wavelength frame: a wrong constant can mimic a real shift.

## Anything outside the table

The 5160–5190 Å region of a solar-type star contains many weaker Fe I, Fe II and
other metal lines, and this skill does not enumerate them. Query a line list —
NIST ASD, or VALD through VAMDC — and cite what you queried and when. If you did not
query one, say the feature is unidentified.
