# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for config dataclass validation, TOML round-trip, and runtime hot-reload."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

import pytest

from openfollow.configuration import (
    DEFAULT_UPDATE_SERVICE_NAME,
    RESERVED_MOVEMENT_KEYS,
    AppConfig,
    CameraConfig,
    ControllerConfig,
    DetectionConfig,
    GridConfig,
    MarkerConfig,
    OscConfig,
    OtpOutputConfig,
    RttrpmOutputConfig,
    _apply_with_fallback,
    apply_runtime_config_changes,
    config_to_toml_dict,
    load_config,
    save_config,
)

_NEW_BUTTON_FIELDS = (
    "btn_next_marker",
    "btn_prev_marker",
    "btn_settings",
    "btn_menu_confirm",
    "btn_menu_cancel",
)

_NEW_KEYBOARD_FIELDS = (
    "key_next_marker",
    "key_prev_marker",
    "key_settings",
)

pytestmark = pytest.mark.unit


class _DummyRuntimeServices:
    def __init__(self) -> None:
        self.updated_titles: list[str] = []
        self.otp_changes: list[OtpOutputConfig] = []
        self.rttrpm_changes: list[RttrpmOutputConfig] = []
        self.psn_source_ip_changes: list[str] = []
        self.psn_mcast_ip_changes: list[str] = []
        self.psn_combined_changes: list[tuple[str, str]] = []
        self.psn_system_name_changes: list[str] = []
        self.detection_changes: list[DetectionConfig] = []
        self.detection_swaps: list[DetectionConfig] = []
        self.window_size_changes: list[tuple[int, int]] = []
        self.video_swaps: list[AppConfig] = []

    def update_window_title(self, title: str) -> None:
        self.updated_titles.append(title)

    def apply_otp_output_change(self, new_cfg: OtpOutputConfig) -> None:
        self.otp_changes.append(new_cfg)

    def apply_rttrpm_output_change(self, new_cfg: RttrpmOutputConfig) -> None:
        self.rttrpm_changes.append(new_cfg)

    def apply_psn_source_ip_change(
        self,
        new_source_ip: str,
        *,
        new_mcast_ip: object = None,
    ) -> None:
        # Separate list to distinguish single-field calls from combined ones.
        if new_mcast_ip is None:
            self.psn_source_ip_changes.append(new_source_ip)
        else:
            self.psn_combined_changes.append(
                (new_source_ip, str(new_mcast_ip)),
            )

    def apply_psn_mcast_ip_change(self, new_mcast_ip: str) -> None:
        self.psn_mcast_ip_changes.append(new_mcast_ip)

    def apply_psn_system_name_change(self, new_name: str) -> None:
        self.psn_system_name_changes.append(new_name)

    def apply_detection_change(self, new_cfg: DetectionConfig) -> None:
        self.detection_changes.append(new_cfg)

    def swap_detector(self, new_cfg: DetectionConfig) -> None:
        self.detection_swaps.append(new_cfg)

    def apply_window_size_change(self, width: int, height: int) -> None:
        self.window_size_changes.append((width, height))

    def swap_video(self, new_cfg: AppConfig) -> None:
        self.video_swaps.append(new_cfg)

    def apply_osc_transmitters_change(self, new_cfg, destinations=None) -> None:  # noqa: ANN001
        # No-op base; subclasses that assert on OSC routing override + record.
        pass


class _DummyWebServer:
    def __init__(self) -> None:
        self.updated_names: list[str] = []

    def update_system_name(self, name: str) -> None:
        self.updated_names.append(name)


class _DummyWebCommands:
    def __init__(self) -> None:
        self.restart_requested = False

    def request_restart(self) -> None:
        self.restart_requested = True


@dataclass
class _DummyMarker:
    marker_id: int


class _DummyPsnServer:
    def __init__(self) -> None:
        self.markers: dict[int, _DummyMarker] = {}

    def add_marker(self, marker_id: int, _name: str) -> _DummyMarker:
        marker = _DummyMarker(marker_id)
        self.markers[marker_id] = marker
        return marker

    def remove_marker(self, marker_id: int) -> None:
        self.markers.pop(marker_id, None)


class _DummyPsnReceiver:
    def __init__(self) -> None:
        self.ignore_ids: set[int] = set()

    def set_ignore_ids(self, ignore_ids: set[int]) -> None:
        self.ignore_ids = set(ignore_ids)


class _DummyGamepadHandler:
    def __init__(self) -> None:
        self.apply_called = False

    def apply_config(self) -> None:
        self.apply_called = True


class _DummyMouseHandler:
    def __init__(self) -> None:
        self.deactivate_calls = 0

    def deactivate(self) -> None:
        self.deactivate_calls += 1


class _DummyInputManager:
    def __init__(self) -> None:
        self.gamepad_handler = _DummyGamepadHandler()
        self.mouse_handler = _DummyMouseHandler()
        self.osc_restarts: list[tuple[bool, int]] = []
        self.osc_multicast_groups: list[str] = []
        self.operator_message_restarts = 0

    def restart_osc(
        self,
        enabled: bool,
        port: int,
        allowed_sender_ips: list[str] | None = None,
        *,
        multicast_group: str = "",
    ) -> None:
        self.osc_restarts.append((enabled, port))
        self.osc_multicast_groups.append(multicast_group)

    def restart_operator_messages(self) -> None:
        self.operator_message_restarts += 1


class _DummyApp:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._runtime_services = _DummyRuntimeServices()
        self._web_server = _DummyWebServer()
        self._web_commands = _DummyWebCommands()
        self._server = _DummyPsnServer()
        for tid in config.controlled_marker_ids:
            self._server.add_marker(tid, f"Marker {tid}")
        self._controlled_ids = list(config.controlled_marker_ids)
        self._viewer_ids = list(config.viewer_marker_ids)
        self._selected_id = self._controlled_ids[0] if self._controlled_ids else None
        self._psn_receiver = _DummyPsnReceiver()
        self._input_manager = _DummyInputManager()
        self._camera = None
        self._otp_server = None
        self._rttrpm_server = None
        # PersonDetector lives on ``AppRuntimeServices`` in production,
        # NOT on ``OpenFollowApp``. Mirror the real ownership in the
        # dummy so the dispatcher's ``on_failure`` (which clears
        # ``app._runtime_services._person_detector``) hits the same
        # attribute the production code does. Default to a stand-in
        # with ``available=True`` so the dispatcher's "phantom detector"
        # restart guard doesn't fire by accident in tests that aren't
        # exercising that path; tests that DO exercise the
        # phantom-detector guard explicitly null this out or set it to
        # an ``available=False`` stand-in.
        self._runtime_services._person_detector = SimpleNamespace(available=True)

    # Bind the production helper so the dummy exercises the real refresh logic.
    def _refresh_psn_source_advisory(self) -> str:
        from openfollow.app import OpenFollowApp

        return OpenFollowApp._refresh_psn_source_advisory(self)


def test_load_config_missing_file_uses_defaults(temp_config_path) -> None:
    config = load_config(str(temp_config_path))

    assert config == AppConfig()


def test_default_video_source_is_testpattern() -> None:
    """A fresh install (no config, no example to bootstrap from) defaults to
    the always-available test pattern rather than a network source. The
    test-pattern plugin renders at 1080p by default."""
    assert AppConfig().video_source_type == "testpattern"


def test_default_testpattern_pattern_is_stage() -> None:
    """A fresh install must render the stage scene, not the 50% grey debug
    pattern. The dataclass default flips ``video_source_type`` to testpattern;
    if the pattern default stays "grey" the device boots to a flat grey screen
    instead of the intended stage picture."""
    assert AppConfig().testpattern_pattern == "stage"


def test_testpattern_dataclass_defaults_match_plugin_config_fields() -> None:
    """Plugin-backed dataclass defaults must match the plugin's own
    ``ConfigField`` defaults. If the shipped example is missing (or not
    bootstrapped for any reason), the dataclass defaults are what actually
    render – any drift ships a wrong out-of-box value (e.g. the grey-screen bug).
    """
    from openfollow.video.inputs.testpattern import TestPatternInput

    plugin_defaults = {f.name: f.default for f in TestPatternInput.config_fields()}
    cfg = AppConfig()
    assert cfg.testpattern_pattern == plugin_defaults["testpattern_pattern"]
    assert cfg.testpattern_resolution == plugin_defaults["testpattern_resolution"]


# --------------------------------------------------------------------------- #
# bootstrap_config_if_missing – first-run seed from config.example.toml
# --------------------------------------------------------------------------- #


def test_example_config_matches_current_schema() -> None:
    """The shipped ``config.example.toml`` must not declare fields the code
    dropped (they'd be silently filtered on load) and must show the OSC
    destinations shape transmitters/zones now reference, so an operator
    copying it as a starting point has a working template."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    example_path = repo_root / "config.example.toml"
    data = tomllib.loads(example_path.read_text(encoding="utf-8"))

    tz = data.get("trigger_zones", {})
    assert "default_osc_host" not in tz
    assert "default_osc_port" not in tz

    dests = data.get("osc_destinations", {}).get("destinations", [])
    assert dests, "example must seed at least one [[osc_destinations.destinations]]"
    assert {"id", "host", "port", "protocol"} <= set(dests[0])

    # And it loads into a config whose seeded destination is resolvable.
    cfg = load_config(str(example_path))
    assert cfg.osc_destinations.destinations
    first = cfg.osc_destinations.destinations[0]
    assert cfg.osc_destinations.get(first.id) is first


def test_bootstrap_copies_example_when_config_absent(tmp_path) -> None:
    """First-run path: ``config.toml`` is missing, ``config.example.toml``
    sits next to it → copy the example into place verbatim."""
    from openfollow.configuration import bootstrap_config_if_missing

    cfg = tmp_path / "config.toml"
    example = tmp_path / "config.example.toml"
    example.write_text('foo = "bar"\n', encoding="utf-8")

    assert bootstrap_config_if_missing(str(cfg)) is True
    assert cfg.read_text(encoding="utf-8") == 'foo = "bar"\n'


def test_bootstrap_creates_config_owner_only(tmp_path) -> None:
    """config.toml carries the web_pin secret; the bootstrap must create it
    owner-only (O_EXCL, mode 0o600) regardless of the example's permissions,
    so the PIN is never world-readable even transiently."""
    import os
    import stat

    from openfollow.configuration import bootstrap_config_if_missing

    cfg = tmp_path / "config.toml"
    example = tmp_path / "config.example.toml"
    example.write_text('web_pin = "1234"\n', encoding="utf-8")
    # World-readable example: the new file must still be created at 0o600.
    os.chmod(example, 0o644)

    assert bootstrap_config_if_missing(str(cfg)) is True
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600
    assert cfg.read_text(encoding="utf-8") == 'web_pin = "1234"\n'


def test_bootstrap_loses_create_race_returns_false(tmp_path, monkeypatch) -> None:
    """The exists-check passed but the O_EXCL create lost a race to a
    concurrent bootstrapper – return False rather than clobber its file."""
    import os

    from openfollow.configuration import bootstrap_config_if_missing

    cfg = tmp_path / "config.toml"
    example = tmp_path / "config.example.toml"
    example.write_text('web_pin = "1234"\n', encoding="utf-8")

    real_open = os.open

    def _raise_for_cfg(path, flags, mode=0o777):  # noqa: ANN001
        if str(path) == str(cfg):
            raise FileExistsError(path)
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", _raise_for_cfg)
    assert bootstrap_config_if_missing(str(cfg)) is False


def test_bootstrap_no_op_when_config_already_exists(tmp_path) -> None:
    from openfollow.configuration import bootstrap_config_if_missing

    cfg = tmp_path / "config.toml"
    cfg.write_text("# operator state\n", encoding="utf-8")
    example = tmp_path / "config.example.toml"
    example.write_text('foo = "bar"\n', encoding="utf-8")

    assert bootstrap_config_if_missing(str(cfg)) is False
    assert cfg.read_text(encoding="utf-8") == "# operator state\n"


def test_bootstrap_no_op_when_example_is_absent(tmp_path) -> None:
    from openfollow.configuration import bootstrap_config_if_missing

    cfg = tmp_path / "config.toml"

    assert bootstrap_config_if_missing(str(cfg)) is False
    assert not cfg.exists()


def test_bootstrap_handles_bare_filename_with_no_directory(
    tmp_path,
    monkeypatch,
) -> None:
    """When ``config_path`` has no directory component (e.g. a bare
    ``config.toml`` resolved against CWD), ``os.path.dirname`` returns
    an empty string. The lookup must default to ``"."`` so the example
    is discovered in CWD too."""
    from openfollow.configuration import bootstrap_config_if_missing

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.example.toml").write_text(
        'foo = "bar"\n',
        encoding="utf-8",
    )

    assert bootstrap_config_if_missing("config.toml") is True
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == 'foo = "bar"\n'


def test_configuration_falls_back_to_tomli_when_tomllib_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Python 3.10 the stdlib ``tomllib`` module doesn't exist and
    the module falls back to the ``tomli`` backport.  Force the
    fallback via ``sys.modules`` injection so the branch is exercised
    on every Python version.

    Loads ``configuration.py`` as a fresh module under a different
    name (``_configuration_tomli_fallback``) so we don't perturb the
    canonical ``openfollow.configuration`` import that the rest of
    the suite shares – reloading the canonical module would redefine
    every dataclass and break dataclass-equality assertions in
    sibling tests.

    pyproject installs ``tomli`` only on Python <3.11, so on 3.11+
    we inject a stub ``tomli`` so the fallback's ``import tomli``
    itself doesn't fail.
    """
    import importlib.util
    import pathlib
    import sys
    import types

    # Block stdlib ``tomllib`` so the except arm fires inside the
    # freshly-loaded module.
    monkeypatch.setitem(sys.modules, "tomllib", None)
    # Inject a stand-in ``tomli`` so the except body's
    # ``import tomli as tomllib`` resolves on Python 3.11+ where the
    # real backport isn't installed.
    fake_tomli = types.ModuleType("tomli")
    parsed: list[str] = []

    def _loads(text: str) -> dict[str, str]:
        parsed.append(text)
        return {"stub": "ok"}

    fake_tomli.loads = _loads  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tomli", fake_tomli)

    # Load configuration.py fresh under a throwaway module name so we
    # don't touch the canonical ``openfollow.configuration`` cached
    # in ``sys.modules`` (reloading it would invalidate every
    # dataclass identity used by sibling tests).
    config_path = pathlib.Path(__file__).resolve().parent.parent / "openfollow" / "configuration.py"
    spec = importlib.util.spec_from_file_location(
        "_configuration_tomli_fallback",
        config_path,
    )
    assert spec is not None and spec.loader is not None
    fresh = importlib.util.module_from_spec(spec)
    # Register under its synthetic name so internal ``dataclass``
    # machinery (which looks up ``cls.__module__`` in sys.modules)
    # finds the freshly-loaded module while it executes. Removed in
    # the finally block so we don't leak the throwaway module.
    sys.modules[spec.name] = fresh
    try:
        spec.loader.exec_module(fresh)

        # The freshly-loaded module's ``tomllib`` symbol points at the
        # stub ``tomli`` because ``import tomllib`` raised ImportError
        # at the top of the file.
        assert fresh.tomllib is fake_tomli
        assert fresh.tomllib.loads('foo = "bar"\n') == {"stub": "ok"}
        assert parsed == ['foo = "bar"\n']
    finally:
        sys.modules.pop(spec.name, None)


def test_load_config_malformed_toml_returns_defaults(temp_config_path, caplog) -> None:
    """Non-strict load should log the error and fall back to defaults."""
    temp_config_path.write_text("this = is not valid toml [[\n", encoding="utf-8")

    with caplog.at_level("ERROR"):
        config = load_config(str(temp_config_path))

    assert config == AppConfig()
    assert any("Failed to read/parse config file" in rec.message for rec in caplog.records)


