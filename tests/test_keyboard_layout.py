# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The keyboard layout the kiosk session starts with, and the helper that tells the launcher."""

from __future__ import annotations

import ast
import os
import re
import runpy
import sys
from pathlib import Path

import pytest

from openfollow import keyboard_layout as kl
from tests._xkb import EVDEV_LST, SESSION_ENV, SYSTEM_GB, write_xkb_root

pytestmark = pytest.mark.unit


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    root = write_xkb_root(tmp_path / "xkb")
    system = tmp_path / "keyboard"
    system.write_text(SYSTEM_GB, encoding="utf-8")
    return {
        "root": root,
        "catalog": root / "rules" / "evdev.lst",
        "symbols": root / "symbols",
        "system": system,
        "config": tmp_path / "config.toml",
    }


@pytest.fixture
def symbols(files: dict[str, Path]) -> Path:
    return files["symbols"]


def _resolve(files: dict[str, Path], config: str | None = None) -> kl.Resolution:
    if config is not None:
        files["config"].write_text(config, encoding="utf-8")
    return kl.resolve(
        files["config"], catalog_path=files["catalog"], system_path=files["system"], xkb_root=files["root"]
    )


def _use_files(monkeypatch: pytest.MonkeyPatch, files: dict[str, Path]) -> None:
    monkeypatch.setattr(kl, "XKB_ROOT", files["root"])
    monkeypatch.setattr(kl, "EVDEV_LST", files["catalog"])
    monkeypatch.setattr(kl, "SYSTEM_KEYBOARD", files["system"])


# --- the catalog -------------------------------------------------------------


def test_the_catalog_lists_layouts_with_their_descriptions(symbols: Path) -> None:
    catalog = kl.parse_catalog(EVDEV_LST, symbols)
    assert catalog.layouts == {
        "al": "Albanian",
        "us": "English (US)",
        "gb": "English (UK)",
        "de": "German",
        "jp": "Japanese",
    }


def test_variants_belong_to_their_layout_and_keep_their_case(symbols: Path) -> None:
    catalog = kl.parse_catalog(EVDEV_LST, symbols)
    # A variant name repeats across layouts: "us" here is German (US), not the layout.
    assert catalog.variants_of("de") == (
        ("nodeadkeys", "German (no dead keys)"),
        ("T3", "German (T3)"),
        ("us", "German (US)"),
    )
    assert catalog.variants_of("jp") == (("OADG109A", "Japanese (OADG 109A)"),)
    assert catalog.variants_of("fr") == ()
    assert catalog.has_variant("de", "nodeadkeys")
    assert not catalog.has_variant("gb", "nodeadkeys")


def test_models_and_options_are_not_layouts(symbols: Path) -> None:
    catalog = kl.parse_catalog(EVDEV_LST, symbols)
    assert "pc105" not in catalog.layouts
    assert "grp" not in catalog.layouts


def test_a_listed_layout_without_symbols_is_not_offered(symbols: Path) -> None:
    """xkb-data lists ``custom`` but ships no symbols for it: Cage could build no keymap from it."""
    catalog = kl.parse_catalog(EVDEV_LST, symbols)
    assert "custom" not in catalog.layouts
    (symbols / "custom").write_text("", encoding="utf-8")
    assert kl.parse_catalog(EVDEV_LST, symbols).layouts["custom"] == "A user-defined custom Layout"


def test_a_symbols_directory_entry_that_is_not_a_file_is_no_layout(symbols: Path) -> None:
    (symbols / "de").unlink()
    (symbols / "de").mkdir()
    assert "de" not in kl.parse_catalog(EVDEV_LST, symbols).layouts


def test_the_catalog_lists_the_options_a_keymap_can_take(symbols: Path) -> None:
    """The ``custom:`` group needs files a user adds; without them no keymap compiles."""
    assert kl.parse_catalog(EVDEV_LST, symbols).options == {"grp", "grp:alt_shift_toggle", "ctrl:nocaps", "custom"}


