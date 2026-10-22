# Plan: the division lens model

The overlay's lens model today is the polynomial

    r_d = r_u (1 + k1 r_u^2 + k2 r_u^4)

about the frame centre, radii over the frame half-diagonal, pinhole to screen.
Followspot camera systems mostly ship with 2.3 mm, 2.8 mm and 4.0 mm M12
lenses, about 126°, 100° and 78° horizontal on 1920x1080. Fitted to idealised
lenses of those fields of view, the polynomial is off by 2-3 px on a 4.0 mm,
12-23 px on a 2.8 mm and 96-204 px on a 2.3 mm, with a band at the corners it
cannot reach at all (the `lens_model.py` docstring already notes the band).
The two-term **division model** is off by 0.0-0.6 px on all three, and by
1.8 px on an equisolid fisheye of 126°. Received OpenCV calibrations of
2.3-16 mm M12 lenses (local, not in the repo) confirm the ranking in screen
space: the division model is at least as good as the polynomial on every lens
and several times better on the 2.3-3.2 mm ones. Several of them also put the
lens centre well off the frame centre, and on the 6-8 mm lenses that offset is
the whole remaining error, so the model bends about a **lens centre** that the
Setup Wizard measures.

This plan replaces the polynomial with the division model, with a lens centre,
in this branch. By product-owner decision it is a clean break: no saved pairs
exist anywhere, so there is no migration, no model tag and no compatibility
code, and nothing in code, docs or UI mentions the previous model.

Scripts behind the numbers (scratch, not in the repo): the model comparison,
the fits below, the inverse convergence and timing, the edge-snap and
edge-chain checks, the distortion-centre simulations (the plumb-line fit with
a centre, its gates and seeds), the gallery equivalents.

## Decisions (product owner, 2026-10-22)

1. The division model `r_u = r_d / (1 + k1 s + k2 s^2)` replaces the
   polynomial in this PR, as a clean break.
2. **The distortion centre is built in this PR** (it was Option A). It is part
   of the design below (§1, §3, §4, §7, §11, §12), and the fit's handling of a
   strong lens far off centre is a checklist item of its own (step 5).
3. **The OpenCV `.xml` import comes later, in its own PR** (it was Option B).
   Its analysis is kept under Later.
4. **Media Gallery:** today's look, with the nearest division pairs: arena
   `-0.15 / -0.03`, concert `-0.20 / -0.07` (§10).

## 1. The model

    v   = (p - F) / D - c                       screen point p, frame centre F, half-diagonal D
    r_d = |v|,  s = r_d^2
    r_u = r_d / (1 + k1 s + k2 s^2)
    q   = F + D * v * r_u / r_d                 the pinhole point

- `c = (cx, cy)` is the **lens centre**, offset from the frame centre in
  half-diagonals, in image axes (x right, y down) like every screen
  coordinate. `c = 0` bends about the frame centre.
- `r_d` is the **screen** (distorted) radius, `r_u` the **pinhole** radius,
  both about the lens centre and over the frame half-diagonal
  (`D = 0.5 * hypot(w, h)`, so `r_d = 1` at a corner when `c = 0`). The
  normalisation stays resolution-independent; a saved calibration still fits
  another resolution of the same picture shape.
- **The lens centre is the pinhole principal point.** `v * r_u / r_d` is the
  ray direction about the lens centre, and `q` places it about the frame
  centre: `q` is exactly what a pinhole camera with its principal point at
  `F` sees along the same rays (OpenCV's model with the principal point at
  `F + c D`). The pinhole solver keeps its principal point at the frame centre
  and needs no change; with `k1 = k2 = 0` and `c != 0` the warp is a pure
  translation, a principal point off centre.
- **Sign convention: barrel is negative.** To first order
  `r_d ≈ r_u (1 + k1 r_u^2 + k2 r_u^4)`, so the sliders keep their direction
  and feel (a 0.005 step still moves the corner by about 5.5 px at 1080p, see
  §5).
- **Paraxial slope 1.** `dr_u/dr_d = 1` at the lens centre, so the camera's
  `fov` keeps its meaning (the paraxial pinhole field of view, §6).
- A stereographic lens (`2f tan(θ/2)`) is the k1-only model exactly, with
  `k1 = -(D/2f)^2` (`D` the half-diagonal in pixels): an absolute test anchor.
- **Screen to pinhole is closed form** (clicks, detection feet, zone feet,
  calibration pins, the lens fit). **Pinhole to screen needs the inverse**
  (everything the HUD draws, `seen` points, predicted curves), §2, followed by
  the translation `+ c D`.

`apply_overlay_distortion` (pinhole to screen) and `invert_overlay_distortion`
(screen to pinhole) keep their names; their maths swap roles and both gain
`cx, cy` after `k1, k2`, **without default values**, so a call site that
forgets the centre fails in mypy and the tests instead of drawing about the
frame centre. The identity shortcut checks all four values. `effective_lens`
returns the four values; the call sites that pass `*effective_lens(cfg)` are
unchanged, the ones that unpack `k1, k2 = effective_lens(...)` (`input/mouse.py`
twice, `runtime/services_marker_visuals.py`) and `OverlayState` (`lens_cx`,
`lens_cy` beside `lens_k1`, `lens_k2`) take all four. `project` and the
overlay draw helpers that take the pair take the centre with it.

## 2. Pinhole to screen: the inverse

Solve `g(r) = r / D(r^2) = r_u` for `r = r_d`, `D(s) = 1 + k1 s + k2 s^2`, then
add the lens centre: `p = F + D (c + u * r_d / r_u)`, `u = (q - F) / D`.

- **Domain.** `g` rises from 0 on `[0, r_lim)`, where `r_lim` is the first
  positive root of either `D(s)` (the pole: `r_u` runs to infinity, the ray is
  90° off the axis) or `P(s) = 1 - k1 s - 3 k2 s^2` (the fold: `g' = P / D^2`
  turns negative). §3 makes `r_lim > 1 + |c|` the validity rule.
- **Barrel (the pole comes first): every pinhole radius has a screen radius**
  inside `r_lim`. The whole pinhole plane lands in a disc, so a grid line
  running to the horizon bends onto that disc instead of folding back. No
  unreachable band, no fold ring.
- **Pincushion (the fold comes first):** pinhole radii beyond `g(r_fold)` have
  no screen point; they land on the fold radius, as an unreachable click lands
  on it today. Validity puts the fold outside the frame, so only off-frame
  geometry is affected.
- **k2 == 0: closed form**, `r_d = 2 r_u / (1 + sqrt(1 - 4 k1 r_u^2))` (stable
  for `k1 -> 0`, always real for barrel; for pincushion the radicand turns
  negative exactly past the fold, which clamps to `1 / sqrt(k1)`).
- **k2 != 0: bracketed Newton on `h(r) = r_u D(r^2) - r`** (no division, so
  no blow-up at the pole; `sign(h) = -sign(g - r_u)` while `D > 0`, which
  keeps the bracket honest), bracket `[0, r_lim]`, bisection when a step leaves
  it, per-element stop at `|Δr| <= 1e-13`. Pinhole radii past a fold are
  clamped to the fold radius before iterating, so they never cost iterations.
- **Measured (scratch prototype):** seeded by the k1-only closed form, 5-7
  steps for every valid pair tested, on screen, at 3x the corner radius and at
  1000x (barrel), to `1e-12` relative; the old prototype that let the bracket
  test reject an exact step took 50, so the bracket test is `cand < lo or
  cand > hi`, not `<=` / `>=`.
