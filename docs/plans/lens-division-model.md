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
and several times better on the 2.3-3.2 mm ones.

This plan replaces the polynomial with the division model in this branch. By
product-owner decision it is a clean break: no saved pairs exist anywhere, so
there is no migration, no model tag and no compatibility code, and nothing in
code, docs or UI mentions the previous model.

Scripts behind the numbers (scratch, not in the repo): the model comparison,
the fits below, the inverse convergence and timing, the edge-snap and
edge-chain checks, the distortion-centre simulation, the gallery equivalents.

## 1. The model

    r_u = r_d / (1 + k1 s + k2 s^2),   s = r_d^2

- `r_d` is the **screen** (distorted) radius, `r_u` the **pinhole** radius,
  both about the frame centre and over the frame half-diagonal
  (`0.5 * hypot(w, h)`, so `r_d = 1` at a corner), as today. The normalisation
  stays resolution-independent; a saved calibration still fits another
  resolution of the same picture shape.
- **Sign convention unchanged: barrel is negative.** To first order
  `r_d ≈ r_u (1 + k1 r_u^2 + k2 r_u^4)`, so the two models agree for weak
  lenses and the sliders keep their direction and feel (a 0.005 step still
  moves the corner by about 5.5 px at 1080p, see §5).
- **Paraxial slope 1.** `dr_u/dr_d = 1` at the centre, so the camera's `fov`
  keeps its meaning (the paraxial pinhole field of view, §6).
- A stereographic lens (`2f tan(θ/2)`) is the k1-only model exactly, with
  `k1 = -(D/2f)^2` (`D` the half-diagonal in pixels): an absolute test anchor.
- **Screen to pinhole is closed form** (clicks, detection feet, zone feet,
  calibration pins, the lens fit). **Pinhole to screen needs the inverse**
  (everything the HUD draws, `seen` points, predicted curves), §2.

`apply_overlay_distortion` (pinhole to screen) and `invert_overlay_distortion`
(screen to pinhole) keep their names, signatures and the `(0, 0)` identity
shortcut; only their maths swap roles. Every caller is unchanged.

## 2. Pinhole to screen: the inverse

Solve `g(r) = r / D(r^2) = r_u` for `r = r_d`, `D(s) = 1 + k1 s + k2 s^2`.

- **Domain.** `g` rises from 0 on `[0, r_lim)`, where `r_lim` is the first
  positive root of either `D(s)` (the pole: `r_u` runs to infinity, the ray is
  90° off the axis) or `P(s) = 1 - k1 s - 3 k2 s^2` (the fold: `g' = P / D^2`
  turns negative). §3 makes `r_lim > 1` the validity rule.
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
  float pair, the pair changes only on a save), `np.interp` gives the seed and
  two Newton steps polish it to `2e-16`. The bracketed solve stays as the
  fallback for any element whose residual is still above tolerance. See §9 for
  the cost.

`invert_normalised_radius`, `_fold_radius`, `_DISTORTION_INVERT_ITERS`,
`_DISTORTION_INVERT_SETTLED` and `_DISTORTION_INVERT_R_CAP` go: the screen to
pinhole direction no longer iterates. The new inverse lives in `scene/solver.py`
as `distort_normalised_radius(r_u, k1, k2)` (vectorised, broadcasting like the
current helper, NaN passes through) and `lens_limit_radius` (§3) is its
bracket.

## 3. Validity

A pair is valid when the map is defined and rising over the whole frame:

    D(s) = 1 + k1 s + k2 s^2 > 0     and     P(s) = 1 - k1 s - 3 k2 s^2 > 0     for all s in [0, 1]

(`P / D^2` is `dr_u/dr_d`.) Both are 1 at `s = 0`, so the rule is "no root of
`D` or `P` in `(0, 1]`".

- **Closed form**, mirroring `lens_fold_radius`:
  `lens_limit_radius(k1, k2) = sqrt(min(s_D, s_P))`, each the smallest positive
  root of its quadratic (`q` form for both, as today, so a tiny `k2` keeps both
  roots exact), `inf` when neither has one. `lens_warp_is_valid` stays the
  public check: finite, `max(|k1|, |k2|) <= 1e3`, `lens_limit_radius > 1`.
  Checked against a brute-force scan of 20,000 random pairs: no mismatch.