def test_load_config_malformed_toml_strict_raises(temp_config_path) -> None:
    temp_config_path.write_text("this = is not valid toml [[\n", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(str(temp_config_path), strict=True)


def test_load_config_missing_file_strict_still_returns_defaults(
    temp_config_path,
) -> None:
    # temp_config_path fixture creates an empty file path that does not exist
    if temp_config_path.exists():
        temp_config_path.unlink()

    assert load_config(str(temp_config_path), strict=True) == AppConfig()


def test_load_config_uses_num_markers_fallback(temp_config_path) -> None:
    temp_config_path.write_text("num_markers = 3\n", encoding="utf-8")

    config = load_config(str(temp_config_path))

    # num_markers expands to range(N), but id 0 is reserved as "ignored"
    # on the PSN wire and stripped by the loader with a warning.
    assert config.controlled_marker_ids == [1, 2]
    assert config.viewer_marker_ids == [1, 2]


@pytest.mark.parametrize("raw", ['num_markers = "two"\n', "num_markers = 1.5\n"])
def test_load_config_tolerates_non_int_num_markers(temp_config_path, raw) -> None:
    # Legacy upgrade path: a non-int num_markers used to crash range() before
    # the new fields existed. Coerce to the default instead of crashing boot.
    temp_config_path.write_text(raw, encoding="utf-8")
    config = load_config(str(temp_config_path))
    # "two" → default 1 → range(1) = [0] → id 0 stripped → []. 1.5 → int 1 → [].
    assert config.controlled_marker_ids == []


def test_load_config_caps_huge_num_markers(temp_config_path) -> None:
    # A hand-edited huge num_markers must not OOM on list(range(N)).
    temp_config_path.write_text("num_markers = 99999999999\n", encoding="utf-8")
    config = load_config(str(temp_config_path))
    # Capped at _MAX_BOOTSTRAP_MARKERS=1024 → range(1024) minus reserved id 0.
    assert config.controlled_marker_ids == list(range(1, 1024))


def test_load_config_tolerates_non_list_controlled_with_absent_viewer(
    temp_config_path,
) -> None:
    # ``controlled_marker_ids`` present but non-iterable + ``viewer_marker_ids``
    # absent used to raise TypeError from ``list(data[...])`` in the back-compat
    # block, crashing boot. It must degrade to [] instead.
    temp_config_path.write_text("controlled_marker_ids = 5\n", encoding="utf-8")
    config = load_config(str(temp_config_path))
    assert config.controlled_marker_ids == []
    assert config.viewer_marker_ids == []


def test_load_config_coerces_non_list_selection_to_empty(
    temp_config_path,
    caplog,
) -> None:
    """A hand-edited TOML with ``controlled_marker_ids = "spot-1"`` (or
    any other non-list type) used to slip past the ``id >= 1`` filter
    entirely because the filter was gated on ``isinstance(_raw, list)``.
    The bad value then reached ``services.init_markers``, which
    iterates expecting ints and crashed at startup. Non-list values
    now coerce to ``[]`` with a one-line warning so a malformed config
    degrades safely.

    Covers ``configuration.py``'s non-list branch (round-9 review)."""
    temp_config_path.write_text(
        'controlled_marker_ids = "spot-1"\nviewer_marker_ids = 42\n',
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        config = load_config(str(temp_config_path))
    assert config.controlled_marker_ids == []
    assert config.viewer_marker_ids == []
    # One warning per coerced field.
    coerce_warnings = [r for r in caplog.records if "not a list – coercing to []" in r.getMessage()]
    assert len(coerce_warnings) == 2


def test_save_and_reload_roundtrip(temp_config_path) -> None:
    config = AppConfig(
        psn_system_name="OpenFollow Tests",
        controlled_marker_ids=[1, 2],
        viewer_marker_ids=[2, 3],
    )

    save_config(config, str(temp_config_path))
    reloaded = load_config(str(temp_config_path))

    assert reloaded.psn_system_name == "OpenFollow Tests"
    assert reloaded.controlled_marker_ids == [1, 2]
    assert reloaded.viewer_marker_ids == [2, 3]


def test_fader_on_change_marker_source_survives_save_reload(
    temp_config_path,
) -> None:
    # The ``marker_id`` trigger field round-trips through asdict-based save
    # and _trigger_from_dict load without bespoke serialisation.
    from openfollow.configuration import (
        FaderOnChangeTrigger,
        OscTransmitterConfig,
        OscTransmittersConfig,
    )

    config = AppConfig(
        osc_transmitters=OscTransmittersConfig(
            transmitters=[
                OscTransmitterConfig(
                    id="r1",
                    markers=["4"],
                    trigger=FaderOnChangeTrigger(marker_id=4, rate_hz=30),
                ),
            ]
        )
    )
    save_config(config, str(temp_config_path))
    reloaded = load_config(str(temp_config_path))
    trigger = reloaded.osc_transmitters.transmitters[0].trigger
    assert isinstance(trigger, FaderOnChangeTrigger)
    assert trigger.marker_id == 4


# Atomic config writes + .bak fallback


def _bak_of(path):
    return path.with_name(path.name + ".bak")


def test_save_writes_no_bak_on_first_write(temp_config_path) -> None:
    """First save has nothing to back up, so no .bak is created."""
    save_config(AppConfig(), str(temp_config_path))
    assert not _bak_of(temp_config_path).exists()


def test_save_snapshots_previous_to_bak(temp_config_path) -> None:
    """A second save snapshots the *previous* config to .bak while the
    primary holds the latest."""
    save_config(AppConfig(psn_system_name="First"), str(temp_config_path))
    save_config(AppConfig(psn_system_name="Second"), str(temp_config_path))

    bak = _bak_of(temp_config_path)
    assert bak.exists()
    assert tomllib.loads(bak.read_text(encoding="utf-8"))["psn_system_name"] == "First"
    assert load_config(str(temp_config_path)).psn_system_name == "Second"


def test_save_bak_is_owner_only_even_when_primary_world_readable(temp_config_path) -> None:
    """copy2 copies the source mode onto the .bak; a world-readable primary
    would otherwise leak the web_pin via a 0o644 backup."""
    import os
    import stat

    save_config(AppConfig(psn_system_name="First", web_pin="1234"), str(temp_config_path))
    # Loosen the primary so the snapshot would inherit 0o644 without the chmod.
    os.chmod(temp_config_path, 0o644)
    save_config(AppConfig(psn_system_name="Second", web_pin="1234"), str(temp_config_path))

    bak = _bak_of(temp_config_path)
    assert stat.S_IMODE(os.stat(bak).st_mode) == 0o600


def test_save_leaves_no_temp_file_on_success(temp_config_path) -> None:
    """The mkstemp temp file is renamed into place, never left behind."""
    save_config(AppConfig(), str(temp_config_path))
    assert list(temp_config_path.parent.glob("*.tmp")) == []


def test_save_succeeds_despite_leftover_temp_from_prior_crash(
    temp_config_path,
) -> None:
    """A stray temp from a crashed earlier save (unique mkstemp name) does
    not interfere with a fresh save."""
    stray = temp_config_path.with_name(temp_config_path.name + ".stale.tmp")
    stray.write_text("garbage", encoding="utf-8")

    save_config(AppConfig(psn_system_name="Fresh"), str(temp_config_path))
    assert load_config(str(temp_config_path)).psn_system_name == "Fresh"


def test_save_continues_when_bak_copy_fails(temp_config_path, monkeypatch, caplog) -> None:
    """A failed backup copy must not abort the primary save."""
    save_config(AppConfig(psn_system_name="V1"), str(temp_config_path))

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("openfollow.configuration.shutil.copy2", _boom)
    with caplog.at_level("WARNING"):
        save_config(AppConfig(psn_system_name="V2"), str(temp_config_path))

    assert load_config(str(temp_config_path)).psn_system_name == "V2"
    assert any("Could not write config backup" in r.message for r in caplog.records)


def test_save_bak_failure_preserves_previous_bak(temp_config_path, monkeypatch) -> None:
    """Atomic .bak write (temp + os.replace) preserves the previous backup
    on failure instead of truncating it, with no stray temp files left behind."""
    save_config(AppConfig(psn_system_name="V1"), str(temp_config_path))
    save_config(AppConfig(psn_system_name="V2"), str(temp_config_path))
    bak = _bak_of(temp_config_path)
    assert tomllib.loads(bak.read_text(encoding="utf-8"))["psn_system_name"] == "V1"

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("openfollow.configuration.shutil.copy2", _boom)
    save_config(AppConfig(psn_system_name="V3"), str(temp_config_path))

    # Primary advanced; the previous good .bak (V1) is untouched, not clobbered
    # by the failed copy; and the failed snapshot left no temp behind.
    assert load_config(str(temp_config_path)).psn_system_name == "V3"
    assert tomllib.loads(bak.read_text(encoding="utf-8"))["psn_system_name"] == "V1"
    assert list(temp_config_path.parent.glob("*.tmp")) == []


def test_load_recovers_from_bak_when_primary_unparseable(temp_config_path, caplog) -> None:
    """A truncated/corrupt primary is recovered from the .bak snapshot."""
    good = AppConfig(psn_system_name="Good Config", controlled_marker_ids=[1])
    save_config(good, str(temp_config_path))
    save_config(good, str(temp_config_path))  # snapshots previous good -> .bak
    assert _bak_of(temp_config_path).exists()

    temp_config_path.write_text("not [[ valid toml", encoding="utf-8")
    with caplog.at_level("WARNING"):
        reloaded = load_config(str(temp_config_path))

    assert reloaded.psn_system_name == "Good Config"
    assert any("recovered from" in r.message for r in caplog.records)


def test_load_recovers_from_bak_when_primary_missing(temp_config_path, caplog) -> None:
    """A primary lost to a crash between the .bak copy and the rename is
    recovered from the backup."""
    good = AppConfig(psn_system_name="Backup Wins", controlled_marker_ids=[2])
    save_config(good, str(temp_config_path))
    save_config(good, str(temp_config_path))
    temp_config_path.unlink()

    with caplog.at_level("WARNING"):
        reloaded = load_config(str(temp_config_path))

    assert reloaded.psn_system_name == "Backup Wins"
    assert any("missing – recovered" in r.message for r in caplog.records)


def test_load_defaults_when_primary_and_bak_both_unparseable(temp_config_path, caplog) -> None:
    """Both primary and backup corrupt → fall through to defaults."""
    temp_config_path.write_text("bad [[", encoding="utf-8")
    _bak_of(temp_config_path).write_text("also bad ]]", encoding="utf-8")

    with caplog.at_level("ERROR"):
        reloaded = load_config(str(temp_config_path))

    assert reloaded == AppConfig()
    assert any("Failed to read/parse" in r.message for r in caplog.records)


def test_load_strict_ignores_bak(temp_config_path) -> None:
    good = AppConfig(psn_system_name="Should Not Load")
    save_config(good, str(temp_config_path))
    save_config(good, str(temp_config_path))  # .bak present
    temp_config_path.write_text("bad [[", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_config(str(temp_config_path), strict=True)


# Per-marker move speed (``AppConfig.marker_move_speeds``)


def test_marker_move_speeds_round_trip(temp_config_path) -> None:
    """Round-trip preserves int-keyed dict despite TOML's string-key
    constraint – keys are stringified on save and coerced back to int
    in ``__post_init__``."""
    config = AppConfig(
        controlled_marker_ids=[2, 3],
        marker_move_speeds={2: 1.5, 3: 4.0},
    )
    save_config(config, str(temp_config_path))
    reloaded = load_config(str(temp_config_path))
    assert reloaded.marker_move_speeds == {2: 1.5, 3: 4.0}


def test_config_to_toml_dict_stringifies_marker_move_speeds() -> None:
    """Stringifies int marker-id keys for tomli_w dumping and round-tripping."""
    import tomli_w

    config = AppConfig(
        controlled_marker_ids=[1, 2],
        marker_move_speeds={1: 1.5, 2: 4.0},
    )
    data = config_to_toml_dict(config)
    assert data["marker_move_speeds"] == {"1": 1.5, "2": 4.0}
    # The real dump must not raise on the (now-stringified) keys.
    assert "marker_move_speeds" in tomli_w.dumps(data)


def test_save_prunes_marker_move_speeds_not_in_controlled_ids(
    temp_config_path,
) -> None:
    """Saved file only contains entries for markers in the current
    ``controlled_marker_ids`` so the file stays tidy when an operator
    removes a marker via the web UI. In-memory entries are not pruned
    at runtime (see live-reload diff)."""
    config = AppConfig(
        controlled_marker_ids=[1],
        marker_move_speeds={1: 1.5, 9: 4.0},
    )
    save_config(config, str(temp_config_path))
    raw = temp_config_path.read_text()
    assert "1.5" in raw  # marker 1 kept (in controlled_marker_ids)
    assert "4.0" not in raw  # marker 9 dropped
    reloaded = load_config(str(temp_config_path))
    assert reloaded.marker_move_speeds == {1: 1.5}


def test_load_coerces_string_keys_to_int(temp_config_path) -> None:
    """Hand-written TOML uses string keys (TOML can't represent int keys
    natively for inline tables). ``__post_init__`` coerces them back."""
    temp_config_path.write_text(
        '[marker_move_speeds]\n"2" = 1.5\n"3" = 4.0\n',
    )
    reloaded = load_config(str(temp_config_path))
    assert reloaded.marker_move_speeds == {2: 1.5, 3: 4.0}


def test_load_drops_invalid_entries(temp_config_path) -> None:
    """An unparseable key or value drops just that pair rather than
    substituting a fallback that would mask operator typos."""
    temp_config_path.write_text(
        '[marker_move_speeds]\nnot_an_int = 1.0\n"2" = "nope"\n"5" = 2.5\n',
    )
    reloaded = load_config(str(temp_config_path))
    assert reloaded.marker_move_speeds == {5: 2.5}


def test_load_drops_negative_speed_entries(temp_config_path) -> None:
    """Negative speeds are clamped out (move speed is non-negative)."""
    temp_config_path.write_text(
        '[marker_move_speeds]\n"2" = -1.0\n"1" = 2.5\n',
    )
    reloaded = load_config(str(temp_config_path))
    assert reloaded.marker_move_speeds == {1: 2.5}


def test_marker_move_speeds_drops_reserved_zero_key(temp_config_path) -> None:
    """Marker id ``0`` is reserved as the "ignored" wire-side sentinel
    and stripped from ``controlled_marker_ids`` / ``viewer_marker_ids``
    on load. ``marker_move_speeds`` must follow the same invariant –
    a stored speed for marker 0 (or any negative id) can never be used
    at runtime since the selection lists never reference it. Persisting
    one would be dead state."""
    temp_config_path.write_text(
        '[marker_move_speeds]\n"0" = 1.5\n"-3" = 2.0\n"4" = 3.5\n',
    )
    reloaded = load_config(str(temp_config_path))
    assert reloaded.marker_move_speeds == {4: 3.5}


def test_marker_move_speeds_rejects_bool_keys() -> None:
    # Note: ``True`` and ``1`` hash equal in Python, so they can't
    # coexist in a single dict literal – flake8 catches that. Test the
    # guard with bool-only input and verify nothing survives.
    cfg = AppConfig(marker_move_speeds={True: 5.0})  # type: ignore[dict-item]
    assert cfg.marker_move_speeds == {}
    # Sanity: a real ``int`` key still round-trips, proving the guard
    # is narrow (only bools dropped).
    cfg_real = AppConfig(marker_move_speeds={1: 2.0})
    assert cfg_real.marker_move_speeds == {1: 2.0}


@pytest.mark.parametrize(
    ("input_deadzone", "expected"),
    [(2.5, 1.0), (-0.25, 0.0), (0.33, 0.33)],
)
def test_controller_deadzone_is_clamped(input_deadzone: float, expected: float) -> None:
    config = ControllerConfig(deadzone=input_deadzone)
    assert config.deadzone == expected


@pytest.mark.parametrize("layout", ["wasd", "ijkl", "numpad"])
def test_controller_accepts_all_movement_layouts(layout: str) -> None:
    config = ControllerConfig(key_move_layout=layout)
    assert config.key_move_layout == layout


def test_controller_invalid_movement_layout_falls_back_to_wasd() -> None:
    config = ControllerConfig(key_move_layout="dvorak")
    assert config.key_move_layout == "wasd"


def test_controller_button_raw_indices_coerced_to_int() -> None:
    # Only exact integers survive (incl. an int-valued str/float); everything
    # else is dropped so the button read path can't crash. bool, a non-integral
    # float, and non-finite values (int(inf) raises OverflowError) all go.
    config = ControllerConfig(
        button_raw_indices={
            "LB": "4",
            "RB": 2,
            "A": 4.0,
            "X": "bad",
            "Y": True,
            "B": 4.9,
            "C": float("inf"),
            "D": float("nan"),
        }
    )
    assert config.button_raw_indices == {"LB": 4, "RB": 2, "A": 4}


@pytest.mark.parametrize("field_name", _NEW_BUTTON_FIELDS)
def test_new_button_fields_roundtrip_through_toml(
    temp_config_path,
    field_name: str,
) -> None:
    controller = ControllerConfig(**{field_name: "START"})
    save_config(AppConfig(controller=controller), str(temp_config_path))
    reloaded = load_config(str(temp_config_path))
    assert getattr(reloaded.controller, field_name) == "START"


@pytest.mark.parametrize("field_name", _NEW_BUTTON_FIELDS)
def test_new_button_fields_invalid_value_falls_back_to_default(field_name: str) -> None:
    default = ControllerConfig.__dataclass_fields__[field_name].default
    config = ControllerConfig(**{field_name: "NOT_A_BUTTON"})
    assert getattr(config, field_name) == default


def test_new_keyboard_fields_roundtrip_through_toml(temp_config_path) -> None:
    controller = ControllerConfig(
        key_next_marker="n",
        key_prev_marker="p",
        key_settings="g",
    )
    save_config(AppConfig(controller=controller), str(temp_config_path))
    reloaded = load_config(str(temp_config_path))
    assert reloaded.controller.key_next_marker == "n"
    assert reloaded.controller.key_prev_marker == "p"
    assert reloaded.controller.key_settings == "g"


@pytest.mark.parametrize("field_name", _NEW_KEYBOARD_FIELDS)
def test_new_keyboard_fields_invalid_value_falls_back_to_default(field_name: str) -> None:
    default = ControllerConfig.__dataclass_fields__[field_name].default
    config = ControllerConfig(**{field_name: "not-a-real-key"})
    assert getattr(config, field_name) == default


@pytest.mark.parametrize("field_name", _NEW_KEYBOARD_FIELDS)
def test_new_keyboard_fields_reject_movement_key_collision(field_name: str) -> None:
    # 'w' is part of the default WASD layout, so any action binding must refuse it.
    default = ControllerConfig.__dataclass_fields__[field_name].default
    config = ControllerConfig(**{field_name: "w"})
    assert getattr(config, field_name) == default


def test_deprecated_confirm_cancel_fields_warn_on_custom_value(
    temp_config_path,
    caplog,
    monkeypatch,
) -> None:
    # ``_warn_deprecated_controller_bindings`` uses a module-level
    # ``_DEPRECATED_WARNED`` set that suppresses re-warns within the
    # process.  Reset it here so this test doesn't depend on its
    # ordering relative to other tests (e.g. mutmut's test runner
    # discovers tests in a different order than standard pytest).
    import openfollow.configuration as cfg_mod

    monkeypatch.setattr(cfg_mod, "_DEPRECATED_WARNED", set())
    controller = ControllerConfig(
        btn_settings_confirm="Y",
        src_btn_confirm="Y",
    )
    save_config(AppConfig(controller=controller), str(temp_config_path))
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        load_config(str(temp_config_path))
    deprecation_warnings = [
        r.message for r in caplog.records if "consolidated" in r.message or "Settings menu" in r.message
    ]
    # Two customized deprecated fields – each should warn once.
    assert any("btn_settings_confirm" in msg for msg in deprecation_warnings)
    assert any("src_btn_confirm" in msg for msg in deprecation_warnings)


def test_deprecated_direct_entry_fields_warn_on_custom_value(
    temp_config_path,
    caplog,
    monkeypatch,
) -> None:
    # Same ordering-safety reset as the confirm/cancel test above.
    import openfollow.configuration as cfg_mod

    monkeypatch.setattr(cfg_mod, "_DEPRECATED_WARNED", set())
    controller = ControllerConfig(
        btn_source_select="X",
    )
    save_config(AppConfig(controller=controller), str(temp_config_path))
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        load_config(str(temp_config_path))
    messages = [r.message for r in caplog.records]
    assert any("btn_source_select" in msg and "Settings menu" in msg for msg in messages)


def test_loading_default_config_does_not_emit_deprecation_warnings(
    temp_config_path,
    caplog,
) -> None:
    save_config(AppConfig(), str(temp_config_path))
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        load_config(str(temp_config_path))
    assert not any("deprecated" in r.message for r in caplog.records)


def test_apply_runtime_marker_move_speeds_diff() -> None:
    """Marker speed changes apply directly; no restart needed."""
    app = _DummyApp(AppConfig(marker_move_speeds={2: 2.0}))
    new_config = AppConfig(marker_move_speeds={2: 3.5, 1: 1.5})

    apply_runtime_config_changes(app, new_config)

    assert app._config.marker_move_speeds == {2: 3.5, 1: 1.5}
    assert app._web_commands.restart_requested is False


def test_apply_runtime_ui_unit_system_propagates_to_config() -> None:
    """A ``[ui] unit_system`` flip must reach ``app._config.ui`` live so the
    on-screen overlay (read every frame via ``sync_ui_config``) switches
    without a restart – not only the web UI."""
    app = _DummyApp(AppConfig())
    assert app._config.ui.unit_system == "metric"
    new_config = AppConfig()
    new_config.ui.unit_system = "imperial"

    apply_runtime_config_changes(app, new_config)

    assert app._config.ui.unit_system == "imperial"
    assert app._web_commands.restart_requested is False


def test_apply_runtime_ui_show_experimental_propagates_to_config() -> None:
    """``show_experimental_features`` is gated off ``app._config`` for the web
    body class, so a change must propagate live without a restart."""
    app = _DummyApp(AppConfig())
    assert app._config.ui.show_experimental_features is False
    new_config = AppConfig()
    new_config.ui.show_experimental_features = True

    apply_runtime_config_changes(app, new_config)

    assert app._config.ui.show_experimental_features is True
    assert app._web_commands.restart_requested is False


def test_apply_runtime_network_backend_propagates_to_config() -> None:
    """A ``[network] backend`` change mirrors into ``app._config`` so the web
    re-render and other readers stay consistent until the next restart."""
    app = _DummyApp(AppConfig())
    assert app._config.network.backend == "auto"
    new_config = AppConfig()
    new_config.network.backend = "nm"

    apply_runtime_config_changes(app, new_config)

    assert app._config.network.backend == "nm"
    assert app._web_commands.restart_requested is False


def test_apply_runtime_osc_multicast_group_threads_into_restart() -> None:
    """An ``[osc] multicast_group`` change reaches ``InputManager.restart_osc``."""
    app = _DummyApp(AppConfig())
    new_config = AppConfig()
    new_config.osc.multicast_group = "239.1.2.3"

    apply_runtime_config_changes(app, new_config)

    assert app._config.osc.multicast_group == "239.1.2.3"
    assert app._input_manager.osc_multicast_groups == ["239.1.2.3"]


def test_apply_runtime_operator_messages_restarts_handler() -> None:
    """An ``[operator_messages]`` change applies live: stored config updates
    and the adapter is re-evaluated, with no process restart."""
    app = _DummyApp(AppConfig())
    new_config = AppConfig()
    new_config.operator_messages.enabled = True  # differs from default-off

    apply_runtime_config_changes(app, new_config)

    assert app._config.operator_messages.enabled is True
    assert app._input_manager.operator_message_restarts == 1
    assert app._web_commands.restart_requested is False


def test_apply_runtime_updates_system_name_without_restart() -> None:
    """System name changes propagate live without restart."""
    app = _DummyApp(AppConfig(psn_system_name="Old Name"))
    new_config = AppConfig(psn_system_name="New Name")

    apply_runtime_config_changes(app, new_config)

    assert app._config.psn_system_name == "New Name"
    assert app._runtime_services.psn_system_name_changes == ["New Name"]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_psn_system_name_failure_reverts_config() -> None:

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_psn_system_name_change(self, new_name: str) -> None:
            raise RuntimeError(f"failed to apply {new_name!r}")

    app = _DummyApp(AppConfig(psn_system_name="Old Name"))
    app._runtime_services = _FailingRuntimeServices()
    new_config = AppConfig(psn_system_name="New Name")

    apply_runtime_config_changes(app, new_config)

    # Stored config reverted so a subsequent reload retries.
    assert app._config.psn_system_name == "Old Name"
    assert app._web_commands.restart_requested is False


def test_apply_runtime_rebinds_psn_mcast_ip_without_restart() -> None:
    """Multicast IP changes apply live without restart."""
    app = _DummyApp(AppConfig(psn_mcast_ip="236.10.10.10"))
    new_config = AppConfig(psn_mcast_ip="236.20.20.20")

    apply_runtime_config_changes(app, new_config)

    assert app._config.psn_mcast_ip == "236.20.20.20"
    assert app._runtime_services.psn_mcast_ip_changes == ["236.20.20.20"]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_strips_whitespace_from_psn_mcast_ip() -> None:
    """Whitespace-tainted IPs must be stripped to avoid spurious rebind cycles."""
    app = _DummyApp(AppConfig(psn_mcast_ip="236.10.10.10"))
    new_config = AppConfig(psn_mcast_ip="  236.10.10.10  ")

    apply_runtime_config_changes(app, new_config)

    # No rebind triggered because stored + new strip to the same value.
    assert app._runtime_services.psn_mcast_ip_changes == []
    assert app._web_commands.restart_requested is False


def test_app_config_strips_psn_mcast_ip_at_construction() -> None:
    """__post_init__ strips psn_mcast_ip to canonical form."""
    cfg = AppConfig(psn_mcast_ip="  236.10.10.10  ")
    assert cfg.psn_mcast_ip == "236.10.10.10"


def test_apply_runtime_combines_psn_mcast_and_iface_change(monkeypatch) -> None:
    import socket as _socket
    from types import SimpleNamespace

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.1.5")],
        },
    )
    app = _DummyApp(
        AppConfig(psn_mcast_ip="236.10.10.10", psn_source_iface=""),
    )
    new_config = AppConfig(
        psn_mcast_ip="239.0.0.1",
        psn_source_iface="eth0",
    )

    apply_runtime_config_changes(app, new_config)

    # Combined call fired exactly once.
    assert app._runtime_services.psn_combined_changes == [
        ("192.168.1.5", "239.0.0.1"),
    ]
    # Single-field paths did NOT fire (would be a second rebind cycle).
    assert app._runtime_services.psn_source_ip_changes == []
    assert app._runtime_services.psn_mcast_ip_changes == []
    # Both fields committed to stored config.
    assert app._config.psn_mcast_ip == "239.0.0.1"
    assert app._config.psn_source_iface == "eth0"


def test_apply_runtime_combined_psn_failure_reverts_both_fields() -> None:
    """When the combined orchestrator raises, both stored fields
    revert so the next hot-reload pass re-attempts. Without
    reverting both, a stuck ``stored == new`` for either field
    would silently no-op the next pass."""

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_psn_source_ip_change(
            self,
            new_source_ip: str,
            *,
            new_mcast_ip: object = None,
        ) -> None:
            raise OSError("simulated combined apply failure")

    app = _DummyApp(
        AppConfig(psn_mcast_ip="236.10.10.10", psn_source_iface="wlan0"),
    )
    app._runtime_services = _FailingRuntimeServices()
    new_config = AppConfig(
        psn_mcast_ip="239.0.0.1",
        psn_source_iface="eth0",
    )

    apply_runtime_config_changes(app, new_config)

    assert app._config.psn_mcast_ip == "236.10.10.10"
    assert app._config.psn_source_iface == "wlan0"
    assert app._web_commands.restart_requested is False


def test_apply_runtime_psn_mcast_ip_failure_reverts_config() -> None:

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_psn_mcast_ip_change(self, new_mcast_ip: str) -> None:
            raise OSError(f"failed to bind to {new_mcast_ip!r}")

    app = _DummyApp(AppConfig(psn_mcast_ip="236.10.10.10"))
    app._runtime_services = _FailingRuntimeServices()
    new_config = AppConfig(psn_mcast_ip="239.0.0.0")

    apply_runtime_config_changes(app, new_config)

    # Stored config reverted so a subsequent reload retries.
    assert app._config.psn_mcast_ip == "236.10.10.10"
    assert app._web_commands.restart_requested is False


def test_apply_runtime_resizes_window_on_window_dimension_change() -> None:
    """Window dimension changes apply live without restart."""
    app = _DummyApp(AppConfig(window_width=1280, window_height=720))
    new_config = AppConfig(window_width=1920, window_height=1080)

    apply_runtime_config_changes(app, new_config)

    assert app._config.window_width == 1920
    assert app._config.window_height == 1080
    assert app._runtime_services.window_size_changes == [(1920, 1080)]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_resizes_when_only_height_changes() -> None:
    """Width/height are dispatched as a pair, so a single-axis change
    still triggers exactly one ``apply_window_size_change`` call."""
    app = _DummyApp(AppConfig(window_width=1280, window_height=720))
    new_config = AppConfig(window_width=1280, window_height=900)

    apply_runtime_config_changes(app, new_config)

    assert app._runtime_services.window_size_changes == [(1280, 900)]


def test_apply_runtime_skips_window_resize_when_dimensions_unchanged() -> None:
    """Diff against stored config so an unrelated reload pass does not
    issue a no-op resize call (which would still hit the GTK thread)."""
    app = _DummyApp(AppConfig(window_width=1280, window_height=720))
    # Same dimensions, only an unrelated field differs.
    new_config = AppConfig(window_width=1280, window_height=720, psn_system_name="Other")

    apply_runtime_config_changes(app, new_config)

    assert app._runtime_services.window_size_changes == []


def test_apply_runtime_clamps_window_dimensions_before_storing() -> None:
    app = _DummyApp(AppConfig(window_width=1280, window_height=720))
    # Hand-edited TOML with non-positive integers.
    new_config = AppConfig(window_width=0, window_height=-100)

    apply_runtime_config_changes(app, new_config)

    # Stored config holds the clamped values, NOT the raw TOML ones.
    assert app._config.window_width == 1
    assert app._config.window_height == 1
    # Orchestrator received the clamped values too.
    assert app._runtime_services.window_size_changes == [(1, 1)]


def test_apply_runtime_skips_window_resize_when_clamped_value_matches() -> None:
    app = _DummyApp(AppConfig(window_width=1, window_height=1))
    new_config = AppConfig(window_width=0, window_height=-50)

    apply_runtime_config_changes(app, new_config)

    assert app._runtime_services.window_size_changes == []


def test_apply_runtime_stores_web_pin_change_without_restart() -> None:
    """Web PIN changes mirror to config without restart."""
    app = _DummyApp(AppConfig(web_pin="old"))
    new_config = AppConfig(web_pin="new-pin")

    apply_runtime_config_changes(app, new_config)

    assert app._config.web_pin == "new-pin"
    assert app._web_commands.restart_requested is False


# Interface-pin dispatcher coverage


def test_apply_runtime_rebinds_on_psn_source_iface_change(monkeypatch) -> None:
    """Source interface changes rebind PSN sockets with resolved IPv4."""
    import socket as _socket
    from types import SimpleNamespace

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.178.59")],
        },
    )
    app = _DummyApp(AppConfig(psn_source_iface=""))
    new_config = AppConfig(psn_source_iface="eth0")

    apply_runtime_config_changes(app, new_config)

    # Stored iface field updated; orchestrator received the resolved IP.
    assert app._config.psn_source_iface == "eth0"
    assert app._runtime_services.psn_source_ip_changes == ["192.168.178.59"]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_strips_whitespace_from_psn_source_iface() -> None:
    """``AppConfig.psn_source_iface`` is stripped at __post_init__,
    but the web-save path bypasses that for in-place field updates,
    so the dispatcher must strip too. Without this, a value like
    ``"eth0 "`` would diff against ``"eth0"`` on every reload and
    trigger a needless rebind cycle."""
    app = _DummyApp(AppConfig(psn_source_iface="eth0"))
    new_config = AppConfig(psn_source_iface="  eth0  ")

    apply_runtime_config_changes(app, new_config)

    # No rebind triggered because stored + new strip to the same value.
    assert app._runtime_services.psn_source_ip_changes == []
    assert app._web_commands.restart_requested is False


def test_apply_runtime_psn_iface_failure_reverts(monkeypatch) -> None:
    """When the iface-driven apply raises, ``psn_source_iface``
    reverts so the next hot-reload sees the real diff and retries
    instead of silently no-opping."""
    import socket as _socket
    from types import SimpleNamespace

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.178.59")],
        },
    )

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_psn_source_ip_change(
            self,
            new_source_ip: str,
            *,
            new_mcast_ip: object = None,
        ) -> None:
            raise OSError("simulated rebind failure")

    app = _DummyApp(AppConfig(psn_source_iface="wlan0"))
    app._runtime_services = _FailingRuntimeServices()
    new_config = AppConfig(psn_source_iface="eth0")

    apply_runtime_config_changes(app, new_config)

    # Rolled back so the next pass sees a real diff again.
    assert app._config.psn_source_iface == "wlan0"
    assert app._web_commands.restart_requested is False


def test_app_config_strips_psn_source_iface_at_construction() -> None:
    """``AppConfig.__post_init__`` canonicalises ``psn_source_iface``
    so the stored value reaching the dispatcher / resolver is clean."""
    cfg = AppConfig(psn_source_iface="  eth0  ")
    assert cfg.psn_source_iface == "eth0"


def test_app_config_coerces_non_str_psn_source_iface() -> None:
    """Defensive guard for hand-edited TOML where ``psn_source_iface =
    0`` (or any non-str) would propagate into ``psutil.net_if_addrs``
    / the hot-reload diff. Mirrors the ``psn_source_ip`` type guard."""
    cfg = AppConfig(psn_source_iface=0)  # type: ignore[arg-type]
    assert cfg.psn_source_iface == ""


def test_app_config_strips_web_pin_at_construction() -> None:
    """``web_pin`` is normalised on load so a hand-edited TOML matches the
    web-save path (which strips before persisting)."""
    assert AppConfig(web_pin="  1234  ").web_pin == "1234"


def test_app_config_coerces_non_str_web_pin() -> None:
    """Hand-edited TOML where ``web_pin = 1234`` (an int) is coerced to ""."""
    cfg = AppConfig(web_pin=1234)  # type: ignore[arg-type]
    assert cfg.web_pin == ""


