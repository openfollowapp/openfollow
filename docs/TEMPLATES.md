# Templates (`.oftemplate`)

Templates are reusable, shareable snapshots of one configuration surface –
an OSC output row, a camera + grid pair, or a set of trigger zones. They let
an operator save a known-good setup once and re-apply it (or hand it to
another station) without re-entering every field.

The system is **kind-agnostic**: OSC outputs are simply the first kind. Every
piece of infrastructure below – the file format, the storage layout, the
loader/writer, and the export/import flow – is driven by the envelope's
`type` field, so a new kind slots in without touching any of it.

## Where templates live

Templates are JSON files under `<config-dir>/templates/`, next to
`config.toml` so they travel with a config backup or clone:

```
templates/
├── system/   # bundled defaults, re-seeded on every start (read-only in the UI)
└── user/     # operator-saved + imported templates
```

Files are named `<type>.<slug>.oftemplate` (e.g.
`osc_output.adm-osc.oftemplate`). The slug comes from the operator-typed
name; filename collisions are auto-numbered (`…-1`, `…-2`).

Files written by an earlier build use the legacy `.openfollowtemplate`
suffix. The loader still reads those, so existing user templates keep
working; the writer only ever emits the canonical `.oftemplate`, and stale
legacy **system** copies are pruned on the next start.

## File format

One template is a JSON object – the *envelope* – with a per-kind `payload`:

| Field         | Type   | Meaning |
|---------------|--------|---------|
| `version`     | int    | **Format** version. The only compatibility gate. Bumped only on a breaking envelope/payload change. |
| `type`        | string | The kind discriminator: `osc_output`, `camera_grid`, or `zones`. Decides which payload validator runs and which folder/section it applies to. |
| `id`          | string | Stable handle, minted if absent. |
| `name`        | string | Operator-facing label. |
| `is_system`   | bool   | Provenance tag. The *source folder* is authoritative; this is informational and overridden on load. |
| `app_version` | string | Which OpenFollow build authored the content. **Diagnostics only** – never accepts or rejects a template (see below). Empty for hand-authored / bundled files. |
| `payload`     | object | The kind-specific body, validated against the same config dataclasses `config.toml` uses, so a template can never carry a value the live config would reject. |

Example (`osc_output`):

```json
{
  "version": 1,
  "type": "osc_output",
  "id": "system-osc_output-adm-osc",
  "name": "ADM-OSC 2D",
  "is_system": true,
  "app_version": "",
  "payload": {
    "address": "/adm/obj/[markerid]/xyz",
    "args": ["[x.frac]", "[y.frac]", "0"],
    "trigger": {"kind": "stream", "rate_hz": 30}
  }
}
```

## Export / import

Templates move between machines as single `.oftemplate` files.

- **Export** – every readable row in the template chooser has an **Export**
  button. It downloads that one template (`GET /api/templates/<filename>/export`),
  re-serialised from the validated envelope so only a well-formed file is ever
  produced. System defaults export too – sharing a built-in is fine.
- **Import** – the chooser's **Import…** button uploads a single file
  (`POST /api/templates/import`). The upload is held to the *exact same*
  validation a disk read uses, then lands in `templates/user/` under a fresh
  canonical filename. The import is kind-agnostic: the landing folder and
  filename derive from the envelope's `type`, so any current or future kind
  imports through the one route. OSC outputs reach this chooser via the
  **Manage templates…** button beside the OSC Outputs toolbar.

This is a LAN/offline flow: operators copy files by hand. There is no central
gallery and no auto-fetch from a URL.

## Versioning & cross-version compatibility

Two independent things can differ between the build that *wrote* a template
and the build that *reads* it. They are handled differently on purpose.

**1. Format version (`version`).** Bumped only on a breaking envelope change,
so it changes rarely. A file whose `version` is newer than the running build
understands is rejected with a clear message ("unsupported version N…").
`openfollow/templates/loader.py::_migrate_envelope` is the single seam where a
future `version` bump lands a forward-migration; today it is a no-op.

**2. Payload drift across app versions (same `version`).** Far more common:
features evolve (a new OSC trigger kind, a new camera field) while the format
holds at 1. Because payloads are validated by constructing the real config
dataclasses, the directions are deliberately asymmetric:

| Direction | Result |
|---|---|
| **Old template → newer build** (upgrade) | Loads cleanly. Missing fields fall back to dataclass defaults; older enum values stay valid. This is a **guaranteed, tested contract**. |
| **New template → older build** (downgrade) | **Rejected, never silently degraded.** An unknown field or enum fails validation – applying it half-way would silently drop operator intent, which is worse than refusing. |

When a downgrade rejection happens and the file's `app_version` is newer than
the running build, the error is annotated with the skew (e.g. *"this template
was created by OpenFollow 0.5.0; you're running 0.4.0… update OpenFollow"*) so
the operator sees a version mismatch instead of a cryptic validation error.
`app_version` only *explains* a rejection – it never causes one. A
newer-build template that happens to use only compatible fields imports fine.

## Adding a new kind

1. Add the id to `VALID_TYPES` in `openfollow/templates/schema.py`.
2. Add its branch to `validate_payload` (reuse the relevant config dataclass –
   don't write a second validator that can drift).
3. Wire a "Save as template…" entry point and a chooser
   (`modalChooseTemplate({type: "<id>", …})`) into that section's UI.

Export and import need **no changes** – they dispatch on `type`.
