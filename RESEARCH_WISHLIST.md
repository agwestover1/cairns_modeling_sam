# Tier-1 Research Wishlist — sam_cairns_project

Articles that would let me replace modelling assumptions with peer-reviewed
values. Grouped by the assumption each one sharpens. **★ = highest value.**
If you can get these, the model gets meaningfully more defensible.

---

## 1. Variable-power pacing on undulating courses (the "ride the course" model)
Currently I redistribute power by gradient from Sam's own data, constrained to
equal Normalized Power. These quantify the *optimal* redistribution and the time
it's worth, validating/improving my grade→power curve.

- ★ **Swain DP (1997).** "A model for optimizing cycling performance by varying
  power on hills and in wind." *Med Sci Sports Exerc* 29(8):1104–1108.
- ★ **Atkinson G, Peacock O, Passfield L (2007).** "Variable versus constant
  power strategies during cycling time-trials: a meta-analysis." *J Sports Sci*
  25(9):1001–1009.
- **Atkinson G, Brunskill A (2000).** "Pacing strategies during a cycling time
  trial with simulated headwinds and tailwinds." *Ergonomics* 43(10):1449–1460.
- **Boswell GP (2012).** "Power variation strategies for cycling time trials: a
  differential equation model." *J Sports Sci* 30(7):651–659.

## 2. Power-balance / CdA field validation (the physics core)
Validates the forward model and my descent/coasting CdA back-solve.

- ★ **Martin JC, Milliken DL, Cobb JE, McFadden KL, Coggan AR (1998).**
  "Validation of a mathematical model for road cycling power." *J Appl Biomech*
  14(3):276–291. *(The canonical equation this sim is built on.)*
- **Chung R.** "Estimating CdA with a power meter" (virtual-elevation method) —
  not peer-reviewed but the reference for the planned flat-segment solve.
- **Debraux P, Grappe F, Manolova AV, Bertucci W (2011).** "Aerodynamic drag in
  cycling: methods of assessment." *Sports Biomech* 10(3):197–218.

## 3. Drafting at legal distances (the 20 m rule)
My ~3% effective figure is interpolated from a Swiss Side blog + the withheld
Ironman study. Peer-reviewed CFD would pin it.

- ★ **Blocken B, Toparlar Y, van Druenen T, Andrianne T (2018).** "Aerodynamic
  drag in cycling pelotons / drafting" series, *J Wind Eng Ind Aerodyn* — the
  trailing-rider drag-vs-distance curves.
- **Belloli M, et al.** wind-tunnel drafting studies for TT/triathlon spacing.

## 4. Rolling resistance: drum → real road
My 0.0040 applies a ~1.6× correction to the 0.00249 drum Crr. A sourced
correction factor for asphalt would tighten it.

- **Grappe F, et al.**, and tyre-impedance work (the "breakpoint pressure" /
  impedance literature, e.g. Silca/BRR methodology papers).

## 5. IRONMAN run: durability & fractional utilization (the sub-3 gap)
My IM-off-bike penalty (~22 s/km vs fresh LT1) is a coaching heuristic. These
quantify how much a fatigued/long-duration athlete loses vs fresh threshold.

- ★ **Maunder E, Seiler S, Mildenhall MJ, Kilding AE, Plews DJ (2021).** "The
  importance of 'durability' in the physiological profiling of endurance
  athletes." *Sports Med* 51(8):1619–1628.
- **Clark IE, et al.** / **Jones AM** on the durability of the
  critical-power / LT relationship over prolonged exercise.

## 6. Grade-adjusted running pace (uphill/downhill)
Minor for flat Cairns, but lets me apply a sourced GAP rather than a heuristic.

- ★ **Minetti AE, Moia C, Roi GS, Susta D, Ferretti G (2002).** "Energy cost of
  walking and running at extreme uphill and downhill slopes." *J Appl Physiol*
  93(3):1039–1046.

## 7. Heat & endurance pace (Cairns ~29 °C)
Heat is the biggest swing on the run. A dose-response for pace decay vs WBGT
would let me model it instead of a +15–25 s/km guess.

- ★ **Périard JD, Racinais S, Sawka MN (2015).** "Adaptations and mechanisms of
  human heat acclimation." *Scand J Med Sci Sports* 25(S1):20–38.
- **Ely MR, Cheuvront SN, Roberts WO, Montain SJ (2007).** "Impact of weather on
  marathon-running performance." *Med Sci Sports Exerc* 39(3):487–493.

---

### What each unlocks
| # | Replaces this assumption | Current value |
|---|---|---|
| 1 | grade→power redistribution shape | Sam's own data, NP-matched |
| 2 | power-balance constants | standard (Martin 1998 form) |
| 3 | 20 m draft benefit | ~3% effective (interpolated) |
| 4 | drum→road Crr factor | ~1.6× → 0.0040 |
| 5 | IM-off-bike run penalty | ~22 s/km vs fresh LT1 |
| 6 | run grade-adjustment | heuristic (~minor on flat course) |
| 7 | heat pace decay | +15–25 s/km guess |