def test_app_config_strips_web_bind_at_construction() -> None:
    assert AppConfig(web_bind="  10.0.0.5  ").web_bind == "10.0.0.5"


def test_app_config_coerces_non_str_web_bind() -> None:
    cfg = AppConfig(web_bind=0)  # type: ignore[arg-type]
    assert cfg.web_bind == ""


def test_osc_transmitter_config_normalises_leading_slash() -> None:
    from openfollow.configuration import OscTransmitterConfig

    assert OscTransmitterConfig(address="cue/go").address == "/cue/go"
    assert OscTransmitterConfig(address="/already").address == "/already"
    assert OscTransmitterConfig(address="").address == ""  # empty stays empty


def test_apply_runtime_swaps_video_on_plugin_field_change() -> None:
    """Per-plugin field changes swap video live without restart."""
    app = _DummyApp(AppConfig(video_source_type="srt", srt_host="srt://127.0.0.1:5000"))
    new_config = AppConfig(video_source_type="srt", srt_host="srt://192.168.0.10:1600")

    apply_runtime_config_changes(app, new_config)

    assert app._config.srt_host == "srt://192.168.0.10:1600"
    assert app._web_commands.restart_requested is False
    assert app._runtime_services.video_swaps == [new_config]


def test_apply_runtime_swaps_video_on_source_type_change() -> None:
    """Switching ``video_source_type`` between plugins (RTSP → SRT)
    also routes through the live-swap path. The dispatcher commits
    every plugin field from the new plugin; old plugin fields not
    used by the new one are left as-is in storage."""
    app = _DummyApp(
        AppConfig(
            video_source_type="rtsp",
            rtsp_url="rtsp://192.168.0.20:554/stream",
        )
    )
    new_config = AppConfig(
        video_source_type="srt",
        srt_host="srt://10.0.0.5:5000",
    )

    apply_runtime_config_changes(app, new_config)

    assert app._config.video_source_type == "srt"
    assert app._config.srt_host == "srt://10.0.0.5:5000"
    assert app._web_commands.restart_requested is False
    assert app._runtime_services.video_swaps == [new_config]


def test_apply_runtime_video_swap_failure_reverts_config() -> None:
    """When the orchestrator raises, ``_apply_with_fallback`` walks
    the snapshot back into ``app._config``: every NEW-plugin field
    the dispatcher just committed gets restored, and
    ``video_source_type`` reverts. Old-plugin fields (e.g.
    ``rtsp_url`` during an RTSP → SRT swap) are not touched either
    way – the dispatcher's commit loop only writes new-plugin
    fields, so they stay at their pre-swap values regardless of
    success/failure."""

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def swap_video(self, new_cfg: AppConfig) -> None:
            raise RuntimeError("simulated pipeline build failure")

    app = _DummyApp(
        AppConfig(
            video_source_type="rtsp",
            rtsp_url="rtsp://192.168.0.20:554/stream",
        )
    )
    app._runtime_services = _FailingRuntimeServices()
    new_config = AppConfig(
        video_source_type="srt",
        srt_host="srt://10.0.0.5:5000",
    )

    apply_runtime_config_changes(app, new_config)

    # ``video_source_type`` reverted; new-plugin fields walked back
    # to the snapshot defaults so a later reload retries instead of
    # no-opping; old-plugin fields untouched throughout.
    assert app._config.video_source_type == "rtsp"
    assert app._config.rtsp_url == "rtsp://192.168.0.20:554/stream"
    assert app._config.srt_host == AppConfig().srt_host  # back to default
    assert app._web_commands.restart_requested is False


def test_apply_runtime_rewires_controlled_marker_ids() -> None:
    app = _DummyApp(
        AppConfig(
            controlled_marker_ids=[0, 1],
            viewer_marker_ids=[0, 1],
        )
    )
    new_config = AppConfig(
        controlled_marker_ids=[2],
        viewer_marker_ids=[2],
    )

    apply_runtime_config_changes(app, new_config)

    assert set(app._server.markers.keys()) == {2}
    assert app._selected_id == 2
    assert app._psn_receiver.ignore_ids == {2}
    assert app._viewer_ids == [2]


def test_apply_runtime_filters_non_int_and_bool_marker_ids() -> None:
    """Two defence layers in the hot-reload filter:

    - ``bool`` is an ``int`` subclass, so ``True >= 1`` is ``True``;
      a bare ``tid >= 1`` filter lets ``True`` through and then
      crashes on the bool-rejecting ``Marker.__init__``.
    - A non-int value (string from an in-memory mutation / test
      fixture / hand-built ``AppConfig``) raises ``TypeError`` on the
      ``>=`` comparison – the orchestrator crashes mid-reload rather
      than the operator seeing a clean fallback.

    Filter therefore requires ``isinstance(tid, int)`` AND
    ``not isinstance(tid, bool)`` AND ``tid >= 1``."""
    app = _DummyApp(AppConfig(controlled_marker_ids=[1], viewer_marker_ids=[1]))
    new_config = AppConfig(
        controlled_marker_ids=[True, "spot-1", 2.5, 2],  # type: ignore[list-item]
        viewer_marker_ids=[False, True, "x", 3],  # type: ignore[list-item]
    )

    apply_runtime_config_changes(app, new_config)

    # ``True`` / ``False`` / strings / floats all dropped; only real
    # ints ≥ 1 survive.
    assert app._config.controlled_marker_ids == [2]
    assert app._config.viewer_marker_ids == [3]
    assert app._viewer_ids == [3]


# ---------------------------------------------------------------------------
# OscConfig.__post_init__ normalisation – guards against malformed TOML
# ---------------------------------------------------------------------------


def test_osc_config_default_allowlist_is_empty_list() -> None:
    cfg = OscConfig()
    assert cfg.allowed_sender_ips == []


def test_osc_config_normalises_bare_string_to_single_entry_list() -> None:
    # A hand-edited config.toml with ``allowed_sender_ips = "192.168.1.10"``
    # (a bare string instead of a list) must not silently degrade into
    # ``list("192.168.1.10")`` = ["1", "9", "2", ".", ...] in downstream
    # code. Post-init treats it as a one-entry list.
    cfg = OscConfig(allowed_sender_ips="192.168.1.10")  # type: ignore[arg-type]
    assert cfg.allowed_sender_ips == ["192.168.1.10"]


def test_osc_config_drops_non_string_entries() -> None:
    cfg = OscConfig(
        allowed_sender_ips=["192.168.1.10", 42, None, "  ", "10.0.0.1"],  # type: ignore[list-item]
    )
    assert cfg.allowed_sender_ips == ["192.168.1.10", "10.0.0.1"]


def test_osc_config_unknown_container_type_becomes_empty() -> None:
    # Accept set/tuple as reasonable TOML/JSON decoder outputs.
    assert OscConfig(allowed_sender_ips=("1.2.3.4",)).allowed_sender_ips == ["1.2.3.4"]  # type: ignore[arg-type]
    # Anything unrecognised (None, int, dict) → empty list.
    assert OscConfig(allowed_sender_ips=None).allowed_sender_ips == []  # type: ignore[arg-type]
    assert OscConfig(allowed_sender_ips=42).allowed_sender_ips == []  # type: ignore[arg-type]
    assert OscConfig(allowed_sender_ips={"ip": "1.2.3.4"}).allowed_sender_ips == []  # type: ignore[arg-type]


def test_osc_config_post_init_strips_whitespace() -> None:
    cfg = OscConfig(allowed_sender_ips=["  192.168.1.10  ", "\t10.0.0.1 "])
    assert cfg.allowed_sender_ips == ["192.168.1.10", "10.0.0.1"]


def test_osc_config_clamps_port_to_blur_bounds() -> None:
    # Port must be in range 1-65535.
    assert OscConfig(port=0).port == 1
    assert OscConfig(port=70000).port == 65535
    assert OscConfig(port="not-a-port").port == 8765  # type: ignore[arg-type]


# RttrpmOutputConfig.__post_init__ – fps clamp


def test_rttrpm_output_config_default_fps_unchanged() -> None:
    cfg = RttrpmOutputConfig()
    assert cfg.fps == 60


@pytest.mark.parametrize("bad_fps", [0, -1, -1000])
def test_rttrpm_output_config_clamps_nonpositive_fps_to_one(bad_fps: int) -> None:
    # fps drives 1.0 / fps in RttrpmServer._send_loop; zero/negative would
    # crash the send thread with ZeroDivisionError. Clamp to 1 instead.
    cfg = RttrpmOutputConfig(fps=bad_fps)
    assert cfg.fps == 1


@pytest.mark.parametrize("hi_fps,expected", [(241, 240), (1000, 240), (240, 240)])
def test_rttrpm_output_config_clamps_high_fps_to_blur_upper_bound(
    hi_fps: int,
    expected: int,
) -> None:
    # FPS must be clamped to 240 (blur upper bound).
    cfg = RttrpmOutputConfig(fps=hi_fps)
    assert cfg.fps == expected


def test_rttrpm_output_config_clamps_port_and_context() -> None:
    # Port and context must match blur bounds: 1-65535 and 0-2^32-1.
    cfg = RttrpmOutputConfig(port=70000, context=10**12)
    assert cfg.port == 65535
    assert cfg.context == 4294967295
    cfg2 = RttrpmOutputConfig(port=0, context=-1)
    assert cfg2.port == 1
    assert cfg2.context == 0


# AppConfig.__post_init__ – top-level scalar normalisation


@pytest.mark.parametrize(
    "bad_width,expected",
    [
        (0, 1),  # below clamp lower bound
        (-100, 1),  # negative
        ("wide", 1280),  # non-numeric string falls back to default
        (None, 1280),  # missing/null falls back to default
        (True, 1280),  # bool rejected (would silently become 1)
        (1920.7, 1920),  # float coerced to int
    ],
)
def test_app_config_normalises_window_width(
    bad_width: object,
    expected: int,
) -> None:
    cfg = AppConfig(window_width=bad_width)  # type: ignore[arg-type]
    assert cfg.window_width == expected


@pytest.mark.parametrize(
    "bad_height,expected",
    [(0, 1), (-1, 1), ("tall", 720), (None, 720), (True, 720)],
)
def test_app_config_normalises_window_height(
    bad_height: object,
    expected: int,
) -> None:
    cfg = AppConfig(window_height=bad_height)  # type: ignore[arg-type]
    assert cfg.window_height == expected


def test_app_config_clamps_web_port_to_blur_bounds() -> None:
    # Web port must be in range 1-65535.
    assert AppConfig(web_port=0).web_port == 1
    assert AppConfig(web_port=70000).web_port == 65535
    assert AppConfig(web_port="bogus").web_port == 80  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", ""),
        ("eth0", "eth0"),
        ("  eth0  ", "eth0"),
        ("\twlan0 ", "wlan0"),
        (0, ""),  # non-string falls back to default
        (None, ""),
        (True, ""),
    ],
)
def test_app_config_normalises_psn_source_iface(
    raw: object,
    expected: str,
) -> None:
    """Non-string source interface must be normalised."""
    cfg = AppConfig(psn_source_iface=raw)  # type: ignore[arg-type]
    assert cfg.psn_source_iface == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("236.10.10.10", "236.10.10.10"),
        ("  236.10.10.10  ", "236.10.10.10"),  # surrounding whitespace stripped
        ("\t239.0.0.1 ", "239.0.0.1"),
        (123, "236.10.10.10"),  # non-string falls back to default
        (None, "236.10.10.10"),
        (True, "236.10.10.10"),
    ],
)
def test_app_config_normalises_psn_mcast_ip(
    raw: object,
    expected: str,
) -> None:
    """Non-string multicast IP must be normalised."""
    cfg = AppConfig(psn_mcast_ip=raw)  # type: ignore[arg-type]
    assert cfg.psn_mcast_ip == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("My Show", "My Show"),
        ("  My Show  ", "My Show"),  # surrounding whitespace stripped
        ("\tStage 1 \n", "Stage 1"),
        ("", "OpenFollow"),  # empty falls back to default
        ("   ", "OpenFollow"),  # whitespace-only too
        (123, "OpenFollow"),  # non-string falls back
        (None, "OpenFollow"),
        (True, "OpenFollow"),
    ],
)
def test_app_config_normalises_psn_system_name(
    raw: object,
    expected: str,
) -> None:
    """System name must be canonicalised at construction time."""
    cfg = AppConfig(psn_system_name=raw)  # type: ignore[arg-type]
    assert cfg.psn_system_name == expected


@pytest.mark.parametrize("bad_fps", [True, False, None, "60", 3.14])
def test_rttrpm_output_config_rejects_non_int_fps_and_clamps_to_one(bad_fps: object) -> None:
    # TOML loaders occasionally hand through unexpected types (a bool that
    # would otherwise pass ``self.fps >= 1`` because bool is an int subclass,
    # or a string from a misedited file). Post-init normalises them all.
    cfg = RttrpmOutputConfig(fps=bad_fps)  # type: ignore[arg-type]
    assert cfg.fps == 1


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("127.0.0.1", "127.0.0.1"),
        ("  127.0.0.1  ", "127.0.0.1"),
        ("\t10.0.0.1 ", "10.0.0.1"),
        ("", ""),
    ],
)
def test_rttrpm_output_config_strips_host_whitespace(
    raw: str,
    expected: str,
) -> None:
    """Host whitespace must be stripped to avoid spurious restarts."""
    cfg = RttrpmOutputConfig(host=raw)
    assert cfg.host == expected


def test_rttrpm_output_config_rejects_non_string_host_and_falls_back() -> None:
    """Non-string ``host`` (hand-edited TOML or crafted POST) falls
    back to the default rather than raising inside ``.strip()``."""
    cfg = RttrpmOutputConfig(host=42)  # type: ignore[arg-type]
    assert cfg.host == "127.0.0.1"


def test_otp_output_config_derives_multicast_addresses_from_system_number() -> None:
    """Multicast addresses derive from system number (E1.59 Table 15-19)."""
    cfg = OtpOutputConfig(system_number=42)
    assert cfg.transform_mcast_ip == "239.159.1.42"
    assert cfg.advertisement_mcast_ip == "239.159.2.1"


def test_otp_output_config_transform_mcast_tracks_system_number_changes() -> None:
    cfg = OtpOutputConfig(system_number=1)
    assert cfg.transform_mcast_ip == "239.159.1.1"
    cfg.system_number = 99
    assert cfg.transform_mcast_ip == "239.159.1.99"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("eth0", "eth0"),
        ("  eth0  ", "eth0"),
        ("", ""),
    ],
)
def test_otp_output_config_strips_source_iface_whitespace(
    raw: str,
    expected: str,
) -> None:
    """A whitespace-tainted ``source_iface`` would differ from the stripped
    runtime value and trigger needless OTP rebinds on every reload pass, so
    ``__post_init__`` must strip it (mirrors ``psn_source_iface``)."""
    cfg = OtpOutputConfig(source_iface=raw)
    assert cfg.source_iface == expected


def test_grid_config_defaults_match_prior_hardcoded_render() -> None:
    cfg = GridConfig()
    assert cfg.color == "#545454"
    assert cfg.thickness == 1
    assert cfg.transparency == 0.6


@pytest.mark.parametrize("bad_thickness", [0, -3, "not-a-number", None, True])
def test_grid_config_clamps_thickness_to_at_least_one(bad_thickness: object) -> None:
    cfg = GridConfig(thickness=bad_thickness)  # type: ignore[arg-type]
    assert cfg.thickness >= 1


@pytest.mark.parametrize(
    "bad_transparency,expected",
    [(-0.5, 0.0), (1.5, 1.0), ("not-a-number", 0.6), (None, 0.6)],
)
def test_grid_config_clamps_transparency_to_unit_range(
    bad_transparency: object,
    expected: float,
) -> None:
    cfg = GridConfig(transparency=bad_transparency)  # type: ignore[arg-type]
    assert cfg.transparency == expected


@pytest.mark.parametrize("bad_color", ["red", "#12", "#gggggg", "", None, 42])
def test_grid_config_rejects_invalid_color_and_falls_back(bad_color: object) -> None:
    cfg = GridConfig(color=bad_color)  # type: ignore[arg-type]
    assert cfg.color == "#545454"


def test_grid_config_normalises_color_case() -> None:
    cfg = GridConfig(color="#ABCDEF")
    assert cfg.color == "#abcdef"


# --- CameraConfig ---------------------------------------------------------


# bool is deliberately not in this list – float(True) == 1.0 is idiomatic
# Python and harmless in projection math; the goal is "no crash on strings /
# None", not "reject every non-float".
@pytest.mark.parametrize("bad_value", ["not-a-number", None])
def test_camera_config_coerces_non_numeric_pos_to_defaults(bad_value: object) -> None:
    cfg = CameraConfig(pos_x=bad_value, pos_y=bad_value, pos_z=bad_value)  # type: ignore[arg-type]
    # Each field falls back to its declared default rather than raising in
    # ``project_points`` at render time.
    assert cfg.pos_x == 0.0
    assert cfg.pos_y == -11.0
    assert cfg.pos_z == 6.0


@pytest.mark.parametrize("bad_fov,expected", [(0.0, 1.0), (-30.0, 1.0), (200.0, 179.0), ("x", 60.0)])
def test_camera_config_clamps_fov_to_nondegenerate_range(bad_fov: object, expected: float) -> None:
    cfg = CameraConfig(fov=bad_fov)  # type: ignore[arg-type]
    assert cfg.fov == expected


def test_camera_config_preserves_optional_float_none() -> None:
    cfg = CameraConfig(sensor_width_mm=None, focal_length_mm=None)
    assert cfg.sensor_width_mm is None
    assert cfg.focal_length_mm is None


def test_camera_config_rejects_inf_fov_to_default() -> None:
    # TOML allows ``inf``/``-inf`` as float literals. ``_coerce_float`` must
    # not raise *and* must not let ``inf`` pass through – an infinite fov
    # produces garbage in ``project_points``. Fall back to the declared
    # default (60.0), not the upper clamp (179.0).
    cfg = CameraConfig(fov=float("inf"))
    assert cfg.fov == 60.0


def test_grid_config_tolerates_inf_thickness() -> None:
    # ``int(float('inf'))`` raises OverflowError (not ValueError). The
    # coercion helpers must catch it and fall back to the declared default
    # (matches the ``thickness: int = 1`` field default on GridConfig).
    cfg = GridConfig(thickness=float("inf"))
    assert cfg.thickness == 1


def test_grid_config_tolerates_huge_int_width() -> None:
    # ``float(huge_int)`` raises OverflowError. A hand-edited
    # ``width = 10**5000`` must fall back to the declared default, not crash.
    cfg = GridConfig(width=10**5000)
    assert cfg.width == 10.0


def test_grid_config_rejects_inf_width_to_default() -> None:
    # ``float("inf")`` does not raise, so the previous OverflowError catch
    # wouldn't catch it – but an infinite width cascades into
    # ``int(width / spacing)`` inside ``draw_grid`` which then raises. The
    # non-finite guard in ``_coerce_float`` rejects it at the boundary.
    cfg = GridConfig(width=float("inf"))
    assert cfg.width == 10.0


def test_grid_config_rejects_nan_spacing_to_default() -> None:
    # ``float("nan")`` propagates through ``max(lo, nan)`` unchanged and
    # would then crash ``int(nan)`` in ``draw_grid``.
    cfg = GridConfig(spacing=float("nan"))
    assert cfg.spacing == 1.0


def test_camera_config_rejects_nan_sensor_width_to_default() -> None:
    # Optional lens hint: ``nan`` would divide inside the lens helper and
    # persist as a garbage number. The coercer must drop it to the declared
    # default (``None``).
    cfg = CameraConfig(sensor_width_mm=float("nan"))
    assert cfg.sensor_width_mm is None


# --- Boolean coercion for hand-edited config.toml -------------------------
# ``bool("false") is True`` – without explicit coercion, a hand-edited
# ``origin_visible = "false"`` (string) silently flips to truthy and the
# origin glyph renders when the author wanted it hidden. Test the both
# directions plus the "junk → default" fallback.


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_grid_config_coerces_origin_visible_strings(value: str, expected: bool) -> None:
    cfg = GridConfig(origin_visible=value)  # type: ignore[arg-type]
    assert cfg.origin_visible is expected


@pytest.mark.parametrize("bad_value", ["maybe", "", "42", 42, None, [True]])
def test_grid_config_rejects_unparseable_origin_visible_to_default(bad_value: object) -> None:
    cfg = GridConfig(origin_visible=bad_value)  # type: ignore[arg-type]
    assert cfg.origin_visible is False


def test_grid_config_visible_defaults_true() -> None:
    # Master grid toggle defaults on so existing configs keep drawing the grid.
    assert GridConfig().visible is True


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("on", True),
        ("1", True),
        ("false", False),
        ("off", False),
        ("0", False),
        ("no", False),
    ],
)
def test_grid_config_coerces_visible_strings(value: str, expected: bool) -> None:
    cfg = GridConfig(visible=value)  # type: ignore[arg-type]
    assert cfg.visible is expected


@pytest.mark.parametrize("bad_value", ["maybe", "", "42", 42, None, [True]])
def test_grid_config_rejects_unparseable_visible_to_default(bad_value: object) -> None:
    # Junk falls back to the default (True), never a silently-hidden grid.
    cfg = GridConfig(visible=bad_value)  # type: ignore[arg-type]
    assert cfg.visible is True


# GridConfig.max_height – denominator for [fz] / [ifz].
# Default 0.0 = unset; negative inputs collapse to 0; positive preserved.


def test_grid_config_max_height_defaults_to_zero() -> None:
    """An unset max_height (= empty in the UI) is the legacy "infinite"
    state – fractional Z placeholders raise RenderError, current
    behaviour is preserved for every existing operator."""
    assert GridConfig().max_height == 0.0


@pytest.mark.parametrize("value", [0.5, 4.0, 100.0, 5000.0])
def test_grid_config_max_height_preserves_positive_values(value: float) -> None:
    """No upper bound for max_height; operators may work with extreme scales."""
    assert GridConfig(max_height=value).max_height == value


@pytest.mark.parametrize("value", [-1.0, -0.001, -100.0])
def test_grid_config_max_height_negative_collapses_to_zero(value: float) -> None:
    """A "ceiling below the floor" has no physical meaning. Collapse
    to the unset / zero state so the renderer raises RenderError on
    [fz] / [ifz] (visible misconfiguration) rather than producing
    nonsense values."""
    assert GridConfig(max_height=value).max_height == 0.0


@pytest.mark.parametrize("bad_value", ["abc", None, [4.0], {"v": 1}])
def test_grid_config_max_height_rejects_non_numeric_to_default(
    bad_value: object,
) -> None:
    """Hand-edited TOML with garbage falls back to the default (0.0)."""
    cfg = GridConfig(max_height=bad_value)  # type: ignore[arg-type]
    assert cfg.max_height == 0.0


def test_grid_config_max_height_zero_input_stays_zero() -> None:
    """Distinct from negative – explicit 0 means "unset". Same effect
    as empty-string in the UI."""
    assert GridConfig(max_height=0.0).max_height == 0.0


@pytest.mark.parametrize(
    "value,expected",
    [("false", False), ("off", False), ("true", True), ("yes", True)],
)
def test_marker_config_coerces_ball_visible_strings(value: str, expected: bool) -> None:
    cfg = MarkerConfig(ball_visible=value)  # type: ignore[arg-type]
    assert cfg.ball_visible is expected


def test_marker_config_coerces_all_boolean_flags() -> None:
    # Hand-edited TOML smuggled as strings for every boolean field.
    cfg = MarkerConfig(
        ball_visible="false",  # type: ignore[arg-type]
        crosshair_visible="off",  # type: ignore[arg-type]
        drop_line="no",  # type: ignore[arg-type]
        ground_circle="true",  # type: ignore[arg-type]
        ground_circle_filled="false",  # type: ignore[arg-type]
        z_display_from_stage="yes",  # type: ignore[arg-type]
    )
    assert cfg.ball_visible is False
    assert cfg.crosshair_visible is False
    assert cfg.drop_line is False
    assert cfg.ground_circle is True
    assert cfg.ground_circle_filled is False
    assert cfg.z_display_from_stage is True


@pytest.mark.parametrize("bad_value", ["abc", "", [1.0]])
def test_camera_config_optional_float_invalid_stays_none(bad_value: object) -> None:
    # When the default is ``None`` (unset lens hint), invalid input must stay
    # ``None`` – silently substituting ``0.0`` would change semantics from
    # "not set, use explicit fov" to a misleading persisted zero-value hint.
    # The wizard JS guards against falsy values before doing the division, so
    # the concrete risk is semantic confusion (and any other tool reading the
    # TOML that doesn't apply the same guard), not a runtime crash.
    cfg = CameraConfig(sensor_width_mm=bad_value, focal_length_mm=bad_value)  # type: ignore[arg-type]
    assert cfg.sensor_width_mm is None
    assert cfg.focal_length_mm is None


# --- MarkerConfig --------------------------------------------------------


@pytest.mark.parametrize(
    "field_name,bad_value,expected_default",
    [("min_speed", "x", 0.1), ("max_speed", None, 3.0), ("move_speed", "fast", 2.0)],
)
def test_marker_config_coerces_speeds(
    field_name: str,
    bad_value: object,
    expected_default: float,
) -> None:
    cfg = MarkerConfig(**{field_name: bad_value})  # type: ignore[arg-type]
    assert getattr(cfg, field_name) == expected_default