- **Hot path seed (decided): a per-pair table.** `r_u` is tabulated on 513
  screen radii in `[0, r_lim)` once per pair (an `lru_cache` keyed on the
  float pair, the pair changes only on a save; the centre is a translation and
  is not part of the key), `np.interp` gives the seed and two Newton steps
  polish it to `2e-16`. The bracketed solve stays as the fallback for any
  element whose residual is still above tolerance. See §9 for the cost.

`invert_normalised_radius`, `_fold_radius`, `_DISTORTION_INVERT_ITERS`,
`_DISTORTION_INVERT_SETTLED` and `_DISTORTION_INVERT_R_CAP` go: the screen to
pinhole direction no longer iterates. The new inverse lives in `scene/solver.py`
as `distort_normalised_radius(r_u, k1, k2)` (vectorised, broadcasting like the
current helper, NaN passes through) and `lens_limit_radius` (§3) is its
bracket.

## 3. Validity

A lens `(k1, k2, cx, cy)` is valid when the map is defined and rising over the
whole frame. The frame corner farthest from the lens centre is at most
`R = 1 + |c|` half-diagonals away from it, whatever the picture's aspect
(`|c| = hypot(cx, cy)`), so the rule is checked out to that radius:

    D(s) = 1 + k1 s + k2 s^2 > 0     and     P(s) = 1 - k1 s - 3 k2 s^2 > 0     for all s in [0, R^2]

(`P / D^2` is `dr_u/dr_d`.) Both are 1 at `s = 0`, so the rule is "no root of
`D` or `P` in `(0, R^2]`", which is `lens_limit_radius(k1, k2) > 1 + |c|`.
`R` is the bound for any aspect, so the config check needs no picture size.
It is exact when the centre lies on a frame diagonal and stricter otherwise:
on 16:9, by at most 0.11 half-diagonals, at the far corner of the centre's
bound. The fit and the UI use the same rule, so nothing the wizard measures
is pulled back on save.

- **Closed form**, mirroring `lens_fold_radius`:
  `lens_limit_radius(k1, k2) = sqrt(min(s_D, s_P))`, each the smallest positive
  root of its quadratic (`q` form for both, as today, so a tiny `k2` keeps both
  roots exact), `inf` when neither has one. `lens_warp_is_valid(k1, k2, cx,
  cy)` stays the public check: all four finite, `max(|k1|, |k2|) <= 1e3`,
  `|cx| <= C_BOUND` and `|cy| <= C_BOUND` (§4), `lens_limit_radius > 1 + |c|`.
  Checked against a brute-force scan of 20,000 random pairs at `c = 0`: no
  mismatch; the same scan runs with random centres in the tests.
- **The rule bounds the pair on its own**: the valid region lies inside
  `k1 in (-1.97, 8.5)`, `k2 in (-6, 0.98)` at `c = 0` and shrinks as `|c|`
  grows. The `1e3` cap stays only as the overflow guard ahead of `k1 * k1`; it
  no longer binds any valid pair. On the axes at `c = 0`: `k2 = 0` is valid
  for `k1 in (-1, 1)`; `k1 = 0` for `k2 in (-1, 1/3)`.
- **Star-shaped in the pair** for a fixed centre: if `(k1, k2)` is valid, so
  is `t (k1, k2)` for `t in [0, 1]` (`D_t = (1 - t) + t D`, `P_t = (1 - t) +
  t P`). The pull-back keeps its shape and has a **closed form**:
  `t* = 1 / max(0, max_s(-k1 s - k2 s^2), max_s(k1 s + 3 k2 s^2))` over
  `s in [0, R^2]` (endpoints and vertex), matching the bisection to `1e-6` on
  the same 20,000 pairs at `c = 0`.
- **`pull_back_to_fold` becomes `pull_back_to_valid(k1, k2, cx, cy)`**: the
  centre is normalised first (§4: each axis clamped to `±C_BOUND`, a
  non-finite one becomes 0), then the pair is returned when valid, else
  `t* (1 - PULL_BACK_MARGIN) * (k1, k2)` for that centre, `(0, 0)` for a
  non-finite pair. The centre is never moved to make a pair valid: it is a
  measured property of the camera, the pair is what the margin trims. It
  stays the `CameraConfig.__post_init__` normalisation (CLAUDE.md: a field
  survives a hand-edited `config.toml`) and `_wizard_lens_coeffs`'s, with the
  one WARNING naming the lens before and after. It is normalisation, not
  compatibility.
- **Names.** "Fold" now names only one of the two limits, so
  `lens_fold_radius` becomes `lens_limit_radius`, the JS mirror
  `wizLensFoldRadius` becomes `wizLensLimitRadius`, `_LENS_FOLD_ERROR` becomes
  `_LENS_LIMIT_ERROR`, `wizShowLensFold` becomes `wizShowLensLimit`.
- **Operator copy changes** (the overlay no longer "folds" under barrel), see
  the UI list.

Rejected alternative, noted for the record: allow the pole inside the frame
(a fisheye whose corners look past 90° then has corners with no pinhole point,
clicks there give nothing and the pole circle shows in the HUD). On synthetic
equidistant fisheyes it matters only past about 155° horizontal (160°: 1.4 px
with the pole inside, 13.6 px without). On the received 2.3 mm calibrations the
strict rule costs nothing measurable (the constrained fit is as good as the
free one). It would add a NaN region to every consumer, a new pin refusal in
the Setup Wizard and a pull-back that is no longer star-shaped. Not done. With
a lens centre the same edge moves in: a 150° fisheye whose centre sits 94 px
off along the diagonal has its far corner past 90° (§7 measures it); the fit
then returns the nearest valid lens and its error shows in the rating.

## 4. Config keys

**Keep `lens_k1` / `lens_k2`; add `lens_cx` / `lens_cy`.** The pair's keys,
the API fields (`lens_k1`, `lens_k2`, the saved calibration's `k1` / `k2`, the
fit's `k1` / `k2`) and the operator labels (Barrel / fisheye, Corner
correction) all describe the same two quantities with the same sign and
first-order meaning. A rename would touch over 220 references to the keys
alone, plus every `k1` / `k2` in the API, the calibration files and the fit,
to tell old values from new ones, which the product owner has ruled
unnecessary (no saved pairs). The meaning is defined in one place,
`lens_model.py`'s docstring, and the comment on `CameraConfig`.

The centre, per the `add-config-field` skill:

| Field | Unit | Default | Bound | Normalisation (`__post_init__`) |
|---|---|---|---|---|
| `camera.lens_cx` | half-diagonals, + right | `0.0` | `-0.25 <= v <= 0.25` | `_coerce_float(v, 0.0, lo=-C_BOUND, hi=C_BOUND)`, then `pull_back_to_valid` on the four |
| `camera.lens_cy` | half-diagonals, + down | `0.0` | `-0.25 <= v <= 0.25` | as `lens_cx` |

- `C_BOUND = 0.25` lives in `lens_model.py` beside `PULL_BACK_MARGIN`: 275 px
  at 1080p, 550 px at 4K, per axis. It covers every received calibration with
  margin; a centre further off is a broken or cropped camera, not a lens.
- **Resolution-free like the pair**, so a saved calibration and an exported
  settings file fit any resolution of the same picture shape. The UI shows
  whole pixels of the station's video (§12); the conversion is the UI's.
- **Not device-local, not device-identity**: the centre belongs to the camera
  and lens, like the pair, so it travels with export, import and a push, and
  Restore defaults resets it to 0 with the pair.
- **Gated by the Lens distortion feature like the pair**: `effective_lens`
  returns `(0, 0, 0, 0)` while the feature is off.
- `apply_section_data` re-runs `__post_init__`, so a crafted
  `POST /section/camera` gets the same clamp and pull-back.
