# Config-only features

Paths needs more testing before operators get it. The code stays on main and in
CI; only the switch goes.

A **config-only** feature is in `FEATURE_KEYS` (so it still gates, hides and loads)
but not offered: it has no row in the Features table and no web route turns it
on. A tester turns it on by adding its key to `[ui] shown_features` in
`config.toml`; import and push carry the list as they find it. Offering it again
is deleting its key from `CONFIG_ONLY_FEATURES`.

- [x] `configuration.py`: `CONFIG_ONLY_FEATURES = {"paths"}` and
      `OFFERED_FEATURES`, `FEATURE_KEYS` minus the config-only, in order.
- [x] Offered keys only: the Features table (`partials/general.tpl`), the
      `features` section in `apply_section_data`, the Station Settings Save, the
      `features` `FIELD_RULES`, and `migrate_ui_keys` (an old
      `show_experimental_features = true` turns on every offered feature).
- [x] Every key still: `_coerce_feature_list` (config.toml), the body classes
      and hide rules in `base.tpl`, every gate.
- [x] A save of the table keeps a config-only key as it is.
- [x] Tests: table rows, form save, section API, crafted POST, migration,
      config.toml turning Paths on, a save keeping it on.
- [x] Docs: `config.md`, `web.md`, `paths.md`; drop the Paths bullet from the
      `general-station.md` help drawer.