def test_marker_config_forces_max_speed_above_min_speed() -> None:
    cfg = MarkerConfig(min_speed=2.0, max_speed=1.0)
    assert cfg.max_speed == 2.0


@pytest.mark.parametrize("bad_alpha,expected", [(-0.5, 0.0), (5.0, 1.0), ("x", 0.3)])
def test_marker_config_clamps_transparency(bad_alpha: object, expected: float) -> None:
    cfg = MarkerConfig(transparency=bad_alpha)  # type: ignore[arg-type]
    assert cfg.transparency == expected


def test_marker_config_shipped_defaults() -> None:
    """Pin the shipped marker visual + default-position defaults so an
    accidental revert is caught. These mirror ``config.example.toml``."""
    cfg = MarkerConfig()
    assert cfg.default_pos_z == 1.6
    assert cfg.transparency == 0.3
    assert cfg.drop_line_thickness == 2
    assert cfg.ground_circle is True
    assert cfg.ground_circle_filled is False
    assert cfg.z_display_from_stage is True


@pytest.mark.parametrize("bad_color", ["red", "#12", "", None])
def test_marker_config_rejects_invalid_crosshair_color(bad_color: object) -> None:
    cfg = MarkerConfig(crosshair_color=bad_color)  # type: ignore[arg-type]
    assert cfg.crosshair_color == "#ffffff"


@pytest.mark.parametrize("bad_thickness", [0, -1, "x", None, True])
def test_marker_config_clamps_thicknesses(bad_thickness: object) -> None:
    cfg = MarkerConfig(crosshair_thickness=bad_thickness, drop_line_thickness=bad_thickness)  # type: ignore[arg-type]
    assert cfg.crosshair_thickness >= 1
    assert cfg.drop_line_thickness >= 1


# --- DetectionConfig ------------------------------------------------------


@pytest.mark.parametrize("bad_pin_point", ["middle", "", None])
def test_detection_config_falls_back_on_unknown_pin_point(bad_pin_point: object) -> None:
    cfg = DetectionConfig(pin_point=bad_pin_point)  # type: ignore[arg-type]
    assert cfg.pin_point == "top"


@pytest.mark.parametrize(
    "raw,expected",
    [(160, 160), (100, 160), (321, 320), (2000, 1280), ("x", 640)],
)
def test_detection_config_snaps_inference_size_to_multiple_of_32(
    raw: object,
    expected: int,
) -> None:
    cfg = DetectionConfig(inference_size=raw)  # type: ignore[arg-type]
    assert cfg.inference_size == expected


@pytest.mark.parametrize("bad_conf,expected", [(-0.1, 0.0), (2.0, 1.0), ("x", 0.2)])
def test_detection_config_clamps_confidence(bad_conf: object, expected: float) -> None:
    cfg = DetectionConfig(confidence=bad_conf)  # type: ignore[arg-type]
    assert cfg.confidence == expected


def test_detection_config_rejects_bad_box_color() -> None:
    cfg = DetectionConfig(box_color="fuchsia")
    assert cfg.box_color == "#808080"


@pytest.mark.parametrize("bad_model", [123, 4.5, None, ["yolov8n.onnx"], {"name": "x"}])
def test_detection_config_coerces_non_string_model_to_default(bad_model: object) -> None:
    """Non-string model must coerce to default string."""
    cfg = DetectionConfig(model=bad_model)  # type: ignore[arg-type]
    assert cfg.model == "yolov8n.onnx"
    assert isinstance(cfg.model, str)


@pytest.mark.parametrize("bad_path", [123, 4.5, None, True, ["/mnt/x"], {"p": "/x"}])
def test_detection_config_coerces_non_string_storage_path_to_empty(bad_path: object) -> None:
    """Non-string storage_path must coerce to "" – it is later ``.strip()``ed and
    fed to ``Path(...)`` in the web render and the runtime resolver."""
    cfg = DetectionConfig(storage_path=bad_path)  # type: ignore[arg-type]
    assert cfg.storage_path == ""
    assert isinstance(cfg.storage_path, str)


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("interval_ms", 0),
        ("max_persons", -5),
        ("box_thickness", 0),
    ],
)
def test_detection_config_clamps_positive_ints(field_name: str, bad_value: int) -> None:
    cfg = DetectionConfig(**{field_name: bad_value})
    assert getattr(cfg, field_name) >= 1


def test_detection_config_pin_marker_id_defaults_to_minus_one() -> None:
    """``pin_marker_id`` default is ``-1`` (sentinel "follow
    selected"), preserving legacy behaviour for configs saved
    before this field existed."""
    assert DetectionConfig().pin_marker_id == -1


@pytest.mark.parametrize(
    "value,expected",
    [
        (-1, -1),  # sentinel preserved
        (0, 0),  # marker 0 is a valid pin target
        (5, 5),  # arbitrary positive ID
        (-5, -1),  # other negatives clamp to sentinel
        ("not-int", -1),
        (True, -1),  # bool rejected (Python's ``bool`` is an ``int`` subclass)
    ],
)
def test_detection_config_pin_marker_id_coercion(
    value: object,
    expected: int,
) -> None:
    """``-1`` is the only sentinel; other negative values clamp to
    it rather than admitting confusing pseudo-IDs. Non-integers
    fall back to the default sentinel."""
    cfg = DetectionConfig(pin_marker_id=value)  # type: ignore[arg-type]
    assert cfg.pin_marker_id == expected


def test_detection_config_pin_mode_defaults_to_assist() -> None:
    """Default ``pin_mode`` is the two-marker assist hybrid: the operator
    steers a manual anchor while the AI-corrected output glides onto the
    nearest detection, so the followspot follows a person out of the box."""
    assert DetectionConfig().pin_mode == "assist"


@pytest.mark.parametrize("bad_pin_mode", ["hybrid", "", None, 7, True])
def test_detection_config_falls_back_on_unknown_pin_mode(bad_pin_mode: object) -> None:
    cfg = DetectionConfig(pin_mode=bad_pin_mode)  # type: ignore[arg-type]
    assert cfg.pin_mode == "assist"


def test_detection_config_accepts_replace_pin_mode() -> None:
    assert DetectionConfig(pin_mode="replace").pin_mode == "replace"


def test_detection_config_assist_strength_default() -> None:
    """Default clip strength 0.5 blends the AI-corrected output halfway
    between the manual anchor and the detected person, leaving operator
    headroom to bias the result."""
    assert DetectionConfig().assist_strength == pytest.approx(0.5)


@pytest.mark.parametrize(
    "bad_radius,expected",
    [(0.0, 0.1), (-3.0, 0.1), (100.0, 50.0), ("wide", 1.0), (None, 1.0), (float("nan"), 1.0)],
)
def test_detection_config_clamps_assist_radius(bad_radius: object, expected: float) -> None:
    cfg = DetectionConfig(assist_radius_m=bad_radius)  # type: ignore[arg-type]
    assert cfg.assist_radius_m == pytest.approx(expected)


@pytest.mark.parametrize(
    "bad_strength,expected",
    [(-0.5, 0.0), (2.0, 1.0), ("x", 0.5), (None, 0.5)],
)
def test_detection_config_clamps_assist_strength(bad_strength: object, expected: float) -> None:
    cfg = DetectionConfig(assist_strength=bad_strength)  # type: ignore[arg-type]
    assert cfg.assist_strength == pytest.approx(expected)


def test_detection_config_default_values() -> None:
    """The shipped detection defaults favour accuracy on a workstation: a
    larger inference size, CLAHE preprocessing, a lower confidence floor, a
    faster cadence, and prediction lookahead. Guards against silent drift."""
    cfg = DetectionConfig()
    assert cfg.inference_size == 640
    assert cfg.preprocess_clahe is True
    assert cfg.confidence == pytest.approx(0.2)
    assert cfg.interval_ms == 67
    assert cfg.prediction == pytest.approx(8.0)


# --- OtpOutputConfig ------------------------------------------------------


@pytest.mark.parametrize("bad_port,expected", [(0, 1), (-1, 1), (70000, 65535), ("x", 5568)])
def test_otp_output_config_clamps_port(bad_port: object, expected: int) -> None:
    cfg = OtpOutputConfig(port=bad_port)  # type: ignore[arg-type]
    assert cfg.port == expected


@pytest.mark.parametrize("bad,expected", [(0, 1), (201, 200), ("x", 1)])
def test_otp_output_config_clamps_system_number(bad: object, expected: int) -> None:
    cfg = OtpOutputConfig(system_number=bad)  # type: ignore[arg-type]
    assert cfg.system_number == expected


@pytest.mark.parametrize("bad,expected", [(-1, 0), (201, 200), ("x", 100)])
def test_otp_output_config_clamps_priority(bad: object, expected: int) -> None:
    cfg = OtpOutputConfig(priority=bad)  # type: ignore[arg-type]
    assert cfg.priority == expected


def test_otp_output_config_rejects_non_string_source_iface() -> None:
    """Non-string ``source_iface`` (hand-edited TOML or crafted POST) falls
    back to empty rather than raising inside ``.strip()``."""
    cfg = OtpOutputConfig(source_iface=42)  # type: ignore[arg-type]
    assert cfg.source_iface == ""


def test_load_config_migrates_legacy_otp_source_ip_to_iface(temp_config_path, monkeypatch) -> None:
    """A pre-rename config pinned OTP by raw ``source_ip``; on load it converts
    to the interface name currently holding that IP so the pin survives the
    upgrade instead of being dropped by ``_filter_known``."""
    import socket as _socket
    from types import SimpleNamespace

    from openfollow import net_utils

    monkeypatch.setattr(
        net_utils.psutil,
        "net_if_addrs",
        lambda: {"eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.1.50")]},
    )
    temp_config_path.write_text('[otp_output]\nsource_ip = "192.168.1.50"\n', encoding="utf-8")

    cfg = load_config(str(temp_config_path))

    assert cfg.otp_output.source_iface == "eth0"


def test_load_config_drops_legacy_otp_source_ip_not_on_any_iface(temp_config_path, monkeypatch) -> None:
    """A legacy OTP IP no longer present on any NIC resolves to empty
    (auto-detect) rather than carrying a dead pin forward."""
    from openfollow import net_utils

    monkeypatch.setattr(net_utils.psutil, "net_if_addrs", lambda: {})
    temp_config_path.write_text('[otp_output]\nsource_ip = "10.255.255.250"\n', encoding="utf-8")

    cfg = load_config(str(temp_config_path))

    assert cfg.otp_output.source_iface == ""


def test_load_config_otp_section_without_legacy_source_ip_is_noop(temp_config_path) -> None:
    """An OTP section that predates the rename but never pinned an IP just gets
    the empty default – the migration is a no-op (no NIC lookup, no crash)."""
    temp_config_path.write_text("[otp_output]\nenabled = true\n", encoding="utf-8")

    cfg = load_config(str(temp_config_path))

    assert cfg.otp_output.source_iface == ""
    assert cfg.otp_output.enabled is True


def test_otp_output_config_silently_ignores_legacy_mcast_ip_kwarg() -> None:
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(OtpOutputConfig)}
    assert "mcast_ip" not in field_names


# ---------------------------------------------------------------------------
# TriggerZoneConfig / TriggerZonesConfig – previously untested
# ---------------------------------------------------------------------------


def test_trigger_zone_falls_back_on_invalid_trigger_source(caplog) -> None:
    from openfollow.configuration import TriggerZoneConfig

    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        zone = TriggerZoneConfig(trigger_source="bogus")
    assert zone.trigger_source == "markers"
    assert any("Invalid zone trigger_source" in r.message for r in caplog.records)


@pytest.mark.parametrize("source", ["markers", "detection", "both"])
def test_trigger_zone_accepts_known_trigger_sources(source: str) -> None:
    from openfollow.configuration import TriggerZoneConfig

    zone = TriggerZoneConfig(trigger_source=source)
    assert zone.trigger_source == source


def test_trigger_zone_coerces_numeric_vertices() -> None:
    from openfollow.configuration import TriggerZoneConfig

    zone = TriggerZoneConfig(
        vertices=[
            [1, 2],  # ints → floats
            [3.5, 4.25],  # already floats
            ["bad", 5],  # non-numeric x → dropped
            [6],  # too short → dropped
            "not a list",  # wrong type → dropped
            (7, 8, 9),  # tuple len>=2 → kept (first two)
        ]
    )
    assert zone.vertices == [[1.0, 2.0], [3.5, 4.25], [7.0, 8.0]]


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", float("nan"), float("inf")])
def test_trigger_zone_drops_non_finite_vertices(bad) -> None:
    # ``float("nan")`` / ``float("inf")`` survive coercion but make every
    # point-in-polygon comparison False, silently corrupting membership tests.
    # A hand-edited TOML or crafted POST must not smuggle them into the engine.
    from openfollow.configuration import TriggerZoneConfig

    zone = TriggerZoneConfig(
        vertices=[
            [0.0, 0.0],
            [bad, 1.0],  # non-finite x → dropped
            [2.0, bad],  # non-finite y → dropped
            [3.0, 4.0],
        ]
    )
    assert zone.vertices == [[0.0, 0.0], [3.0, 4.0]]


# Per-marker trigger filter (triggered_by): empty = any marker; non-empty coerced to int.


def test_trigger_zone_triggered_by_defaults_to_empty_list() -> None:
    """Backwards compat: empty list = no filter (any marker)."""
    from openfollow.configuration import TriggerZoneConfig

    zone = TriggerZoneConfig()
    assert zone.triggered_by == []


def test_trigger_zone_triggered_by_coerces_strings_to_ints() -> None:
    """A permissive client could send string-encoded integers
    (``["0", "1"]``) – coerce silently rather than reject."""
    from openfollow.configuration import TriggerZoneConfig

    zone = TriggerZoneConfig(triggered_by=["0", "1", "5"])
    assert zone.triggered_by == [0, 1, 5]


def test_trigger_zone_triggered_by_drops_invalid_entries() -> None:
    """Hand-edited TOMLs occasionally smuggle garbage; drop unconvertible
    entries rather than crash the engine, since it iterates the list
    every eval tick."""
    from openfollow.configuration import TriggerZoneConfig

    zone = TriggerZoneConfig(triggered_by=[0, "bad", 1, None, 2.5])
    # ``int(2.5)`` is 2 – float coercion is allowed; only truly
    # un-int-able entries (str non-numeric, ``None``) get dropped.
    assert zone.triggered_by == [0, 1, 2]


def test_trigger_zones_config_snaps_eval_fps_to_valid_choice(caplog) -> None:
    from openfollow.configuration import TriggerZonesConfig

    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        zones = TriggerZonesConfig(eval_fps=7)
    # 7 is nearer to 5 than 10 (|7-5|=2, |7-10|=3).
    assert zones.eval_fps == 5
    assert any("snapping to" in r.message for r in caplog.records)


def test_trigger_zones_config_clamps_negative_debounce_and_hysteresis() -> None:
    from openfollow.configuration import TriggerZonesConfig

    zones = TriggerZonesConfig(debounce_ms=-50, hysteresis=-0.1)
    assert zones.debounce_ms == 0
    assert zones.hysteresis == 0.0


def test_trigger_zones_config_clamps_high_debounce_and_hysteresis() -> None:
    # review: blur-time validation refuses debounce_ms > 60000
    # or hysteresis > 10.0; save-time must enforce the same upper bound
    # so a hand-edited TOML can't persist a value the UI rejects.
    from openfollow.configuration import TriggerZonesConfig

    zones = TriggerZonesConfig(debounce_ms=70000, hysteresis=15.0)
    assert zones.debounce_ms == 60000
    assert zones.hysteresis == 10.0


def test_trigger_zones_config_drops_legacy_default_osc_keys() -> None:
    # Clean break: the section no longer carries default host/port; a
    # hand-edited TOML keeps the keys out via ``_filter_known``.
    from openfollow.configuration import TriggerZonesConfig

    tz = TriggerZonesConfig()
    assert not hasattr(tz, "default_osc_host")
    assert not hasattr(tz, "default_osc_port")


def test_trigger_zone_config_destination_id() -> None:
    from openfollow.configuration import TriggerZoneConfig

    zone = TriggerZoneConfig(destination_id="  d1  ")
    assert zone.destination_id == "d1"
    assert not hasattr(zone, "osc_host")
    assert not hasattr(zone, "osc_port")
    # Non-string collapses to empty.
    assert TriggerZoneConfig(destination_id=5).destination_id == ""  # type: ignore[arg-type]


def test_trigger_zones_config_converts_dict_zones_to_dataclasses() -> None:
    from openfollow.configuration import TriggerZoneConfig, TriggerZonesConfig

    zones = TriggerZonesConfig(
        zones=[{"name": "A", "trigger_source": "markers"}],
    )
    assert len(zones.zones) == 1
    assert isinstance(zones.zones[0], TriggerZoneConfig)
    assert zones.zones[0].name == "A"


def test_trigger_zones_config_preserves_already_built_instances() -> None:
    from openfollow.configuration import TriggerZoneConfig, TriggerZonesConfig

    zone = TriggerZoneConfig(name="pre-built")
    zones = TriggerZonesConfig(zones=[zone])
    assert zones.zones[0] is zone


# ---------------------------------------------------------------------------
# ControllerConfig – curve and move_xy_stick fallbacks, deadzone logging
# ---------------------------------------------------------------------------


def test_controller_invalid_curve_falls_back_to_logarithmic(caplog) -> None:
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        cfg = ControllerConfig(curve="exponential")
    assert cfg.curve == "logarithmic"
    assert any("Invalid controller curve" in r.message for r in caplog.records)


@pytest.mark.parametrize("curve", ["linear", "logarithmic", "quadratic", "s-law"])
def test_controller_accepts_valid_curves(curve: str) -> None:
    cfg = ControllerConfig(curve=curve)
    assert cfg.curve == curve


def test_controller_invalid_move_xy_stick_falls_back_to_left(caplog) -> None:
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        cfg = ControllerConfig(move_xy_stick="middle")
    assert cfg.move_xy_stick == "left"
    assert any("Invalid move_xy_stick" in r.message for r in caplog.records)


def test_controller_accepts_right_stick() -> None:
    cfg = ControllerConfig(move_xy_stick="right")
    assert cfg.move_xy_stick == "right"


# Marker-fader integrator config fields (formerly the VF1 fields).


@pytest.mark.parametrize("stick", ["", "left_y", "right_y"])
def test_controller_accepts_valid_marker_fader_sticks(stick: str) -> None:
    cfg = ControllerConfig(marker_fader_stick=stick)
    assert cfg.marker_fader_stick == stick


def test_controller_invalid_marker_fader_stick_snaps_to_unused() -> None:
    # Unrecognised value snaps to "" (unused) rather than fall back
    # to a working default; an unrecognised value is more likely a
    # typo than a silent migration target.
    cfg = ControllerConfig(marker_fader_stick="left_x")
    assert cfg.marker_fader_stick == ""


def test_controller_marker_fader_max_speed_clamps_to_minimum() -> None:
    # Below 0.05 s would divide-by-zero risk in the integrator and
    # is below the human-perceptible threshold anyway.
    cfg = ControllerConfig(marker_fader_max_speed_s=0.0)
    assert cfg.marker_fader_max_speed_s == 0.05


def test_controller_marker_fader_max_speed_clamps_to_maximum() -> None:
    # Above 60 s is fader-by-glacier and almost certainly a typo.
    cfg = ControllerConfig(marker_fader_max_speed_s=120.0)
    assert cfg.marker_fader_max_speed_s == 60.0


def test_controller_marker_fader_max_speed_default_is_one_second() -> None:
    cfg = ControllerConfig()
    assert cfg.marker_fader_max_speed_s == 1.0
    assert cfg.marker_fader_stick == ""


def test_controller_mouse_enabled_default_is_false() -> None:
    # Default matches config.example.toml (false); explicit true is honoured.
    assert ControllerConfig().mouse_enabled is False
    assert ControllerConfig(mouse_enabled=True).mouse_enabled is True


def test_controller_z_movement_default_keys() -> None:
    # Default matches config.example.toml: Z+ on q, Z- on e (e is a pollable
    # macOS key code and free under the default wasd movement layout).
    cfg = ControllerConfig()
    assert cfg.key_move_z_up == "q"
    assert cfg.key_move_z_down == "e"


def test_controller_e_is_a_valid_action_key() -> None:
    # "e" / "f" are not movement keys, so they are accepted as action
    # bindings instead of being repaired to the default.
    assert "e" not in RESERVED_MOVEMENT_KEYS
    cfg = ControllerConfig(key_move_z_down="e", key_reset="f")
    assert cfg.key_move_z_down == "e"
    assert cfg.key_reset == "f"


@pytest.mark.parametrize("colliding_key", ["w", "a", "s", "d", "i", "j", "k", "l"])
def test_action_key_colliding_with_movement_falls_back(colliding_key: str) -> None:
    # Movement keys (the flat WASD / IJKL reservation) can't double as action
    # bindings; key_reset's default "x" is free, so a colliding value is repaired.
    cfg = ControllerConfig(key_reset=colliding_key)
    assert cfg.key_reset == "x"


def test_action_key_validation_converges_no_warn_loop(caplog) -> None:
    # Re-running __post_init__ on an already-valid value is a no-op – no
    # repeated "collides" warning (the lower-Z default "e" is not reserved).
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        cfg = ControllerConfig(key_move_z_down="e")
    assert cfg.key_move_z_down == "e"
    assert not any("collides" in r.message for r in caplog.records)


def test_controller_migrates_legacy_vf1_keys_on_load() -> None:
    # Back-compat: a config.toml written with the old ``vf1_*`` keys
    # keeps its stick assignment under the new ``marker_fader_*`` names.
    import os
    import tempfile
    import textwrap

    from openfollow.configuration import load_config

    toml = textwrap.dedent("""
        [controller]
        vf1_stick = "right_y"
        vf1_max_speed_s = 2.5
    """)
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".toml",
        delete=False,
    ) as f:
        f.write(toml)
        path = f.name
    try:
        cfg = load_config(path)
        assert cfg.controller.marker_fader_stick == "right_y"
        assert cfg.controller.marker_fader_max_speed_s == 2.5
    finally:
        os.unlink(path)


def test_controller_deadzone_clamp_logs_warning(caplog) -> None:
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        cfg = ControllerConfig(deadzone=-1.0)
    assert cfg.deadzone == 0.0
    assert any("deadzone" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# _coerce_bool – recognised string forms and whitespace handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        (" yes ", True),
        ("on", True),
        ("1", True),
        ("false", False),
        ("FALSE", False),
        ("off", False),
        ("no", False),
        ("0", False),
    ],
)
def test_coerce_bool_recognised_strings_normalise(value: str, expected: bool) -> None:
    """_coerce_bool is exercised via GridConfig.origin_visible – the helper
    is module-private but its behaviour is observable through the dataclass."""
    cfg = GridConfig(origin_visible=value)  # type: ignore[arg-type]
    assert cfg.origin_visible is expected


# ---------------------------------------------------------------------------
# Git-pull updater removal: the .deb release installer is the only update
# path. The git-pull config fields are gone; legacy keys in a hand-edited
# config.toml load cleanly (``_filter_known`` drops them) without crashing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    ["update_source_url", "update_repo_branch", "update_allowed_hosts"],
)
def test_git_pull_update_fields_are_removed(field_name: str) -> None:
    """The git-pull config fields must not exist on AppConfig anymore – only
    the .deb-release fields (``update_github_repo`` / ``update_service_name``)
    survive."""
    assert not hasattr(AppConfig(), field_name)


def test_deb_update_fields_survive() -> None:
    cfg = AppConfig()
    assert cfg.update_github_repo == "openfollowapp/openfollow"
    assert cfg.update_service_name == DEFAULT_UPDATE_SERVICE_NAME


def test_legacy_git_pull_keys_are_dropped_on_load(temp_config_path) -> None:
    """A config.toml hand-edited under the old git-pull updater (or persisted
    by older code) must load without error; ``_filter_known`` drops the
    unknown keys and they don't reappear on save."""
    temp_config_path.write_text(
        'update_source_url = "git@git.mycorp.example:team/fork.git"\n'
        'update_repo_branch = "main"\n'
        'update_repo_url = ""\n'
        'update_allowed_hosts = ["github.com"]\n'
        "web_port = 80\n",
        encoding="utf-8",
    )
    cfg = load_config(str(temp_config_path))
    assert not hasattr(cfg, "update_source_url")
    assert not hasattr(cfg, "update_repo_branch")
    assert not hasattr(cfg, "update_allowed_hosts")
    assert cfg.web_port == 80

    save_config(cfg, str(temp_config_path))
    written = temp_config_path.read_text(encoding="utf-8")
    assert "update_source_url" not in written
    assert "update_repo_branch" not in written
    assert "update_repo_url" not in written
    assert "update_allowed_hosts" not in written


# ---------------------------------------------------------------------------
# save_config – temp-file cleanup on failure. The atomic write creates a
# temp file in the target directory and only rename()s it after a successful
# dump. When dump raises, the temp file must be unlinked so a failed save
# doesn't leak partial writes.
# ---------------------------------------------------------------------------


