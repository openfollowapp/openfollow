# Status & Warning Language

The web UI and the HUD show state with one visual language: four levels, one set
of colour tokens per level, and a fixed set of components. This document is the
canonical reference. A new element picks a level and an existing component and
uses the tokens; it never introduces a colour, an alpha, a radius or an icon of
its own. That is what keeps the UI from growing a new red for every feature.

If nothing here fits a new element, extend this document in the same change,
with the reasoning, before adding the variant.

## Levels

| Level | Means | Examples |
|---|---|---|
| **Error** | It does not work, and will not until someone acts | Video unreachable, a refused save, a missing controller, an OSC binding that can never fire, a marker two stations control, detection or 3D Mouse support unavailable |
| **Caution** | It works, with a limitation worth knowing | A feature marked experimental, a marker no station controls, a web UI pin that missed and serves on every interface |
| **Info** | Nothing is wrong; progress or a fact | Starting, idle, restarting, an update is available, a value Save will correct |
| **Success** | Something just worked, or is healthy | Saved, connected, running, marker added |

A feature that is switched off on purpose is **neutral**, not a level: the grey
chip and the plain grey dot. "Off" is never red.

**Pick the level by consequence, not by mechanism.** An unresolved OSC
placeholder leaves Save usable, but the binding can never fire, so it is an
error. A marker the station does not control inside an OSC binding is also an
error for that binding; the same marker in the marker table, controlled by no
station at all, is a caution. Ask what the operator loses if they ignore it.

## Tokens

Web tokens are CSS custom properties on `:root` in
[`base.tpl`](../openfollow/web/templates/base.tpl). Templates, partials and
scripts use the tokens or the component classes below, never literal colours
or inline `style` colours.

| Token | Error | Caution | Info | Success |
|---|---|---|---|---|
| `--<level>-fill` (box) | `#6b1414` at 80% | `#ffbc00` at 12% | `#2664b0` at 20% | `#3d9a60` at 20% |
| `--<level>-border` (box) | `#B02626` | `#ffbc00` at 60% | `#2664b0` at 50% | `#3d9a60` at 50% |
| `--<level>-chip` (chip fill) | `#6b1414` at 80% | `#9b7200` at 80% | `#173d6b` at 80% | `#255e3a` at 80% |
| `--<level>-line` (solid line) | `#B02626` | `#ffbc00` | `#2664b0` | `#3d9a60` |
| `--<level>-text` (inline text) | `#f44848` | `#ffbc00` | `#4f8dd9` | `#5cc98c` |
| `--<level>-mark` (dots) | `#e04848` | none | none | `#5cc98c` |
| `--<level>-mark-muted` (dot centre or ring) | `#e04848` at 40% | none | none | `#5cc98c` at 40% |
| `--error-row` (row tint) | `hsl(2 64% 14%)`, opaque | | | |

Text inside a box, chip or row is the normal off-white `--text`, with the next
step in `--muted`. Never pink, never a tinted text colour on a tinted fill.