- **`FIELD_RULES`** (`web/validation.py`): `lens_cx`, `lens_cy` as
  `FieldRule(_as_float, lo=-C_BOUND, hi=C_BOUND, human_error=...)`, and
  `_LENS_FIELDS` grows to the four, so the cross-field limit check
  (`_lens_limit_error`) reads all three siblings from the form context with
  the same "empty sibling keeps its saved value" rule.
- Gallery TOMLs (`*_default.toml`) record no centre: their pictures are
  rendered about the frame centre, and a missing key is the default 0.

Consequence of the clean break: a `config.toml` or saved lens
calibration written by an earlier build is read with the new meaning. Test
stations that ran builds of this branch get their `lens-calibrations` folder
cleared during verification (asking first, per station).

## 5. Ranges, steps and bounds

Fits (least squares on `r_d / r_u - 1`, error measured on the 1920x1080 screen
through the true lens; `k1+k2` = both fitted, `k1` = k2 held at 0):

| Synthetic lens | H field | k1 / k2 (both) | err px | k1 only | err px | corner `r_u` | paraxial `fov` |
|---|---|---|---|---|---|---|---|
| Equidistant 6 mm | 54° | -0.097 / -0.002 | 0.0 | -0.099 | 0.6 | 1.11 | 50.5° |
| Equidistant 4.0 mm | 78° | -0.203 / -0.009 | 0.0 | -0.210 | 2.3 | 1.27 | 68.5° |
| Equidistant 2.8 mm | 100° | -0.333 / -0.025 | 0.2 | -0.351 | 5.8 | 1.56 | 82.2° |
| Equidistant 2.3 mm | 126° | -0.527 / -0.069 | 0.6 | -0.576 | 12.9 | 2.48 | 95.4° |
| Equidistant 2.1 mm | 140° | -0.647 / -0.111 | 1.1 | -0.727 | 18.3 | 4.18 | 101.4° |
| Stereographic 4.0 mm | 78° | -0.165 / 0 | 0.0 | -0.165 | 0.0 | 1.20 | 70.6° |
| Stereographic 2.8 mm | 100° | -0.286 / 0 | 0.0 | -0.286 | 0.0 | 1.40 | 86.0° |
| Stereographic 2.3 mm | 126° | -0.494 / 0 | 0.0 | -0.494 | 0.0 | 1.98 | 101.6° |
| Equisolid 2.8 mm | 100° | -0.350 / -0.044 | 0.5 | -0.381 | 9.4 | 1.65 | – |
| Equisolid 2.3 mm | 126° | -0.527 / -0.118 | 1.8 | -0.613 | 20.1 | 2.85 | – |
| Equidistant fisheye | 150° | -0.740 / -0.152 | 1.5 | – | – | – | – |
| Equidistant fisheye | 170° | -0.951 / -0.251 | 1.3 (fitted under 85° off axis; corners past 90°) | – | – | – | – |

Real lenses carry a much stronger corner term than these idealised ones: at
the wide end the received calibrations of 2.3-16 mm lenses (local, not in the
repo) need several times the Corner correction of any lens in the table, while
their Barrel / fisheye stays inside the table's span. The ranges below cover
both with margin.

| Control | Today | Proposed | Why |
|---|---|---|---|
| Barrel / fisheye slider | -0.6 to 0.6, step 0.005 | **-1.0 to 0.5**, step 0.005 | covers the 170° synthetic fisheye; `k1 = -1` is the k2-free pole at the corner. Pincushion lenses are rare and fold at `k1 = 1`, so the positive side is half as long |
| Corner correction slider | -0.4 to 0.4, step 0.005 | **-0.8 to 0.3**, step 0.005 | covers the received wide-angle calibrations with margin; `k2 = 1/3` folds at the corner with `k1 = 0` |
| Number fields | any valid pair, step 0.005 | unchanged | |
| Slider widening for an out-of-span value (`wizWriteLensCoeff`, `min()` / `max()` in `camera.tpl`) | yes | unchanged | |
| Lens centre (two number fields, new) | – | whole pixels of the station's video, step 1, `±floor(C_BOUND * D)` per axis (±275 at 1080p), default 0 | §4, §12 |

Step resolution: one 0.005 step moves the screen corner by
`0.005 * D / P(1)` px, at most 5.5 px at 1080p (`P(1) = 1 - k1 - 3 k2 >= 1`
for barrel): the same feel as today. Config bounds on the pair: none beyond §3
(no box), as today.

The default `(0, 0, 0, 0)` draws exactly what a pinhole camera sees.

## 6. Field of view and the pinhole solver

- The stored `fov` is the paraxial pinhole field of view, as today. The
  pinhole picture is wider than the screen for barrel: the screen corner sits
  at pinhole radius `1 / D(1)` (2.48 half-diagonals on the 2.3 mm). Paraxial
  `fov` for the table's lenses runs 50-109°, well inside `POSE_FOV_RANGE_DEG`
  (10-170°) and `CameraConfig`'s 1-179°. No bound changes.
- The lens centre needs nothing from the solver (§1: the pinhole picture is
  already centred on the principal point).
- Places that assume the frame edge maps near `r = 1` and must be checked:
  `_DISTORTION_INVERT_R_CAP = 4.0` (goes, §2); `screen_axes_at`'s round-trip
  guard (stays as a generic guard, its fold comment is reworded); the
  generators' `CLIP` box "far outside it the warp folds back" (true no longer
  under barrel; the clip stays to keep the SVG small, the comment changes);
  `overlay_picture.py`'s comment that lens distortion reaches Cairo's 2^23
  limit (barrel now maps everything inside the pole disc; the guard stays for
  near-horizon pinhole points, the comment changes).
- Calibration pins are undistorted into pinhole pixels and weighted
  `PIXEL_SIGMA = 1` there. Under a 2.3 mm a corner pin's pinhole pixel is up to
  10x a screen pixel radially, so corner pins weigh more than their precision
  warrants. This predates the change (the polynomial could not reach these
  lenses at all) and four corners still fit exactly; weighting pins by the
  lens Jacobian is listed under Out of scope. (The lens fit gets that
  weighting in this PR, §7, because its gates depend on it.)

## 7. The lens fit from lines (`scene/lens_fit.py`)

The fit measures `k1`, `k2` and the lens centre from the traced lines and
keeps each extra term only when the lines show it.

### What failed in the first simulation, and why

The first centre gate (fit the centre on top of the pair; keep it when the
cost drops by more than 6 residual variances **and** the centre's own sigma is
under 15 px) missed a strong lens far off centre: on a `-0.53 / -0.07` lens
offset by (80, 50) px, four edges and a diagonal, 1 px placement noise, a
re-run kept the centre 0 times in 12 and left a 46 px frame error. Four causes,
each confirmed in the scratch simulation:

1. **Residuals in pinhole pixels.** The fit measures each point's distance from
   its line in the pinhole picture. Under a strong lens a pinhole pixel at the
   corner is several screen pixels, so 1 px of placement noise there counts up
   to ten times over: the summed cost of the *true* lens was 300-1000 where 15
   is expected, the residual variance was inflated, and both gates misread it.
2. **The k2 gate decided before the centre.** A decentred lens fitted about
   the frame centre leaves a misfit that inflates k2's sigma, so k2 was
   dropped (`k2 = 0`) and the centre was then fitted with k2 held at 0. With
   no noise at all, that order recovers a 15 px error; fitting all four
   together recovers the lens exactly.
3. **The 15 px sigma gate.** The centre's own position trades against `k1`
   along a valley, so its sigma stays at 20-35 px even where it moves the
   frame corners by 40-90 px: the warp is well measured, the centre alone is
   not. The gate rejected exactly the lenses that need the centre.
