# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The keyboard layout the kiosk session starts with, and the helper that tells the launcher."""

from __future__ import annotations

import ast
import re
import runpy
import sys
from pathlib import Path

import pytest

from openfollow import keyboard_layout as kl
from tests._xkb import EVDEV_LST, SYSTEM_GB

pytestmark = pytest.mark.unit


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    catalog = tmp_path / "evdev.lst"
    catalog.write_text(EVDEV_LST, encoding="utf-8")
    system = tmp_path / "keyboard"
    system.write_text(SYSTEM_GB, encoding="utf-8")
    return {"catalog": catalog, "system": system, "config": tmp_path / "config.toml"}


def _resolve(files: dict[str, Path], config: str | None = None) -> kl.Resolution:
    if config is not None:
        files["config"].write_text(config, encoding="utf-8")
    return kl.resolve(files["config"], catalog_path=files["catalog"], system_path=files["system"])


# --- the catalog -------------------------------------------------------------


def test_the_catalog_lists_layouts_with_their_descriptions() -> None:
    catalog = kl.parse_catalog(EVDEV_LST)
    assert catalog.layouts == {
        "al": "Albanian",
        "us": "English (US)",
        "gb": "English (UK)",
        "de": "German",
        "jp": "Japanese",
    }


def test_variants_belong_to_their_layout_and_keep_their_case() -> None:
    catalog = kl.parse_catalog(EVDEV_LST)
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


def test_models_and_options_are_not_layouts() -> None:
    catalog = kl.parse_catalog(EVDEV_LST)
    assert "pc105" not in catalog.layouts
    assert "grp" not in catalog.layouts


def test_a_missing_catalog_is_empty(tmp_path: Path) -> None:
    catalog = kl.read_catalog(tmp_path / "absent.lst")
    assert catalog.layouts == {}
    assert catalog.variants == {}


def test_the_catalog_is_read_from_the_system_path_by_default(monkeypatch, files) -> None:
    monkeypatch.setattr(kl, "EVDEV_LST", files["catalog"])
    assert "de" in kl.read_catalog().layouts


# --- the system file ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (SYSTEM_GB, {"XKBMODEL": "pc105", "XKBLAYOUT": "gb", "XKBVARIANT": "", "XKBOPTIONS": ""}),
        ("XKBLAYOUT='de'\n", {"XKBLAYOUT": "de"}),
        ("XKBLAYOUT=de,us\n", {"XKBLAYOUT": "de,us"}),
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
    monkeypatch.setattr(kl, "SYSTEM_KEYBOARD", files["system"])
    assert kl.read_system_keyboard()["XKBLAYOUT"] == "gb"


# --- the resolution order ----------------------------------------------------


def test_the_stations_layout_comes_first(files) -> None:
    found = _resolve(files, '[controller]\nkeyboard_layout = "de"\nkeyboard_variant = "nodeadkeys"\n')
    assert found == kl.Resolution("de", "nodeadkeys", "config")


def test_a_station_layout_never_carries_the_system_keyboards_model_or_options(files) -> None:
    files["system"].write_text('XKBMODEL="pc105"\nXKBLAYOUT="gb"\nXKBOPTIONS="ctrl:nocaps"\n', encoding="utf-8")
    found = _resolve(files, '[controller]\nkeyboard_layout = "de"\n')
    assert (found.model, found.options) == ("", "")


def test_a_variant_of_another_layout_is_dropped(files) -> None:
    found = _resolve(files, '[controller]\nkeyboard_layout = "gb"\nkeyboard_variant = "nodeadkeys"\n')
    assert found == kl.Resolution("gb", "", "config")


def test_the_stations_layout_is_trimmed(files) -> None:
    found = _resolve(files, '[controller]\nkeyboard_layout = " de "\nkeyboard_variant = " T3 "\n')
    assert found == kl.Resolution("de", "T3", "config")


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


def test_without_a_catalog_nothing_is_trusted(files) -> None:
    files["catalog"].unlink()
    assert _resolve(files, '[controller]\nkeyboard_layout = "de"\n') == kl.Resolution("us", "", "default")


# --- the helper --------------------------------------------------------------


def test_the_helper_prints_one_line_per_setting(monkeypatch, files, capsys) -> None:
    monkeypatch.setattr(kl, "EVDEV_LST", files["catalog"])
    monkeypatch.setattr(kl, "SYSTEM_KEYBOARD", files["system"])
    files["config"].write_text('[controller]\nkeyboard_layout = "jp"\nkeyboard_variant = "OADG109A"\n')
    assert kl.main(["env", str(files["config"])]) == 0
    assert capsys.readouterr().out == "layout=jp\nvariant=OADG109A\nsource=config\nmodel=\noptions=\n"


def test_the_helper_reads_config_toml_in_the_working_directory(monkeypatch, files, capsys) -> None:
    monkeypatch.setattr(kl, "EVDEV_LST", files["catalog"])
    monkeypatch.setattr(kl, "SYSTEM_KEYBOARD", files["system"])
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
    monkeypatch.setattr(kl, "EVDEV_LST", files["catalog"])
    monkeypatch.setattr(kl, "SYSTEM_KEYBOARD", files["system"])
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


def test_without_a_toml_reader_the_station_setting_reads_as_unset(monkeypatch, files) -> None:
    """Python 3.10 has no ``tomllib``; the app still imports this module there."""
    files["config"].write_text('[controller]\nkeyboard_layout = "de"\n')
    monkeypatch.setitem(sys.modules, "tomllib", None)
    assert kl.read_configured(files["config"]) == ("", "")
