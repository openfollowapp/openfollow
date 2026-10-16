# Plan: "Features" list replaces the experimental toggle

A per-feature list on the General tab replaces the single "Show experimental
features" checkbox. A feature that is off is hidden in the web UI **and** does
not run; its settings are kept, so ticking it again restores it unchanged.
Own PR from `main`.

## Decisions

| Topic | Decision |
|---|---|
| Entries | `zones` (Trigger zones + Zone editor), `midi` (MIDI section + virtual faders' MIDI source), `detection` (whole Person Detection tab: model, tracking, masks), `rttrpm` (RTTrPM output + its interface row), `paths` (Paths, Path editor, Pathfinder / Toggle Paths bindings, OSC path commands, the touch remote's Pathfinder button), `ground_grid` (Ground Grid section), `calibration_points` (Camera group + the wizard's Corner Pinning Advanced section and Review group), `touch_remote` (Touch remote section, launcher and card) |
| Experimental | `detection`, `rttrpm`, `paths`, `ground_grid`, `calibration_points`, `touch_remote` carry the badge, default off. `zones`, `midi` are not experimental, default on |
| Zones | No longer experimental: the warning in `help/trigger_zones.md` goes |
| Lens distortion | Not experimental and not in the list: always shown, always applied, no badge |
| OTP | Not in the list; unchanged (its help still says experimental) |
| Config | `[ui] shown_features: list[str]`, entries from a fixed enum, unknown / duplicate / non-string entries dropped. Missing key ⇒ default (`["zones", "midi"]`) |
| Migration | Old `show_experimental_features = true` with no `shown_features` ⇒ defaults + every experiment; `false` or missing ⇒ defaults. The old key is read once on load and never written again |
| Off means | Hidden in UI + runtime gate: subsystems ask `cfg.ui.feature_on(key)`; no cascade writes. MIDI devices not opened, faders' MIDI source inert, detection / zones / RTTrPM not running, the paths engine disabled, the ground grid neither drawn nor dropping markers, every touch remote route answers 403 |
| Calibration points | Off hides them only: the saved points stay in the config and the solver keeps using them, as today |
| Two-level gates | `detection`, `rttrpm`, `ground_grid`, `touch_remote` keep their own `enabled` switch; a subsystem runs only when its entry is on **and** its switch is on |
| Transfer | Travels with export / import and peer push; the Features group gets "Apply to all stations" |
| UI | General tab "Features" group in place of the experimental checkbox: one checkbox per feature (noun label + inline action label), experimental badge, saves on change; explanations in `help/general-station.md` |

## Steps

### 1. Config (`add-config-field` skill)
- [ ] `FEATURE_KEYS` (ordered) and `EXPERIMENTAL_FEATURES` in
      `configuration.py`; `UiConfig.shown_features` with a `_coerce_*` list
      helper (enum filter, dedupe, keep order); `UiConfig.feature_on(key)`.
- [ ] Remove `show_experimental_features` from `UiConfig`; `load_config`
      migrates it before `_filter_known` drops it (also on import and push
      payloads from older stations).
- [ ] Hot reload: a `shown_features` change re-applies the gated services
      through `_apply_with_fallback`. Toggling `detection` while
      `detection.enabled` follows the existing detection restart rule.
- [ ] `FIELD_RULES` entry for the list and validate markup on its checkboxes.
- [ ] Tests (`tests/test_configuration.py`): valid list, non-list / `None` /
      `"abc"` / `True`, unknown key, duplicate, non-string entries, default
      when missing, migration true / false / absent / both keys present
      (new key wins), TOML round trip, export / import / push carry it.

### 2. Runtime gates (one check per subsystem, no scattered checks)
- [ ] Detection: detector started / kept only when `feature_on("detection")`
      and `detection.enabled`.
- [ ] Zones: zone engine evaluates, sends and draws only when `zones` is on.
- [ ] RTTrPM: sender runs only when `rttrpm` is on and
      `rttrpm_output.enabled`; `uses_interface` follows the same rule
      (config only, per the network invariant).
- [ ] MIDI: MIDI input not opened when `midi` is off; virtual faders with a
      MIDI source read as unbound.
- [ ] Paths: `app._paths.reload(..., enabled=feature_on("paths"))` in `app.py`
      and `runtime/paths_runtime.py`; the `paths_on` remote state and the
      help-overlay binding list follow it. No path's `active` flag is written.
- [ ] Ground grid: `scene/ground_grid.py` and
      `runtime/services_marker_visuals.py` read `feature_on("ground_grid")` in
      place of `show_experimental_features`; `ground_grid.enabled` is never
      written by the gate.
- [ ] Touch remote: `_register_touch_remote_routes` answers 403 and the
      launcher is not rendered unless `touch_remote` is on and
      `touch_remote.enabled`; turning the entry off releases every hold
      through hot reload.
- [ ] Remove `_turn_off_experimental_features` and its call sites.
- [ ] Tests: config → hot-reload per gate (`tests/test_configuration.py`),
      gate → overlay (`tests/test_services_marker_visuals.py`: zones and
      ground grid not drawn, paths not drawn), detection restart rule, RTTrPM
      `uses_interface`, touch remote routes 403 with the entry off, settings
      untouched after off → on.

### 3. Web
- [ ] `POST /settings/features` (replaces `/settings/experimental`): one
      form, a checkbox per key; malformed input ⇒ the enum filter; no
      cascade writes. Peer push and bulk import carry `ui.shown_features`.
- [ ] Gating markup: `.experimental-feature` / `body.show-experimental`
      become `data-feature="<key>"` + `body` classes `feature-<key>` and one
      CSS rule per key generated from `FEATURE_KEYS`; tab fallback in
      `initTabs` checks the owning tab's feature. Wrap: Detection tab and
      its sections, RTTrPM section and interface row, Trigger zones + Zone
      editor, MIDI section, fader MIDI source option, Paths and Path editor
      sections, Pathfinder / Toggle Paths rows in keyboard, gamepad and 3D
      mouse, Ground Grid section, Camera calibration points group, wizard
      `calib-section` and `review-calib`, Touch remote section.
- [ ] `wizard.tpl`: `_show_calib` reads `feature_on("calibration_points")`.
- [ ] Client toggle flips the body class immediately (no reload), as today.
- [ ] Tests (`tests/test_web_helpers.py`, replacing
      `test_web_experimental_toggle.py` / `test_web_experimental_gate.py`):
      route saves list, rejects junk, renders body classes; every
      `data-feature` value in templates is a known key and every key wraps
      at least one element (template scan); `tests/test_status_language.py`
      still passes; checkbox label rule.
- [ ] Help: `general-station.md` "Features" section (what each one is, that
      off also stops it, settings kept, calibration points are only hidden);
      replace "experimental features on/off" wording in `paths.md`,
      `path_editor.md`, `ground_grid.md`, `osc.md`, `keyboard.md`,
      `gamepad.md`, `mouse3d.md`, `camera.md`, `touch_remote.md`; drop the
      experimental note from `trigger_zones.md`; add the badge note to
      `touch_remote.md`.

### 4. Docs
- [ ] `docs/architecture/web.md`: feature gating contract (key enum,
      `data-feature`, `feature_on` is the only runtime check), touch remote
      gate.
- [ ] `docs/architecture/config.md`: `shown_features`, migration, transfer;
      `GroundGridConfig` line names the `ground_grid` entry.
- [ ] `docs/architecture/paths.md`, `hud.md`: the `paths` / `ground_grid`
      entries in place of the experimental switch.
- [ ] `docs/PROJECT_STRUCTURE.md`: drop "(experimental)" wording where it now
      names an entry.
- [ ] Commit: `feat(web): replace the experimental toggle with a per-feature list`.

### 5. Verify
- [ ] `make ci-remote` green; manual: untick each feature, confirm its UI is
      gone and it stops (MIDI device closed, zones not sent, paths released,
      ground grid not dropping, touch remote card gone and routes 403), tick
      again and confirm settings came back.

## Open risks
- Upgrading a station that uses detection but had experimental shown is
  covered by the migration. One with RTTrPM enabled but experimental hidden
  will now have it gated off at runtime (before: it kept sending while
  hidden).
- The touch remote has no experimental gate today. A station with
  `touch_remote.enabled` and experimental hidden loses the remote after the
  upgrade until someone ticks the entry. Open: should the migration also
  tick `touch_remote` when `touch_remote.enabled` is set?
- Turning experimental off today deactivates every path and switches
  detection and the ground grid off. With no cascade, a station upgraded
  with those flags set keeps them, and they run again once their entry is
  ticked.
- Gating MIDI touches the fader dispatch path; regression tests needed.