Chips use a darker, stronger fill than boxes. At 20% a chip reads almost like
the grey neutral chip, so each chip fill is its level colour darkened to 61%
(the error box's own recipe, `#B02626` to `#6b1414`) at 80%, with a solid
`--<level>-line` border.

Small marks (dots, rings) use the brighter `--<level>-mark` tones, because the
surface colours vanish at dot size on the dark page.

The HUD carries the error level as `COLOR_WARNING_FILL` and
`COLOR_WARNING_BORDER` in
[`overlay_draw_style.py`](../openfollow/runtime/overlay_draw_style.py), the same
`#6b1414` at 80% and `#B02626`. Caution, info and success are `COLOR_CAUTION_*`,
`COLOR_INFO_*` and `COLOR_SUCCESS_*`, each the web chip of its level: fill
`--<level>-chip`, border `--<level>-line`. `STATUS_LEVEL_COLORS` maps a level
name to the three.

### Why the row tint is opaque

The page background is a dark green gradient. A red at low alpha blends with it
to brown: a light red at 12% lands at hue 22°, and alpha tints of `#B02626`
at 10° to 36°. Only a solid colour keeps the error hue (2°), so `--error-row` is
opaque. Do not replace it with an alpha tint.

## Components

### Boxes

One class per level: `.notice.error`, `.notice.warning` (caution), `.notice`
(info), `.notice.success`. Fill, 1px border and icon from the level's tokens;
radius 6px. The icon sits at the left: an off-white warning triangle for
error, an off-white circle with an "i" for caution and info, an off-white
circle with a check for success.

A box says what the station observed on its main line (off-white, 600) and
one next step on its second line (`--muted`, 400). The two never blur into one
sentence.

The dialog and diagnostics errors, the Media Gallery error, the network
result, the Detection progress and result, the Statistics warning and the NDI
box are all one of these four. Do not add an inline-styled box.

A box that a poll re-renders must not carry `role="alert"`: every swap inserts
it again, and a screen reader announces it again. `hx-preserve` does not help,
because htmx moves the preserved node out of the page and back in. Either the
poll answers 204 while what it shows is unchanged (the 3D Mouse section), or
the box has no role and an announcer outside the swap speaks it (Live
Statistics, `web/live_alerts.py`).

### Status chips

`.stat-chip` with `.off` (error), `.warn` (caution), `.info`, `.ok` (success),
or no modifier for neutral. Pill-shaped, uppercase, `--<level>-chip` fill,
solid `--<level>-line` border, off-white text, **no icon**. "Starting" and
"Idle" are info; "Off" is neutral. The OSC diagnostics health pill and the
Experimental badge (caution) are chips too, and so are the state words in a
table row: "Conflict" and "Not controlled" (caution) in the marker catalog. A
row places a chip, it never restyles one.

### Small row pills

Free text inside a row, like the OSC binding fault pill's list of reasons:
chip colours, radius 0.4rem, 0.7rem mixed-case text. A state word is a chip,
never a row pill. The footer's "Update available: v0.4.4" is one too, in the
info chip colours.

The one state word that is a row pill is the Controller Slots state, which
shares a line with the controller's kind: "Connected" and "Reserved" in the
neutral chip colours, "Missing" in the error chip colours. The notes under a
controller's name are caution row pills where they affect control ("Buttons not
recognised", "Button map is for another model"); "Can't identify" is a fact,
not a fault, and is plain `--muted` text. An OSC binding whose trigger shares
its input with a gamepad or keyboard action carries a caution row pill naming
that action in its summary.

### OSC placeholder pills

| Token state | Look |
|---|---|
| Valid placeholder | `--success-fill` fill, `--success-border` border, `--success-text` text |
| Unresolved (the binding cannot fire) | `--error-row` fill, `--error-line` border, off-white text |
| Not a placeholder (sent as literal text) | No pill: plain off-white text with a `--error-text` wavy underline |

The gold accent is never a state colour. A `:cN` controller reference is only
checked while running, so it never shows as unresolved.

### Pill labels

Every chip and pill centres its label on the cap height. The page falls back to
the system font, whose line box sits a label up to 2px high, so a pill sets only
`--pill-pad-y` and its inline padding, and the shared pill rule at the end of
`base.tpl`'s style trims the line box (`text-box: trim-both cap alphabetic`) and
recomputes the vertical padding so the pill keeps its size. A new pill joins
that rule's selector list. Never give a pill its own vertical padding or
`display: flex`: the trim does not apply to a flex box.

### State dots

Online, on, healthy and their failures, on the web (OSC bindings, the station
list, the controller activity dot):

| State | Look |
|---|---|
| On / online / in use | 10px `--success-mark` centre, 2px `--success-mark-muted` border |
| Broken / offline | 8px `--error-mark-muted` centre, 3px solid `--error-mark` border |
| Off / idle | The plain 0.55rem `--muted` dot |

Both coloured dots are 14px outside. Use whole-pixel borders (a fractional one
rounds down at 1x and the two drift apart), `background-clip: content-box` (or
the fill shows through the translucent border and the muted ring vanishes),
and a -2.6px margin so names stay aligned with the grey dot.

### Tables

Every table is `.data-table`: text at the row buttons' size (`--btn-font-sm`),
cells vertically centred, headers muted and semi-bold at that size, rows divided
by `--border-soft`. A row's actions sit in `td.row-actions` at the right edge,
as small buttons. A table sets none of this itself; the chips and row pills in
it keep their own size. [`tests/test_web_tables.py`](../tests/test_web_tables.py)
holds the line.

### Table rows

| Row | Look |
|---|---|
| A fault row (missing controller slot, marker conflict) | `--error-row` tint, the state in error colours, no outline |
| Station list: this station | Info box colours: `--info-fill` and `--info-border` |
| Station list: online | Success box colours |
| Station list: offline | `--error-row` tint with an `--error-line` border; never faded |
| A broken OSC binding | `--error-line` outline, no fill |
| An OSC binding marker the station does not control | Red `--error-mark` dot, `--error-text` text |

### Borders and flashes

| Element | Look |
|---|---|
| Invalid / valid preview (Setup Wizard) | 1px `--error-line` / `--success-line` |
| Failed / successful save | 2px ring for 0.55 s in `--error-line` / `--success-line` |
| Invalid field, a control marked unresolved, or a binding another field took | `--error-line` border plus a 1px `--error-line` shadow |
| An OSC trigger on an input a gamepad or keyboard action also uses | `--caution-line` border plus a 1px `--caution-line` shadow |

### Inline text

Error, caution, info and success text take their `--<level>-text`, keeping the
weight and size of the element. Inline error text has no icon. Inline
confirmations lead with the success sign in `--success-text` (a filled green
circle with the check cut out).

A binding edit that takes an input from another field notes it in both: info
text on the field that took it, error text (`.field-warn-msg`, which leaves Save
open) on the field that lost it, whose action does nothing until it is bound
again. A caution under an OSC trigger (`.field-caution-msg`) names the action
that fires on the same press.

A failed save is a red line under the form's actions, what the station
observed plus one next step, next to the red ring. It is not a box and never a
toast.

### Toast

The toast only confirms: success fill and border over the opaque dark base,
off-white text, led by the success sign. A failure never uses the toast.

### Destructive actions

Delete, Discard, Forget, Restore Defaults and Restart are not states, but red
already means "stop or lose something", so they take the error red rather than a
second, paler one:

| Control | Look |
|---|---|
| Danger button (`button.danger`, `.btn-danger`, a dialog's confirm) | Outline: `--error-text` text on a `--error-line` border |
| Text-only delete (Templates dialog, Detection Masks, Media Gallery ×) | `--error-text` |
| Any of them under the pointer | `--error-row` fill; a disabled one does not react |

### Confirmation

A question before an action is asked in OpenFollow's own modal, never the
browser's `confirm()`, `alert()` or `prompt()`. The browser's dialog is titled
with the station's address, has no danger button, and once a browser has muted
a page's dialogs it answers "no" without showing anything, so the action
silently never happens.

- The title asks about the action ("Delete zone?"), the text says what it
  costs, and the buttons are Cancel and one named after the action ("Delete",
  "Restart"), never "OK".
- A destructive action (above) confirms with the danger button.
- Esc, the close button and the backdrop cancel.
- A script awaits `modalConfirm({title, message, confirmLabel, danger})` from
  `base.tpl`. An `hx-confirm` goes through the same modal: it names its button
  with `data-confirm-label` and its title with `data-confirm-title`, and a
  destructive one adds `data-confirm-danger`.

[`tests/test_save_feedback.py`](../tests/test_save_feedback.py) fails a native
dialog, an `hx-confirm` without a named button, and a destructive confirm
without the danger button.

### Selection and drawings

Selection is the accent, never a level colour: the selected Media Gallery tile,
the active Setup Wizard step and the selected Detection Mask take a gold border
on `--accent-soft`. A finished Setup Wizard step is `--success-text`.

Drawings use the text tones: the Setup Wizard's axes keep X red, Y green,
Z blue as `#f44848`, `#5cc98c` and `#4f8dd9`, labels at full strength and lines at
their own alpha; drawn Detection Mask shapes are `--success-mark`, and the
first corner of one being drawn is the accent. A drawing sets these as SVG
attributes, so it spells the token's value.

### HUD

- The status rows, the Settings ERROR box, the bottom-left panel in a failure
  state and a missing controller's marker card use the error fill and border.
  The marker card keeps its marker-coloured border.
- Status rows and the ERROR box lead with the off-white warning sign
  (`draw_warning_sign`).
- An info status row takes the info chip colours (`COLOR_INFO_FILL`,
  `COLOR_INFO_BORDER`), led by the off-white "i" sign (`draw_info_sign`). A
  caution row takes the caution chip colours the same way, led by the same sign.
- The on-screen Network screen: a notice is a row in its level's fill and border,
  led by its sign (`draw_level_sign`), its text wrapped rather than cut. An
  interface pill that names a state takes its level's chip colours (a link-local
  address is an error, worded `fallback` when DHCP gave it and `link-local` when it
  was set by hand, an interface the web UI does not answer on is info); how
  the address was come by, and no address at all, are the neutral grey. An
  action's result line takes its level; a confirmation is a success row, led by
  the off-white check, as the web's success box.
- An offline marker shows an off-white disc with a cut-out cross.
- The online dot and confirmations use the success mark (`COLOR_OK`,
  `#5cc98c`); a confirmation such as "Detection Complete!" leads with the
  success sign (`draw_success_sign`).
- Text uses the HUD's normal colours, never a red or pink text. A notice that
  is neither a fault nor a state, like the About screen's safety line, is
  plain bold off-white.

## Corner radius

- **Web boxes:** 6px, every level and every box.
- **Chips and pills:** fully rounded. Small row pills: 0.4rem.
- **HUD:** named per nesting level in `overlay_draw_style.py`:
  - `MODAL_RADIUS` (14) for the frame around full-screen menus such as Settings.
  - `PANEL_RADIUS` (6) for panels, cards, message cards and badge rows.
  - `ROW_RADIUS` (4) for rows inside a panel.
  - Progress bars keep their own 2.5 and 3.

An inner corner is never rounder than its container.

## Contrast

Every text colour passes WCAG AA (4.5 : 1) against the background it actually
sits on. Measure the rendered pixel, not the token: a translucent fill changes
with whatever is under it. Two numbers that set the tokens:

- Red text passes only near full saturation. Pure `#ff0000` reaches 4.14 : 1 on
  the page, so the error text is `#f44848` (4.6 to 4.9 : 1), not the dot's
  `#e04848` (4.1 : 1).
- Text on a red fill is off-white (15 : 1), not red (4.7 : 1).

## Adding or changing an element

1. Pick the level by consequence.
2. Pick an existing component. If none fits, extend this document first.
3. Use the tokens and component classes. No literal colours, alphas or radii
   in templates, partials or scripts.
4. Never: a pink or light red, a low-alpha red on the page, the gold accent for
   a state, an icon in a chip, a toast for a failure, a new corner radius, a
   browser dialog.
5. Check the contrast of every text on its real background.
6. Compare variants rendered together in one image on a flat background. Two
   separate screenshots sit on different parts of the page gradient and cannot
   be compared.

[`tests/test_status_language.py`](../tests/test_status_language.py) holds the
line: each `:root` token must match the table above, a rule for a status class
may use only tokens and neutral greys, and the retired one-off colours stay
out of the markup. Add a new status class to its selector list.
