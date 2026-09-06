# Identifying absorption lines in PEPSI atlas spectra

A normalized stellar spectrum invites naming its lines, and that is the step where
recalled wavelengths go wrong. This file is the identification authority for this
skill. It is deliberately short: it states the group the atlas demo plots, and
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

| Fraunhofer | Wavelength (Å) | Species | Transition |
|---|---|---|---|
| b1 | 5183.604 | Mg I | 3s3p ³P°₂ – 3s4s ³S₁ |
| b2 | 5172.684 | Mg I | 3s3p ³P°₁ – 3s4s ³S₁ |
| b3 | 5168.901 | **Fe I** | not magnesium — see below |
| b4 | 5167.321 | Mg I | 3s3p ³P°₀ – 3s4s ³S₁ |

Four statements about this group are made wrongly often enough to be worth writing
down:

- **The numbering runs from the red end downward.** b1 is the *longest* wavelength.
  Calling 5167 Å "b1" inverts the series.
- **b3 is an Fe I line**, historically grouped with the others by Fraunhofer. The
  magnesium triplet is **b1, b2 and b4** — three Mg lines, not "b1, b2, b3".
- **These are not ground-state transitions.** The lower level is the excited
  3s3p ³P° term; the ground state of Mg I is 3s² ¹S₀.
- **The Na I D lines are not here.** D₂ is 5889.95 Å and D₁ is 5895.92 Å, some 700 Å
  redward. Neither can appear in the b region. **Ca II** has no line here either: H
  and K are 3933.66 and 3968.47 Å, and the infrared triplet is 8498.02, 8542.09 and
  8662.14 Å.

## Quoting agreement

The atlas is finely sampled: a 30 Å window carries a few thousand samples, so
consecutive samples are roughly 0.01 Å apart. A *nearest sample* therefore always
lies within about half that of any wavelength you name.

**"The nearest sample is within 0.005 Å of the reference" is guaranteed by the
sampling and is evidence of nothing.** Report the difference between the reference
wavelength and the measured or fitted line core, and do not quote an agreement finer
than the sampling supports. If a core sits materially off its reference, check the
reference value before reporting a physical shift — a misremembered constant and a
real velocity offset look identical in a table.

## Anything outside the table

The 5160–5190 Å region of a solar-type star contains many weaker Fe I, Fe II and
other metal lines, and this skill does not enumerate them. Query a line list —
NIST ASD, or VALD through VAMDC — and cite what you queried and when. If you did not
query one, say the feature is unidentified.