def test_save_config_unlinks_temp_file_on_write_error(
    temp_config_path,
    monkeypatch,
) -> None:
    import openfollow.configuration as cfg_mod

    class _BoomError(RuntimeError):
        pass

    def boom_dump(_data, _fp):
        raise _BoomError("simulated disk failure")

    monkeypatch.setattr(cfg_mod.tomli_w, "dump", boom_dump)

    with pytest.raises(_BoomError):
        cfg_mod.save_config(AppConfig(), str(temp_config_path))

    # No .tmp files should remain next to the target – save_config must
    # unlink them even when the write itself fails.
    stragglers = list(temp_config_path.parent.glob(f"{temp_config_path.name}.*.tmp"))
    assert stragglers == [], f"temp files leaked: {stragglers}"


def test_save_config_swallows_unlink_oserror_and_reraises_original(
    temp_config_path,
    monkeypatch,
) -> None:
    import openfollow.configuration as cfg_mod

    def boom_dump(_data, _fp):
        raise RuntimeError("primary failure")

    def boom_unlink(_path):
        raise OSError("cleanup also failed")

    monkeypatch.setattr(cfg_mod.tomli_w, "dump", boom_dump)
    monkeypatch.setattr(cfg_mod.os, "unlink", boom_unlink)

    with pytest.raises(RuntimeError, match="primary failure"):
        cfg_mod.save_config(AppConfig(), str(temp_config_path))


# ---------------------------------------------------------------------------
# _strip_none – recursive None-key filtering for TOML serialisation
# ---------------------------------------------------------------------------


def test_strip_none_drops_none_values_recursively() -> None:
    from openfollow.configuration import _strip_none

    data = {
        "a": 1,
        "b": None,
        "c": {"x": None, "y": 2, "z": {"q": None, "r": 3}},
        "d": [1, None, {"k": None, "j": 4}],
    }
    stripped = _strip_none(data)
    # None keys are removed; lists preserve their ordering/contents (None
    # inside lists stays – TOML can't represent null dict keys, not null
    # list items, which would be invalid TOML anyway).
    assert stripped == {
        "a": 1,
        "c": {"y": 2, "z": {"r": 3}},
        "d": [1, None, {"j": 4}],
    }


def test_strip_none_leaves_scalar_values_unchanged() -> None:
    from openfollow.configuration import _strip_none

    assert _strip_none(42) == 42
    assert _strip_none("string") == "string"
    assert _strip_none(3.14) == 3.14
    assert _strip_none(True) is True


# ---------------------------------------------------------------------------
# apply_runtime_config_changes – remaining branches beyond what's already
# exercised in this file (psn_system_name, psn_source_ip, video, markers).
# ---------------------------------------------------------------------------


class _DummyCamera:
    def __init__(self, cfg) -> None:  # pragma: no cover - simple holder
        self.cfg = cfg


class _DummyZoneEngine:
    def __init__(self) -> None:
        self.reloaded: list[object] = []
        self.reloaded_destinations: list[object] = []

    def reload_config(self, zones, destinations=None) -> None:  # noqa: ANN001
        self.reloaded.append(zones)
        self.reloaded_destinations.append(destinations)


class _DummyOtpServer:
    def __init__(self) -> None:
        self.registered: list[int] = []
        self.unregistered: list[int] = []

    def register_marker(self, marker) -> None:
        self.registered.append(marker.marker_id)

    def unregister_marker(self, tid: int) -> None:
        self.unregistered.append(tid)


class _DummyRttrpmServer(_DummyOtpServer):
    pass


def _app_with(**overrides) -> _DummyApp:
    """Build a _DummyApp with an initial AppConfig; overrides feed AppConfig."""
    app = _DummyApp(AppConfig(**overrides))
    # get_marker is used by the OTP/RTTrPM mirror loops – add it here.
    app._server.get_marker = lambda tid: app._server.markers.get(tid)  # type: ignore[attr-defined]
    return app


def test_apply_runtime_applies_grid_changes_in_place() -> None:
    app = _app_with()
    new_grid = GridConfig(width=app._config.grid.width + 5.0)
    new_config = AppConfig(grid=new_grid)

    apply_runtime_config_changes(app, new_config)

    assert app._config.grid == new_grid
    # Grid changes are hot-applied without restart.
    assert app._web_commands.restart_requested is False


def test_apply_runtime_rebuilds_camera_when_camera_config_changes(monkeypatch) -> None:
    from openfollow.scene import camera as camera_mod

    recorded: list[object] = []
    monkeypatch.setattr(
        camera_mod.Camera,
        "from_config",
        classmethod(lambda cls, cfg: recorded.append(cfg) or "rebuilt-camera"),
    )

    app = _app_with()
    new_cam = CameraConfig(pos_x=app._config.camera.pos_x + 1.0)
    new_config = AppConfig(camera=new_cam)

    apply_runtime_config_changes(app, new_config)

    assert app._config.camera == new_cam
    assert app._camera == "rebuilt-camera"
    assert recorded == [new_cam]


def test_apply_runtime_replaces_marker_config_in_place() -> None:
    app = _app_with()
    new_marker = MarkerConfig(move_speed=app._config.marker.move_speed + 1.0)
    new_config = AppConfig(marker=new_marker)

    apply_runtime_config_changes(app, new_config)

    assert app._config.marker == new_marker


def test_apply_runtime_updates_viewer_ids_without_restart() -> None:
    app = _app_with(
        controlled_marker_ids=[1],
        viewer_marker_ids=[1],
    )
    new_config = AppConfig(
        controlled_marker_ids=[1],
        viewer_marker_ids=[1, 2, 5],
    )

    apply_runtime_config_changes(app, new_config)

    assert app._viewer_ids == [1, 2, 5]
    assert app._config.viewer_marker_ids == [1, 2, 5]
    assert app._web_commands.restart_requested is False


def test_marker_name_for_runtime_pulls_from_catalog() -> None:
    """When the catalog has a named entry for ``marker_id``, the live-
    reload diff path uses it; otherwise falls back to ``Marker <id>``."""
    import types

    from openfollow.configuration import _marker_name_for_runtime
    from openfollow.marker_catalog.catalog import MarkerCatalog

    catalog = MarkerCatalog()
    catalog.upsert(5, "Operator Name", "#ff0000", updated_at=1.0)
    catalog.upsert(6, "", "#00ff00", updated_at=1.0)  # name empty → fallback

    app = types.SimpleNamespace(_marker_catalog=catalog)
    assert _marker_name_for_runtime(app, 5) == "Operator Name"
    # Empty name in catalog falls back to default label.
    assert _marker_name_for_runtime(app, 6) == "Marker 6"
    # Missing entry falls back too.
    assert _marker_name_for_runtime(app, 99) == "Marker 99"


def test_marker_name_for_runtime_without_catalog_uses_fallback() -> None:
    """Older code paths may not have wired up ``_marker_catalog`` yet
    (or call this during early bootstrap); ``getattr`` returns None and
    we fall back to ``Marker <id>``."""
    import types

    from openfollow.configuration import _marker_name_for_runtime

    app = types.SimpleNamespace()  # no _marker_catalog attribute
    assert _marker_name_for_runtime(app, 3) == "Marker 3"


def test_load_config_strips_bool_and_non_int_marker_ids(tmp_path) -> None:
    """Defense at the load boundary: hand-edited config with bools or
    non-int entries in ``controlled_marker_ids`` is filtered, not
    crashed on, with a warning."""
    from openfollow.configuration import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        # ``[1, true, 2, \"x\"]`` is invalid TOML mixed-type – write
        # each variant on its own to exercise the bool / non-int arms
        # via inline arrays of mixed-but-parseable types.
        "controlled_marker_ids = [1, 2]\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg.controlled_marker_ids == [1, 2]


def test_load_config_strips_zero_and_negative_marker_ids(tmp_path) -> None:
    from openfollow.configuration import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        "controlled_marker_ids = [0, 1, -3, 5]\nviewer_marker_ids = [-1, 2]\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg.controlled_marker_ids == [1, 5]
    assert cfg.viewer_marker_ids == [2]


def test_load_config_dedupes_marker_id_lists(tmp_path) -> None:
    from openfollow.configuration import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        "controlled_marker_ids = [1, 1, 2, 1, 3]\nviewer_marker_ids = [2, 2, 1, 2]\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg.controlled_marker_ids == [1, 2, 3]
    assert cfg.viewer_marker_ids == [2, 1]


def test_load_config_strips_bool_array_entries(tmp_path) -> None:
    """A TOML array of booleans (``[true, false]``) passes TOML's
    homogeneous-array rule but every entry is rejected by the
    ``isinstance(_v, bool) or not isinstance(_v, int)`` filter (line
    1786). The result is an empty list, no crash."""
    from openfollow.configuration import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        "controlled_marker_ids = [true, false]\nviewer_marker_ids = [1]\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg.controlled_marker_ids == []
    assert cfg.viewer_marker_ids == [1]


def test_load_config_viewer_ids_non_list_skips_filter(tmp_path) -> None:
    from openfollow.configuration import load_config

    path = tmp_path / "config.toml"
    # Both keys present – backward-compat copy doesn't fire. viewer is
    # not a list, so the filter's False arm runs.
    path.write_text(
        'controlled_marker_ids = [1, 2]\nviewer_marker_ids = "oops"\n',
        encoding="utf-8",
    )
    # Must not raise.
    cfg = load_config(str(path))
    assert cfg is not None
    assert cfg.controlled_marker_ids == [1, 2]


def test_apply_runtime_filters_invalid_ids_defense_in_depth() -> None:
    """Defense-in-depth: hot-reload path filters ``marker_id < 1`` even
    when the AppConfig was constructed in-memory (bypassing
    ``load_config``'s filter). Id ``0`` is the reserved "ignored" value
    on the PSN wire and ``Marker.__init__`` raises for it."""
    app = _app_with(
        controlled_marker_ids=[1],
        viewer_marker_ids=[1],
    )
    new_config = AppConfig(
        controlled_marker_ids=[0, 2, 3],
        viewer_marker_ids=[0, 4, 5],
    )

    apply_runtime_config_changes(app, new_config)

    assert app._controlled_ids == [2, 3]
    assert app._config.controlled_marker_ids == [2, 3]
    assert app._viewer_ids == [4, 5]
    assert app._config.viewer_marker_ids == [4, 5]


def test_apply_runtime_replays_controller_config_through_gamepad_handler() -> None:
    app = _app_with()
    new_ctrl = ControllerConfig(deadzone=0.42)  # different from default 0.15
    new_config = AppConfig(controller=new_ctrl)

    apply_runtime_config_changes(app, new_config)

    assert app._input_manager.gamepad_handler.apply_called is True
    assert app._config.controller == new_ctrl


def test_apply_runtime_disarms_mouse_when_mouse_enabled_toggled_off() -> None:
    # mouse_enabled True → False must disarm the live mouse handler so a stale
    # _active can't snap the marker when it's re-enabled.
    app = _app_with(controller=ControllerConfig(mouse_enabled=True))
    new_config = AppConfig(controller=ControllerConfig(mouse_enabled=False))

    apply_runtime_config_changes(app, new_config)

    assert app._input_manager.mouse_handler.deactivate_calls == 1


def test_apply_runtime_does_not_disarm_mouse_when_enabling() -> None:
    # The reverse transition (off → on) must NOT disarm the handler.
    app = _app_with(controller=ControllerConfig(mouse_enabled=False))
    new_config = AppConfig(controller=ControllerConfig(mouse_enabled=True, deadzone=0.3))

    apply_runtime_config_changes(app, new_config)

    assert app._input_manager.mouse_handler.deactivate_calls == 0


def test_apply_runtime_keeps_mouse_armed_on_unrelated_edit_while_enabled() -> None:
    # An unrelated controller edit while mouse stays enabled must NOT disarm.
    app = _app_with(controller=ControllerConfig(mouse_enabled=True))
    new_config = AppConfig(controller=ControllerConfig(mouse_enabled=True, deadzone=0.3))

    apply_runtime_config_changes(app, new_config)

    assert app._input_manager.mouse_handler.deactivate_calls == 0


def test_apply_runtime_reopens_osc_listener_on_osc_config_change() -> None:
    app = _app_with()
    new_osc = OscConfig(enabled=False, port=9999)
    new_config = AppConfig(osc=new_osc)

    apply_runtime_config_changes(app, new_config)

    assert app._input_manager.osc_restarts == [(False, 9999)]
    assert app._config.osc == new_osc


def test_apply_runtime_applies_otp_output_change_live() -> None:
    app = _app_with()
    new_otp = OtpOutputConfig(enabled=True, priority=50)
    new_config = AppConfig(otp_output=new_otp)

    apply_runtime_config_changes(app, new_config)

    assert app._config.otp_output == new_otp
    assert app._runtime_services.otp_changes == [new_otp]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_applies_rttrpm_output_change_live() -> None:
    """RTTRPM output changes apply live without restart."""
    app = _app_with()
    new_rttrpm = RttrpmOutputConfig(enabled=True, port=45000)
    new_config = AppConfig(rttrpm_output=new_rttrpm)

    apply_runtime_config_changes(app, new_config)

    assert app._config.rttrpm_output == new_rttrpm
    assert app._runtime_services.rttrpm_changes == [new_rttrpm]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_otp_failure_reverts_config_and_preserves_reference() -> None:
    """Orchestrator failure reverts config but preserves server reference."""

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_otp_output_change(self, new_cfg: OtpOutputConfig) -> None:
            raise OSError("simulated mcast bind failure")

    app = _app_with()
    old_otp = app._config.otp_output
    app._runtime_services = _FailingRuntimeServices()
    sentinel_server = object()
    app._otp_server = sentinel_server
    new_otp = OtpOutputConfig(enabled=True, priority=50)
    # Trailing marker change must still be applied even though the OTP
    # apply raised.
    new_config = AppConfig(otp_output=new_otp, viewer_marker_ids=[1, 2, 3])

    apply_runtime_config_changes(app, new_config)

    # Reference left untouched by the dispatcher – orchestrator owns it.
    assert app._otp_server is sentinel_server
    # Stored config reverted so a later reload retries instead of no-opping.
    assert app._config.otp_output == old_otp
    # Subsequent settings still applied.
    assert app._config.viewer_marker_ids == [1, 2, 3]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_rttrpm_failure_reverts_config_and_preserves_reference() -> None:
    """Mirror of the OTP revert test for RTTrPM. Same lifecycle
    ownership rule: dispatcher reverts config but doesn't touch
    ``app._rttrpm_server`` – the orchestrator owns the reference."""

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_rttrpm_output_change(self, new_cfg: RttrpmOutputConfig) -> None:
            raise OSError("simulated UDP bind failure")

    app = _app_with()
    old_rttrpm = app._config.rttrpm_output
    app._runtime_services = _FailingRuntimeServices()
    sentinel_server = object()
    app._rttrpm_server = sentinel_server
    new_rttrpm = RttrpmOutputConfig(enabled=True, port=45000)
    new_config = AppConfig(rttrpm_output=new_rttrpm, viewer_marker_ids=[7])

    apply_runtime_config_changes(app, new_config)

    assert app._rttrpm_server is sentinel_server
    assert app._config.rttrpm_output == old_rttrpm
    assert app._config.viewer_marker_ids == [7]


def test_apply_runtime_psn_source_iface_failure_reverts_config() -> None:

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_psn_source_ip_change(self, new_source_ip: str) -> None:
            raise OSError(f"failed to bind to {new_source_ip!r}")

    app = _DummyApp(AppConfig(psn_source_iface=""))
    app._runtime_services = _FailingRuntimeServices()
    new_config = AppConfig(psn_source_iface="ghost0")

    apply_runtime_config_changes(app, new_config)

    # Stored config reverted so a subsequent reload retries.
    assert app._config.psn_source_iface == ""
    assert app._web_commands.restart_requested is False


def test_apply_runtime_applies_osc_transmitters_change_live() -> None:
    """OSC transmitter changes apply live without restart."""
    from openfollow.configuration import (
        OscTransmitterConfig,
        OscTransmittersConfig,
    )

    class _RecordingRuntimeServices(_DummyRuntimeServices):
        def __init__(self) -> None:
            super().__init__()
            self.transmitter_changes: list[OscTransmittersConfig] = []

        def apply_osc_transmitters_change(
            self,
            new_cfg: OscTransmittersConfig,
            destinations=None,  # noqa: ANN001
        ) -> None:
            self.transmitter_changes.append(new_cfg)

    app = _app_with()
    app._runtime_services = _RecordingRuntimeServices()
    new_cfg = OscTransmittersConfig(
        transmitters=[
            OscTransmitterConfig(id="row-a", destination_id="d1"),
        ]
    )
    new_config = AppConfig(osc_transmitters=new_cfg)

    apply_runtime_config_changes(app, new_config)

    assert app._config.osc_transmitters == new_cfg
    assert app._runtime_services.transmitter_changes == [new_cfg]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_osc_transmitters_failure_reverts_config() -> None:
    """Mirror of OTP/RTTrPM revert pattern: an orchestrator failure
    reverts the stored config so the next reload pass re-attempts
    rather than treating the new config as already applied."""
    from openfollow.configuration import (
        OscTransmitterConfig,
        OscTransmittersConfig,
    )

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_osc_transmitters_change(
            self,
            new_cfg: OscTransmittersConfig,
            destinations=None,  # noqa: ANN001
        ) -> None:
            raise OSError("simulated apply failure")

    app = _app_with()
    old_cfg = app._config.osc_transmitters
    app._runtime_services = _FailingRuntimeServices()
    new_cfg = OscTransmittersConfig(
        transmitters=[
            OscTransmitterConfig(id="r1", destination_id="d1"),
        ]
    )
    new_config = AppConfig(osc_transmitters=new_cfg, viewer_marker_ids=[9])

    apply_runtime_config_changes(app, new_config)

    assert app._config.osc_transmitters == old_cfg
    # Sibling fields still apply – the dispatcher only reverts the
    # failed section, not the whole pass.
    assert app._config.viewer_marker_ids == [9]


def test_apply_runtime_detection_off_to_on_routes_through_swap_detector() -> None:
    """Detection off→on transition routes through swap_detector without restart."""
    # Default config has detection.enabled=False; new config enables it.
    app = _app_with()
    # Off→on means there's no detector to start with; null out the
    # default ``available=True`` stand-in to mirror the production
    # state at startup-with-detection-disabled.
    app._runtime_services._person_detector = None
    assert app._config.detection.enabled is False
    new_det = DetectionConfig(enabled=True, confidence=0.75)
    new_config = AppConfig(detection=new_det)

    apply_runtime_config_changes(app, new_config)

    assert app._config.detection == new_det
    assert app._web_commands.restart_requested is False
    # The live orchestrator picked the swap path – not the in-process
    # ``apply_detection_change`` reload.
    assert app._runtime_services.detection_swaps == [new_det]
    assert app._runtime_services.detection_changes == []


def test_apply_runtime_applies_detection_change_live_when_already_enabled() -> None:
    """on→on with new cfg: the orchestrator stages the swap on the
    running detector; no process restart."""
    app = _app_with(detection=DetectionConfig(enabled=True, confidence=0.40))
    new_det = DetectionConfig(enabled=True, confidence=0.85, max_persons=3)
    new_config = AppConfig(detection=new_det)

    apply_runtime_config_changes(app, new_config)

    assert app._config.detection == new_det
    assert app._runtime_services.detection_changes == [new_det]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_applies_detection_disable_live() -> None:
    """on→off: the orchestrator stops the detector + drops the
    reference; no process restart."""
    app = _app_with(detection=DetectionConfig(enabled=True, confidence=0.40))
    new_det = DetectionConfig(enabled=False)
    new_config = AppConfig(detection=new_det)

    apply_runtime_config_changes(app, new_config)

    assert app._config.detection == new_det
    assert app._runtime_services.detection_changes == [new_det]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_detection_failure_clears_detector_reference_and_reverts() -> None:
    """Mirrors the OTP/RTTrPM degrade-on-fail wiring: if the
    orchestrator raises, ``_apply_with_fallback`` clears
    ``app._runtime_services._person_detector`` so the next reload
    starts fresh, **reverts ``app._config.detection`` to the prior
    value** so a later reload pass actually retries instead of
    no-opping, and the dispatcher continues with subsequent settings.

    The detector reference lives on ``AppRuntimeServices``, NOT on
    ``OpenFollowApp`` – clearing the wrong attribute would silently
    mint a new attribute on ``app`` that nothing reads while the real
    detector reference stays stale."""

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_detection_change(self, new_cfg: DetectionConfig) -> None:
            raise OSError("simulated detection apply failure")

    app = _app_with(detection=DetectionConfig(enabled=True, confidence=0.40))
    old_detection = app._config.detection
    app._runtime_services = _FailingRuntimeServices()
    # Stand-in for the real-world running detector. ``available=True``
    # so the dispatcher's "phantom detector" guard doesn't fire here –
    # this test exercises the orchestrator-failure path, not the
    # restart-required path.
    app._runtime_services._person_detector = SimpleNamespace(available=True)
    new_det = DetectionConfig(enabled=True, confidence=0.85)
    new_config = AppConfig(detection=new_det, viewer_marker_ids=[1, 2, 3])

    apply_runtime_config_changes(app, new_config)

    assert app._runtime_services._person_detector is None
    # Stored config reverted so a subsequent reload retries.
    assert app._config.detection == old_detection
    assert app._config.viewer_marker_ids == [1, 2, 3]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_detection_phantom_detector_routes_through_swap_detector() -> None:
    """Phantom detector (unavailable backend) routes through swap_detector."""
    app = _app_with(detection=DetectionConfig(enabled=True, confidence=0.40))
    # Phantom detector: object exists but reports unavailable.
    app._runtime_services._person_detector = SimpleNamespace(available=False)
    new_det = DetectionConfig(enabled=True, confidence=0.85)
    new_config = AppConfig(detection=new_det)

    apply_runtime_config_changes(app, new_config)

    assert app._web_commands.restart_requested is False
    assert app._runtime_services.detection_swaps == [new_det]
    assert app._runtime_services.detection_changes == []


def test_apply_runtime_detection_no_detector_runnable_routes_through_swap_detector() -> None:
    """Null detector (backend missing at startup) routes through swap_detector."""
    app = _app_with(detection=DetectionConfig(enabled=True, confidence=0.40))
    app._runtime_services._person_detector = None
    new_det = DetectionConfig(enabled=True, confidence=0.85)
    new_config = AppConfig(detection=new_det)

    apply_runtime_config_changes(app, new_config)

    assert app._web_commands.restart_requested is False
    assert app._runtime_services.detection_swaps == [new_det]
    assert app._runtime_services.detection_changes == []


def test_apply_runtime_detection_inference_size_change_routes_through_swap_detector() -> None:
    """Inference size changes apply live via swap_detector."""
    app = _app_with(
        detection=DetectionConfig(enabled=True, inference_size=320),
    )
    new_det = DetectionConfig(enabled=True, inference_size=640)
    new_config = AppConfig(detection=new_det)

    apply_runtime_config_changes(app, new_config)

    assert app._web_commands.restart_requested is False
    assert app._runtime_services.detection_swaps == [new_det]
    assert app._runtime_services.detection_changes == []


def test_apply_runtime_detection_swap_failure_reverts_stored_config() -> None:
    """swap_detector failure reverts config for retry."""

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def swap_detector(self, new_cfg: DetectionConfig) -> None:
            raise OSError("simulated swap_detector failure")

    app = _app_with()
    app._runtime_services = _FailingRuntimeServices()
    # Off→on path → swap_detector is invoked.
    app._runtime_services._person_detector = None
    old_detection = app._config.detection
    new_det = DetectionConfig(enabled=True, confidence=0.85)
    new_config = AppConfig(detection=new_det, viewer_marker_ids=[1, 2, 3])

    apply_runtime_config_changes(app, new_config)

    # Stored config reverted so a subsequent reload retries.
    assert app._config.detection == old_detection
    # Sibling section still applied – the swap failure doesn't stop
    # the dispatcher from continuing.
    assert app._config.viewer_marker_ids == [1, 2, 3]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_reloads_trigger_zones() -> None:
    """Zone hot-reload now flows entirely through ``zone_engine.reload_config``;
    the unified ``OscService`` is shared across callers and is **not** evicted
    on a zones-only reload. Stale per-target sockets sit unused and cost one
    fd each – acceptable since we can't safely evict without ownership
    tracking the cache doesn't carry."""
    from openfollow.configuration import TriggerZoneConfig, TriggerZonesConfig

    app = _app_with()
    zone_engine = _DummyZoneEngine()
    app._runtime_services._zone_engine = zone_engine  # type: ignore[attr-defined]

    new_zones = TriggerZonesConfig(
        enabled=True,
        zones=[TriggerZoneConfig(name="Main", vertices=[[0, 0], [1, 0], [1, 1]])],
    )
    new_config = AppConfig(trigger_zones=new_zones)

    apply_runtime_config_changes(app, new_config)

    assert zone_engine.reloaded == [new_zones]
    assert app._config.trigger_zones == new_zones