def test_a_catalog_option_outside_the_name_characters_is_dropped(symbols: Path) -> None:
    catalog = kl.parse_catalog("! option\n  ctrl:nocaps  Caps Lock as Ctrl\n  bad;opt  Not a name\n", symbols)
    assert catalog.options == {"ctrl:nocaps"}


def test_a_missing_catalog_is_empty(tmp_path: Path) -> None:
    catalog = kl.read_catalog(tmp_path / "absent.lst")
    assert catalog.layouts == {}
    assert catalog.variants == {}


def test_the_catalog_is_read_from_the_system_path_by_default(monkeypatch, files) -> None:
    _use_files(monkeypatch, files)
    assert "de" in kl.read_catalog().layouts


def test_without_the_symbols_directory_no_layout_is_known(monkeypatch, files, tmp_path: Path) -> None:
    monkeypatch.setattr(kl, "XKB_ROOT", tmp_path / "absent")
    assert kl.read_catalog(files["catalog"]).layouts == {}


# --- the system file ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (SYSTEM_GB, {"XKBMODEL": "pc105", "XKBLAYOUT": "gb", "XKBVARIANT": "", "XKBOPTIONS": ""}),
        ("XKBLAYOUT='de'\n", {"XKBLAYOUT": "de"}),
        ("XKBLAYOUT=de,us\n", {"XKBLAYOUT": "de,us"}),
        ('export XKBLAYOUT="de"\n', {"XKBLAYOUT": "de"}),
        ("  export\tXKBVARIANT=nodeadkeys\n", {"XKBVARIANT": "nodeadkeys"}),
        ('exportXKBLAYOUT="de"\n', {}),
        ('XKBLAYOUT ="de"\n', {}),
        ('XKBLAYOUT="fr" # the office\n', {"XKBLAYOUT": "fr"}),
        ('XKBLAYOUT="unterminated\n', {}),
        ("XKBLAYOUT=\n", {"XKBLAYOUT": ""}),
        ("no assignment here\nOTHER=1\n", {}),
    ],
)
def test_the_system_file_is_read_as_shell_assignments(text: str, expected: dict[str, str]) -> None:
    assert kl.parse_system_keyboard(text) == expected


def test_a_missing_system_file_is_empty(tmp_path: Path) -> None:
    assert kl.read_system_keyboard(tmp_path / "absent") == {}


def test_the_system_file_is_read_from_the_system_path_by_default(monkeypatch, files) -> None:
    _use_files(monkeypatch, files)
    assert kl.read_system_keyboard()["XKBLAYOUT"] == "gb"


# --- the resolution order ----------------------------------------------------


def test_the_stations_layout_comes_first(files) -> None:
    found = _resolve(files, '[controller]\nkeyboard_layout = "de"\nkeyboard_variant = "nodeadkeys"\n')
    assert found == kl.Resolution("de", "nodeadkeys", "config", "pc105", "")


@pytest.mark.parametrize(
    ("config", "system_layout", "expected"),
    [
        ('[controller]\nkeyboard_layout = "de"\n', "gb", ("de", "config")),
        ("", "gb", ("gb", "system")),
        ("", "xx", ("us", "default")),
    ],
)
def test_every_layout_keeps_the_system_keyboards_model_and_options(
    files, config: str, system_layout: str, expected: tuple[str, str]
) -> None:
    """Model and options describe the keyboard, so they hold whichever source picks the layout."""
    files["system"].write_text(
        f'XKBMODEL="pc104"\nXKBLAYOUT="{system_layout}"\nXKBOPTIONS="ctrl:nocaps,custom:types"\n', encoding="utf-8"
    )
    found = _resolve(files, config)
    assert found == kl.Resolution(expected[0], "", expected[1], "pc104", "ctrl:nocaps")


def test_a_variant_of_another_layout_is_dropped(files) -> None:
    found = _resolve(files, '[controller]\nkeyboard_layout = "gb"\nkeyboard_variant = "nodeadkeys"\n')
    assert found == kl.Resolution("gb", "", "config", "pc105", "")


