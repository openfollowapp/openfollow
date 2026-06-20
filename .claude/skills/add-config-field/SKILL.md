---
name: add-config-field
description: >-
  Use when adding, removing, or changing a field on any OpenFollow config
  dataclass (AppConfig, CameraConfig, GridConfig, MarkerConfig, ControllerConfig,
  DetectionConfig, OscConfig, OtpOutputConfig, RttrpmOutputConfig,
  TriggerZonesConfig, ...) or adding a new editable field to a web settings form.
  Triggers on "add a config field", "new setting", "config option", "settings
  form field", "FieldRule", "web form validation". Walks the full vertical slice:
  dataclass normalisation, web-form markup, FieldRule registration, tests, and docs.
---

# Add / change a config field (the full contract)

A config field in OpenFollow is only "done" when it survives a hand-edited
`config.toml` **and** a crafted `POST /section/<name>` without crashing or
silently mis-rendering – and when its web form, validation rule, tests, and docs
all agree. Python dataclasses do not validate types at runtime, so every step
below is load-bearing. Do them in order; skipping one is how a field passes CI
while leaving a real gap.

## 1. Normalise in `__post_init__`

In [`openfollow/configuration.py`](../../../openfollow/configuration.py), coerce
the new field at the top of the dataclass's `__post_init__` using the existing
helpers (don't hand-roll coercion):

- `_coerce_float(value, default, *, lo=None, hi=None)` – catches `OverflowError`, rejects `inf`/`nan`.
- `_coerce_int(value, default, *, lo=None, hi=None)` – rejects `bool` (it's an `int` subclass).
- `_coerce_optional_float(value, default, *, lo=None, hi=None)` – allows empty/None.
- `_coerce_hex_color(value, default)` – normalises to lowercase `#rrggbb`.
- `_coerce_choice(value, valid_choices, default)` – enum-like validation.
- `_coerce_multicast_ipv4(value, default="")` – IPv4 multicast (empty = off).

Worked example: `GridConfig.__post_init__` (`self.width = _coerce_float(self.width, 10.0, lo=0.1)`).
List fields normalise to `list[str]` / `list[int]` so malformed TOML can't feed
bad entries downstream (see `OscConfig.allowed_sender_ips`).

## 2. Re-run `__post_init__` after web saves

In `apply_section_data` in [`openfollow/web/routes.py`](../../../openfollow/web/routes.py),
the dispatch chain calls `cfg.<section>.__post_init__()` per section so a crafted
POST gets the same validation a hand-edited TOML would trip. If the field is on an
**existing** section, it's already covered. If you add a **new** section, add its
branch here – otherwise the POST path bypasses validation.

## 3. Web-form markup (if the field is user-editable)

Add the input to `openfollow/web/templates/partials/<section>.tpl`, copying the
standard block from
[`partials/grid.tpl`](../../../openfollow/web/templates/partials/grid.tpl):

```html
<input id="<section>-<field>" name="<field>" value="..." ...
       hx-get="/api/validate/<section>/<field>" hx-trigger="blur changed delay:200ms"
       hx-target="#<section>-<field>-error" hx-swap="innerHTML" hx-include="closest form"
       aria-describedby="<section>-<field>-error" aria-invalid="false">
<span id="<section>-<field>-error" class="field-error"></span>
```

Button rules (the form-gate JS depends on these):
- **Save** buttons MUST be `<button type="submit" class="save-btn">`.
- **Broadcast** buttons MUST carry `onclick="broadcastSection(...)"`.
- Non-save buttons (Reset to Defaults, Detection Install/Uninstall) MUST NOT
  carry `type="submit"` or `broadcastSection(...)` – that keeps them clickable
  while a validation error is showing.

Keep inline `field-note` copy terse (one orienting line). Behavioural explanation
goes in the help drawer (step 6), not inline.

## 4. Register a FieldRule

Add an entry to `FIELD_RULES` in
[`openfollow/web/validation.py`](../../../openfollow/web/validation.py). The
`FieldRule` fields are `parser, lo, hi, choices, pattern, max_len, sanitiser,
strip_whitespace, type_error, human_error, custom`. The contract:

- **`parser` MUST be the same callable used at save time** – the `_as_*` parsers
  imported from `routes` (`_as_float`, `_as_int`, `_as_positive_int`, `_as_bool`,
  `_as_int_list`, `_as_ip_list`, `_as_str`, ...).
- **`lo` / `hi` / `choices` / `pattern` MUST mirror the `__post_init__` bounds**
  from step 1.
- String fields declare their hygiene contract: `strip_whitespace` (default
  `True`), `max_len`, optional `sanitiser` / `pattern`. A `__post_init__` strip
  or coercion without a matching `FieldRule` flag is a CI failure.
- List fields with per-entry rules use a `custom` validator that names the
  offending entry (e.g. `"Entry 3 ('999.0.0.1') is not a valid IPv4 ..."`).
- Cross-field auto-corrections (e.g. `max_speed >= min_speed`) are advisory blue
  `note()`s – NOT errors. They don't set `aria-invalid` and don't gate Save.

## 5. Regression tests

Add parametrized tests in
[`tests/test_configuration.py`](../../../tests/test_configuration.py) following the
`test_grid_config_*` / `test_detection_config_*` pattern – one
`@pytest.mark.parametrize` block per failure mode:

- wrong type (`"abc"`, `None`, `True`)
- out-of-range (below `lo`, above `hi`)
- enum mismatch (for `_coerce_choice` fields)
- heterogeneous entries (for list fields)

Assert on the coerced field value through the public boundary
(`GridConfig(width="0")`), not on the private `_coerce_*` helper. Test against the
spec, not the implementation you just wrote.

## 6. Update the docs (REQUIRED)

- **Help drawer:** update `openfollow/web/help/<section>.md` – the per-section `?`
  drawer (`data-help="<section>"`) is the single home for *how the control
  behaves*. Behavioural detail belongs here, not in a long inline `field-note`.
- Keep the **website-docs mirror** in mind (the "Check help on merge" auto-memory).
- For a notable top-level `AppConfig` or sub-config field, add/adjust the row in
  the config tables in `CLAUDE.md`.
- If any new source line carries a `# pragma: no cover`, add the matching row to
  the Pragma audit table in [`docs/COVERAGE.md`](../../../docs/COVERAGE.md) – a
  pragma without an audit row is a review blocker.

## 7. Verify

Run `make ci` (lint + tests + build; the authoritative pre-push gate).
[`tests/test_template_validation_consistency.py`](../../../tests/test_template_validation_consistency.py)
walks every partial and fails CI if an `<input>` whose `name` is in `FIELD_RULES`
lacks the standard markup, or a field ships without a rule.

**Done when:** dataclass + routes dispatch + template + `FieldRule` + tests are
all touched, the help `.md` (and any `CLAUDE.md` / `COVERAGE.md`) is updated, and
`make ci` is green.