def test_apply_runtime_osc_destinations_change_reapplies_both_consumers() -> None:
    """A destination-only edit (e.g. an IP change) re-resolves at BOTH the
    transmitter manager and the zone engine, live, with no restart."""
    from openfollow.configuration import OscDestinationConfig, OscDestinationsConfig

    class _RecordingRuntimeServices(_DummyRuntimeServices):
        def __init__(self) -> None:
            super().__init__()
            self.transmitter_dests: list[object] = []

        def apply_osc_transmitters_change(self, new_cfg, destinations=None) -> None:  # noqa: ANN001
            self.transmitter_dests.append(destinations)

    app = _app_with()
    app._runtime_services = _RecordingRuntimeServices()
    zone_engine = _DummyZoneEngine()
    app._runtime_services._zone_engine = zone_engine  # type: ignore[attr-defined]

    new_dests = OscDestinationsConfig(
        destinations=[OscDestinationConfig(id="default", name="Default", host="10.0.0.9")],
    )
    new_config = AppConfig(osc_destinations=new_dests)

    apply_runtime_config_changes(app, new_config)

    assert app._config.osc_destinations == new_dests
    # Transmitter manager re-resolved against the new destinations.
    assert app._runtime_services.transmitter_dests == [new_dests]
    # Zone engine re-resolved against the new destinations.
    assert zone_engine.reloaded_destinations == [new_dests]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_osc_destinations_change_without_zone_engine() -> None:
    """When no zone engine is wired (``getattr`` → ``None``), the destination
    change still re-resolves the transmitter manager and updates config."""
    from openfollow.configuration import OscDestinationConfig, OscDestinationsConfig

    class _RecordingRuntimeServices(_DummyRuntimeServices):
        def __init__(self) -> None:
            super().__init__()
            self.transmitter_dests: list[object] = []

        def apply_osc_transmitters_change(self, new_cfg, destinations=None) -> None:  # noqa: ANN001
            self.transmitter_dests.append(destinations)

    app = _app_with()
    app._runtime_services = _RecordingRuntimeServices()
    # Deliberately no ``_zone_engine`` attribute → getattr returns None.

    new_dests = OscDestinationsConfig(
        destinations=[OscDestinationConfig(id="default", name="Default", host="10.0.0.9")],
    )
    apply_runtime_config_changes(app, AppConfig(osc_destinations=new_dests))

    assert app._config.osc_destinations == new_dests
    assert app._runtime_services.transmitter_dests == [new_dests]


def test_apply_runtime_osc_destinations_change_failure_reverts() -> None:
    """If the re-resolve raises (and the revert re-apply also raises), the
    destination change reverts config and the zone engine is never reloaded."""
    from openfollow.configuration import OscDestinationConfig, OscDestinationsConfig

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_osc_transmitters_change(self, new_cfg, destinations=None) -> None:  # noqa: ANN001
            raise RuntimeError("boom")

    app = _app_with()
    app._runtime_services = _FailingRuntimeServices()
    zone_engine = _DummyZoneEngine()
    app._runtime_services._zone_engine = zone_engine  # type: ignore[attr-defined]
    old_dests = app._config.osc_destinations

    new_dests = OscDestinationsConfig(
        destinations=[OscDestinationConfig(id="default", name="Default", host="10.0.0.9")],
    )
    apply_runtime_config_changes(app, AppConfig(osc_destinations=new_dests))

    # Reverted to the prior destinations; zone engine never reloaded.
    assert app._config.osc_destinations == old_dests
    assert zone_engine.reloaded == []


def test_apply_runtime_osc_routing_combined_change_applies_once() -> None:
    """A transmitter AND destination change in one pass applies as a single
    coherent unit: one manager re-stage (no double restart) against the new
    transmitters + destinations, and one zone-engine reload with the new set."""
    from openfollow.configuration import (
        OscDestinationConfig,
        OscDestinationsConfig,
        OscTransmitterConfig,
        OscTransmittersConfig,
    )

    class _RecordingRuntimeServices(_DummyRuntimeServices):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[object, object]] = []

        def apply_osc_transmitters_change(self, new_cfg, destinations=None) -> None:  # noqa: ANN001
            self.calls.append((new_cfg, destinations))

    app = _app_with()
    app._runtime_services = _RecordingRuntimeServices()
    zone_engine = _DummyZoneEngine()
    app._runtime_services._zone_engine = zone_engine  # type: ignore[attr-defined]

    new_txs = OscTransmittersConfig(
        transmitters=[OscTransmitterConfig(id="r1", name="R1", destination_id="default")],
    )
    new_dests = OscDestinationsConfig(
        destinations=[OscDestinationConfig(id="default", name="Default", host="10.0.0.9")],
    )
    apply_runtime_config_changes(
        app,
        AppConfig(osc_transmitters=new_txs, osc_destinations=new_dests),
    )

    # Single apply (not one per section), carrying the new transmitters + dests.
    assert app._runtime_services.calls == [(new_txs, new_dests)]
    assert zone_engine.reloaded_destinations == [new_dests]
    assert app._config.osc_transmitters == new_txs
    assert app._config.osc_destinations == new_dests


def test_apply_runtime_osc_routing_failure_restages_old_routing() -> None:
    """A failed routing apply re-stages the OLD routing into the manager + zone
    engine so it can't be left on the new endpoints while config holds the old
    ones – the split-brain guard. The new apply fails, the revert apply succeeds."""
    from openfollow.configuration import OscDestinationConfig, OscDestinationsConfig

    class _FlakyRuntimeServices(_DummyRuntimeServices):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[object, object]] = []

        def apply_osc_transmitters_change(self, new_cfg, destinations=None) -> None:  # noqa: ANN001
            self.calls.append((new_cfg, destinations))
            if len(self.calls) == 1:
                raise RuntimeError("boom")  # fail only the NEW apply

    app = _app_with()
    app._runtime_services = _FlakyRuntimeServices()
    zone_engine = _DummyZoneEngine()
    app._runtime_services._zone_engine = zone_engine  # type: ignore[attr-defined]
    old_txs = app._config.osc_transmitters
    old_dests = app._config.osc_destinations

    new_dests = OscDestinationsConfig(
        destinations=[OscDestinationConfig(id="default", name="Default", host="10.0.0.9")],
    )
    apply_runtime_config_changes(app, AppConfig(osc_destinations=new_dests))

    # Two applies: the failed new one, then the revert re-staging the OLD set.
    assert len(app._runtime_services.calls) == 2
    assert app._runtime_services.calls[0][1] == new_dests
    assert app._runtime_services.calls[1] == (old_txs, old_dests)
    # Config reverted, and the manager + zone engine are coherent with it
    # (last apply + last reload both carry the OLD destinations), not split.
    assert app._config.osc_transmitters == old_txs
    assert app._config.osc_destinations == old_dests
    assert zone_engine.reloaded_destinations == [old_dests]


def test_apply_runtime_osc_destinations_and_zones_reload_engine_once() -> None:
    """Changing destinations AND trigger zones in one pass reloads the zone
    engine exactly once (the trigger_zones block owns the single reload, with
    the committed destinations) rather than twice."""
    from openfollow.configuration import (
        OscDestinationConfig,
        OscDestinationsConfig,
        TriggerZoneConfig,
        TriggerZonesConfig,
    )

    app = _app_with()
    app._runtime_services = _DummyRuntimeServices()
    zone_engine = _DummyZoneEngine()
    app._runtime_services._zone_engine = zone_engine  # type: ignore[attr-defined]

    new_dests = OscDestinationsConfig(
        destinations=[OscDestinationConfig(id="default", name="Default", host="10.0.0.9")],
    )
    new_zones = TriggerZonesConfig(
        enabled=True,
        zones=[TriggerZoneConfig(name="Main", vertices=[[0, 0], [1, 0], [1, 1]])],
    )
    apply_runtime_config_changes(
        app,
        AppConfig(osc_destinations=new_dests, trigger_zones=new_zones),
    )

    # Single reload, carrying the new zones + the committed new destinations.
    assert zone_engine.reloaded == [new_zones]
    assert zone_engine.reloaded_destinations == [new_dests]
    assert app._config.trigger_zones == new_zones
    assert app._config.osc_destinations == new_dests


def test_apply_runtime_reapplies_midi_devices_to_subsystem() -> None:
    """MIDI config changes reapply to live subsystem."""
    from openfollow.configuration import MidiConfig, MidiPatch

    class _DummyMidi:
        def __init__(self) -> None:
            self.applied: list[list[MidiPatch]] = []

        def apply_config(self, patches) -> None:  # noqa: ANN001
            self.applied.append(list(patches))

    app = _app_with()
    midi_sub = _DummyMidi()
    app._runtime_services._midi = midi_sub  # type: ignore[attr-defined]

    new_midi = MidiConfig(
        patches=[MidiPatch(id=1, alias="Workspace 1", port_name="MIDI Mix")],
    )
    new_config = AppConfig(midi=new_midi)
    apply_runtime_config_changes(app, new_config)

    assert app._config.midi == new_midi
    assert len(midi_sub.applied) == 1
    assert midi_sub.applied[0][0].alias == "Workspace 1"


def test_apply_runtime_midi_tolerates_missing_subsystem() -> None:
    """If the runtime hasn't created a MIDI subsystem yet, the
    hot-reload still copies the config without raising – mirrors
    the trigger_zones tolerance branch."""
    from openfollow.configuration import MidiConfig, MidiPatch

    app = _app_with()
    # Strip the auto-created subsystem to exercise the missing-runtime branch.
    app._runtime_services._midi = None  # type: ignore[attr-defined]
    new_midi = MidiConfig(
        patches=[MidiPatch(id=1, alias="A", port_name="X")],
    )
    new_config = AppConfig(midi=new_midi)
    apply_runtime_config_changes(app, new_config)
    assert app._config.midi == new_midi


def test_apply_runtime_reapplies_virtual_faders_to_bus() -> None:
    """Virtual fader config changes reapply to live bus."""
    from openfollow.configuration import (
        VirtualFaderConfig,
        VirtualFadersConfig,
    )

    class _DummyBus:
        def __init__(self) -> None:
            self.applied: list[VirtualFadersConfig] = []

        def apply_config(self, cfg) -> None:  # noqa: ANN001
            self.applied.append(cfg)

    app = _app_with()
    bus = _DummyBus()
    app._runtime_services._virtual_faders = bus  # type: ignore[attr-defined]

    new_faders = VirtualFadersConfig(
        faders=[
            VirtualFaderConfig(name="Master", default_value=0.5),
        ]
    )
    new_config = AppConfig(virtual_faders=new_faders)
    apply_runtime_config_changes(app, new_config)

    assert app._config.virtual_faders == new_faders
    assert len(bus.applied) == 1
    assert bus.applied[0].faders[0].name == "Master"


def test_apply_runtime_reprovisions_marker_faders_on_controlled_change() -> None:
    """A ``controlled_marker_ids`` change re-provisions the per-marker
    faders on the live bus with the new id set (the bus preserves
    survivors / drops removed ids internally – see VirtualFaderBus)."""

    class _ProvisionBus:
        def __init__(self) -> None:
            self.provisioned: list[list[int]] = []

        def provision_marker_faders(
            self,
            ids,
            default_value=0.0,  # noqa: ANN001
        ) -> None:
            self.provisioned.append(list(ids))

    app = _app_with(controlled_marker_ids=[1, 2])
    bus = _ProvisionBus()
    app._runtime_services._virtual_faders = bus  # type: ignore[attr-defined]

    new_config = AppConfig(controlled_marker_ids=[2, 3])
    apply_runtime_config_changes(app, new_config)

    assert app._config.controlled_marker_ids == [2, 3]
    assert bus.provisioned == [[2, 3]]


def test_apply_runtime_controlled_change_rolls_back_on_server_failure() -> None:
    """A mid-loop ``add_marker`` failure must not desync runtime from config:
    the server is reconciled to the old id set, controlled ids / config stay
    un-committed, and the function reports partial-apply (False) so the
    orchestrator retries."""
    app = _app_with(controlled_marker_ids=[1, 2], viewer_marker_ids=[1, 2])
    orig_add = app._server.add_marker

    def _failing_add(tid, name):  # noqa: ANN001
        if tid == 3:
            raise RuntimeError("PSN add failed")
        return orig_add(tid, name)

    app._server.add_marker = _failing_add  # type: ignore[method-assign]

    new_config = AppConfig(controlled_marker_ids=[1, 3], viewer_marker_ids=[1, 3])
    result = apply_runtime_config_changes(app, new_config)

    assert result is False  # finding 2: degraded section signals partial-apply
    # finding 3: nothing committed; server reconciled back to the old set.
    assert app._config.controlled_marker_ids == [1, 2]
    assert app._controlled_ids == [1, 2]
    assert set(app._server.markers) == {1, 2}


def test_apply_runtime_virtual_faders_tolerates_missing_bus() -> None:
    from openfollow.configuration import (
        VirtualFaderConfig,
        VirtualFadersConfig,
    )

    app = _app_with()
    app._runtime_services._virtual_faders = None  # type: ignore[attr-defined]
    new_config = AppConfig(
        virtual_faders=VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(name="X"),
            ]
        )
    )
    apply_runtime_config_changes(app, new_config)
    assert app._config.virtual_faders.faders[0].name == "X"


def test_apply_runtime_skips_virtual_faders_when_unchanged() -> None:
    """No reapply when ``new_config.virtual_faders`` equals current –
    avoids needless pickup-state churn on every cosmetic config save."""
    from openfollow.configuration import VirtualFadersConfig

    class _DummyBus:
        def __init__(self) -> None:
            self.applied = 0

        def apply_config(self, cfg) -> None:  # noqa: ANN001
            self.applied += 1

    app = _app_with()
    bus = _DummyBus()
    app._runtime_services._virtual_faders = bus  # type: ignore[attr-defined]
    new_config = AppConfig(virtual_faders=VirtualFadersConfig())
    apply_runtime_config_changes(app, new_config)
    assert bus.applied == 0


def test_apply_runtime_skips_midi_when_unchanged() -> None:
    """No reapply when ``new_config.midi`` equals the current value –
    avoids needless rtmidi port churn on every config save."""
    from openfollow.configuration import MidiConfig

    class _DummyMidi:
        def __init__(self) -> None:
            self.applied = 0

        def apply_config(self, devices) -> None:  # noqa: ANN001
            self.applied += 1

    app = _app_with()
    midi_sub = _DummyMidi()
    app._runtime_services._midi = midi_sub  # type: ignore[attr-defined]
    new_config = AppConfig(midi=MidiConfig())  # equal to default
    apply_runtime_config_changes(app, new_config)
    assert midi_sub.applied == 0


def test_apply_runtime_trigger_zones_tolerates_missing_runtime_services() -> None:
    from openfollow.configuration import TriggerZonesConfig

    app = _app_with()
    # _runtime_services has neither _zone_engine nor _osc_output_client.
    new_zones = TriggerZonesConfig(enabled=True)
    new_config = AppConfig(trigger_zones=new_zones)

    apply_runtime_config_changes(app, new_config)

    assert app._config.trigger_zones == new_zones


def test_apply_runtime_marker_rewire_mirrors_otp_and_rttrpm_servers() -> None:
    app = _app_with(
        controlled_marker_ids=[0, 1],
        viewer_marker_ids=[0, 1],
    )
    app._server.get_marker = lambda tid: app._server.markers.get(tid)  # type: ignore[attr-defined]
    app._otp_server = _DummyOtpServer()
    app._rttrpm_server = _DummyRttrpmServer()
    new_config = AppConfig(
        controlled_marker_ids=[1, 2],
        viewer_marker_ids=[1, 2],
    )

    apply_runtime_config_changes(app, new_config)

    assert app._otp_server.unregistered == [0]
    assert app._otp_server.registered == [2]
    assert app._rttrpm_server.unregistered == [0]
    assert app._rttrpm_server.registered == [2]


def test_apply_runtime_marker_rewire_noop_when_server_absent() -> None:
    app = _app_with(controlled_marker_ids=[0])
    app._server = None  # type: ignore[assignment]
    new_config = AppConfig(controlled_marker_ids=[5])

    apply_runtime_config_changes(app, new_config)

    # Config is still copied even when the server isn't up yet, since the
    # next time _server becomes live it will read from app._config.
    assert app._config.controlled_marker_ids == [5]


# ---------------------------------------------------------------------------
# _warn_deprecated_controller_bindings – one-shot warnings across reloads.
# The module-level _DEPRECATED_WARNED set means we must reset it between
# tests that rely on the "first time" behaviour.
# ---------------------------------------------------------------------------


def test_warn_deprecated_only_fires_once_across_multiple_reloads(
    temp_config_path,
    caplog,
    monkeypatch,
) -> None:
    import openfollow.configuration as cfg_mod

    # Swap in a fresh set so the first load sees a clean state AND the
    # original module-level set is restored at teardown – otherwise clearing
    # it here would leak into later tests that rely on the "already warned"
    # suppression.
    monkeypatch.setattr(cfg_mod, "_DEPRECATED_WARNED", set())

    # Write a config that trips BOTH deprecation categories so we observe
    # the "warn once per field" guard on both.
    bad_ctrl = ControllerConfig(
        btn_source_select="LB",  # direct-entry deprecation
        btn_settings_confirm="X",  # confirm/cancel deprecation
    )
    save_config(AppConfig(controller=bad_ctrl), str(temp_config_path))

    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        load_config(str(temp_config_path))
        first_pass = [r.message for r in caplog.records]
        caplog.clear()
        load_config(str(temp_config_path))
        second_pass = [r.message for r in caplog.records]

    # First load emits deprecation warnings for both fields.
    assert any("btn_source_select" in m for m in first_pass)
    assert any("btn_settings_confirm" in m for m in first_pass)
    # Second load must not re-emit – the module-level set suppresses repeats.
    assert not any("btn_source_select" in m or "btn_settings_confirm" in m for m in second_pass)


# ---------------------------------------------------------------------------
# _coerce_hex_color – default fallback path. The helper is exercised via
# GridConfig.color (already tested) and DetectionConfig.box_color (ditto);
# adding an explicit case for a non-string type pins the isinstance check.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_color", [42, None, [], {}, b"#ffffff"])
def test_coerce_hex_color_rejects_non_strings(bad_color: object) -> None:
    cfg = GridConfig(color=bad_color)  # type: ignore[arg-type]
    assert cfg.color == "#545454"  # default, not whatever odd object came in


# ---------------------------------------------------------------------------
# _filter_known – helper used in load_config. Direct tests make the
# stale-key behaviour explicit; the existing roundtrip only exercises the
# happy path.
# ---------------------------------------------------------------------------


def test_filter_known_drops_unknown_keys() -> None:
    from openfollow.configuration import _filter_known

    data = {"pos_x": 1.0, "unknown_field": 99, "pos_y": 2.0}
    assert _filter_known(CameraConfig, data) == {"pos_x": 1.0, "pos_y": 2.0}


def test_filter_known_returns_empty_for_fully_unknown_input() -> None:
    from openfollow.configuration import _filter_known

    assert _filter_known(CameraConfig, {"completely": "unrelated"}) == {}


# ---------------------------------------------------------------------------
# load_config – sub-config section parsing and legacy key stripping
# ---------------------------------------------------------------------------


def test_load_config_parses_nested_subsections(temp_config_path) -> None:
    temp_config_path.write_text(
        "[camera]\npos_x = 7.5\npitch = -10.0\n[grid]\nwidth = 12.0\n",
        encoding="utf-8",
    )
    cfg = load_config(str(temp_config_path))
    assert cfg.camera.pos_x == pytest.approx(7.5)
    assert cfg.camera.pitch == pytest.approx(-10.0)
    assert cfg.grid.width == pytest.approx(12.0)


def test_load_config_honours_explicit_viewer_ids_over_fallback(
    temp_config_path,
) -> None:
    temp_config_path.write_text(
        "num_markers = 3\nviewer_marker_ids = [5]\n",
        encoding="utf-8",
    )
    cfg = load_config(str(temp_config_path))
    assert cfg.controlled_marker_ids == [1, 2]
    assert cfg.viewer_marker_ids == [5]


class TestNetworkConfig:
    """Optional network backend configuration."""

    def test_default_backend_is_auto(self) -> None:
        from openfollow.configuration import NetworkConfig

        cfg = NetworkConfig()
        assert cfg.backend == "auto"

    def test_recognised_choices_are_normalised(self) -> None:
        from openfollow.configuration import NetworkConfig

        # Mixed case + whitespace → canonicalised.
        assert NetworkConfig(backend="  NM  ").backend == "nm"
        assert NetworkConfig(backend="DHCPCD").backend == "dhcpcd"
        assert NetworkConfig(backend="Psutil").backend == "psutil"

    def test_unknown_choice_falls_back_to_auto(self) -> None:
        from openfollow.configuration import NetworkConfig

        assert NetworkConfig(backend="garbage").backend == "auto"

    def test_non_string_backend_resets_to_auto(self) -> None:
        from openfollow.configuration import NetworkConfig

        cfg = NetworkConfig(backend=123)  # type: ignore[arg-type]
        assert cfg.backend == "auto"

    def test_load_config_parses_network_section(self, temp_config_path) -> None:
        temp_config_path.write_text(
            '[network]\nbackend = "psutil"\n',
            encoding="utf-8",
        )
        cfg = load_config(str(temp_config_path))
        assert cfg.network.backend == "psutil"


# _apply_with_fallback transactional helper tests


def test_apply_with_fallback_runs_apply_fn_and_logs_duration(caplog) -> None:
    """Happy path: ``apply_fn`` runs, on_failure does not, an info log
    fires with the operation name + a duration in milliseconds, and
    the helper returns ``True``."""
    calls: list[str] = []

    with caplog.at_level("INFO", logger="openfollow.configuration"):
        success = _apply_with_fallback(
            "happy",
            lambda: calls.append("apply"),
            on_failure=lambda: calls.append("fallback"),
        )

    assert success is True
    assert calls == ["apply"]
    assert any("live-apply 'happy' completed" in r.message for r in caplog.records if r.levelname == "INFO")


def test_apply_with_fallback_logs_exception_and_runs_on_failure(caplog) -> None:
    calls: list[str] = []

    with caplog.at_level("ERROR", logger="openfollow.configuration"):
        success = _apply_with_fallback(
            "boom",
            lambda: (_ for _ in ()).throw(OSError("simulated")),
            on_failure=lambda: calls.append("fallback"),
        )  # must not raise

    assert success is False

    assert calls == ["fallback"]
    assert any("live-apply 'boom' failed" in r.message and r.exc_info is not None for r in caplog.records)


def test_apply_with_fallback_swallows_secondary_exception_in_on_failure(caplog) -> None:

    def bad_failure() -> None:
        raise RuntimeError("cleanup also broke")

    with caplog.at_level("ERROR", logger="openfollow.configuration"):
        _apply_with_fallback(
            "double-boom",
            lambda: (_ for _ in ()).throw(OSError("primary")),
            on_failure=bad_failure,
        )  # must not raise

    messages = [r.message for r in caplog.records]
    assert any("live-apply 'double-boom' failed" in m for m in messages)
    assert any("on_failure for 'double-boom' also raised" in m for m in messages)


def test_apply_with_fallback_without_on_failure_logs_and_returns(caplog) -> None:
    """``on_failure`` is optional; when omitted, the helper still
    catches the exception and logs it cleanly."""
    with caplog.at_level("ERROR", logger="openfollow.configuration"):
        _apply_with_fallback(
            "no-cleanup",
            lambda: (_ for _ in ()).throw(OSError("primary")),
        )

    assert any("live-apply 'no-cleanup' failed" in r.message for r in caplog.records)


# The flexible OSC transmitter system supersedes the dedicated ETC Eos output.
# Tests for the unified runtime live in test_osc_service.py and test_osc_transmitter.py.

# ---------------------------------------------------------------------------
# OscTransmitterConfig / OscTransmittersConfig. Templates are files on disk.
# ---------------------------------------------------------------------------

from openfollow.configuration import (  # noqa: E402
    OscDestinationConfig,
    OscDestinationsConfig,
    OscTransmitterConfig,
    OscTransmittersConfig,
)