- **The rule bounds the pair on its own**: the valid region lies inside
  `k1 in (-1.97, 8.5)`, `k2 in (-6, 0.98)`. The `1e3` cap stays only as the
  overflow guard ahead of `k1 * k1`; it no longer binds any valid pair.
  On the axes: `k2 = 0` is valid for `k1 in (-1, 1)`; `k1 = 0` for
  `k2 in (-1, 1/3)`.
- **Star-shaped**: if `(k1, k2)` is valid, so is `t (k1, k2)` for `t in [0, 1]`
  (`D_t = (1 - t) + t D`, `P_t = (1 - t) + t P`). So the pull-back keeps its
  shape and now has a **closed form**: `t* = 1 / max(0, max_s(-k1 s - k2 s^2),
  max_s(k1 s + 3 k2 s^2))` over `s in [0, 1]` (endpoints and vertex), matching
  the bisection to `1e-6` on the same 20,000 pairs.
- **`pull_back_to_fold` becomes `pull_back_to_valid`**: the pair when valid,
  else `t* (1 - PULL_BACK_MARGIN) * (k1, k2)`, `(0, 0)` for a non-finite pair.
  It stays the `CameraConfig.__post_init__` normalisation (CLAUDE.md: a field
  survives a hand-edited `config.toml`) and `_wizard_lens_coeffs`'s, with the
  one WARNING naming both pairs. It is normalisation, not compatibility.
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
the Setup Wizard and a pull-back that is no longer star-shaped. Not done.

## 4. Config keys

**Keep `lens_k1` / `lens_k2`.** The keys, the API fields (`lens_k1`,
`lens_k2`, the saved calibration's `k1` / `k2`, the fit's `k1` / `k2`) and the
operator labels (Barrel / fisheye, Corner correction) all describe the same
two quantities with the same sign and first-order meaning. A rename would touch
over 220 references to the keys alone, plus every `k1` / `k2` in the API,
the calibration files and the fit, to tell old values from
new ones, which the product owner has ruled unnecessary (no saved pairs). The
meaning is defined in one place, `lens_model.py`'s docstring, and the comment
on `CameraConfig`.

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

Step resolution: one 0.005 step moves the screen corner by
`0.005 * D / P(1)` px, at most 5.5 px at 1080p (`P(1) = 1 - k1 - 3 k2 >= 1`
for barrel): the same feel as today. Config bounds: none beyond §3 (no box),
as today.

The default `(0, 0)` is unchanged.

## 6. Field of view and the pinhole solver

- The stored `fov` is the paraxial pinhole field of view, as today. The
  pinhole picture is wider than the screen for barrel: the screen corner sits
  at pinhole radius `1 / D(1)` (2.48 half-diagonals on the 2.3 mm). Paraxial
  `fov` for the table's lenses runs 50-109°, well inside `POSE_FOV_RANGE_DEG`
  (10-170°) and `CameraConfig`'s 1-179°. No bound changes.
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
  lens Jacobian is listed under Out of scope.

## 7. The lens fit from lines (`scene/lens_fit.py`)

It gets simpler and faster.

- `_Lines.undistort` is the closed form: no Newton per grid cell. The seeding
  grid costs one division per point per cell.
- **Seed grid** widens to the new span: `_K1_SCAN = -1.2 .. 0.6 step 0.05`,
  `_K2_SCAN = -1.0 .. 0.4 step 0.02` (37 x 71 cells, 2627, against 1683 today;
  each cell is far cheaper). Cells failing §3 are dropped, as today.
- `_K_BOUND` goes from 1.0 to 2.0, the span §3 allows for k1.
- **Corner uncertainty** (`_corner_uncertainty_px`): the screen corner's
  sensitivity is `dr_d/dk1 = dr_d/dk2 = 1 / P(1)` at `r_d = 1`, so the sigma is
  `D * sqrt(c11 + c22 + 2 c12) / (1 - k1 - 3 k2)`. Today's formula drops the
  `1 / P(1)` factor that the polynomial did not have.
- **Rival spread** (`_rival_spread_px`): `d1 r^3 + d2 r^5` is the polynomial's
  displacement. Replace it by the exact screen gap: for screen radii in
  `[0, 1]`, undistort through the fit and re-distort through the rival.
- **`K2_SIGMA_GATE = 0.05`** keeps its meaning: in both models a k2 change of
  0.05 moves the corner by about 55 px / `P(1)`. Kept, then confirmed by
  re-running `scripts/lens_fit_simulation.py` (its `LENSES` move to the table's
  pairs); the gate and `RATING_THRESHOLDS_PX` change only if the table says so.
  Real lenses have a large k2, so a gate that fits k2 more readily is a likely
  outcome; any change lands with the simulation table in the PR description.
