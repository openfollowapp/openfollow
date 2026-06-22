#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""CI guard: verify every _() / _l() / {{_()}} call has a matching
translation in the zh_CN catalog.

Exit 0 when every user-facing string used in templates and Python code has a
corresponding ``msgstr`` in ``locale/zh_CN/LC_MESSAGES/openfollow.po``.
Exit 1 otherwise, printing missing entries so the PR author can fix them.

Usage:  python3 scripts/validate_i18n.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def extract_used_strings() -> dict[str, set[Path]]:
    """Walk templates and Python sources; return {msgid: {files}}."""
    used: dict[str, set[Path]] = {}

    def _add(s: str, fpath: Path) -> None:
        used.setdefault(s, set()).add(fpath)

    # Template patterns: {{_('…')}}  and  {{_("…")}}
    tpl_re = re.compile(r"\{\{_\(['\"](.+?)['\"]\)\}\}")

    for fpath in (ROOT / "openfollow").rglob("*.py"):
        if "i18n" in fpath.parts:
            continue
        text = fpath.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"\b_[l]?\('(.+?)'\)", text):
            _add(m.group(1), fpath.relative_to(ROOT))
        for m in re.finditer(r'\b_[l]?\("(.+?)"\)', text):
            _add(m.group(1), fpath.relative_to(ROOT))

    for fpath in (ROOT / "openfollow" / "web" / "templates").rglob("*.tpl"):
        for m in tpl_re.finditer(fpath.read_text(encoding="utf-8")):
            _add(m.group(1), fpath.relative_to(ROOT))

    return used


def load_po(po_path: Path) -> tuple[dict[str, str], set[str]]:
    """Return (translated: {msgid→msgstr}, all_msgids)."""
    text = po_path.read_text(encoding="utf-8")
    translated: dict[str, str] = {}
    all_ids: set[str] = set()
    for block in re.split(r"\n(?=msgid )", text):
        m = re.search(r'msgid "(.+?)"\nmsgstr "(.+?)"', block, re.DOTALL)
        if m:
            mid, mstr = m.group(1), m.group(2)
            all_ids.add(mid)
            if mstr:  # non-empty msgstr = translated (including same-as-English)
                translated[mid] = mstr
    return translated, all_ids


def main() -> int:
    used = extract_used_strings()
    translated, all_ids = load_po(ROOT / "locale" / "zh_CN" / "LC_MESSAGES" / "openfollow.po")

    missing_msgid = sorted(s for s in used if s not in all_ids)

    print(f"Used in code:      {len(used)}")
    print(f"Catalog entries:   {len(all_ids)}")
    print(f"Translated:        {len(translated)}")
    print()

    rc = 0
    if missing_msgid:
        rc = 1
        print(f"❌  Not in catalog ({len(missing_msgid)}):")
        for s in missing_msgid:
            f = ", ".join(str(p) for p in sorted(used[s])[:2])
            print(f"    [{f}]  {s}")
        print()

    if rc == 0:
        print("✅  All used strings are translated!")
    else:
        print("→  Fix: add missing msgid/msgstr pairs to locale/zh_CN/LC_MESSAGES/openfollow.po")
        print("         then run:  msgfmt openfollow.po -o openfollow.mo")

    return rc


if __name__ == "__main__":
    sys.exit(main())