4. **Validity checked to `r = 1` only.** With the centre off, the far corner
   is at `1 + |c|`, so a fitted pair could put its pole inside the frame.

### The fit

1. **Screen-pixel residuals.** Each point's perpendicular residual about its
   line's TLS line is divided by `|J^T n|`, `n` the line normal in the
   pinhole picture and `J = g I + 2 g'(s) v v^T` the closed-form Jacobian of
   the undistortion at the point (`g = 1 / D(s)`, `g' = dg/ds`). That is the
   residual in screen pixels, where the placement noise lives, so the cost of
   the true lens is about its degrees of freedom again, and the residual
   variance, the covariance and every gate mean what they say. Cheap: one
   2x2 product per point.
2. **The pair, centre at 0** (today's grid and refinement): seed grid over
   `_K1_SCAN = -1.2 .. 0.6 step 0.05`, `_K2_SCAN = -1.0 .. 0.4 step 0.02`
   (37 x 71 cells, 2627, against 1683 today; each cell is far cheaper, since
   `undistort` is now closed form), cells failing §3 dropped, Levenberg-
   Marquardt from the best cell per `k1` column (`_SEEDS = 4`), `k1` and `k2`
   free. Cost `C2`.
3. **The full lens**: Levenberg-Marquardt on `(k1, k2, cx, cy)` from the step-2
   result with `c = 0` and from the next two seeds of step 2. Cost `C4`.
   Seeding from a grid of centres as well (25 centres over ±0.16) changed no
   result in the simulation, so it is not done.
4. **Centre gate**: keep the centre when
   `C2 - C4 > CENTRE_LR_GATE * s4^2`, `s4^2` the residual variance of the full
   fit, `CENTRE_LR_GATE = 6.0` (the 95 % point of two parameters, the same
   margin as `_RIVAL_MARGIN`). No sigma gate on the centre: the rating (below)
   carries its uncertainty.
5. **k2 gate on the model kept**: from that model's covariance, as today
   (`K2_SIGMA_GATE = 0.05` until the simulation says otherwise). When it
   fails, refit `k1` (and the centre, if kept) with `k2 = 0`.
6. **Bounds**: `_in_bounds` checks `|k1|, |k2| <= _K_BOUND` (2.0),
   `|cx|, |cy| <= C_BOUND` and §3 out to `1 + |c|`, so no step leaves the valid
   region.

Measured on synthetic decentred division lenses (scratch prototype of the
steps above, lines as the wizard sends them: five points, four edges plus a
diagonal, 1 px placement noise, 12 trials; error = the largest screen error
over the frame after the free homography the camera solve absorbs, median /
worst px):

| Lens `k1 / k2` | Offset px | Centre ignored | This fit | Centre kept |
|---|---|---|---|---|
| -0.10 / 0 | 0 | 0.8 / 2.3 | 0.8 / 2.3 | 0 of 12 |
| -0.10 / 0 | (40, -25) | 3.4 / 4.0 | 2.2 / 4.0 | 10 of 12 |
| -0.10 / 0 | (80, 50) | 6.6 / 7.3 | 2.4 / 4.8 | 12 of 12 |
| -0.10 / 0 | (120, 80) | 10.0 / 10.5 | 2.3 / 3.6 | 12 of 12 |
| -0.27 / -0.09 | 0 | 1.5 / 3.6 | 1.5 / 3.6 | 0 of 12 |
| -0.27 / -0.09 | (40, -25) | 13.4 / 13.9 | 2.4 / 3.4 | 12 of 12 |
| -0.27 / -0.09 | (80, 50) | 27.1 / 27.9 | 2.9 / 4.4 | 12 of 12 |
| -0.27 / -0.09 | (120, 80) | 44.3 / 45.1 | 2.4 / 3.0 | 12 of 12 |
| -0.53 / -0.07 | 0 | 3.1 / 8.9 | 3.7 / 8.9 | 1 of 12 |
| -0.53 / -0.07 | (40, -25) | 25.0 / 27.5 | 4.2 / 9.0 | 12 of 12 |
| **-0.53 / -0.07** | **(80, 50)** | **50.9 / 53.1** | **4.9 / 11.3** | **12 of 12** |
| -0.53 / -0.07 | (120, 80) | 88.2 / 91.1 | 3.3 / 8.6 | 12 of 12 |
| -0.53 / -0.118 | 0 | 3.4 / 7.8 | 4.4 / 13.9 | 2 of 12 |
| -0.53 / -0.118 | (80, 50) | 60.5 / 64.9 | 4.6 / 11.7 | 12 of 12 |

The strongest common lens offset by (80, 50) px, in other layouts: four edges
only 51.5 -> 8.3 / 12.8; four edges and two middle lines 49.2 -> 4.8 / 8.7;
top, bottom and a diagonal 53.9 -> 3.5 / 6.2; four edges and a diagonal at
3 px noise 52.2 -> 12.9 / 29.5; the centre kept 12 of 12 in each.

- **A centred lens** keeps the centre 0-2 times in 12 (the gate's 5 %); a kept
  centre then costs up to 6 px at the worst corner of a strong lens, and the
  rating says so.
- **A weak lens** keeps the centre when it is far enough off to matter
  (40 px and more on `-0.10`); the centre itself is then loose (20-70 px off
  the truth) but the warp is right to 2-5 px.
- **Past the edge of §3**: the 150° equidistant fisheye (`-0.74 / -0.152`)
  offset by (80, 50) px or more has its far corner past 90°, which no valid
  lens can show; the fit returns the nearest valid one, 50-100 px off at that
  corner. Inside the edge (centred, or 20-40 px off) the 0.05 k2 gate drops k2
  on this lens in most trials with screen-pixel residuals, leaving 20-30 px
  where the kept k2 gives 15-20 px: input to the k2-gate retune below.
- **Cost**: the prototype fit (steps 1-6, without the misfit and rival checks)
  takes 50-110 ms median, 0.4 s at worst, on the Mac. The real fit adds the
  leave-one-out and rival work; it is timed on pi59 in the PR.

### The rest of the fit

- `_Lines.undistort` is the closed form with the centre: no Newton per grid
  cell. The seeding grid costs one division per point per cell.
- `_K_BOUND` goes from 1.0 to 2.0, the span §3 allows for k1.
- **Corner uncertainty** (`_corner_uncertainty_px`, the rating): the predicted
  sigma of the screen position at each frame corner, from the covariance of
  the free parameters (two, three or four), propagated through the screen
  Jacobian at the corner, with the pinhole picture's translation (the
  undistorted frame centre's own movement) removed; the largest over the four
  corners. With `c = 0` and the pair only it reduces to
  `D * sqrt(c11 + c22 + 2 c12) / (1 - k1 - 3 k2)` (today's formula drops the
  `1 / P(1)` factor that the polynomial did not have).
- **Rival spread** (`_rival_spread_px`): `d1 r^3 + d2 r^5` is the polynomial's
  displacement. Replace it by the exact screen gap between the fit and each
  rival minimum of the same stage (the stage kept): for screen points over the
  frame, undistort through the fit and re-distort through the rival, after
  removing the translation between the two pinhole pictures.
- **Misfit** (leave-one-out): `k1` is refitted without the line, `k2` and the
  centre held, as `k2` is held today.
- **`K2_SIGMA_GATE = 0.05`** keeps its meaning: in both models a k2 change of
  0.05 moves the corner by about 55 px / `P(1)`. With screen-pixel residuals
  its sigma changes scale, so it is re-derived by re-running
  `scripts/lens_fit_simulation.py` (its `LENSES` move to the §5 pairs plus
  decentred ones, its scenarios gain the centre) and kept or retuned with
  `RATING_THRESHOLDS_PX` from the table; the table lands in the PR
  description. Real lenses have a large k2, so a gate that fits k2 more
  readily is a likely outcome (the 150° fisheye above already asks for it).
- **The rating shifts and must be retuned, not only checked.** On the
  mockup's concert lines (seven straight suggestions, none near a corner) the
  division fit lands on the picture's pair (`-0.208 / -0.059` against
  `-0.20 / -0.07`), but the corner uncertainty rises from 10.6 px with the
  polynomial to 24.4 px, which today's thresholds rate Not usable: the
  division model's corner term is freer, so lines that stay away from the
  corners say less about it. The retune of `RATING_THRESHOLDS_PX` lands with
  the simulation table; the mockup keeps today's thresholds until then and
  rates its demo lines one step lower than before.
- `curve()` uses `apply_overlay_distortion` with the centre (now the iterative
  direction): 24 samples a line, negligible.
- The hint and `MAX_LINES` are model-free. The result gains `cx`, `cy` and
  `centre_fitted` (`fit_result_to_dict`).

## 8. Edge snap and edge suggestions

Measured on straight lines through strong division lenses (scratch):

- **Reference bows** (`_REFERENCE_K1 = _REFERENCE_K2 = 0.2`): the two-bow
  basis plus end shifts fits a 2.3 mm-like lens to 0.3 px, and the widest
  synthetic lenses to 2.5-3.6 px along the top edge (under `_POINT_WINDOW`,
  6 px). Pincushion references stay valid (`P(1) = 0.4`); the comment's reason
  ("a barrel reference folds") is reworded: both signs are valid now and the
  pincushion pair fits the strong lenses slightly better. The references bend
  about the frame centre; the end shifts absorb a lens centre's effect on a
  short chord, checked on the decentred lenses of §7.
- **Corridor** (`reach + 2 + 3 |bow1| + 2 |bow2|`) is too narrow: the true bow
  of a top-edge line reaches 1.1-1.8 corridors on the widest lenses (2.3
  mm-like: 0.8-1.06). `_CORRIDOR_K1` / `_CORRIDOR_K2` are re-derived from the
  slider ends (the bow of `(-1.0, 0)` and `(0, -0.8)`), and the existing test
  that a brighter parallel edge further off is not taken is re-run with them.
- **`edge_chains._MAX_SAG_FRACTION = 0.1` rejects real lines**: a straight
  line near the frame edge sags 0.15 of its chord through a 2.3 mm-like lens
  and 0.23-0.25 through the widest, so the Lens step would offer no
  suggestions along the edges where they matter most. A fixed fraction cannot
  separate them from a curved stage front (0.25). Replace it with a
  position-dependent bound: the sag the chord would have through the slider's
  widest lens, with the centre up to `C_BOUND` off, times a margin, computed
  with `apply_overlay_distortion`. The curvature bound
  (`_MAX_CURVATURE_WIDTH = 4.0`) holds: 1.3-2.0 measured.
- `_DISTORTION_SUBDIVISIONS = 12`: a 450 px bow drawn with 12 chords is about
  3 px off its arc at the middle of a chord. Measure on the strongest lens and
  raise it only if an edge line visibly polygonises (the grid is cached, the
  per-frame cost is the markers').

## 9. HUD cost

Measured on the Mac (numpy, one call, points per call):

| Points | Polynomial today | Division k2 = 0 | Division, k1 seed + Newton | Division, table seed + 2 Newton |
|---|---|---|---|---|
| 50 | 3 µs | at most 12 µs | 77 µs | 16 µs |
| 300 | 3 µs | at most 12 µs | 86 µs | 28 µs |
| 2000 | 5 µs | 12 µs | 146 µs | 118 µs |

- Per HUD frame the warp runs on markers only (each marker's projection is one
  call; grid, zones and paths are cached pictures redrawn on a view change).
  With eight markers that is roughly 10-20 calls: about 0.3 ms a frame on the
  Mac with the table seed, an estimated 1-2 ms on a Pi 5 against today's
  ~0.2 ms. The k1-only seed alone would be 3-12 ms on a Pi, which is why §2
  decides the table. The centre adds one translation per call.
- The click, detection-foot, zone-foot and pin paths get cheaper: closed form
  instead of today's 24-40 Newton steps.
- Verified on the station with the HUD bench (eight markers, cones, a strong
  lens with a centre off) before merge; the GL Operator Screen composites what
  Cairo drew and does not warp (`video/gl_display.py` has no lens code).

## 10. Media Gallery defaults (decided)

The arena and concert pictures are rendered by their generators through
`apply_overlay_distortion`, and the TOML records the pair. **Re-derive by
regenerating**, not by refitting the TOML: the generator is the source of
truth ("a station set to the printed camera and lens draws its grid on the
tape").

| Picture | Today | New pair | Largest gap to today's picture |
|---|---|---|---|
| Stage | 0 / 0 | 0 / 0 | – |
| Arena | -0.15 / 0.02 | **-0.15 / -0.03** | 1.5 px |
| Concert | -0.2 / 0.03 | **-0.20 / -0.07** | 1.3 px |

The new pairs are the rounded division equivalents of today's warps, so the
pictures barely change; the centre stays 0 (the generators render about the
frame centre). Set `K1, K2` in `scripts/gen_arena_default.py` and
`scripts/gen_concert_default.py`, re-run both (`rsvg-convert` and Pillow, both
present on the Mac), commit the new JPEG + TOML pairs, and let
`tests/test_gallery_calibration.py` (grid corners land on the tape) prove it.
The mockup's `concert.jpg` and `marks.py` take the same picture and pair once
step 9 has run.

## 11. Saved lens calibrations (`web/lens_calibrations.py`)

- `LensCalibration` gains `cx`, `cy` (half-diagonals, as in the config); each
  file stores `"cx"` and `"cy"` beside `"k1"` and `"k2"`. Both are required,
  like the pair (clean break: no file without them exists).
- `pair_problem(k1, k2)` becomes `lens_problem(k1, k2, cx, cy)`: four real
  numbers (not `bool`, not a JSON integer too large for a float, finite), each
  centre axis within `±C_BOUND`, `lens_warp_is_valid` on the four. A file that
  fails it does not read, as today.
- The save route (`POST /api/wizard/lens/calibrations`) takes `cx`, `cy` and
  answers a malformed one with 400 and the reason, never 500.
- Loading a calibration sets all four values; the list and the result line
  name the centre when it is not 0 (§12).
- The wizard's settings-file load reads `camera.lens_cx` / `lens_cy` the way
  the station's config load would: a missing key is 0; a value that is not a
  finite number, or that the station would clamp or pull back, refuses the
  file with the reason (UI items 6, 6a).

## 12. The UI for the lens centre

**Setup Wizard, Lens step** (in the mockup):

- **Adjust by hand (optional)**: under the two sliders, a row **Lens centre**
  with two number fields, **Right of the picture centre (px)** and **Above
  the picture centre (px)**: whole pixels of the snapshot (the station's
  video), signed (left and below are negative), default 0, step 1, range
  `±floor(C_BOUND * D)` (±275 at 1080p). The fields convert to the stored
  half-diagonals (`cy = -above / D`). Editing them counts as adjusting by
  hand, like the sliders.
- **The result line** names the centre when one was measured: "Good –
  barrel / fisheye −0.198, corner correction −0.071, lens centre 42 px right,
  18 px up". When the gate keeps the centre at 0 nothing is added; a
  component that rounds to 0 px is left out ("lens centre 42 px right").
  The loaded-calibration line names a non-zero centre the same way.
- **Load lens calibration**: each saved calibration's line adds
  "· Lens centre 42 px right, 18 px up" when its centre is not 0.
- **Errors** under Adjust by hand: the pair's error (UI item 4) when the pair
  alone is invalid; else, when the centre makes it invalid, "With this lens
  centre, the overlay cannot follow the picture out to its corners. Set the
  lens centre closer to 0." (item 4a); a centre field outside its range or not
  a number, "Enter a number from −275 to 275." with the snapshot's bound
  (item 4b).
- Review and the Camera Calibration step's Lens chip are unchanged (they show
  the pair only).

**Camera tab** (Station Settings, `partials/camera.tpl`, Lens distortion
group; proposal, no mockup):

- Under the two sliders, the same row **Lens centre** with **Right of the
  picture centre (px)** and **Above the picture centre (px)**, whole pixels of
  the station's current video (`receiver.resolution`, handed to the page like
  the other live values), step 1, `min`/`max` from that size.
- They are helper fields without a `name`, like Sensor Size and Focal Length,
  which write `fov`: each writes its hidden `lens_cx` / `lens_cy` through the
  size the page was rendered with. The px fields carry the validate markup
  for their hidden field (`hx-get="/api/validate/camera/lens_cx"`,
  `hx-include="closest form"`, the error span), so the cross-field limit
  error shows under them; FIELD_RULES and `apply_section_data` check the
  hidden fields on Save.
- **No video size known** (no video yet): the two fields are disabled with
  the placeholder "No video", and the hidden fields keep the saved values, so
  Save never changes a centre it cannot show. No silent fallback to an
  assumed size.
- Camera tab error for the centre (item 5a): "This lens centre stops the
  overlay short of the picture's corners. Bring it closer to 0."
- Help drawer `camera.md`: what the lens centre is, its unit (pixels of the
  video, from the picture centre, left and below negative), that the Setup
  Wizard measures it, and that a calibration from another resolution of the
  same picture shape still fits.

**Help drawer `wizard-lens.md`** (and the mockup's help template): one
paragraph under Rating, "**Lens centre** is the point the lens bends the
picture around. On many cameras it is a little off the centre of the
picture. It is only measured when the lines show it clearly; otherwise it
stays at the centre of the picture and the result does not name it.", and
one under a new heading **Adjust by hand** for the two fields.

## UI changes for approval

Against the mockup (`~/.claude/plans/wizard-calibration-mockup/mock/template.html`,
whose own JS lens model, fit grid, predicted curves and sliders change with
it; built 2026-10-22):

1. Barrel / fisheye slider: -0.6..0.6 to **-1.0..0.5** (wizard Adjust by hand
   and Camera tab); zero is no longer centred.
2. Corner correction slider: -0.4..0.4 to **-0.8..0.3** (both places); zero is
   no longer centred.
3. Steps unchanged (0.005), number fields unchanged.
4. Wizard field error: "With these two values, the overlay folds over itself
   inside the picture. Set one of the values closer to 0." to **"With these
   two values, the overlay cannot follow the picture out to its corners. Set
   one of the values closer to 0."**
   - 4a. New, the centre makes the lens invalid: **"With this lens centre,
     the overlay cannot follow the picture out to its corners. Set the lens
     centre closer to 0."**
   - 4b. New, a centre field out of range or empty: **"Enter a number from
     −275 to 275."** (the bound of the snapshot's size).
5. Camera tab field error: "This pair folds the overlay inside the frame.
   Bring either value closer to 0." to **"This pair cannot follow the picture
   out to its corners. Bring either value closer to 0."**
   - 5a. New: **"This lens centre stops the overlay short of the picture's
     corners. Bring it closer to 0."**
6. Settings-file load error: "With the lens values of this file, the overlay
   folds over itself inside the picture." to **"With the lens values of this
   file, the overlay cannot follow the picture out to its corners."** (also
   for a centre out of bounds or one that makes the lens invalid).
   - 6a. A centre that is not a number: the existing "This settings file has
     no lens calibration."
7. Saving a calibration (server reason): "These lens values fold the overlay
   inside the frame." to **"These lens values cannot follow the picture out to
   its corners."** (covers the centre).
8. Help drawer `camera.md`: the limit paragraph (no fold wording, no earlier
   release, new example values: a 100° lens lands around -0.33 / -0.03, a 126°
   one around -0.53 / -0.07), the slider span sentence, and the lens centre
   (§12).
9. Media Gallery arena and concert pictures re-rendered (at most 1.5 px
   different) with new printed values.
10. **Lens centre, Adjust by hand**: the row **Lens centre** with **Right of
    the picture centre (px)** and **Above the picture centre (px)**, step 1,
    ±275 px at 1080p, default 0 (§12).
11. **Lens centre, result line**: ", lens centre 42 px right, 18 px up" when
    measured, nothing when the gate keeps it at 0; the same on the
    loaded-calibration line and in the Load dialog's list (§12).
12. **Lens centre, Camera tab**: the same two fields in pixels of the live
    video, disabled with "No video" without one (§12; not in the mockup).
13. Help drawer `wizard-lens.md`: the lens centre under Rating and the new
    Adjust by hand paragraph (§12).

The approved Setup Wizard plan (`docs/plans/setup-wizard-calibration-modes.md`)
quotes item 6's old text and the fold wording in its copy tables; those rows
are updated with the approval.

## Checklist

Each step lands with its tests; targeted tests while iterating, one
`make ci-remote` at the end (pi59).

### 0. Approval
- [x] Mockup: lens model (`limitRadius` / `distort` / `undistort` /
      `distortRadius` to the division model with the centre, `lensValid` to
      §3), fit grid (its residuals stay in pinhole pixels and its centre is
      faked from the lines: a stand-in, not the §7 fit), predicted curves, slider
      attributes, error copy (items 4, 4a, 4b, 6), the lens centre row, the
      result line, the Load list, help text; rebuilt with `build.py`,
      `node harness.js` exits 0 (2026-10-22).
- [ ] Mockup `concert.jpg` and `marks.py` to `(-0.20, -0.07)` from step 9
      (the mockup picture is today's render until then, within 1.3 px).
- [x] Product owner decides Options A and B (2026-10-22: centre now, OpenCV
      import later).
- [ ] Product owner approves the UI list, including the lens centre (items
      10-13) and the Camera tab design.

### 1. The rule (`openfollow/lens_model.py`, stdlib-only)
- [ ] Docstring: the model with the lens centre, normalisation, sign, both
      limits, the `1 + |c|` radius.
- [ ] `C_BOUND`, `lens_limit_radius`, `lens_warp_is_valid(k1, k2, cx, cy)`,
      `pull_back_to_valid` (centre clamped first, closed-form `t*` out to
      `R^2`), `PULL_BACK_MARGIN` kept.
- [ ] `tests/test_lens_model.py` rewritten (see Tests).

### 2. The warp (`openfollow/scene/solver.py`)
- [ ] `invert_overlay_distortion`: closed form with the centre;
      `apply_overlay_distortion`: `distort_normalised_radius` (closed form for
      k2 = 0, table-seeded Newton, bracketed fallback, fold clamp) plus the
      translation; `cx, cy` required in both; drop the Newton helpers and
      constants.
- [ ] `project` and the overlay helpers that take the pair take the centre.
- [ ] `screen_axes_at` comment.
- [ ] `tests/test_solver_distortion.py` rewritten; `tests/test_solver.py`
      lens cases.
- [ ] Timing check against §9 (scratch bench, numbers in the PR).

### 3. Config and web plumbing (`add-config-field` for `lens_cx`, `lens_cy`)
- [ ] `configuration.py`: `CameraConfig` fields and comment, `__post_init__`
      (clamp, `pull_back_to_valid` on the four), the WARNING text ("cannot
      follow the picture out to its corners"), `effective_lens` returns four.
- [ ] `web/validation.py`: `FIELD_RULES` for both, `_LENS_FIELDS` to four,
      `_LENS_LIMIT_ERROR` copy (items 5, 5a).
- [ ] `web/lens_calibrations.py`: `cx`, `cy`, `lens_problem`, reason text
      (item 7); the save route takes the centre.
- [ ] `web/routes.py`: `_wizard_lens_coeffs` (four values, docstring,
      pull-back), the fit route's answer with the centre.
- [ ] Export, import, push and Restore defaults carry / reset the centre with
      the pair (config guide's field lists unchanged: not device-local, not
      identity).
- [ ] Tests: `test_configuration.py` (per field: wrong type `None` / `"abc"` /
      `True`, NaN, inf, `1e400`, each bound and a hair past it; out-of-region
      lenses pulled back to the closed-form point for a given centre, the
      centre kept; both limits; hot reload of a centre change),
      `test_validation.py` (FIELD_RULES agree with the server, the
      cross-field check with each of the three siblings empty or invalid),
      `test_web_helpers.py` (form to config to renderer with a centre),
      `test_web_server.py`, `test_wizard_solve_route.py`,
      `test_lens_calibrations.py` (a file without `cx`, with `True`, with a
      huge integer, out of bounds), `test_template_validation_consistency.py`.

### 4. Consumers (verify and adjust)
- [ ] `input/mouse.py`, `services.py`, `runtime/services_detection_pin.py`,
      `runtime/services_marker_visuals.py`, `runtime/overlay_state.py`,
      `runtime/overlay_draw_scene.py`, `overlay_draw_zones.py`,
      `overlay_draw_paths.py`, `runtime/overlay_picture.py` comment,
      `input/control_frame.py`.
- [ ] Tests: `test_input_mouse.py` (a corner click under a decentred strong
      lens lands under the cursor's stage point),
      `test_services_detection_pin.py`, `test_services_zone_orchestration.py`,
      `test_overlay_draw_*`, `test_services_marker_visuals.py` (input to
      overlay), `_hud_scenes.py` lenses; HUD cost tests unchanged in call
      counts.
- [ ] `_DISTORTION_SUBDIVISIONS` measured on the strongest lens (§8).

### 5. The fit (`openfollow/scene/lens_fit.py`, `scripts/lens_fit_simulation.py`)
- [ ] Closed-form `undistort` with the centre, new grid and `_K_BOUND`.
- [ ] **The strong decentred lens** (§7): screen-pixel residuals (`|J^T n|`),
      the full four-parameter fit before any gate (seeds: the pair's result
      and its next two seeds, centre 0), `CENTRE_LR_GATE = 6.0` on the cost
      drop, no sigma gate on the centre, the k2 gate on the model kept,
      `_in_bounds` out to `1 + |c|`. Done when the simulation's `-0.53 /
      -0.07` lens offset by (80, 50) px keeps the centre in at least 11 of 12
      trials and lands within 12 px at the worst corner (four edges and a
      diagonal, 1 px), and a centred lens keeps it in at most 2 of 12.
- [ ] Corner uncertainty over the four corners from the full covariance,
      translation removed; exact rival spread; misfit with the centre held.
- [ ] Simulation `LENSES` to the §5 pairs plus decentred ones (offsets 0,
      (20, -12), (40, -25), (80, 50), (120, 80)); scenarios as today; re-run;
      keep or retune `K2_SIGMA_GATE` and `RATING_THRESHOLDS_PX` from the
      table (the 150° fisheye's k2).
- [ ] `fit_result_to_dict`: `cx`, `cy`, `centre_fitted`.
- [ ] Timed on pi59 (one fit route call with 12 lines), number in the PR.
- [ ] `tests/test_lens_fit.py`, `tests/_lens_scene.py`,
      `tests/test_lens_calibration.py`: synthetic stages through 2.3 mm-like
      and corner-heavy pairs, pair back within 0.01; a decentred strong lens
      gives its centre back within 15 px and the frame within the threshold
      above; a centred lens reports `centre_fitted` false on a fixed seed;
      a lens whose far corner is past 90° returns a valid lens.

### 6. Edge snap and suggestions
- [ ] `scene/edge_snap.py`: reference comment, corridor constants from the
      slider ends.
- [ ] `scene/edge_chains.py`: sag bound from the widest lens, centre up to
      `C_BOUND` off, at the chord's position.
- [ ] `tests/test_edge_snap.py`, `tests/test_edge_chains.py`: a straight line
      along the top edge of a 2.3 mm-like, a corner-heavy and a decentred
      picture is suggested and snapped; a curved stage front is still
      rejected; a brighter parallel edge outside the corridor is not taken.

### 7. Web UI
- [ ] `partials/camera.tpl`: slider spans (items 1, 2), the Lens centre row
      (item 12) with its helper script and the "No video" state.
- [ ] `wizard.tpl`: slider spans, `wizLensLimitRadius`, `wizLensIsValid`
      (four values), `wizApplyDistortion` (table-free bracketed Newton is
      enough in the browser, plus the translation), `wizInvertDistortion`
      (closed form), the Lens centre row and its errors (items 4a, 4b, 10),
      the result line and the Load list (item 11), copy (items 4, 6), the
      constants block, the saved calibration's centre.
- [ ] `tests/test_wizard_js.py` (runs in node on GitHub Linux only): limit
      radius and validity equal the server's with and without a centre, the
      inverse equals the server's on a grid of lenses, a fitted lens shown as
      valid, the settings-file lens taken exactly when the station would
      (missing centre 0, out of bounds refused), px to half-diagonal
      conversion and its sign (a centre 42 px up stores `-42 / D`).

### 8. Help and docs
- [ ] `docs/architecture/web.md` (Lens distortion section: model, centre,
      rule, inverse, the fit and its gates, snap corridor, sag rule),
      `docs/architecture/video.md` (gallery pairs),
      `docs/architecture/config.md` (calibration files: the four values, the
      validity check), `docs/PROJECT_STRUCTURE.md` (`lens_model.py`,
      `solver.py` lines), `docs/plans/setup-wizard-calibration-modes.md` copy
      rows.
- [ ] Help drawer: `openfollow/web/help/camera.md` (item 8),
      `openfollow/web/help/wizard-lens.md` (item 13).
- [ ] Sweep: `grep -rn "fold\|r\^4\|1 + k1" openfollow docs scripts tests`
      for leftovers; no em-dash.

### 9. Media Gallery
- [ ] `scripts/gen_arena_default.py`, `scripts/gen_concert_default.py`: new
      `K1, K2`, `pull_back_to_valid` assert, `CLIP` comment; re-run; new
      `arena_default.jpg/.toml`, `concert_default.jpg/.toml`.
- [ ] `tests/test_gallery_calibration.py` green (tape test needs the detection
      extra locally).

### 10. Verify
- [ ] `make ci-remote` green on pi59.
- [ ] Station check on pi66 (ask the operator; check `.overlay-ref` first):
      Media Gallery concert picture, grid on the tape, mouse drags a marker to
      the cursor in a corner with a centre set by hand, HUD bench with eight
      cone markers within §9.
- [ ] Setup Wizard Lens step on synthetic 2.3 mm pictures (rendered with
      `_lens_scene`), centred and offset by (80, 50) px: suggestions along the
      edges, fit within 0.01 and the centre named in the result line only for
      the offset picture, Review.
- [ ] Camera tab: the centre fields with video, "No video" without, Save keeps
      the centre.
- [ ] Clear `lens-calibrations` on test stations that ran this branch (ask
      first).

## Tests

- **Absolute, not round trips.** A round-trip test passes under a swapped
  direction or sign. Each direction gets hand-computed values:
  `k = (-0.2, 0)`: screen radius 0.5 is pinhole `0.5 / 0.95`, in pixels on
  1920x1080 along a stated direction; the k2 = 0 closed form against its
  formula; a stereographic lens's points (`2f tan(θ/2)`) undistort to
  `f tan θ` exactly with `k1 = -(D / 2f)^2`; barrel moves a pinhole point
  inward on screen and pincushion outward, by a stated amount; an equidistant
  126° lens stays within 0.7 px with the §5 pair.
- **The centre, absolutely**: with `k = (0, 0)` and `c = (0.1, 0)` a screen
  point maps to the pinhole point `0.1 D` to its left; with `k = (-0.2, 0)`
  and a centre the lens centre's own screen point maps to the frame centre,
  and a point `0.5` half-diagonals right of the lens centre maps to
  `0.5 / 0.95` right of the frame centre; the sign of `cy` (down) pinned
  through a stated pixel.
- **Validity at its exact edges**: `k2 = 0` valid on `(-1, 1)` and not at
  either end; `k1 + k2 = -1` (pole at the corner) and `k1 + 3 k2 = 1` (fold at
  the corner) invalid, a hair inside valid; the same pole moved to `1 + |c|`
  with a centre (valid at `|c|` just under, invalid just over); each centre
  bound and a hair past it; an interior vertex case for each quadratic; a tiny
  `k2` against a large `k1` (the `q` form); the region's boundedness; the
  closed-form pull-back equal to bisection on a grid of lenses and centres.
- **Inverse**: barrel radius past 1000x the corner converges; a pincushion
  radius past the fold lands on the fold radius; NaN passes; broadcasting a
  grid of pairs; the table seed and the bracketed solve agree to `1e-12`.
- **Fit**: §7's acceptance on fixed seeds; screen-pixel residuals pinned by a
  case where pinhole residuals would pick the wrong model (a strong lens with
  its noise at the corners).
- **Config per add-config-field**: wrong type (`None`, `"abc"`, `True`) per
  field, NaN, inf, overflow, each limit, one parametrize per failure mode;
  `POST /section/camera` crafted bodies; FIELD_RULES agree with the server.
- **Integration seams**: form to config to renderer; config to hot reload;
  input to overlay with a strong decentred lens (a click in a corner lands
  under the cursor's stage point).
- **Mutation-checked**: commit first, then `mutmut` on `lens_model.py`, the
  two warp functions and the fit's gate code (clean `mutants/` before a
  module run); every surviving mutant gets a test or a reason in the PR.
- **Hermetic and fast**: no real window, no wall clock in assertions, timing
  only in the scratch bench, never in the suite.
- **Synthetic lenses only.** No received calibration or value derived from one
  in any test, fixture, doc or commit.

## Review plan

- At least three review rounds, and more until a round finds only cosmetic
  issues; after round three, rare edge cases are noted, not chased.
- Round 1: the maths (both directions, the centre's translation and sign,
  validity out to `1 + |c|`, pull-back, the inverse's bracket) against the
  absolute tests and this plan.
- Round 2: the fit (screen-pixel residuals, the order of the gates, bounds,
  corner uncertainty with the centre, rivals, misfit) against §7 and the
  simulation table in the PR; every consumer and mirror (JS against Python,
  HUD, clicks, pins, snap, gallery, calibration files, settings-file load);
  config per add-config-field; the UI copy against the approved list; docs.
- Round 3: tests that cannot fail (mutation results), CLAUDE.md rules (no
  em-dash, no issue numbers, help drawer only, status language), unresolved
  review threads via GraphQL before merge.

## Open questions for the product owner

1. Approve the slider spans (-1.0..0.5 and -0.8..0.3), the off-centre zero
   they bring, and the error texts (items 4-7 with 4a, 4b, 5a, 6a).
2. The lens centre UI: the Adjust by hand row and its labels, the result
   line and the Load list (items 10, 11). Review and the Camera Calibration
   chip show the pair only: right, or should Review name the centre too?
3. The Camera tab: the two pixel fields of §12 (disabled without video), or
   no fields there (the centre set only by the Setup Wizard and shown
   read-only)?

## Later: OpenCV calibration import (`.xml`), its own PR

Decided 2026-10-22: after this PR. The analysis stays here for that PR.

Through the existing **Load lens calibration...** dialog: its file chooser
also accepts `.xml`; the file's text goes to a new
`POST /api/wizard/lens/opencv` with the snapshot's width and height; the answer
is the lens (pair and centre), the fit error and any notes, and it loads like
a saved calibration (`S.lensLoaded`), then can be saved by name.

- **Parsing, server-side, untrusted input**: body cap 64 KiB (calibration files
  are about 1 KB); refuse anything with `<!DOCTYPE`, `<!ENTITY` or a processing
  instruction other than the XML declaration before parsing; parse with
  `xml.parsers.expat` with `StartDoctypeDeclHandler` and `EntityDeclHandler`
  raising and `SetParamEntityParsing(XML_PARAM_ENTITY_PARSING_NEVER)`, an
  element-count cap, root `opencv_storage`; read only `cameraMatrix` (3x3,
  `dt` `d`), `dist_coeffs` (4, 5, 8, 12 or 14 values), `isFisheye`, and the
  resolution (`cameraResolution`, else `image_width` / `image_height`); every
  number finite. Malformed input answers 400 with a reason, never 500. Confirm
  bandit stays clean (it flags `xml.etree`, not `xml.parsers.expat`); no new
  dependency.
- **Models**: standard `r(1 + k1 r^2 + k2 r^4 + k3 r^6)` (rational `k4-k6`
  when present), fisheye `θ(1 + k1 θ^2 + ... + k4 θ^8)`. Thin-prism / tilt
  terms non-zero: refused. All-zero coefficients: a lens without distortion,
  loads `(0, 0)`.
- **Fit**: over a screen grid limited to where the file's own curve still
  rises (slope at least 0.2), map each point to the file's pinhole picture and
  fit the division pair plus a free scale (the camera solve's `fov` absorbs
  scale) on screen-pixel error. The answer reports the largest and 95th
  percentile error and the share of the frame covered; the dialog shows them.
- **Tangential terms (p1, p2)**: not representable; left in the target, so
  their cost shows in the reported error; a note when they move the corner
  more than 2 px.
- **Principal point**: becomes the lens centre (§1), clamped to `C_BOUND`
  with a note when it is further off.
- **Resolution / crop**: same aspect as the snapshot: scale the matrix by the
  width ratio (a 3840x2160 file on a 1920x1080 station). Another aspect or no
  resolution in the file: refused with a reason ("made for a picture of
  another shape" / "does not say which picture size it was made for"), since a
  crop cannot be inferred. `fx != fy` by more than 1 %: refused.
- **UI to approve**: the chooser's wording (`.ofsettings` and `.xml`), the
  dialog line for an imported file (name, the values, "fits within N px"),
  the refusal texts, and the help drawer paragraph; whether another aspect is
  refused outright or offered as a centred crop.

All tests use synthetic lenses and synthetic XML written by the tests; no
received file or value from one enters the repo.

## Out of scope

- Weighting calibration pins by the lens Jacobian (§6).
- Warping the video instead of the overlay.
- A third radial term or a non-radial model (tangential, thin prism).
