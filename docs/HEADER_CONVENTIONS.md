# File Header & Comment Conventions

Every code file in this repo carries a license header, a short description of what it
does, and comments trimmed to relevant technical content. This document is the canonical
reference for that convention – new files must follow it, and it drives the repo-wide
cleanup tracked in the cleanup manifest.

## Scope

**In scope** (gets header + description + comment cleanup):
`*.py`, `*.sh`, `*.yml`/`*.yaml`, `*.toml`, `*.service`, `*.j2`, `Makefile`,
`packaging/debian/{postinst,prerm,postrm}`.

**Out of scope** (never touched by this convention):
- UI: `*.tpl`, `*.js`, `*.css`, `*.svg`
- Docs/help: all `*.md` (including `openfollow/web/help/*.md`)
- Data / generated / binary: `*.openfollowtemplate` (JSON, no comment syntax),
  `poetry.lock`, `*.pem`, `*.jpg`, `LICENSE`, `packaging/debian/copyright`

## The header

Two `#` lines, identical everywhere:

```
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
```

The copyright holder is the generic project – never individual names.

### Python

Header as `#` comments **above** the module docstring; the description **is** the
docstring (so `__doc__` is preserved and `from __future__ import annotations` stays the
first statement):

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Short technical description of what this module does."""

from __future__ import annotations
```

With a shebang (`scripts/`, debian maintainer scripts) the shebang stays on line 1:

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Short technical description."""
```

### Non-Python

Header as `#` lines, description as a third `#` line, placed after any shebang and after a
leading YAML `---` document marker:

```sh
#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Short description of what this script does.
```

`.j2` ansible templates render to shell/yaml/unit files where `#` is a native comment, so
the same `#` header is used (no Jinja `{# #}` wrapper).

## The description

Delivered as the Python module docstring, or a `#` line on non-Python files.

- Terse but technically complete: name the file's responsibility, its key
  types/entry-points, and any non-obvious contract a reader needs.
- One line for small/obvious modules; a short multi-line docstring (in the style of
  `configuration.py` / `services.py`) for complex ones.
- If a docstring already exists, refine it – don't stack a second one.
- Ruff has no pydocstyle (`D`) rules enabled, so docstring formatting is not linted.

## Comment cleanup

Trim every comment to the relevant technical fact, and **strip** these reference classes
from comments and docstrings:

- Issue / PR / pull numbers (`#88`, `issue #123`, `PR #514`, `(#107)`)
- Implementation phases (`Phase 1`, `Phase A/B`)
- Person / author / brand / customer names, "as requested by …"
- Design-doc / ticket cross-references

Keep the fact, drop the reference:
`# issue #88: marker.pos is PSN-absolute` → `# marker.pos is PSN-absolute`.

### Never touch (these are not prose comments)

- `# pragma: no cover …` and its reason text
- `# noqa`, `# noqa: E501`, `# type: ignore`, `# nosec`, `# fmt: off`/`on`,
  `# isort:skip`, `# mypy:`, `# pylint:`
- Shebang lines (`#!`), encoding cookies (`# -*- coding: … -*-`)
- The SPDX / copyright lines themselves
- Anything inside a **string literal** (a `#123` in a string is data, not a comment)
- The code on a line that also carries a comment – edit only the comment text

### Change

- Verbose / narrative / redundant comments → trim to the technical point
- Stale comments that merely restate the code → delete
- Reference-laden comments → strip the reference, keep the substance

> `docs/COVERAGE.md` pragma-audit rows may reference issues; that file is a `.md` doc and
> is out of scope – leave it.

## Invariants the cleanup must preserve

- No executable code line changes – only header, comment, and docstring lines.
- Pragma / `noqa` / `type:` / `nosec` counts are unchanged before and after.
- `ruff check` and `ruff format --check` stay clean; `make ci` passes.
