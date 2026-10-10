# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Run the setup wizard's own script functions in node, for the maths only the browser runs."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

WIZARD_TEMPLATE = Path(__file__).resolve().parent.parent / "openfollow" / "web" / "templates" / "wizard.tpl"
UNITS_JS = Path(__file__).resolve().parent.parent / "openfollow" / "web" / "static" / "js" / "units.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def wizard_function(name: str) -> str:
    """The source of ``function name(...) { ... }`` in the wizard, found by matching its braces."""
    src = WIZARD_TEMPLATE.read_text(encoding="utf-8")
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise ValueError(f"unbalanced braces in {name}")


def wizard_var(name: str) -> str:
    """The wizard's one-line ``var name = ...;`` declaration."""
    match = re.search(rf"^\s*var {name} = [^\n]*;$", WIZARD_TEMPLATE.read_text(encoding="utf-8"), re.M)
    if match is None:
        raise ValueError(f"no var {name}")
    return match.group(0).strip()


def run_wizard_js(
    expression: str,
    *,
    functions: tuple[str, ...] = (),
    variables: tuple[str, ...] = (),
    prelude: str = "",
    **inputs: Any,
) -> Any:
    """Evaluate ``expression`` beside the named wizard functions and variables; ``inputs`` arrive as JSON.

    ``prelude`` runs first: the globals a function reads from the page (``document``, ``WUNIT``).
    ``Infinity`` and ``NaN`` inputs survive (``json.dumps`` writes them as JS literals); the
    result comes back through ``JSON.stringify``, where they read as ``null``.
    """
    assert NODE is not None
    parts = [prelude] + [wizard_var(v) for v in variables] + [wizard_function(f) for f in functions]
    parts += [f"var {key} = {json.dumps(value)};" for key, value in inputs.items()]
    parts.append(f"process.stdout.write(JSON.stringify({expression}));")
    done = subprocess.run([NODE, "-e", "\n".join(parts)], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(done.stdout)


def length_page(system: str, fields: dict[str, dict[str, Any]]) -> str:
    """A ``prelude``: ``units.js`` under ``system`` and a page whose inputs are ``fields`` (id -> value, dataset)."""
    return "\n".join(
        (
            f"var window = {{ OPENFOLLOW_UNIT_SYSTEM: {json.dumps(system)} }};",
            UNITS_JS.read_text(encoding="utf-8"),
            "var WUNIT = window.OpenFollow.units;",
            f"var fields = {json.dumps(fields)};",
            "var document = { getElementById: function(id) { return fields[id] || null; } };",
        )
    )


def assert_script_parses(source: str, path: Path) -> None:
    """``node --check`` the script, written to ``path``; a syntax error fails with node's message."""
    assert NODE is not None
    path.write_text(source, encoding="utf-8")
    done = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