def test_the_stations_layout_is_trimmed(files) -> None:
    found = _resolve(files, '[controller]\nkeyboard_layout = " de "\nkeyboard_variant = " T3 "\n')
    assert found == kl.Resolution("de", "T3", "config", "pc105", "")


@pytest.mark.parametrize(
    "config",
    [
        None,  # no config file at all
        "",  # nothing configured
        '[controller]\nkeyboard_layout = ""\n',  # System
        '[controller]\nkeyboard_layout = "xx"\n',  # a layout this system does not know
        '[controller]\nkeyboard_layout = "Bad"\n',
        "[controller]\nkeyboard_layout = 7\n",
        '[controller]\nkeyboard_layout = ["de"]\n',
        'controller = "de"\n',
        "[controller\nkeyboard_layout = 'de'\n",  # malformed TOML
    ],
)
def test_otherwise_the_system_layout(files, config: str | None) -> None:
    assert _resolve(files, config) == kl.Resolution("gb", "", "system", "pc105", "")


def test_a_config_that_is_not_utf8_reads_as_unset(files) -> None:
    files["config"].write_bytes(b'[controller]\nkeyboard_layout = "\xff"\n')
    assert _resolve(files).source == "system"


def test_the_system_variant_and_options_come_with_the_system_layout(files) -> None:
    files["system"].write_text(
        'XKBMODEL="pc105"\nXKBLAYOUT="de,us"\nXKBVARIANT="nodeadkeys,"\nXKBOPTIONS="grp:alt_shift_toggle,ctrl:nocaps"\n',
        encoding="utf-8",
    )
    assert _resolve(files) == kl.Resolution("de", "nodeadkeys", "system", "pc105", "grp:alt_shift_toggle,ctrl:nocaps")


def test_a_system_variant_the_layout_lacks_is_dropped(files) -> None:
    files["system"].write_text('XKBLAYOUT="gb"\nXKBVARIANT="nodeadkeys"\n', encoding="utf-8")
    assert _resolve(files) == kl.Resolution("gb", "", "system")


@pytest.mark.parametrize(
    ("options", "kept"),
    [
        ("custom:types", ""),  # listed in the rules, but no keymap compiles without the user's files
        ("ctrl:nocaps,custom:types", "ctrl:nocaps"),
        ("custom:types,grp:alt_shift_toggle", "grp:alt_shift_toggle"),
        ("ctrl:nocaps,nosuch:option", "ctrl:nocaps"),
        ("ctrl:nocaps, grp:alt_shift_toggle,,", "ctrl:nocaps,grp:alt_shift_toggle"),
    ],
)
def test_only_system_options_the_catalog_lists_are_passed_on(files, options: str, kept: str) -> None:
    files["system"].write_text(f'XKBLAYOUT="gb"\nXKBOPTIONS="{options}"\n', encoding="utf-8")
    assert _resolve(files).options == kept


def test_an_unknown_system_model_is_passed_on(files) -> None:
    """Models are not checked against the catalog: an unknown one still compiles."""
    files["system"].write_text('XKBMODEL="mymodel"\nXKBLAYOUT="gb"\n', encoding="utf-8")
    assert _resolve(files).model == "mymodel"


def test_a_listed_layout_without_symbols_falls_through(files) -> None:
    files["system"].write_text('XKBLAYOUT="custom"\n', encoding="utf-8")
    assert _resolve(files, '[controller]\nkeyboard_layout = "custom"\n') == kl.Resolution("us", "", "default")


def test_system_model_and_options_outside_the_safe_characters_are_dropped(files) -> None:
    files["system"].write_text('XKBMODEL="pc105;x"\nXKBLAYOUT="gb"\nXKBOPTIONS="$(reboot)"\n', encoding="utf-8")
    assert _resolve(files) == kl.Resolution("gb", "", "system", "", "")


@pytest.mark.parametrize("system", [None, 'XKBLAYOUT="xx"\n', 'XKBLAYOUT=""\n', "garbage\n"])
def test_last_the_default_layout(files, system: str | None) -> None:
    if system is None:
        files["system"].unlink()
    else:
        files["system"].write_text(system, encoding="utf-8")
    assert _resolve(files) == kl.Resolution("us", "", "default")