- `curve()` uses `apply_overlay_distortion` (now the iterative direction): 24
  samples a line, negligible.
- The misfit test, the hint and `MAX_LINES` are model-free.

## 8. Edge snap and edge suggestions

Measured on straight lines through strong division lenses (scratch):

- **Reference bows** (`_REFERENCE_K1 = _REFERENCE_K2 = 0.2`): the two-bow
  basis plus end shifts fits a 2.3 mm-like lens to 0.3 px, and the widest
  synthetic lenses to 2.5-3.6 px along the top edge (under `_POINT_WINDOW`,
  6 px). Pincushion references stay valid (`P(1) = 0.4`); the comment's reason
  ("a barrel reference folds") is reworded: both signs are valid now and the
  pincushion pair fits the strong lenses slightly better.
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
  widest lens, times a margin, computed with `apply_overlay_distortion`. The
  curvature bound (`_MAX_CURVATURE_WIDTH = 4.0`) holds: 1.3-2.0 measured.
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
  decides the table.
- The click, detection-foot, zone-foot and pin paths get cheaper: closed form
  instead of today's 24-40 Newton steps.
- Verified on the station with the HUD bench (eight markers, cones, a strong
  lens) before merge; the GL Operator Screen composites what Cairo drew and
  does not warp (`video/gl_display.py` has no lens code).

## 10. Media Gallery defaults

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
pictures barely change. Set `K1, K2` in `scripts/gen_arena_default.py` and
`scripts/gen_concert_default.py`, re-run both (`rsvg-convert` and Pillow, both
present on the Mac), commit the new JPEG + TOML pairs, and let
`tests/test_gallery_calibration.py` (grid corners land on the tape) prove it.
The mockup's `concert.jpg` and `marks.py` take the same picture and pair.

## Options for the product owner (not decided)

### Option A: a distortion centre (two more parameters)

Several received calibrations put the lens centre well off the frame centre,
and on the 6-8 mm lenses that offset is the whole remaining error. The model
then bends about `F + c` instead of the frame centre `F`:

    v = (p - F - c) / D,   v_u = v / D(|v|^2),   pinhole = F + v_u D

so the lens centre becomes the pinhole principal point and the pinhole maths
is untouched (only a translation is added). `c = 0` is today's model.

**Can the line fit find it?** Synthetic decentred division lenses, lines as the
wizard sends them (five points, 1 px placement noise, four edges plus a
diagonal), errors after the free homography the camera solve can absorb:

| Lens | Offset | Centre ignored (max / p95 px) | Centre fitted (max / p95 px) | Centre sigma |
|---|---|---|---|---|
| -0.10 / 0 | 40, -25 px | 3.6 / 1.8 | 1.7 / 0.8 | 30 px |
| -0.10 / 0 | 100, 60 px | 7.9 / 4.3 | 1.8 / 0.8 | 30 px |
| -0.27 / -0.09 | 40, -25 px | 14.5 / 6.9 | 3.4 / 2.5 | 8 px |
| -0.27 / -0.09 | 100, 60 px | 35.1 / 17.9 | 2.9 / 1.9 | 11 px |
| -0.53 / -0.07 | 40, -25 px | 25.4 / 11.4 | 3.5 / 2.7 | 13 px |
| any | 0 | 1-4 | 2-8 (fitting noise) | 9-55 px |

Fitted always, the centre adds 1-4 px of noise on a centred lens. **Gate**: fit
it only when the cost drops by more than 6 residual variances (the 95 % point
of two parameters, the same margin as `_RIVAL_MARGIN`) **and** its predicted
sigma is under 15 px. With that gate (4 edges + diagonal, 12 trials each): a
centred lens fitted it 0-1 times in 12; a moderate lens offset by 20-80 px 9-12
times in 12, largest error 2-3 px median; a weak lens never (its centre costs
under 7 px there). On the strongest lens with an 80 px offset the gate still
misses half the time and the far corner can pass the pole; that case needs
the validity radius below and more simulation before shipping.

What it costs:

- **Config**: `lens_cx`, `lens_cy`, offset from the frame centre in
  half-diagonals (resolution-free like k1, k2), finite, `|c| <= 0.25` (275 px
  at 1080p); validity is checked out to radius `1 + |c|` (the farthest frame
  corner from the lens centre is at most that, without knowing the aspect).
  `effective_lens` returns four values; FIELD_RULES, `add-config-field` tests.
