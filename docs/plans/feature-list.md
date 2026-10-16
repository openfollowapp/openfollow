# Plan: "Features" list replaces the experimental toggle

A per-feature list on the General tab replaces the single "Show experimental
features" checkbox. A feature that is off is hidden in the web UI **and** does
not run; its settings are kept, so ticking it again restores it unchanged.
Own PR from `main`.

## Decisions

| Topic | Decision |
|---|---|
| Entries | `zones` (Trigger zones + Zone editor), `midi` (MIDI section + virtual faders' MIDI source), `lens_distortion` (Camera "Lens distortion" group + the wizard's Lens step), `psn_input` (markers received from other stations; no UI is hidden), `detection` (whole Person Detection tab: model, tracking, masks), `rttrpm` (RTTrPM output + its interface row), `paths` (Paths, Path editor, Pathfinder / Toggle Paths bindings, OSC path commands, the touch remote's Pathfinder button), `ground_grid` (Ground Grid section), `calibration_points` (Camera group + the wizard's Corner Pinning Advanced section and Review group), `touch_remote` (Touch remote section, launcher and card). The Toggle Zones bindings go with `zones` |
| Experimental | `detection`, `rttrpm`, `paths`, `ground_grid`, `calibration_points`, `touch_remote` carry the badge, default off. `zones`, `midi`, `lens_distortion`, `psn_input` are not experimental, default on |
| Zones | No longer experimental: the warning in `help/trigger_zones.md` goes |
| Lens distortion | Not experimental, default on. Off: the Camera group is hidden, the wizard skips its Lens step (Video Source → Camera Position) and Apply leaves the saved k1/k2 untouched, and every reader treats k1 = k2 = 0 |
| OTP | Not in the list; unchanged (its help still says experimental) |
| Config | `[ui] shown_features: list[str]`, entries from a fixed enum, unknown / duplicate / non-string entries dropped, stored in `FEATURE_KEYS` order. Missing key ⇒ default (`["zones", "midi", "lens_distortion", "psn_input"]`) |
| Migration | Old `show_experimental_features = true` with no `shown_features` ⇒ defaults + every experiment; `false` or missing ⇒ defaults. The old key is read once on load and never written again. `touch_remote` needs no special case: no release with it has shipped |
| Off means | Hidden in UI + runtime gate: subsystems ask `cfg.ui.feature_on(key)`; no cascade writes. Lens k1/k2 treated as 0, the PSN receiver not started, MIDI devices not opened, faders' MIDI source inert, detection / zones / RTTrPM not running, the paths engine disabled, the ground grid neither drawn nor dropping markers, every touch remote route answers 403 |
| PSN input | Off stops the receiver: other stations' markers are neither drawn, nor counted by zones, nor reported online. The viewed-markers setting stays visible and is kept |
| Calibration points | Off hides them only: the saved points stay in the config and the solver keeps using them, as today |
| Two-level gates | `detection`, `rttrpm`, `ground_grid`, `touch_remote` keep their own `enabled` switch; a subsystem runs only when its entry is on **and** its switch is on |
| Transfer | Travels with export / import and peer push; the Features group gets "Apply to all stations" |
| UI | General tab "Features" group in place of the experimental checkbox: one checkbox per feature (noun label + inline action label), experimental badge, saves on change; explanations in `help/general-station.md` |

## Steps

### 1. Config (`add-config-field` skill)
- [x] `FEATURE_KEYS` (ordered) and `EXPERIMENTAL_FEATURES` in
      `configuration.py`; `UiConfig.shown_features` with a `_coerce_*` list
      helper (enum filter, dedupe, `FEATURE_KEYS` order); `UiConfig.feature_on(key)`.
- [x] Remove `show_experimental_features` from `UiConfig`; `load_config`
      migrates it before `_filter_known` drops it (also on import and push
      payloads from older stations).
- [x] Hot reload: a `shown_features` change re-applies the gated services
      through `_apply_with_fallback`. Toggling `detection` while
      `detection.enabled` follows the existing detection restart rule.
- [x] `FIELD_RULES` entry for the list and validate markup on its checkboxes.
- [x] Tests (`tests/test_configuration.py`): valid list, non-list / `None` /
      `"abc"` / `True`, unknown key, duplicate, non-string entries, default
      when missing, migration true / false / absent / both keys present
      (new key wins), TOML round trip, export / import / push carry it.