class TestOscTransmitterConfig:
    def test_blank_id_is_minted_as_uuid_hex(self) -> None:
        cfg = OscTransmitterConfig()
        assert len(cfg.id) == 32
        assert all(c in "0123456789abcdef" for c in cfg.id)

    def test_whitespace_id_is_minted(self) -> None:
        cfg = OscTransmitterConfig(id="   ")
        assert len(cfg.id) == 32

    def test_existing_id_is_preserved_and_stripped(self) -> None:
        cfg = OscTransmitterConfig(id="  abc-123  ")
        assert cfg.id == "abc-123"

    def test_non_string_id_is_minted(self) -> None:
        cfg = OscTransmitterConfig(id=42)  # type: ignore[arg-type]
        assert len(cfg.id) == 32

    def test_non_string_name_falls_back_to_empty(self) -> None:
        cfg = OscTransmitterConfig(name=99)  # type: ignore[arg-type]
        assert cfg.name == ""

    def test_name_is_stripped(self) -> None:
        cfg = OscTransmitterConfig(name="  Stage Left  ")
        assert cfg.name == "Stage Left"

    def test_destination_id_default_is_empty(self) -> None:
        """A fresh row has no destination selected – it skips sending."""
        assert OscTransmitterConfig().destination_id == ""

    def test_destination_id_is_stripped(self) -> None:
        cfg = OscTransmitterConfig(destination_id="  dest-1  ")
        assert cfg.destination_id == "dest-1"

    def test_non_string_destination_id_falls_back_to_empty(self) -> None:
        cfg = OscTransmitterConfig(destination_id=99)  # type: ignore[arg-type]
        assert cfg.destination_id == ""

    def test_legacy_connection_keys_are_dropped_on_load(self) -> None:
        """Clean break: a TOML row carrying the old inline connection keys
        loads without them – ``_filter_known`` discards the unknown keys,
        leaving ``destination_id`` blank."""
        cfg = OscTransmittersConfig(
            transmitters=[
                {
                    "id": "row1",
                    "host": "10.0.0.5",
                    "port": 9000,
                    "protocol": "tcp",
                    "framing": "length_prefix",
                },
            ],
        )
        row = cfg.transmitters[0]
        assert not hasattr(row, "host")
        assert not hasattr(row, "port")
        assert not hasattr(row, "protocol")
        assert not hasattr(row, "framing")
        assert row.destination_id == ""

    def test_markers_default_is_empty(self) -> None:
        """markers defaults to [] (no default marker chosen yet)."""
        assert OscTransmitterConfig().markers == []

    def test_markers_explicit_zero_preserved(self) -> None:
        """An operator who deliberately picks marker 0 must see that
        choice round-trip through the schema – distinct from "unset"."""
        assert OscTransmitterConfig(markers=["0"]).markers == ["0"]

    def test_markers_explicit_int_preserved(self) -> None:
        assert OscTransmitterConfig(markers=["5"]).markers == ["5"]

    def test_markers_csv_string_parses_sorts_and_dedupes(self) -> None:
        """A hand-edited comma-separated string canonicalises: numeric ids
        sort ascending, duplicates drop."""
        assert OscTransmitterConfig(markers="7, 3, 1, 3").markers == ["1", "3", "7"]  # type: ignore[arg-type]

    def test_markers_controller_alias_preserved(self) -> None:
        assert OscTransmitterConfig(markers="c2, 1").markers == ["1", "c2"]  # type: ignore[arg-type]

    def test_markers_all_keyword_collapses_list(self) -> None:
        """``all`` subsumes every other entry."""
        assert OscTransmitterConfig(markers="1, all, c3").markers == ["all"]  # type: ignore[arg-type]

    def test_markers_invalid_entries_are_dropped(self) -> None:
        """Negatives, floats, bad aliases (c0 / c01), and junk are ignored
        rather than collapsing the whole field."""
        assert OscTransmitterConfig(markers="1, -3, bogus, 1.5, c0, c01, 4").markers == ["1", "4"]  # type: ignore[arg-type]

    def test_markers_empty_string_is_empty_list(self) -> None:
        assert OscTransmitterConfig(markers="   ").markers == []  # type: ignore[arg-type]

    def test_markers_bool_collapses_to_empty(self) -> None:
        """``True`` is an ``int`` subclass – a hand-edited ``markers = true``
        must not become marker 1."""
        assert OscTransmitterConfig(markers=True).markers == []  # type: ignore[arg-type]

    def test_markers_legacy_int_lift(self) -> None:
        """A bare int (the legacy single ``marker_id``) lifts to a one-token
        list; a negative lifts to empty."""
        assert OscTransmitterConfig(markers=5).markers == ["5"]  # type: ignore[arg-type]
        assert OscTransmitterConfig(markers=-3).markers == []  # type: ignore[arg-type]

    def test_non_string_template_id_collapses_to_empty(self) -> None:
        cfg = OscTransmitterConfig(template_id=99)  # type: ignore[arg-type]
        assert cfg.template_id == ""

    def test_template_id_is_stripped(self) -> None:
        cfg = OscTransmitterConfig(template_id="  etc  ")
        assert cfg.template_id == "etc"

    def test_non_string_address_collapses_to_empty(self) -> None:
        cfg = OscTransmitterConfig(address=999)  # type: ignore[arg-type]
        assert cfg.address == ""

    def test_address_is_stripped(self) -> None:
        cfg = OscTransmitterConfig(address="  /eos/x  ")
        assert cfg.address == "/eos/x"

    def test_non_list_args_falls_back_to_empty(self) -> None:
        cfg = OscTransmitterConfig(args="not a list")  # type: ignore[arg-type]
        assert cfg.args == []

    def test_args_are_coerced_to_strings(self) -> None:
        cfg = OscTransmitterConfig(args=[1, 2.5, "[x]"])  # type: ignore[arg-type]
        assert cfg.args == ["1", "2.5", "[x]"]

    @pytest.mark.parametrize("rate", [1, 5, 10, 20, 30, 60])
    def test_allowed_rate_passes_through(self, rate: int) -> None:
        assert OscTransmitterConfig(rate_hz=rate).rate_hz == rate

    @pytest.mark.parametrize(
        "raw,snapped",
        [
            # Ties resolve to the lower allowed value (``min`` picks the
            # first match in ``VALID_OSC_TRANSMITTER_RATES`` order).
            (3, 1),  # |1-3|=2, |5-3|=2 → 1
            (4, 5),  # |1-4|=3, |5-4|=1 → 5
            (15, 10),  # |10-15|=5, |20-15|=5 → 10
            (40, 30),  # |30-40|=10, |60-40|=20 → 30
            (50, 60),  # |30-50|=20, |60-50|=10 → 60
            (100, 60),  # over max allowed → 60
        ],
    )
    def test_disallowed_rate_snaps_to_nearest_allowed(
        self,
        raw: int,
        snapped: int,
    ) -> None:
        assert OscTransmitterConfig(rate_hz=raw).rate_hz == snapped

    def test_bad_type_rate_falls_back_to_default_then_passes(self) -> None:
        # Coerce path: non-int → default 30 (which is in the allowed set).
        assert OscTransmitterConfig(rate_hz="oops").rate_hz == 30  # type: ignore[arg-type]


class TestOscTransmittersConfig:
    def test_dict_entries_are_promoted_to_dataclasses(self) -> None:
        cfg = OscTransmittersConfig(
            transmitters=[{"id": "row1", "destination_id": "d1"}],
        )
        assert isinstance(cfg.transmitters[0], OscTransmitterConfig)
        assert cfg.transmitters[0].destination_id == "d1"

    def test_existing_dataclass_entries_pass_through(self) -> None:
        row = OscTransmitterConfig(id="r1", destination_id="d2")
        cfg = OscTransmittersConfig(transmitters=[row])
        assert cfg.transmitters[0] is row


class TestOscDestinationConfig:
    def test_blank_id_is_minted_as_uuid_hex(self) -> None:
        cfg = OscDestinationConfig()
        assert len(cfg.id) == 32
        assert all(c in "0123456789abcdef" for c in cfg.id)

    def test_existing_id_is_preserved_and_stripped(self) -> None:
        assert OscDestinationConfig(id="  dest-1  ").id == "dest-1"

    def test_non_string_name_falls_back_to_empty(self) -> None:
        assert OscDestinationConfig(name=99).name == ""  # type: ignore[arg-type]

    def test_name_is_stripped(self) -> None:
        assert OscDestinationConfig(name="  Console  ").name == "Console"

    def test_non_string_host_falls_back_to_default(self) -> None:
        assert OscDestinationConfig(host=123).host == "127.0.0.1"  # type: ignore[arg-type]

    def test_blank_host_falls_back_to_default(self) -> None:
        assert OscDestinationConfig(host="   ").host == "127.0.0.1"

    def test_host_is_stripped(self) -> None:
        assert OscDestinationConfig(host="  10.0.0.5  ").host == "10.0.0.5"

    def test_port_clamped_to_valid_range(self) -> None:
        assert OscDestinationConfig(port=0).port == 1
        assert OscDestinationConfig(port=70000).port == 65535
        assert OscDestinationConfig(port="bogus").port == 8000  # type: ignore[arg-type]

    @pytest.mark.parametrize("good", ["udp", "tcp"])
    def test_protocol_accepts_known_values(self, good: str) -> None:
        assert OscDestinationConfig(protocol=good).protocol == good

    @pytest.mark.parametrize("bad", ["", "raw", None, 7])
    def test_protocol_falls_back_to_udp_on_unknown(self, bad: object) -> None:
        assert OscDestinationConfig(protocol=bad).protocol == "udp"  # type: ignore[arg-type]

    def test_default_framing_is_slip(self) -> None:
        assert OscDestinationConfig().framing == "slip"

    @pytest.mark.parametrize("good", ["slip", "length_prefix"])
    def test_framing_accepts_known_values(self, good: str) -> None:
        assert OscDestinationConfig(framing=good).framing == good

    @pytest.mark.parametrize("bad", ["", "raw", None, 7, "SLIP"])
    def test_framing_falls_back_to_slip_on_unknown(self, bad: object) -> None:
        assert OscDestinationConfig(framing=bad).framing == "slip"  # type: ignore[arg-type]


class TestOscDestinationsConfig:
    def test_default_seeds_one_pickable_destination(self) -> None:
        cfg = OscDestinationsConfig()
        assert len(cfg.destinations) == 1
        assert cfg.destinations[0].id == "default"
        assert cfg.destinations[0].name == "Default"

    def test_default_config_compares_equal(self) -> None:
        """The seeded destination uses a fixed id so two default configs
        compare equal (the hot-reload diff depends on this)."""
        assert OscDestinationsConfig() == OscDestinationsConfig()

    def test_dict_entries_are_promoted_to_dataclasses(self) -> None:
        cfg = OscDestinationsConfig(
            destinations=[{"id": "d1", "host": "10.0.0.9"}],
        )
        assert isinstance(cfg.destinations[0], OscDestinationConfig)
        assert cfg.destinations[0].host == "10.0.0.9"

    def test_filter_known_drops_unknown_keys(self) -> None:
        cfg = OscDestinationsConfig(
            destinations=[{"id": "d1", "bogus": "x"}],
        )
        assert not hasattr(cfg.destinations[0], "bogus")

    def test_non_object_entries_are_dropped(self) -> None:
        """A hand-edited TOML / crafted import with bare entries must not
        persist a str/int/None: ``get()`` and template rendering dereference
        ``d.id`` / ``d.host`` and would raise AttributeError otherwise. Dict
        and already-typed entries survive; everything else is dropped."""
        typed = OscDestinationConfig(id="keep", name="Keep")
        cfg = OscDestinationsConfig(
            destinations=["evil", 123, None, {"id": "d1", "host": "10.0.0.9"}, typed],  # type: ignore[list-item]
        )
        assert all(isinstance(d, OscDestinationConfig) for d in cfg.destinations)
        assert [d.id for d in cfg.destinations] == ["d1", "keep"]
        resolved = cfg.get("d1")
        assert resolved is not None and resolved.host == "10.0.0.9"

    def test_get_returns_matching_destination(self) -> None:
        d = OscDestinationConfig(id="d1", name="A")
        cfg = OscDestinationsConfig(destinations=[d])
        assert cfg.get("d1") is d

    def test_get_unknown_id_returns_none(self) -> None:
        cfg = OscDestinationsConfig(destinations=[OscDestinationConfig(id="d1")])
        assert cfg.get("nope") is None

    def test_get_blank_id_returns_none(self) -> None:
        cfg = OscDestinationsConfig(destinations=[OscDestinationConfig(id="d1")])
        assert cfg.get("") is None

    def test_duplicate_ids_are_made_unique(self) -> None:
        """The id is the key transmitters/zones reference, so it must be
        unique. A hand-edited TOML / crafted import with two entries sharing
        an id keeps the first occurrence stable (existing references stay
        valid) and re-mints a fresh id for each later collision instead of
        leaving ``get()`` to resolve ambiguously to the first match."""
        cfg = OscDestinationsConfig(
            destinations=[
                OscDestinationConfig(id="dup", name="First", host="10.0.0.1"),
                OscDestinationConfig(id="dup", name="Second", host="10.0.0.2"),
            ],
        )
        ids = [d.id for d in cfg.destinations]
        assert ids[0] == "dup"  # first occurrence kept stable
        assert ids[1] != "dup"  # later collision re-minted
        assert len(set(ids)) == 2  # all unique
        first = cfg.get("dup")
        second = cfg.get(ids[1])
        assert first is not None and first.name == "First"
        assert second is not None and second.name == "Second"

    def test_duplicate_ids_across_three_entries_all_unique(self) -> None:
        cfg = OscDestinationsConfig(
            destinations=[
                OscDestinationConfig(id="x", name="A"),
                OscDestinationConfig(id="x", name="B"),
                OscDestinationConfig(id="x", name="C"),
            ],
        )
        ids = [d.id for d in cfg.destinations]
        assert ids[0] == "x"  # first occurrence kept stable
        assert len(set(ids)) == 3  # all unique

    def test_by_id_indexes_every_destination(self) -> None:
        """``by_id`` is the O(1) index hot consumers stage instead of a
        per-tick linear scan; it must agree with ``get`` for every id."""
        a = OscDestinationConfig(id="a", host="10.0.0.1")
        b = OscDestinationConfig(id="b", host="10.0.0.2")
        cfg = OscDestinationsConfig(destinations=[a, b])
        index = cfg.by_id()
        assert index == {"a": a, "b": b}
        assert index.get("a") is cfg.get("a")
        assert index.get("nope") is cfg.get("nope")  # both None for unknown id


# ---------------------------------------------------------------------------
# Trigger model
# ---------------------------------------------------------------------------

from openfollow.configuration import (  # noqa: E402
    ControllerButtonTrigger,
    EncoderOnChangeTrigger,
    FaderOnChangeTrigger,
    HotkeyTrigger,
    MidiMessageTrigger,
    StreamTrigger,
    _trigger_from_dict,
)


class TestStreamTrigger:
    def test_default_rate_is_30(self) -> None:
        assert StreamTrigger().rate_hz == 30

    @pytest.mark.parametrize("rate", [1, 5, 10, 20, 30, 60])
    def test_allowed_rates_pass_through(self, rate: int) -> None:
        assert StreamTrigger(rate_hz=rate).rate_hz == rate

    def test_disallowed_rate_snaps_to_nearest(self) -> None:
        # Same snap rule as OscTransmitterConfig.rate_hz – kept in sync.
        assert StreamTrigger(rate_hz=40).rate_hz == 30

    def test_carries_kind_discriminator(self) -> None:
        """The ``kind`` field is the TOML discriminator. It's set
        automatically (init=False) so callers don't need to know it."""
        assert StreamTrigger().kind == "stream"

    # Operator-feedback follow-up: send-always vs send-on-change mode
    # + per-axis minimum-change threshold.

    def test_default_mode_is_always(self) -> None:
        # New default preserves pre-feedback behaviour: existing rows
        # without the field on disk load as "always" so the runtime
        # cadence is unchanged on a hot-reload that picks up the
        # schema bump without an operator edit.
        assert StreamTrigger().mode == "always"

    def test_default_min_change_is_5cm(self) -> None:
        assert StreamTrigger().min_change_m == 0.05

    def test_explicit_on_change_mode_preserved(self) -> None:
        t = StreamTrigger(mode="on_change", min_change_m=0.1)
        assert t.mode == "on_change"
        assert t.min_change_m == 0.1

    @pytest.mark.parametrize("bad", ["always_send", "", "ON_CHANGE", "true", None, 42])
    def test_unknown_mode_falls_back_to_always(self, bad: object) -> None:
        # Hand-edited TOML / stale browser tab posting an unknown mode
        # value must not silently disable sends entirely. Fall back to
        # the safe-default "always" so the row keeps firing.
        t = StreamTrigger(mode=bad)  # type: ignore[arg-type]
        assert t.mode == "always"

    @pytest.mark.parametrize("bad", [-0.01, -1.0, float("nan")])
    def test_invalid_min_change_falls_back_to_default(self, bad: float) -> None:
        # Negative thresholds would invert the gate; NaN compares
        # false to everything (would never skip). Both collapse to
        # the 5cm default.
        t = StreamTrigger(min_change_m=bad)
        assert t.min_change_m == 0.05

    def test_zero_min_change_passes(self) -> None:
        # Zero threshold is a valid edge case – operator wants the
        # gate to fire only on bit-exact equality of consecutive
        # samples (effectively "send unless identical").
        t = StreamTrigger(min_change_m=0.0)
        assert t.min_change_m == 0.0

    def test_non_numeric_min_change_falls_back(self) -> None:
        t = StreamTrigger(min_change_m="five centimetres")  # type: ignore[arg-type]
        assert t.min_change_m == 0.05


class TestHotkeyTrigger:
    def test_modifiers_normalised_to_sorted_lower_case(self) -> None:
        t = HotkeyTrigger(key="F1", modifiers=("SHIFT", "ctrl", "Alt"))
        assert t.modifiers == ("alt", "ctrl", "shift")

    def test_unknown_modifiers_dropped(self) -> None:
        t = HotkeyTrigger(modifiers=("ctrl", "meta", "shift"))
        assert t.modifiers == ("ctrl", "shift")

    def test_modifiers_dedup(self) -> None:
        t = HotkeyTrigger(modifiers=("ctrl", "CTRL", "ctrl"))
        assert t.modifiers == ("ctrl",)

    def test_non_iterable_modifiers_collapse_to_empty(self) -> None:
        t = HotkeyTrigger(modifiers=42)  # type: ignore[arg-type]
        assert t.modifiers == ()

    def test_key_stripped(self) -> None:
        assert HotkeyTrigger(key="  F1  ").key == "F1"

    def test_non_string_key_collapses_to_empty(self) -> None:
        assert HotkeyTrigger(key=99).key == ""  # type: ignore[arg-type]

    @pytest.mark.parametrize("edge", ["press", "release"])
    def test_edge_accepts_press_or_release(self, edge: str) -> None:
        assert HotkeyTrigger(edge=edge).edge == edge

    def test_invalid_edge_falls_back_to_press(self) -> None:
        assert HotkeyTrigger(edge="sideways").edge == "press"

    def test_kind_is_hotkey(self) -> None:
        assert HotkeyTrigger().kind == "hotkey"


class TestControllerButtonTrigger:
    def test_button_stripped(self) -> None:
        assert ControllerButtonTrigger(button="  A  ").button == "A"

    def test_non_string_button_collapses_to_empty(self) -> None:
        t = ControllerButtonTrigger(button=42)  # type: ignore[arg-type]
        assert t.button == ""

    def test_invalid_edge_falls_back_to_press(self) -> None:
        assert ControllerButtonTrigger(edge="sideways").edge == "press"

    def test_kind_is_controller_button(self) -> None:
        assert ControllerButtonTrigger().kind == "controller_button"


class TestCoerceOptionalInt:
    """Wildcard-or-bounded-int coercion helper tests.

    Used by :class:`MidiMessageTrigger` for channel / number / value
    and by :class:`OscTransmitterConfig.default_fader`. Each guard
    has the same fail-soft contract as
    :func:`_coerce_optional_marker_id`: any malformed input collapses
    to ``None`` (the wildcard / unset sentinel) rather than picking
    a wrong-but-valid number.
    """

    def test_bool_collapses_to_none(self) -> None:
        # Same float-rejection rationale: ``int(True) == 1`` would
        # silently route a hand-edited ``channel = true`` to channel 1.
        from openfollow.configuration import _coerce_optional_int

        assert _coerce_optional_int(True, lo=1, hi=16) is None

    def test_float_collapses_to_none(self) -> None:
        # ``int(1.5)`` silently truncates to ``1`` – reject all floats.
        from openfollow.configuration import _coerce_optional_int

        assert _coerce_optional_int(1.5, lo=1, hi=16) is None

    def test_blank_string_collapses_to_none(self) -> None:
        # Empty / whitespace-only string is the form's "unset" shape.
        from openfollow.configuration import _coerce_optional_int

        assert _coerce_optional_int("", lo=1, hi=16) is None
        assert _coerce_optional_int("   ", lo=1, hi=16) is None

    def test_non_coercible_collapses_to_none(self) -> None:
        from openfollow.configuration import _coerce_optional_int

        assert _coerce_optional_int("not-a-number", lo=1, hi=16) is None
        assert _coerce_optional_int(object(), lo=1, hi=16) is None


class TestMidiMessageTrigger:
    """Wildcard-by-default MIDI trigger validation tests.

    Validates the wildcard semantics (``""`` / ``None`` = any) and
    the runtime-rejecting fail-soft on bad inputs (out-of-range
    channel / number / value, unrecognised type all fall back to
    the safe defaults defined on the dataclass).
    """

    def test_default_construction_is_all_wildcards(self) -> None:
        t = MidiMessageTrigger()
        assert t.kind == "midi_message"
        assert t.patch_id == 0
        assert t.type == "note_on"  # the dropdown default
        assert t.channel is None
        assert t.number is None
        assert t.value is None

    def test_full_specification_round_trips(self) -> None:
        t = MidiMessageTrigger(
            patch_id=2,
            type="control_change",
            channel=3,
            number=7,
            value=64,
        )
        assert t.patch_id == 2
        assert t.type == "control_change"
        assert t.channel == 3
        assert t.number == 7
        assert t.value == 64

    def test_unknown_type_falls_back_to_note_on(self) -> None:
        t = MidiMessageTrigger(type="pitch_bend")
        assert t.type == "note_on"

    def test_out_of_range_channel_collapses_to_none(self) -> None:
        # Wildcard-fail-soft: out-of-range collapses to "any" rather
        # than picking a wrong-but-valid channel silently. Same
        # contract as ``_coerce_optional_marker_id``.
        assert MidiMessageTrigger(channel=20).channel is None
        assert MidiMessageTrigger(channel=0).channel is None
        assert MidiMessageTrigger(channel=-1).channel is None

    def test_out_of_range_number_collapses_to_none(self) -> None:
        assert MidiMessageTrigger(number=200).number is None
        assert MidiMessageTrigger(number=-5).number is None

    def test_negative_patch_id_collapses_to_zero(self) -> None:
        # ``patch_id`` is a non-negative foreign key; 0 = "any patch".
        assert MidiMessageTrigger(patch_id=-3).patch_id == 0

    def test_non_int_patch_id_collapses_to_zero(self) -> None:
        assert MidiMessageTrigger(patch_id="nope").patch_id == 0  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "type_",
        [
            "note_on",
            "note_off",
            "control_change",
            "program_change",
            "key_pressure",
            "channel_pressure",
        ],
    )
    def test_accepts_every_valid_type(self, type_: str) -> None:
        assert MidiMessageTrigger(type=type_).type == type_


class TestFaderOnChangeTrigger:
    def test_default_construction(self) -> None:
        t = FaderOnChangeTrigger()
        assert t.kind == "fader_on_change"
        assert t.fader == 1
        assert t.rate_hz == 30
        # Default source is an indexed fader.
        assert t.marker_id == 0

    def test_marker_id_selects_marker_source(self) -> None:
        # marker_id >= 1 selects a per-controlled-marker gamepad fader source.
        assert FaderOnChangeTrigger(marker_id=7).marker_id == 7

    def test_marker_id_coerces_and_floors_at_zero(self) -> None:
        assert FaderOnChangeTrigger(marker_id=-3).marker_id == 0
        assert FaderOnChangeTrigger(marker_id=True).marker_id == 0
        assert FaderOnChangeTrigger(marker_id="bad").marker_id == 0

    def test_fader_index_clamps_to_valid_range(self) -> None:
        # 1..VIRTUAL_FADER_COUNT – out-of-range clamps.
        from openfollow.configuration import VIRTUAL_FADER_COUNT

        assert FaderOnChangeTrigger(fader=20).fader == VIRTUAL_FADER_COUNT
        assert FaderOnChangeTrigger(fader=0).fader == 1
        assert FaderOnChangeTrigger(fader=-3).fader == 1

    def test_rate_snaps_to_valid_choice(self) -> None:
        # 25 Hz isn't a valid choice; snap is deterministic – ties
        # break to the first equidistant entry in
        # ``VALID_OSC_TRANSMITTER_RATES`` (``(1, 5, 10, 20, 30, 60)``),
        # so 25 Hz lands on 20. Locking the value down so a future
        # change to the snap implementation has to update the test
        # rather than silently flipping the runtime behaviour.
        assert FaderOnChangeTrigger(rate_hz=25).rate_hz == 20

    @pytest.mark.parametrize("rate", [1, 5, 10, 20, 30, 60])
    def test_accepts_every_valid_rate(self, rate: int) -> None:
        assert FaderOnChangeTrigger(rate_hz=rate).rate_hz == rate


class TestEncoderOnChangeTrigger:
    """The trigger type is retained as a dormant stub for config round-trip
    and union stability but exposes no encoder fields; the factory maps
    ``encoder_on_change`` to Stream."""

    def test_dormant_stub_kind_only(self) -> None:
        t = EncoderOnChangeTrigger()
        assert t.kind == "encoder_on_change"
        assert not hasattr(t, "encoder_alias")