def test_without_a_catalog_no_layout_or_option_is_trusted(files) -> None:
    files["catalog"].unlink()
    files["system"].write_text('XKBMODEL="pc105"\nXKBLAYOUT="gb"\nXKBOPTIONS="ctrl:nocaps"\n', encoding="utf-8")
    found = _resolve(files, '[controller]\nkeyboard_layout = "de"\n')
    assert found == kl.Resolution("us", "", "default", "pc105", "")


# --- the helper --------------------------------------------------------------


def test_the_helper_prints_one_line_per_setting(monkeypatch, files, capsys) -> None:
    _use_files(monkeypatch, files)
    files["config"].write_text('[controller]\nkeyboard_layout = "jp"\nkeyboard_variant = "OADG109A"\n')
    assert kl.main(["env", str(files["config"])]) == 0
    assert capsys.readouterr().out == "layout=jp\nvariant=OADG109A\nsource=config\nmodel=pc105\noptions=\n"


def test_the_helper_reads_config_toml_in_the_working_directory(monkeypatch, files, capsys) -> None:
    _use_files(monkeypatch, files)
    files["config"].write_text('[controller]\nkeyboard_layout = "de"\n')
    monkeypatch.chdir(files["config"].parent)
    assert kl.main(["env"]) == 0
    assert "layout=de\n" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [[], ["show"], ["env", "a", "b"]])
def test_the_helper_refuses_anything_else(argv: list[str], capsys) -> None:
    assert kl.main(argv) == 2
    assert "usage: keyboard-layout env" in capsys.readouterr().err


@pytest.mark.parametrize(
    "system",
    [
        'XKBMODEL="pc105 x"\nXKBLAYOUT="gb"\nXKBOPTIONS="a b"\n',
        'XKBMODEL="$(id)"\nXKBLAYOUT="gb"\nXKBOPTIONS="`id`"\n',
        'XKBLAYOUT="gb\nevil=1"\n',
    ],
)
def test_the_helper_only_prints_values_the_launcher_accepts(monkeypatch, files, capsys, system: str) -> None:
    files["system"].write_text(system, encoding="utf-8")
    _use_files(monkeypatch, files)
    kl.main(["env", str(files["config"])])
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("=", 1)[0] for line in lines] == ["layout", "variant", "source", "model", "options"]
    for line in lines:
        assert re.fullmatch(r"[a-z]+=[A-Za-z0-9_:,-]*", line), line


def test_runs_as_the_installed_helper(monkeypatch, capsys) -> None:
    """The .deb installs this very file as the helper; run it that way."""
    monkeypatch.setattr(sys, "argv", ["keyboard-layout"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(kl.__file__, run_name="__main__")
    assert exit_info.value.code == 2
    assert "usage: keyboard-layout" in capsys.readouterr().err


def test_the_helper_file_is_standard_library_only() -> None:
    """The launcher runs this file with the system interpreter, never code from the venv."""
    source = Path(kl.__file__).read_text()
    assert source.startswith("#!/usr/bin/python3 -I\n")
    tree = ast.parse(source)
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [node])
    }
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}


def test_the_package_installs_it_as_the_helper() -> None:
    build = (Path(kl.__file__).resolve().parents[1] / "packaging" / "build-deb.sh").read_text()
    assert 'install -m 0755 "$REPO_ROOT/openfollow/keyboard_layout.py"          "$SHARE/keyboard-layout"' in build


def test_the_package_depends_on_the_xkb_catalog() -> None:
    control = (Path(kl.__file__).resolve().parents[1] / "packaging" / "debian" / "control.in").read_text()
    depends = control[control.index("Depends:") : control.index("Recommends:")]
    assert re.search(r"\bxkb-data\b", depends)


def test_no_test_sees_the_hosts_keyboard() -> None:
    """A station's xkb data, keyboard file or kiosk session would leak into any test that renders the row."""
    assert kl.read_catalog() == kl.XkbCatalog()
    assert kl.read_system_keyboard() == {}
    assert [name for name in SESSION_ENV if name in os.environ] == []