### 2. Runtime gates (one check per subsystem, no scattered checks)
- [x] Lens: one `effective_lens(cfg) -> (k1, k2)` returning `(0, 0)` when
      `lens_distortion` is off; every `lens_k1/k2` reader goes through it
      (`services.py`, `input/mouse.py`, `runtime/services_marker_visuals.py`,
      `runtime/services_detection_pin.py`, `runtime/overlay_state.py`,
      `runtime/overlay_draw_scene.py`, `overlay_draw_paths.py`,
      `overlay_draw_zones.py`, the scene / wizard routes). Validation of the
      saved pair is unchanged.
- [x] PSN input: `init_psn_receiver` starts the receiver only when
      `feature_on("psn_input")`, and hot reload starts / stops it through
      the same path as an interface change. Consumers (HUD viewer markers,
      zones, marker online state) already handle a receiver with no
      markers; none reads the feature itself.
- [x] Detection: detector started / kept only when `feature_on("detection")`
      and `detection.enabled`.
- [x] Zones: zone engine evaluates, sends and draws only when `zones` is on.
- [x] RTTrPM: sender runs only when `rttrpm` is on and
      `rttrpm_output.enabled`; its network plane follows the same rule
      (config only, per the network invariant).
- [x] MIDI: MIDI input not opened when `midi` is off; virtual faders with a
      MIDI source read as unbound.
- [x] Paths: `app._paths.reload(..., enabled=feature_on("paths"))` in `app.py`
      and `runtime/paths_runtime.py`; the `paths_on` remote state and the
      help-overlay binding list follow it. No path's `active` flag is written.
- [x] Ground grid: `scene/ground_grid.py` and
      `runtime/services_marker_visuals.py` read `feature_on("ground_grid")` in
      place of `show_experimental_features`; `ground_grid.enabled` is never
      written by the gate.
- [x] Touch remote: `_register_touch_remote_routes` answers 403 and the
      launcher is not rendered unless `touch_remote` is on and
      `touch_remote.enabled`; turning the entry off releases every hold
      through hot reload.
- [x] Remove `_turn_off_experimental_features` and its call sites.
- [x] Tests: config → hot-reload per gate (`tests/test_configuration.py`),
      gate → overlay (`tests/test_services_marker_visuals.py`: zones and
      ground grid not drawn; mouse with no lens), detection swap and reload
      paths, touch remote routes 403 with the entry off, settings untouched
      after off → on.

### 3. Web
- [x] `POST /section/features` (replaces `/settings/experimental`): one
      form, a checkbox per key; malformed input ⇒ the enum filter; no
      cascade writes. Peer push and bulk import carry `ui.shown_features`.
- [x] Gating markup: `.experimental-feature` / `body.show-experimental`
      become `data-feature="<key>"` + `body` classes `feature-<key>` and one
      CSS rule per key generated from `FEATURE_KEYS`; tab fallback in
      `initTabs` checks the owning tab's feature. Wrap: Detection tab and
      its sections, RTTrPM section and interface row, Trigger zones + Zone
      editor, MIDI section, fader MIDI source option, Camera "Lens distortion"
      group, Paths and Path editor
      sections, Pathfinder / Toggle Paths rows in keyboard, gamepad and 3D
      mouse, Ground Grid section, Camera calibration points group, wizard
      `calib-section` and `review-calib`, Touch remote section.
- [x] `wizard.tpl`: `_show_calib` reads `feature_on("calibration_points")`;
      `_steps` drops `lens` when `lens_distortion` is off, and Apply then
      sends no lens pair (the saved one stays).
- [x] Client toggle flips the body class immediately (no reload), as today.
- [x] Tests (`tests/test_web_features.py`, `tests/test_web_features_gate.py`,
      replacing `test_web_experimental_toggle.py` / `test_web_experimental_gate.py`):
      route saves list, rejects junk, renders body classes; every
      `data-feature` value in templates is a known key and every key except
      `psn_input` wraps at least one element (template scan); `tests/test_status_language.py`
      still passes; checkbox label rule.