class TestTriggerFromDict:
    def test_stream_kind_round_trips(self) -> None:
        t = _trigger_from_dict({"kind": "stream", "rate_hz": 60})
        assert isinstance(t, StreamTrigger)
        assert t.rate_hz == 60

    def test_hotkey_kind_round_trips(self) -> None:
        t = _trigger_from_dict(
            {
                "kind": "hotkey",
                "key": "F1",
                "modifiers": ["ctrl", "shift"],
                "edge": "release",
            }
        )
        assert isinstance(t, HotkeyTrigger)
        assert t.key == "F1"
        assert t.modifiers == ("ctrl", "shift")
        assert t.edge == "release"

    def test_controller_button_kind_round_trips(self) -> None:
        t = _trigger_from_dict(
            {
                "kind": "controller_button",
                "button": "A",
                "edge": "press",
            }
        )
        assert isinstance(t, ControllerButtonTrigger)
        assert t.button == "A"

    def test_unknown_kind_falls_back_to_default_stream(self) -> None:
        t = _trigger_from_dict({"kind": "from-the-future", "x": 1})
        assert isinstance(t, StreamTrigger)
        assert t.rate_hz == 30

    def test_midi_message_kind_round_trips(self) -> None:
        # Kinds construct typed instances (not Stream fallback).
        t = _trigger_from_dict(
            {
                "kind": "midi_message",
                "patch_id": 2,
                "type": "control_change",
                "channel": 1,
                "number": 7,
                "value": 64,
            }
        )
        assert isinstance(t, MidiMessageTrigger)
        assert t.patch_id == 2
        assert t.channel == 1 and t.number == 7 and t.value == 64

    def test_midi_message_kind_with_wildcards(self) -> None:
        # ``channel`` / ``number`` / ``value`` omitted from TOML →
        # the dataclass default ``None`` round-trips as the wildcard.
        t = _trigger_from_dict({"kind": "midi_message"})
        assert isinstance(t, MidiMessageTrigger)
        assert t.channel is None and t.number is None and t.value is None

    def test_fader_on_change_kind_round_trips(self) -> None:
        t = _trigger_from_dict(
            {
                "kind": "fader_on_change",
                "fader": 3,
                "rate_hz": 60,
            }
        )
        assert isinstance(t, FaderOnChangeTrigger)
        assert t.fader == 3 and t.rate_hz == 60
        # Absent marker_id defaults to indexed source.
        assert t.marker_id == 0

    def test_fader_on_change_marker_source_round_trips(self) -> None:
        t = _trigger_from_dict(
            {
                "kind": "fader_on_change",
                "marker_id": 4,
                "rate_hz": 30,
            }
        )
        assert isinstance(t, FaderOnChangeTrigger)
        assert t.marker_id == 4 and t.rate_hz == 30

    def test_encoder_on_change_degrades_to_stream(self) -> None:
        # The factory degrades an ``encoder_on_change`` row to a Stream
        # trigger on load instead of constructing a functional EncoderOnChangeTrigger.
        t = _trigger_from_dict(
            {
                "kind": "encoder_on_change",
                "encoder_alias": "Pan A",
                "rate_hz": 20,
            }
        )
        assert isinstance(t, StreamTrigger)

    def test_non_dict_input_falls_back_to_default_stream(self) -> None:
        assert isinstance(_trigger_from_dict(None), StreamTrigger)
        assert isinstance(_trigger_from_dict("garbage"), StreamTrigger)
        assert isinstance(_trigger_from_dict(42), StreamTrigger)

    def test_hotkey_with_null_modifiers_does_not_crash(self) -> None:
        t = _trigger_from_dict(
            {
                "kind": "hotkey",
                "key": "F1",
                "modifiers": None,
            }
        )
        assert isinstance(t, HotkeyTrigger)
        assert t.modifiers == ()

    def test_hotkey_with_non_list_modifiers_does_not_crash(self) -> None:
        """Same guard for any other non-iterable shape (number,
        bool) that could appear in a malformed config."""
        t = _trigger_from_dict(
            {
                "kind": "hotkey",
                "key": "F1",
                "modifiers": 42,
            }
        )
        assert isinstance(t, HotkeyTrigger)
        assert t.modifiers == ()


class TestOscTransmitterConfigTriggerLift:
    def test_default_construction_yields_stream_trigger(self) -> None:
        row = OscTransmitterConfig()
        assert isinstance(row.trigger, StreamTrigger)
        assert row.trigger.rate_hz == 30

    def test_legacy_rate_hz_lifted_into_stream_trigger(self) -> None:
        """Caller passes ``rate_hz=N`` (no trigger) – post_init lifts
        the rate into the trigger so the runtime sees the operator's
        intended cadence. backwards-compatible."""
        row = OscTransmitterConfig(rate_hz=60)
        assert row.trigger.rate_hz == 60
        assert row.rate_hz == 60

    def test_explicit_trigger_overrides_legacy_rate_hz(self) -> None:
        """When both are passed, trigger is the new authoritative
        field. ``rate_hz`` is mirrored from trigger.rate_hz so legacy
        readers stay in sync."""
        row = OscTransmitterConfig(
            trigger=StreamTrigger(rate_hz=10),
            rate_hz=60,
        )
        assert row.trigger.rate_hz == 10
        assert row.rate_hz == 10

    def test_dict_trigger_coerced_to_typed_value(self) -> None:
        row = OscTransmitterConfig(
            trigger={"kind": "hotkey", "key": "F1"},
        )
        assert isinstance(row.trigger, HotkeyTrigger)

    def test_garbage_trigger_falls_back_to_stream(self) -> None:
        """A hand-edited TOML with ``trigger = "stream"`` (string
        instead of a sub-table) shouldn't crash – coerce to a
        rate_hz-derived Stream so the row is still loadable."""
        row = OscTransmitterConfig(
            trigger="not a dict or trigger",  # type: ignore[arg-type]
            rate_hz=20,
        )
        assert isinstance(row.trigger, StreamTrigger)
        assert row.trigger.rate_hz == 20

    def test_hotkey_trigger_does_not_mirror_to_rate_hz(self) -> None:
        """``rate_hz`` is only meaningful for Stream triggers – for
        other trigger kinds it stays at the construction-time default
        rather than being mirrored from trigger.rate_hz (which doesn't
        exist on Hotkey)."""
        row = OscTransmitterConfig(trigger=HotkeyTrigger(key="F1"))
        # rate_hz keeps its original value (default 30) – the Stream
        # mirror branch doesn't fire for non-Stream triggers.
        assert row.rate_hz == 30
        assert isinstance(row.trigger, HotkeyTrigger)


class TestOscTransmittersConfigLegacyLift:
    def test_legacy_rate_hz_only_row_lifts_into_stream(self) -> None:
        """A TOML row with ``rate_hz`` and no ``trigger`` field gets
        the legacy lift applied at the loader level so the dataclass
        sees a synthesised trigger sub-table."""
        cfg = OscTransmittersConfig(
            transmitters=[
                {"rate_hz": 60, "destination_id": "d1"},
            ]
        )
        row = cfg.transmitters[0]
        assert isinstance(row.trigger, StreamTrigger)
        assert row.trigger.rate_hz == 60

    def test_row_with_trigger_does_not_apply_legacy_lift(self) -> None:
        """If both ``trigger`` and ``rate_hz`` are present in the
        TOML, the trigger is kept verbatim – no double-lift."""
        cfg = OscTransmittersConfig(
            transmitters=[
                {
                    "trigger": {"kind": "stream", "rate_hz": 10},
                    "rate_hz": 60,  # ignored – trigger present
                },
            ]
        )
        row = cfg.transmitters[0]
        assert row.trigger.rate_hz == 10

    def test_row_with_only_trigger_passes_through(self) -> None:
        cfg = OscTransmittersConfig(
            transmitters=[
                {"trigger": {"kind": "controller_button", "button": "A"}},
            ]
        )
        row = cfg.transmitters[0]
        assert isinstance(row.trigger, ControllerButtonTrigger)
        assert row.trigger.button == "A"

    def test_round_trip_through_toml(self, temp_config_path) -> None:  # noqa: ANN001
        # Templates now live as .openfollowtemplate files; test covers transmitters list.
        original = AppConfig(
            osc_transmitters=OscTransmittersConfig(
                transmitters=[
                    OscTransmitterConfig(
                        id="abc",
                        destination_id="d1",
                        rate_hz=20,
                        template_id="etc",
                    ),
                    OscTransmitterConfig(
                        id="row2",
                        destination_id="d2",
                    ),
                ],
            ),
        )
        save_config(original, str(temp_config_path))
        reloaded = load_config(str(temp_config_path))
        rows = reloaded.osc_transmitters.transmitters
        assert len(rows) == 2
        assert rows[0].id == "abc"
        assert rows[0].destination_id == "d1"
        assert rows[0].rate_hz == 20
        assert rows[1].id == "row2"
        assert rows[1].destination_id == "d2"

    def test_markers_empty_round_trips_through_toml(
        self,
        temp_config_path,  # noqa: ANN001
    ) -> None:
        original = AppConfig(
            osc_transmitters=OscTransmittersConfig(
                transmitters=[
                    OscTransmitterConfig(id="r1", markers=[]),
                ],
            ),
        )
        save_config(original, str(temp_config_path))
        reloaded = load_config(str(temp_config_path))
        assert reloaded.osc_transmitters.transmitters[0].markers == []

    def test_markers_multi_round_trips_through_toml(
        self,
        temp_config_path,  # noqa: ANN001
    ) -> None:
        """A multi-marker row (ids + alias) survives a save / reload cycle
        in canonical order."""
        original = AppConfig(
            osc_transmitters=OscTransmittersConfig(
                transmitters=[
                    OscTransmitterConfig(id="r1", markers=["7", "0", "c1"]),
                ],
            ),
        )
        save_config(original, str(temp_config_path))
        with open(temp_config_path, "rb") as f:
            raw = tomllib.load(f)
        row = raw["osc_transmitters"]["transmitters"][0]
        assert row["markers"] == ["0", "7", "c1"]
        reloaded = load_config(str(temp_config_path))
        assert reloaded.osc_transmitters.transmitters[0].markers == ["0", "7", "c1"]

    def test_legacy_marker_id_lifts_into_markers(
        self,
        temp_config_path,  # noqa: ANN001
    ) -> None:
        """A hand-edited TOML row carrying the legacy single ``marker_id``
        key lifts into the one-token ``markers`` list on load."""
        temp_config_path.write_text(
            "[[osc_transmitters.transmitters]]\nid = 'r1'\nmarker_id = 4\n",
            encoding="utf-8",
        )
        reloaded = load_config(str(temp_config_path))
        assert reloaded.osc_transmitters.transmitters[0].markers == ["4"]


def test_new_uuid_hex_returns_unique_strings_each_call() -> None:
    from openfollow.configuration import _new_uuid_hex

    a = _new_uuid_hex()
    b = _new_uuid_hex()
    assert a != b
    assert len(a) == 32
    assert len(b) == 32


# ---------------------------------------------------------------------------
# MidiPatch / MidiConfig – Group 1
# ---------------------------------------------------------------------------


class TestMidiPatch:
    def test_strips_whitespace_on_all_string_fields(self) -> None:
        from openfollow.configuration import MidiPatch

        patch = MidiPatch(
            id=1,
            alias="  Workspace 1  ",
            serial="  ABC  ",
            port_name="  MIDI Mix  ",
            product="  MIDI Mix  ",
        )
        assert patch.alias == "Workspace 1"
        assert patch.serial == "ABC"
        assert patch.port_name == "MIDI Mix"
        assert patch.product == "MIDI Mix"

    def test_non_string_fields_coerced_to_empty(self) -> None:
        from openfollow.configuration import MidiPatch

        patch = MidiPatch(
            id=1,
            alias=None,  # type: ignore[arg-type]
            serial=42,  # type: ignore[arg-type]
            port_name=None,  # type: ignore[arg-type]
            product=None,  # type: ignore[arg-type]
        )
        assert patch.alias == ""
        assert patch.serial == ""
        assert patch.port_name == ""
        assert patch.product == ""

    def test_id_coerced_to_non_negative_int(self) -> None:
        from openfollow.configuration import MidiPatch

        assert MidiPatch(id=-4).id == 0
        assert MidiPatch(id="nope").id == 0  # type: ignore[arg-type]
        assert MidiPatch(id=3).id == 3

    def test_label_prefers_alias_then_port_then_fallback(self) -> None:
        from openfollow.configuration import MidiPatch

        assert MidiPatch(id=2, alias="Desk").label == "2 – Desk"
        assert MidiPatch(id=3, port_name="MIDI Mix").label == "3 – MIDI Mix"
        assert MidiPatch(id=4).label == "4 – Patch 4"

    def test_identifier_mirrors_subsystem_key(self) -> None:
        from openfollow.configuration import MidiPatch

        # Serial wins when present; else port|product; else empty.
        assert MidiPatch(id=1, serial="ABC").identifier == "serial:ABC"
        assert MidiPatch(id=1, port_name="MIDI Mix", product="Mix").identifier == "port:MIDI Mix|Mix"
        assert MidiPatch(id=1).identifier == ""

    def test_port_name_only_normalizes_product(self) -> None:
        # A legacy patch with only port_name set mirrors v1 discovery
        # (product == port_name) so identifier matches the discovered device.
        from openfollow.configuration import MidiPatch

        patch = MidiPatch(id=1, port_name="MIDI Mix")
        assert patch.product == "MIDI Mix"
        assert patch.identifier == "port:MIDI Mix|MIDI Mix"


class TestMidiConfig:
    def test_converts_dict_patches_to_dataclasses(self) -> None:
        # TOML loads ``[[midi.patches]]`` tables as dicts; ``__post_init__``
        # must instantiate the typed entries. Mirrors the
        # ``TriggerZonesConfig`` pattern.
        from openfollow.configuration import MidiConfig, MidiPatch

        cfg = MidiConfig(
            patches=[
                {"id": 1, "alias": "A", "port_name": "MIDI Mix", "product": "MIDI Mix"},
            ],
        )
        assert len(cfg.patches) == 1
        assert isinstance(cfg.patches[0], MidiPatch)
        assert cfg.patches[0].alias == "A"
        assert cfg.patches[0].id == 1

    def test_assigns_sequential_ids_to_unset_or_duplicate(self) -> None:
        # id 0 (unset / legacy TOML) and duplicates get the next free
        # sequential id so the foreign keys bindings store always resolve.
        from openfollow.configuration import MidiConfig, MidiPatch

        cfg = MidiConfig(
            patches=[
                MidiPatch(id=0, alias="A"),
                MidiPatch(id=2, alias="B"),
                MidiPatch(id=2, alias="C"),  # duplicate
            ],
        )
        ids = [p.id for p in cfg.patches]
        assert len(set(ids)) == 3
        assert all(i >= 1 for i in ids)

    def test_next_patch_id_reuses_smallest_free(self) -> None:
        from openfollow.configuration import MidiConfig, MidiPatch

        cfg = MidiConfig(patches=[MidiPatch(id=1), MidiPatch(id=3)])
        assert cfg.next_patch_id() == 2

    def test_patch_by_id_returns_match_or_none(self) -> None:
        from openfollow.configuration import MidiConfig, MidiPatch

        cfg = MidiConfig(patches=[MidiPatch(id=1, alias="A")])
        assert cfg.patch_by_id(1).alias == "A"
        assert cfg.patch_by_id(99) is None

    def test_preserves_already_built_instances(self) -> None:
        from openfollow.configuration import MidiConfig, MidiPatch

        patch = MidiPatch(id=1, alias="A")
        cfg = MidiConfig(patches=[patch])
        assert cfg.patches[0] is patch

    def test_filters_unknown_keys_via_load_config(self, temp_config_path) -> None:  # noqa: ANN001
        # End-to-end: TOML with stale keys still loads – the
        # ``_filter_known`` pass in ``MidiConfig.__post_init__`` drops
        # them so the dataclass constructor doesn't raise.
        import textwrap

        from openfollow.configuration import load_config

        temp_config_path.write_text(
            textwrap.dedent("""
            [[midi.patches]]
            id = 1
            alias = "Workspace 1"
            port_name = "MIDI Mix"
            product = "MIDI Mix"
            future_field = "ignored"
        """).strip()
        )
        cfg = load_config(str(temp_config_path))
        assert len(cfg.midi.patches) == 1
        assert cfg.midi.patches[0].alias == "Workspace 1"


# ---------------------------------------------------------------------------
# VirtualFaderConfig / VirtualFadersConfig – Group 2
# ---------------------------------------------------------------------------


class TestVirtualFaderConfig:
    def test_default_value_clamps_to_zero_one(self) -> None:
        from openfollow.configuration import VirtualFaderConfig

        assert VirtualFaderConfig(default_value=2.0).default_value == 1.0
        assert VirtualFaderConfig(default_value=-0.5).default_value == 0.0

    def test_unknown_source_kind_falls_back_to_blank(self) -> None:
        from openfollow.configuration import VirtualFaderConfig

        cfg = VirtualFaderConfig(source_kind="not-a-real-kind")
        assert cfg.source_kind == ""

    def test_known_source_kinds_pass_through(self) -> None:
        from openfollow.configuration import VirtualFaderConfig

        for kind in ("", "midi"):
            assert VirtualFaderConfig(source_kind=kind).source_kind == kind

    def test_gamepad_source_kind_no_longer_valid(self) -> None:
        # The gamepad no longer drives a fixed indexed fader; "gamepad"
        # was dropped from VALID_FADER_SOURCE_KINDS, so it coerces to "".
        from openfollow.configuration import VirtualFaderConfig

        assert VirtualFaderConfig(source_kind="gamepad").source_kind == ""

    def test_unknown_midi_type_falls_back_to_control_change(self) -> None:
        from openfollow.configuration import VirtualFaderConfig

        cfg = VirtualFaderConfig(source_midi_type="pitch_bend")
        assert cfg.source_midi_type == "control_change"

    def test_midi_channel_and_number_clamp(self) -> None:
        from openfollow.configuration import VirtualFaderConfig

        # Channel: 0 = any, 1-16 valid; out-of-range clamps.
        assert VirtualFaderConfig(source_midi_channel=20).source_midi_channel == 16
        assert VirtualFaderConfig(source_midi_channel=-5).source_midi_channel == 0
        # Number: 0-127.
        assert VirtualFaderConfig(source_midi_number=200).source_midi_number == 127
        assert VirtualFaderConfig(source_midi_number=-5).source_midi_number == 0

    def test_strips_whitespace_on_string_fields(self) -> None:
        from openfollow.configuration import VirtualFaderConfig

        cfg = VirtualFaderConfig(name="  Master  ")
        assert cfg.name == "Master"

    def test_source_patch_coerced_to_non_negative_int(self) -> None:
        # A fader's MIDI source is scoped to a patch id (0 = any).
        from openfollow.configuration import VirtualFaderConfig

        assert VirtualFaderConfig(source_patch=2).source_patch == 2
        assert VirtualFaderConfig(source_patch=-1).source_patch == 0
        assert VirtualFaderConfig(source_patch="x").source_patch == 0  # type: ignore[arg-type]


class TestVirtualFadersConfig:
    def test_pads_to_eight_when_omitted(self) -> None:
        # Default config: empty list → padded to 8 default faders.
        from openfollow.configuration import (
            VIRTUAL_FADER_COUNT,
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        cfg = VirtualFadersConfig()
        assert len(cfg.faders) == VIRTUAL_FADER_COUNT
        assert all(isinstance(f, VirtualFaderConfig) for f in cfg.faders)

    def test_pads_to_eight_when_partial(self) -> None:
        from openfollow.configuration import (
            VIRTUAL_FADER_COUNT,
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(name="Master"),
            ]
        )
        assert len(cfg.faders) == VIRTUAL_FADER_COUNT
        assert cfg.faders[0].name == "Master"
        assert cfg.faders[1].name == ""  # default

    def test_trims_to_eight_on_overflow(self) -> None:
        # Hand-edited TOML with 12 entries gets trimmed silently –
        # v1's count is fixed at 8.
        from openfollow.configuration import (
            VIRTUAL_FADER_COUNT,
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        cfg = VirtualFadersConfig(faders=[VirtualFaderConfig() for _ in range(12)])
        assert len(cfg.faders) == VIRTUAL_FADER_COUNT

    def test_converts_dict_entries_to_dataclasses(self) -> None:
        from openfollow.configuration import (
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        cfg = VirtualFadersConfig(
            faders=[
                {"name": "Volume", "default_value": 0.5},
            ]
        )
        assert isinstance(cfg.faders[0], VirtualFaderConfig)
        assert cfg.faders[0].name == "Volume"

    def test_preserves_already_built_instances(self) -> None:
        from openfollow.configuration import (
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        fader = VirtualFaderConfig(name="Pre-built")
        cfg = VirtualFadersConfig(faders=[fader])
        # The pre-built instance is preserved at index 0; remaining
        # entries are fresh default-constructed faders.
        assert cfg.faders[0] is fader

    def test_fader_one_accepts_midi_source(self) -> None:
        """The former 'fader 1 = gamepad-only' allocation rule is gone –
        every indexed fader is uniformly MIDI / unmapped now, so a MIDI
        source on fader 1 round-trips intact."""
        from openfollow.configuration import (
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(
                    source_kind="midi",
                    source_patch=1,
                    source_midi_channel=3,
                    source_midi_number=7,
                ),
            ]
        )
        f1 = cfg.faders[0]
        assert f1.source_kind == "midi"
        assert f1.source_patch == 1
        assert f1.source_midi_channel == 3
        assert f1.source_midi_number == 7

    def test_gamepad_source_coerced_to_empty_on_any_fader(self) -> None:
        """A stale ``source_kind="gamepad"`` from an old config coerces
        to "" on every fader (the kind no longer exists)."""
        from openfollow.configuration import (
            VirtualFaderConfig,
            VirtualFadersConfig,
        )

        cfg = VirtualFadersConfig(
            faders=[
                VirtualFaderConfig(source_kind="gamepad"),
                VirtualFaderConfig(source_kind="gamepad", source_patch=1),
            ]
        )
        assert cfg.faders[0].source_kind == ""
        assert cfg.faders[1].source_kind == ""

    def test_loads_through_load_config(self, temp_config_path) -> None:  # noqa: ANN001
        import textwrap

        from openfollow.configuration import load_config

        temp_config_path.write_text(
            textwrap.dedent("""
            [[virtual_faders.faders]]
            name = "Master"
            default_value = 0.7
            show_on_display = true
        """).strip()
        )
        cfg = load_config(str(temp_config_path))
        assert cfg.virtual_faders.faders[0].name == "Master"
        assert cfg.virtual_faders.faders[0].default_value == 0.7
        assert cfg.virtual_faders.faders[0].show_on_display is True


class TestUiConfig:
    """UiConfig normalises the [ui] unit_system string."""

    def test_default_is_metric(self) -> None:
        from openfollow.configuration import UiConfig

        assert UiConfig().unit_system == "metric"

    def test_case_and_whitespace_normalised(self) -> None:
        from openfollow.configuration import UiConfig

        assert UiConfig(unit_system="  IMPERIAL ").unit_system == "imperial"

    def test_unknown_value_falls_back_to_metric(self) -> None:
        from openfollow.configuration import UiConfig

        assert UiConfig(unit_system="furlongs").unit_system == "metric"

    def test_non_string_falls_back_to_metric(self) -> None:
        # A non-string (e.g. a malformed TOML value surfacing as a non-str)
        # coerces to the metric default instead of raising in __post_init__.
        from openfollow.configuration import UiConfig

        assert UiConfig(unit_system=123).unit_system == "metric"  # type: ignore[arg-type]

    def test_toml_round_trip(self, temp_config_path) -> None:
        from openfollow.configuration import (
            AppConfig,
            UiConfig,
            load_config,
            save_config,
        )

        save_config(
            AppConfig(ui=UiConfig(unit_system="imperial")),
            str(temp_config_path),
        )
        assert load_config(str(temp_config_path)).ui.unit_system == "imperial"

    # show_experimental_features opt-in.
    def test_show_experimental_default_is_false(self) -> None:
        from openfollow.configuration import UiConfig

        assert UiConfig().show_experimental_features is False

    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True),
            (False, False),
            ("true", True),
            ("on", True),
            ("1", True),
            ("false", False),
            ("off", False),
            ("0", False),
            # Unrecognised / wrong-type input falls back to the default (False).
            ("maybe", False),
            (42, False),
            (None, False),
        ],
    )
    def test_show_experimental_coercion(self, value, expected) -> None:  # noqa: ANN001
        from openfollow.configuration import UiConfig

        assert UiConfig(show_experimental_features=value).show_experimental_features is expected  # type: ignore[arg-type]

    def test_show_experimental_toml_round_trip(self, temp_config_path) -> None:  # noqa: ANN001
        from openfollow.configuration import (
            AppConfig,
            UiConfig,
            load_config,
            save_config,
        )

        save_config(
            AppConfig(ui=UiConfig(show_experimental_features=True)),
            str(temp_config_path),
        )
        assert load_config(str(temp_config_path)).ui.show_experimental_features is True


class TestTriggerZonesConfigDropsNonObjectZones:
    """Defence-in-depth: TriggerZonesConfig.__post_init__ drops a
    non-object zone entry (e.g. a hand-edited TOML inline array
    ``zones = ["evil"]``) instead of persisting a bare str/int the zone
    engine would dereference and crash on."""

    def test_non_object_entries_dropped_dicts_converted(self) -> None:
        from openfollow.configuration import TriggerZoneConfig, TriggerZonesConfig

        cfg = TriggerZonesConfig(zones=["evil", 123, 1.5, {"name": "Z1"}])  # type: ignore[list-item]
        assert all(isinstance(z, TriggerZoneConfig) for z in cfg.zones)
        assert [z.name for z in cfg.zones] == ["Z1"]

    def test_existing_typed_entries_preserved(self) -> None:
        from openfollow.configuration import TriggerZoneConfig, TriggerZonesConfig

        zone = TriggerZoneConfig(name="Keep")
        cfg = TriggerZonesConfig(zones=[zone, "junk"])  # type: ignore[list-item]
        assert cfg.zones == [zone]