- **Solver / HUD**: one translation in `apply` / `invert`; the `(0, 0)`
  shortcut checks all four; HUD cost unchanged. About 25 call sites pass
  `*lens` already; the rest get the two extra arguments.
- **Wizard**: the fit gains two parameters and the gate; the JS mirrors gain
  the translation; the result line names the centre when fitted; saved lens
  calibrations store `cx`, `cy`.
- **Camera tab**: two number fields, or none (wizard-only, shown read-only).
- **UI to approve**: where the centre shows ("Lens centre: 12 px right, 8 px
  up" in the result line and Review?), whether Adjust by hand gets two number
  fields (in snapshot pixels, stored normalised), and the Camera tab choice.
  Help drawer text for both.

### Option B: import an OpenCV calibration (`.xml`)

Through the existing **Load lens calibration...** dialog: its file chooser
also accepts `.xml`; the file's text goes to a new
`POST /api/wizard/lens/opencv` with the snapshot's width and height; the answer
is a pair (plus a centre with Option A), the fit error and any notes, and it
loads like a saved calibration (`S.lensLoaded`), then can be saved by name.

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
- **Principal point**: with Option A it becomes the centre; without it, it is
  ignored and its cost is in the error (most of it absorbed by the camera
  solve, as the received calibrations show).
- **Resolution / crop**: same aspect as the snapshot: scale the matrix by the
  width ratio (a 3840x2160 file on a 1920x1080 station). Another aspect or no
  resolution in the file: refused with a reason ("made for a picture of
  another shape" / "does not say which picture size it was made for"), since a
  crop cannot be inferred. `fx != fy` by more than 1 %: refused.
- **UI to approve**: the chooser's wording (`.ofsettings` and `.xml`), the
  dialog line for an imported file (name, the two values, "fits within N px"),
  the refusal texts, and the help drawer paragraph.

All tests for both options use synthetic lenses and synthetic XML written by
the tests; no received file or value from one enters the repo.

## UI changes for approval

Against the mockup (`~/.claude/plans/wizard-calibration-mockup/mock/template.html`,
whose own JS lens model, fit grid and sliders change with it):

1. Barrel / fisheye slider: -0.6..0.6 to **-1.0..0.5** (wizard Adjust by hand
   and Camera tab); zero is no longer centred.
2. Corner correction slider: -0.4..0.4 to **-0.8..0.3** (both places); zero is
   no longer centred.
3. Steps unchanged (0.005), number fields unchanged.
4. Wizard field error: "With these two values, the overlay folds over itself
   inside the picture. Set one of the values closer to 0." to **"With these
   two values, the overlay cannot follow the picture out to its corners. Set
   one of the values closer to 0."**
5. Camera tab field error: "This pair folds the overlay inside the frame.
   Bring either value closer to 0." to **"This pair cannot follow the picture
   out to its corners. Bring either value closer to 0."**
6. Settings-file load error: "With the lens values of this file, the overlay
   folds over itself inside the picture." to **"With the lens values of this
   file, the overlay cannot follow the picture out to its corners."**
7. Saving a calibration (server reason): "These lens values fold the overlay
   inside the frame." to **"These lens values cannot follow the picture out to
   its corners."**
8. Help drawer `camera.md`: the limit paragraph (no fold wording, no earlier
   release, new example values: a 100° lens lands around -0.33 / -0.03, a 126°
   one around -0.53 / -0.07) and the slider span sentence.
9. Media Gallery arena and concert pictures re-rendered (at most 1.5 px
   different) with new printed values.
10. Option A and B items above, only if chosen.

The approved Setup Wizard plan (`docs/plans/setup-wizard-calibration-modes.md`)
quotes item 6's old text and the fold wording in its copy tables; those rows
are updated with the approval.

## Checklist

Each step lands with its tests; targeted tests while iterating, one
`make ci-remote` at the end (pi59).

### 0. Approval
- [ ] Update the mockup's lens model (`foldRadius` / `distort` / `undistort` /
      `invertRadius` to the division model, `lensValid` to §3), its fit grid
      and slider attributes, the error copy, `marks.py` to `(-0.20, -0.07)`,
      `concert.jpg` from step 8, rebuild with `build.py`; publish for the
      product owner with the UI list above.
- [ ] Product owner approves the UI list and decides Options A and B.

### 1. The rule (`openfollow/lens_model.py`, stdlib-only)
- [ ] Docstring: the model, normalisation, sign, both limits.
- [ ] `lens_limit_radius`, `lens_warp_is_valid`, `pull_back_to_valid` (closed
      form `t*`), `PULL_BACK_MARGIN` kept.
- [ ] `tests/test_lens_model.py` rewritten (see Tests).

### 2. The warp (`openfollow/scene/solver.py`)
- [ ] `invert_overlay_distortion`: closed form; `apply_overlay_distortion`:
      `distort_normalised_radius` (closed form for k2 = 0, table-seeded Newton,
      bracketed fallback, fold clamp); drop the Newton helpers and constants.
- [ ] `screen_axes_at` comment.
- [ ] `tests/test_solver_distortion.py` rewritten; `tests/test_solver.py`
      lens cases.
- [ ] Timing check against §9 (scratch bench, numbers in the PR).

### 3. Config and web plumbing
- [ ] `configuration.py`: `CameraConfig` comment, `pull_back_to_valid`, the
      WARNING text ("cannot follow the picture out to its corners").
- [ ] `web/validation.py`: `_LENS_LIMIT_ERROR` copy (UI item 5).
- [ ] `web/lens_calibrations.py`: reason text (UI item 7).
- [ ] `web/routes.py`: `_wizard_lens_coeffs` docstring and pull-back.
- [ ] Tests: `test_configuration.py` (wrong type `None` / `"abc"` / `True`,
      NaN, inf, `1e400`, out-of-region pairs pulled back to the closed-form
      point, both limits, hot reload), `test_validation.py`,
      `test_web_helpers.py` (form to config to renderer),
      `test_web_server.py`, `test_wizard_solve_route.py`,
      `test_template_validation_consistency.py`.

### 4. The fit (`openfollow/scene/lens_fit.py`, `scripts/lens_fit_simulation.py`)
- [ ] Closed-form `undistort`, new grid and `_K_BOUND`, corner uncertainty
      with `1 / P(1)`, exact rival spread.
- [ ] Simulation `LENSES` to the §5 pairs; re-run; keep or retune
      `K2_SIGMA_GATE` and `RATING_THRESHOLDS_PX` from the table.
- [ ] `tests/test_lens_fit.py`, `tests/_lens_scene.py`,
      `tests/test_lens_calibration.py` (synthetic stages through 2.3 mm-like
      and corner-heavy pairs, pair back within 0.01).

### 5. Edge snap and suggestions
- [ ] `scene/edge_snap.py`: reference comment, corridor constants from the
      slider ends.
- [ ] `scene/edge_chains.py`: sag bound from the widest lens at the chord's
      position.
- [ ] `tests/test_edge_snap.py`, `tests/test_edge_chains.py`: a straight line
      along the top edge of a 2.3 mm-like and a corner-heavy picture is
      suggested and snapped; a curved stage front is still rejected; a
      brighter parallel edge outside the corridor is not taken.

### 6. Consumers (no signature change; verify and adjust comments)
- [ ] `input/mouse.py`, `services.py`, `runtime/services_detection_pin.py`,
      `runtime/overlay_draw_scene.py`, `overlay_draw_zones.py`,
      `overlay_draw_paths.py`, `runtime/overlay_picture.py` comment,
      `input/control_frame.py`.
- [ ] Tests: `test_input_mouse.py`, `test_services_detection_pin.py`,
      `test_services_zone_orchestration.py`, `test_overlay_draw_*`,
      `test_services_marker_visuals.py` (input to overlay), `_hud_scenes.py`
      pairs; HUD cost tests unchanged in call counts.
- [ ] `_DISTORTION_SUBDIVISIONS` measured on the strongest lens (§8).

### 7. Web UI
- [ ] `partials/camera.tpl`: slider spans (UI items 1, 2).
- [ ] `wizard.tpl`: slider spans, `wizLensLimitRadius`, `wizLensIsValid`,
      `wizApplyDistortion` (table-free bracketed Newton is enough in the
      browser), `wizInvertDistortion` (closed form), copy (items 4, 6), the
      constants block.
- [ ] `tests/test_wizard_js.py` (runs in node on GitHub Linux only): limit
      radius and validity equal the server's, the inverse equals the server's
      on a grid of pairs, a fitted pair shown as valid, the settings-file pair
      taken exactly when the station would.

### 8. Media Gallery
- [ ] `scripts/gen_arena_default.py`, `scripts/gen_concert_default.py`: new
      `K1, K2`, `pull_back_to_valid` assert, `CLIP` comment; re-run; new
      `arena_default.jpg/.toml`, `concert_default.jpg/.toml`.
- [ ] `tests/test_gallery_calibration.py` green (tape test needs the detection
      extra locally).

### 9. Docs
- [ ] `docs/architecture/web.md` (Lens distortion section: model, rule,
      inverse, fit changes, snap corridor, sag rule), `docs/architecture/video.md`
      (gallery pairs), `docs/architecture/config.md` (calibration files: the
      validity check), `docs/PROJECT_STRUCTURE.md` (`lens_model.py`,
      `solver.py` lines), `docs/plans/setup-wizard-calibration-modes.md` copy
      rows.
- [ ] Help drawer: `openfollow/web/help/camera.md` (UI item 8),
      `openfollow/web/help/wizard-lens.md` if the copy changes.
- [ ] Sweep: `grep -rn "fold\|r\^4\|1 + k1" openfollow docs scripts tests`
      for leftovers; no em-dash.

### 10. Options (only if chosen)
- [ ] Option A: config fields (add-config-field), solver translation,
      `effective_lens`, fit + gate, JS mirrors, calibration files, UI, help,
      simulation table in the PR.
- [ ] Option B: parser, converter, endpoint, dialog, help; tests with
      synthetic standard and fisheye XML, hostile XML (DOCTYPE, entity
      expansion, oversize, wrong shapes, non-finite, `True`, text), aspect and
      resolution cases.

### 11. Verify
- [ ] `make ci-remote` green on pi59.
- [ ] Station check on pi66 (ask the operator; check `.overlay-ref` first):
      Media Gallery concert picture, grid on the tape, mouse drags a marker to
      the cursor in a corner, HUD bench with eight cone markers within §9.
- [ ] Setup Wizard Lens step on a synthetic 2.3 mm picture (rendered with
      `_lens_scene`): suggestions along the edges, fit within 0.01, Review.
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
- **Validity at its exact edges**: `k2 = 0` valid on `(-1, 1)` and not at
  either end; `k1 + k2 = -1` (pole at the corner) and `k1 + 3 k2 = 1` (fold at
  the corner) invalid, a hair inside valid; an interior vertex case for each
  quadratic; a tiny `k2` against a large `k1` (the `q` form); the region's
  boundedness; the closed-form pull-back equal to bisection on a grid.
- **Inverse**: barrel radius past 1000x the corner converges; a pincushion
  radius past the fold lands on the fold radius; NaN passes; broadcasting a
  grid of pairs; the table seed and the bracketed solve agree to `1e-12`.
- **Config per add-config-field**: wrong type (`None`, `"abc"`, `True`) per
  field, NaN, inf, overflow, each limit, one parametrize per failure mode;
  `POST /section/camera` crafted bodies; FIELD_RULES agree with the server.
- **Integration seams**: form to config to renderer; config to hot reload;
  input to overlay with a strong lens (a click in a corner lands under the
  cursor's stage point).
- **Mutation-checked**: commit first, then `mutmut` on `lens_model.py` and the
  two warp functions (clean `mutants/` before a module run); every surviving
  mutant gets a test or a reason in the PR.
- **Hermetic and fast**: no real window, no wall clock in assertions, timing
  only in the scratch bench, never in the suite.
- **Synthetic lenses only.** No received calibration or value derived from one
  in any test, fixture, doc or commit.

## Review plan

- At least three review rounds, and more until a round finds only cosmetic
  issues; after round three, rare edge cases are noted, not chased.
- Round 1: the maths (both directions, validity, pull-back, the inverse's
  bracket) against the absolute tests and this plan.
- Round 2: every consumer and mirror (JS against Python, HUD, clicks, pins,
  fit, snap, gallery), the UI copy against the approved list, docs.
- Round 3: tests that cannot fail (mutation results), CLAUDE.md rules (no
  em-dash, no issue numbers, help drawer only, status language), unresolved
  review threads via GraphQL before merge.

## Open questions for the product owner

1. Approve the slider spans (-1.0..0.5 and -0.8..0.3), the off-centre zero
   they bring, and the four error texts.
2. Option A (distortion centre): build it in this PR, later, or not at all;
   if built, where it shows (result line, Review, Adjust by hand, Camera tab).
3. Option B (OpenCV import): build it, later, or not at all; refuse another
   aspect outright, or offer a centred crop?
4. Gallery: the rounded equivalents above, or a pair matching one of the
   common lenses (for instance the 4.0 mm synthetic) for the concert picture?

## Out of scope

- Weighting calibration pins by the lens Jacobian (§6).
- Warping the video instead of the overlay.
- A third radial term or a non-radial model.