- [x] Help: `general-station.md` "Features" section (what each one is, that
      off also stops it, settings kept, calibration points are only hidden,
      lens off draws and maps with no lens);
      replace "experimental features on/off" wording in `paths.md`,
      `path_editor.md`, `ground_grid.md`, `osc.md`, `keyboard.md`,
      `gamepad.md`, `mouse3d.md`, `camera.md`, `touch_remote.md`; drop the
      experimental note from `trigger_zones.md`; add the badge note to
      `touch_remote.md`.

### 4. Docs
- [x] `docs/architecture/web.md`: feature gating contract (key enum,
      `data-feature`, `feature_on` is the only runtime check), touch remote
      gate.
- [x] `docs/architecture/config.md`: `shown_features`, migration, transfer;
      `GroundGridConfig` line names the `ground_grid` entry.
- [x] `docs/architecture/paths.md`, `hud.md`: the `paths` / `ground_grid`
      entries in place of the experimental switch.
- [x] `docs/PROJECT_STRUCTURE.md`: drop "(experimental)" wording where it now
      names an entry.
- [x] Commit: `feat(web): replace the experimental toggle with a per-feature list`.

### 5. Verify
- [ ] `make ci-remote` green (local suite, lint, mypy, bandit green; no testing Pi reachable from the cloud session); manual: untick each feature, confirm its UI is
      gone and it stops (MIDI device closed, zones not sent, paths released,
      ground grid not dropping, PSN receiver thread gone and other
      stations' markers not drawn, lens overlay straight and the wizard
      without its Lens step, touch remote card gone and routes 403), tick
      again and confirm settings came back.

### 6. Placement: a table under Station Settings › Advanced Settings
The list sits in the Station Settings box, inside its Advanced Settings
disclosure, as a `data-table` with a checkbox, the feature's title and a
one-line description per row. The separate Features section goes.
- [x] `labels.py`: `FEATURE_LABELS` holds (title, description) per key.
- [x] `partials/general.tpl`: the Features group and its table inside Advanced
      Settings; the box keeps saving on change, `onFeatureToggle` and
      **Apply to all stations** unchanged; the Features section removed.
- [x] `base.tpl`: table styles from the theme tokens only.
- [x] Help `general-station.md`: the Features entry names its new place.
- [x] Tests: one row per `FEATURE_KEYS` in order, title as the checkbox's
      label, description from `FEATURE_LABELS`, checked state from
      `feature_on`, the table inside the Advanced Settings disclosure, no
      Features section left.
- [ ] `make ci-remote` green.

### 7. Applied on Save, with a warning, never broadcast
A change to the list waits for the Station Settings **Save**: switching a
feature off mid-show must be deliberate.
- [ ] The checkboxes join the Save's form (`form="general-network-section"`
      plus a `features_submitted` marker); `/section/general` applies the list
      in the same locked write and refreshes the page when the touch remote's
      launcher appears or goes. A POST without the marker leaves the list alone.
- [ ] `/section/features` removed: nothing posts to it.
- [ ] Saving with a box switched off opens a modal naming each such feature
      and what stops with it (`FEATURE_LABELS` gains the line); Cancel sends
      nothing, the red **Turn off** saves.
- [ ] The page flips its `feature-<key>` classes after a successful save only.
- [ ] **Apply to all stations** removed; `features` joins
      `_BROADCAST_EXCLUDED_SECTIONS`.
- [ ] Help, `docs/architecture/web.md` and this plan describe the Save.
- [ ] Tests: the general save applies the list on and off, a POST without the
      marker keeps it, a crafted value, the refresh header, the modal's
      per-feature lines, the broadcast refused, no broadcast button, the
      removed route.
- [ ] `make ci-remote` green.

## Open risks
- Upgrading a station that uses detection but had experimental shown is
  covered by the migration. One with RTTrPM enabled but experimental hidden
  will now have it gated off at runtime (before: it kept sending while
  hidden).
- Turning experimental off today deactivates every path and switches
  detection and the ground grid off. With no cascade, a station upgraded
  with those flags set keeps them, and they run again once their entry is
  ticked.
- Lens off changes the picture-to-stage mapping (mouse, detection pin,
  zone feet), not just the overlay; a station that unticks it needs its
  corners pinned again before a show.
- Gating MIDI touches the fader dispatch path; regression tests needed.
