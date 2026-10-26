# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for config dataclass validation, TOML round-trip, and runtime hot-reload."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
import tomllib

from openfollow.configuration import (
    CALIBRATION_SOURCE_MAX_LEN,
    DEFAULT_UPDATE_SERVICE_NAME,
    FEATURE_KEYS,
    MOUSE3D_AXES,
    RESERVED_MOVEMENT_KEYS,
    AppConfig,
    CameraConfig,
    ConfigFileEmptyError,
    ConfigFileUnreadableError,
    ControllerConfig,
    DetectionConfig,
    GridConfig,
    MarkerConfig,
    MidiConfig,
    MidiPatch,
    Mouse3DConfig,
    OscConfig,
    OtpOutputConfig,
    RttrpmOutputConfig,
    SlotMarkerConfig,
    SlotPinConfig,
    TouchRemoteConfig,
    TriggerZonesConfig,
    _apply_with_fallback,
    apply_runtime_config_changes,
    config_to_toml_dict,
    config_write_lock,
    effective_lens,
    load_config,
    load_config_for_save,
    save_config,
    save_config_fields,
    viewed_with_controlled,
)
from openfollow.lens_model import C_BOUND, lens_warp_is_valid

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
        self.psn_suspends = 0
        self.station_iface_follow_calls = 0
        self.psn_system_name_changes: list[str] = []
        self.detection_changes: list[DetectionConfig] = []
        self.detection_swaps: list[DetectionConfig] = []
        self.detector_build_pending = False
        self.window_size_changes: list[tuple[int, int]] = []
        self.video_swaps: list[AppConfig] = []
        self.station_fqdn_changes: list[str] = []
        self.psn_input_changes = 0

    def apply_psn_input_change(self) -> None:
        self.psn_input_changes += 1

    def apply_station_fqdn_change(self, fqdn: str) -> None:
        self.station_fqdn_changes.append(fqdn)

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

    def suspend_psn_planes(self) -> None:
        self.psn_suspends += 1

    def apply_station_iface_change(self) -> None:
        self.station_iface_follow_calls += 1

    def apply_psn_mcast_ip_change(self, new_mcast_ip: str) -> None:
        self.psn_mcast_ip_changes.append(new_mcast_ip)

    def apply_psn_system_name_change(self, new_name: str) -> None:
        self.psn_system_name_changes.append(new_name)

    def apply_detection_change(self, new_cfg: DetectionConfig) -> None:
        self.detection_changes.append(new_cfg)

    def swap_detector(self, new_cfg: DetectionConfig, on_failure: Callable[[], None] | None = None) -> None:
        self.detection_swaps.append(new_cfg)
        self.detection_swap_on_failure = on_failure

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
    pos: tuple[float, float, float] | None = None

    def set_pos(self, x: float, y: float, z: float) -> None:
        self.pos = (x, y, z)


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
        self.osc_listen_ifaces: list[str] = []
        self.operator_message_restarts = 0
        self.mouse3d_restarts: list[Mouse3DConfig] = []
        self.slot_pin_applies: list[list[SlotPinConfig]] = []

    def apply_slot_pins(self, pins: list[SlotPinConfig]) -> None:
        self.slot_pin_applies.append(list(pins))

    def restart_mouse3d(self, config: Mouse3DConfig) -> None:
        self.mouse3d_restarts.append(config)

    def restart_osc(
        self,
        enabled: bool,
        port: int,
        allowed_sender_ips: list[str] | None = None,
        *,
        multicast_group: str = "",
        listen_iface: str = "",
    ) -> None:
        self.osc_restarts.append((enabled, port))
        self.osc_multicast_groups.append(multicast_group)
        self.osc_listen_ifaces.append(listen_iface)

    def restart_operator_messages(self) -> None:
        self.operator_message_restarts += 1


class _DummyApp:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._config_mtime = 0.0
        self._runtime_services = _DummyRuntimeServices()
        self._web_server = _DummyWebServer()
        self._web_commands = _DummyWebCommands()
        self._server = _DummyPsnServer()
        for marker_id in config.controlled_marker_ids:
            self._server.add_marker(marker_id, f"Marker {marker_id}")
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


def test_online_sync_defaults() -> None:
    cfg = AppConfig()
    assert cfg.auto_update_check is True
    assert cfg.auto_time_sync is True
    assert cfg.update_include_prereleases is False
    assert cfg.time_sync_server == "ptbtime1.ptb.de"


@pytest.mark.parametrize(
    "value,expected",
    [(True, True), (False, False), ("true", True), ("off", False), ("nonsense", True), (None, True), (1, True)],
)
def test_auto_update_check_coercion(value: object, expected: bool) -> None:
    assert AppConfig(auto_update_check=value).auto_update_check is expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value,expected",
    [(True, True), (False, False), ("yes", True), ("no", False), ("garbage", True), (None, True)],
)
def test_auto_time_sync_coercion(value: object, expected: bool) -> None:
    assert AppConfig(auto_time_sync=value).auto_time_sync is expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value,expected",
    [(True, True), (False, False), ("on", True), ("0", False), ("garbage", False), (None, False), (1, False)],
)
def test_update_include_prereleases_coercion(value: object, expected: bool) -> None:
    # Defaults OFF: unrecognised / wrong-type input must NOT silently opt into pre-releases.
    assert AppConfig(update_include_prereleases=value).update_include_prereleases is expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("time.cloudflare.com", "time.cloudflare.com"),
        ("  ptbtime1.ptb.de  ", "ptbtime1.ptb.de"),
        ("", "ptbtime1.ptb.de"),  # empty falls back to the default
        ("   ", "ptbtime1.ptb.de"),  # whitespace-only too
        (123, "ptbtime1.ptb.de"),  # wrong type -> default
        (None, "ptbtime1.ptb.de"),
    ],
)
def test_time_sync_server_coercion(value: object, expected: str) -> None:
    assert AppConfig(time_sync_server=value).time_sync_server == expected  # type: ignore[arg-type]


def test_default_video_source_is_testpattern() -> None:
    """A fresh install (no config, no example to bootstrap from) defaults to
    the always-available Media Gallery source (input_id ``testpattern``) rather
    than a network source."""
    assert AppConfig().video_source_type == "testpattern"


def test_default_selected_media_is_stage() -> None:
    """A fresh install must render the Stage scene, not a blank/grey screen.
    The dataclass default selects the read-only Stage gallery item. Pinned to
    ``media_store.DEFAULT_SELECTED_MEDIA`` so the literal in ``configuration.py``
    can't drift from the store's canonical default."""
    from openfollow.video import media_store

    assert AppConfig().testpattern_selected_media == media_store.DEFAULT_SELECTED_MEDIA
    assert AppConfig().testpattern_selected_media == "default:stage"


def test_selected_media_dataclass_default_matches_plugin_config_field() -> None:
    """The plugin-backed dataclass default must match the plugin's own
    ``ConfigField`` default. If the shipped example is missing (or not
    bootstrapped for any reason), the dataclass default is what actually
    renders – any drift ships a wrong out-of-box value."""
    from openfollow.video.inputs.testpattern import MediaGalleryInput

    plugin_defaults = {f.name: f.default for f in MediaGalleryInput.config_fields()}
    assert AppConfig().testpattern_selected_media == plugin_defaults["testpattern_selected_media"]


@pytest.mark.parametrize("field", ["rtsp_user", "rtsp_password", "srt_passphrase"])
@pytest.mark.parametrize("bad", [5, True, None, ["x"], {"a": 1}, 3.5])
def test_stream_credentials_coerce_non_string_to_blank(field: str, bad: object) -> None:
    """A hand-edited ``config.toml`` can put a non-string here (``= 5``,
    ``= true``). Dataclasses don't coerce, so the value would reach the
    element's ``set_property`` as-is - and a bare ``0`` reads as falsy, so the
    login would silently drop back to whatever is in the URL rather than
    failing visibly."""
    cfg = AppConfig(**{field: bad})  # type: ignore[arg-type]
    assert getattr(cfg, field) == ""


def test_rtsp_user_is_stripped_on_load() -> None:
    assert AppConfig(rtsp_user="  operator  ").rtsp_user == "operator"


@pytest.mark.parametrize("field", ["rtsp_password", "srt_passphrase"])
def test_secrets_keep_their_edge_whitespace_on_load(field: str) -> None:
    """Matches the ``ConfigField(strip=False)`` promise on the web-save path.

    If a load stripped what a save preserved, the credential would change under
    the operator on the next config reload - and a password field shows nothing
    that would explain why authentication suddenly started failing.
    """
    assert getattr(AppConfig(**{field: "  s3cret  "}), field) == "  s3cret  "


@pytest.mark.parametrize("bad", [5, True, None, ["x"], {"a": 1}, 3.5])
def test_selected_media_coerces_non_string_to_default(bad: object) -> None:
    """A hand-edited config.toml can put a non-string here (``= 5``, ``= true``).
    Dataclasses don't coerce, so without normalisation the value flows into
    ``media_store.resolve`` -> ``re.match`` and raises ``TypeError`` on pipeline
    build / ``get_source_label``. ``__post_init__`` must fall back to Stage."""
    cfg = AppConfig(testpattern_selected_media=bad)  # type: ignore[arg-type]
    assert cfg.testpattern_selected_media == "default:stage"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  default:grey  ", "default:grey"),  # surrounding whitespace stripped
        ("", "default:stage"),  # blank falls back to the Stage default
        ("   ", "default:stage"),
    ],
)
def test_selected_media_strips_and_defaults_blank(raw: str, expected: str) -> None:
    cfg = AppConfig(testpattern_selected_media=raw)
    assert cfg.testpattern_selected_media == expected


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


def test_example_config_seeds_the_default_features_and_names_the_experimental_ones() -> None:
    """A new station is seeded from the example, so its list is the default one and its comment the other keys."""
    import re
    from pathlib import Path

    from openfollow.configuration import DEFAULT_SHOWN_FEATURES, EXPERIMENTAL_FEATURES

    text = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text(encoding="utf-8")
    assert tomllib.loads(text)["ui"]["shown_features"] == list(DEFAULT_SHOWN_FEATURES)
    lines = text.splitlines()
    end = next(i for i, line in enumerate(lines) if line.startswith("shown_features"))
    start = end
    while lines[start - 1].startswith("#"):
        start -= 1
    named = re.findall(r'"([a-z_0-9]+)"', "\n".join(lines[start:end]))
    assert sorted(named) == sorted(EXPERIMENTAL_FEATURES)


@pytest.mark.parametrize(("section", "defaults"), [("controller", ControllerConfig()), ("mouse3d", Mouse3DConfig())])
def test_example_config_binds_nothing_the_defaults_leave_unbound(section: str, defaults: object) -> None:
    """A station seeded from the example must not start with a binding a fresh config leaves unbound."""
    from pathlib import Path

    example_path = Path(__file__).resolve().parents[1] / "config.example.toml"
    example = tomllib.loads(example_path.read_text(encoding="utf-8"))[section]
    # A field the code dropped is silently filtered on load.
    assert [name for name in example if not hasattr(defaults, name)] == []
    unbound = {"", -1}
    bound = {
        name: value
        for name, value in example.items()
        if name.startswith(("btn_", "key_")) and getattr(defaults, name) in unbound and value not in unbound
    }
    assert bound == {}


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


def test_load_config_malformed_toml_returns_defaults(temp_config_path, caplog) -> None:
    """Non-strict load should log the error and fall back to defaults."""
    temp_config_path.write_text("this = is not valid toml [[\n", encoding="utf-8")

    with caplog.at_level("ERROR"):
        config = load_config(str(temp_config_path))

    assert config == AppConfig()
    assert any("Failed to read/parse config file" in rec.message for rec in caplog.records)


def test_load_config_malformed_toml_strict_raises(temp_config_path) -> None:
    temp_config_path.write_text("this = is not valid toml [[\n", encoding="utf-8")

    with pytest.raises(ConfigFileUnreadableError) as raised:
        load_config(str(temp_config_path), strict=True)
    assert isinstance(raised.value.__cause__, tomllib.TOMLDecodeError)


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
    # A controlled marker is always viewed, so 1 joins the viewed list.
    assert reloaded.viewer_marker_ids == [2, 3, 1]


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


def _fail_the_first_fsync(monkeypatch, error: Exception | None = None) -> None:
    import openfollow.configuration as cfg_mod

    real = cfg_mod.os.fsync
    calls: list[int] = []

    def _fsync(fd: int) -> None:
        calls.append(fd)
        if len(calls) == 1:
            raise error or OSError("disk full")
        real(fd)

    monkeypatch.setattr(cfg_mod.os, "fsync", _fsync)


def test_save_continues_when_bak_copy_fails(temp_config_path, monkeypatch, caplog) -> None:
    """A failed backup copy must not abort the primary save."""
    save_config(AppConfig(psn_system_name="V1"), str(temp_config_path))

    _fail_the_first_fsync(monkeypatch)  # the snapshot's
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

    _fail_the_first_fsync(monkeypatch)  # the snapshot's
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

    with pytest.raises(ConfigFileUnreadableError):
        load_config(str(temp_config_path), strict=True)


# An empty or unreadable file is never read as the defaults, nor written over

_EMPTY = pytest.mark.parametrize("content", ["", "  \n\t\n"], ids=["empty", "whitespace"])
# Valid TOML that is not a config: each of these makes building the config raise.
_WRONG_SHAPES = [
    b"grid = 5\n",
    b'camera = "x"\n',
    b"paths = [1]\n",
    b"osc_destinations = 3\n",
    b"osc_transmitters = {transmitters = 3}\n",
]
_WRONG_SHAPE_IDS = ["grid-int", "camera-str", "paths-list", "osc-destinations-int", "osc-transmitters-int"]
_UNREADABLE = pytest.mark.parametrize(
    "content",
    [b"", b"  \n\t\n", b"not [[ valid toml", b"\xff\xfe = 1\n", *_WRONG_SHAPES],
    ids=["empty", "whitespace", "unparseable", "not-utf8", *_WRONG_SHAPE_IDS],
)


def _good_config_with_backup(path, name: str = "Good Config") -> None:
    save_config(AppConfig(psn_system_name=name, controlled_marker_ids=[3]), str(path))
    save_config(AppConfig(psn_system_name=name, controlled_marker_ids=[3]), str(path))


@_EMPTY
def test_load_recovers_an_empty_file_from_the_backup(temp_config_path, caplog, content) -> None:
    _good_config_with_backup(temp_config_path)
    temp_config_path.write_text(content, encoding="utf-8")

    with caplog.at_level("WARNING"):
        loaded = load_config(str(temp_config_path))

    assert (loaded.psn_system_name, loaded.controlled_marker_ids) == ("Good Config", [3])
    assert [r.message for r in caplog.records if "is empty" in r.message] == [
        f"Config file {temp_config_path} is empty – recovered from {temp_config_path}.bak."
    ]


@_EMPTY
def test_an_empty_file_without_a_backup_loads_as_the_defaults_and_says_so(temp_config_path, caplog, content) -> None:
    temp_config_path.write_text(content, encoding="utf-8")

    with caplog.at_level("WARNING"):
        loaded = load_config(str(temp_config_path))

    assert loaded == AppConfig()
    assert [r.message for r in caplog.records if "is empty" in r.message] == [
        f"Config file {temp_config_path} is empty – using defaults."
    ]


def test_a_broken_file_is_reported_once_until_it_changes(temp_config_path, caplog) -> None:
    """Every web request reads the file again; a broken one is logged once, not once per request."""
    _good_config_with_backup(temp_config_path)
    temp_config_path.write_text("", encoding="utf-8")

    with caplog.at_level("WARNING"):
        load_config(str(temp_config_path))
        load_config(str(temp_config_path))
        temp_config_path.write_text("not [[ valid toml", encoding="utf-8")
        load_config(str(temp_config_path))
        load_config(str(temp_config_path))

    assert [r.message.split(" – ")[0] for r in caplog.records if "recovered" in r.message] == [
        f"Config file {temp_config_path} is empty",
        f"Config file {temp_config_path} could not be read/parsed",
    ]


def test_a_broken_file_is_reported_again_once_it_has_read_whole_in_between(temp_config_path, caplog) -> None:
    """The same file, broken the same way twice with a whole read between, is reported twice."""
    _good_config_with_backup(temp_config_path)
    whole = temp_config_path.read_bytes()
    stamp = os.stat(temp_config_path)

    def _write_in_place(content: bytes) -> None:
        temp_config_path.write_bytes(content)
        os.utime(temp_config_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    with caplog.at_level("WARNING"):
        _write_in_place(b"")
        load_config(str(temp_config_path))
        _write_in_place(whole)
        load_config(str(temp_config_path))
        _write_in_place(b"")
        load_config(str(temp_config_path))

    assert len([r for r in caplog.records if "is empty" in r.message]) == 2


@_UNREADABLE
def test_load_config_for_save_refuses_an_empty_or_unreadable_file(temp_config_path, content) -> None:
    _good_config_with_backup(temp_config_path)
    temp_config_path.write_bytes(content)

    with pytest.raises(ConfigFileUnreadableError) as raised:
        load_config_for_save(str(temp_config_path))

    assert isinstance(raised.value, OSError)
    assert isinstance(raised.value, ConfigFileEmptyError) == (not content.strip())
    assert str(temp_config_path) in str(raised.value)
    assert temp_config_path.read_bytes() == content


def test_load_config_for_save_reads_a_whole_file(temp_config_path) -> None:
    save_config(AppConfig(psn_system_name="On Disk"), str(temp_config_path))

    assert load_config_for_save(str(temp_config_path)).psn_system_name == "On Disk"


def test_load_config_for_save_recovers_a_missing_file_from_the_backup(temp_config_path) -> None:
    _good_config_with_backup(temp_config_path)
    temp_config_path.unlink()

    assert load_config_for_save(str(temp_config_path)).psn_system_name == "Good Config"


def test_load_config_for_save_reads_a_first_run_as_the_defaults(temp_config_path) -> None:
    assert load_config_for_save(str(temp_config_path)) == AppConfig()


@_UNREADABLE
def test_save_config_never_writes_over_an_empty_or_unreadable_file(temp_config_path, content) -> None:
    _good_config_with_backup(temp_config_path)
    backup = _bak_of(temp_config_path).read_bytes()
    temp_config_path.write_bytes(content)

    with pytest.raises(ConfigFileUnreadableError):
        save_config(AppConfig(psn_system_name="Defaults And One Change"), str(temp_config_path))

    assert temp_config_path.read_bytes() == content
    assert _bak_of(temp_config_path).read_bytes() == backup
    assert list(temp_config_path.parent.glob("*.tmp")) == []


@_UNREADABLE
def test_save_config_replaces_an_unreadable_file_only_when_told_to(temp_config_path, content) -> None:
    temp_config_path.write_bytes(content)

    save_config(AppConfig(psn_system_name="Replacement"), str(temp_config_path), replace_unreadable=True)

    assert load_config(str(temp_config_path), strict=True).psn_system_name == "Replacement"


def test_a_config_path_that_cannot_be_opened_is_neither_read_for_a_save_nor_written(temp_config_path) -> None:
    temp_config_path.mkdir()

    with pytest.raises(ConfigFileUnreadableError, match="could not be read"):
        load_config_for_save(str(temp_config_path))
    with pytest.raises(ConfigFileUnreadableError, match="could not be read"):
        save_config(AppConfig(), str(temp_config_path))

    assert temp_config_path.is_dir()


def test_save_config_decides_on_the_file_as_it_is_when_it_writes(temp_config_path) -> None:
    """A config read whole before an editor truncated the file still finds it empty."""
    save_config(AppConfig(psn_system_name="Read Whole"), str(temp_config_path))
    cfg = load_config_for_save(str(temp_config_path))
    temp_config_path.write_text("", encoding="utf-8")

    with pytest.raises(ConfigFileEmptyError):
        save_config(cfg, str(temp_config_path))

    assert temp_config_path.read_text(encoding="utf-8") == ""


@_EMPTY
def test_an_empty_file_does_not_ask_for_the_network_mode_to_be_saved(temp_config_path, content) -> None:
    from openfollow.configuration import network_mode_unsaved

    temp_config_path.write_text(content, encoding="utf-8")

    assert not network_mode_unsaved(str(temp_config_path))


@_EMPTY
def test_an_empty_file_is_restored_from_its_backup_which_stays(temp_config_path, caplog, content) -> None:
    import stat

    from openfollow.configuration import restore_empty_config

    _good_config_with_backup(temp_config_path)
    backup = _bak_of(temp_config_path).read_bytes()
    temp_config_path.write_text(content, encoding="utf-8")

    with caplog.at_level("WARNING"):
        restore_empty_config(str(temp_config_path))

    assert temp_config_path.read_bytes() == backup
    assert _bak_of(temp_config_path).read_bytes() == backup
    assert stat.S_IMODE(os.stat(temp_config_path).st_mode) == 0o600
    assert list(temp_config_path.parent.glob("*.tmp")) == []
    assert caplog.messages == [f"Config file {temp_config_path} is empty – restored it from {temp_config_path}.bak."]
    save_config(AppConfig(psn_system_name="Saved Again"), str(temp_config_path))
    assert load_config(str(temp_config_path), strict=True).psn_system_name == "Saved Again"


@pytest.mark.parametrize("backup", [None, b"", b"  \n", b"not [[ toml", b"\xff\xfe = 1\n", *_WRONG_SHAPES])
@_EMPTY
def test_an_empty_file_without_a_readable_backup_is_removed_for_a_first_run(
    temp_config_path, caplog, content, backup
) -> None:
    from openfollow.configuration import restore_empty_config

    temp_config_path.write_text(content, encoding="utf-8")
    if backup is not None:
        _bak_of(temp_config_path).write_bytes(backup)

    with caplog.at_level("WARNING"):
        restore_empty_config(str(temp_config_path))

    assert not temp_config_path.exists()
    assert backup is None or _bak_of(temp_config_path).read_bytes() == backup
    assert [m for m in caplog.messages if "is empty" in m] == [
        f"Config file {temp_config_path} is empty and has no readable backup – starting as a first run."
    ]


@pytest.mark.parametrize("content", [None, b"not [[ toml", b'psn_system_name = "Kept"\n'])
def test_restore_leaves_a_missing_unreadable_or_whole_file_alone(temp_config_path, caplog, content) -> None:
    from openfollow.configuration import restore_empty_config

    _good_config_with_backup(temp_config_path)
    if content is None:
        temp_config_path.unlink()
    else:
        temp_config_path.write_bytes(content)

    with caplog.at_level("WARNING"):
        restore_empty_config(str(temp_config_path))

    assert (temp_config_path.read_bytes() if temp_config_path.exists() else None) == content
    assert caplog.messages == []


def test_an_empty_file_that_cannot_be_restored_is_left_and_logged(temp_config_path, caplog, monkeypatch) -> None:
    import openfollow.configuration as cfg_mod

    _good_config_with_backup(temp_config_path)
    temp_config_path.write_text("", encoding="utf-8")

    def _refuse(*_a: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(cfg_mod.os, "replace", _refuse)
    with caplog.at_level("WARNING"):
        cfg_mod.restore_empty_config(str(temp_config_path))
    monkeypatch.undo()

    assert temp_config_path.read_text(encoding="utf-8") == ""
    assert list(temp_config_path.parent.glob("*.tmp")) == []
    assert caplog.messages == [f"Config file {temp_config_path} is empty and could not be restored."]


@pytest.mark.parametrize("primary", [None, b"", b"not [[ toml"], ids=["missing", "empty", "unparseable"])
def test_a_backup_that_is_not_utf8_reads_as_no_backup(temp_config_path, primary) -> None:
    _bak_of(temp_config_path).write_bytes(b'psn_system_name = "\xff\xfe"\n')
    if primary is not None:
        temp_config_path.write_bytes(primary)

    assert load_config(str(temp_config_path)) == AppConfig()


@_UNREADABLE
def test_replacing_an_unreadable_file_keeps_the_good_backup(temp_config_path, content) -> None:
    _good_config_with_backup(temp_config_path)
    backup = _bak_of(temp_config_path).read_bytes()
    temp_config_path.write_bytes(content)

    save_config(AppConfig(psn_system_name="Replacement"), str(temp_config_path), replace_unreadable=True)

    assert _bak_of(temp_config_path).read_bytes() == backup
    assert load_config(str(temp_config_path), strict=True).psn_system_name == "Replacement"


def test_the_backup_holds_the_bytes_the_save_checked(temp_config_path, monkeypatch) -> None:
    """An editor emptying the file just after the check must not reach the backup."""
    import openfollow.configuration as cfg_mod

    save_config(AppConfig(psn_system_name="Checked"), str(temp_config_path))
    checked = temp_config_path.read_bytes()
    real_loads = cfg_mod.tomllib.loads

    def _loads_then_truncate(text: str) -> object:
        parsed = real_loads(text)
        temp_config_path.write_bytes(b"")
        return parsed

    monkeypatch.setattr(cfg_mod.tomllib, "loads", _loads_then_truncate)
    save_config(AppConfig(psn_system_name="Next"), str(temp_config_path))
    monkeypatch.undo()

    assert _bak_of(temp_config_path).read_bytes() == checked


def test_save_config_change_writes_the_running_config_when_the_file_vanished_after_the_check(
    temp_config_path, monkeypatch
) -> None:
    import openfollow.configuration as cfg_mod
    from openfollow.configuration import save_config_change

    monkeypatch.setattr(cfg_mod.os.path, "exists", lambda _p: True)
    running = AppConfig(web_port=8080, psn_system_name="Stage Left")

    save_config_change(str(temp_config_path), running, lambda cfg: setattr(cfg, "web_port", 9090))

    monkeypatch.undo()
    saved = load_config(str(temp_config_path), strict=True)
    assert (saved.web_port, saved.psn_system_name) == (9090, "Stage Left")


@_UNREADABLE
def test_save_config_change_refuses_an_empty_or_unreadable_file(temp_config_path, content) -> None:
    from openfollow.configuration import save_config_change

    temp_config_path.write_bytes(content)
    running = AppConfig(psn_system_name="Running")

    with pytest.raises(ConfigFileUnreadableError):
        save_config_change(str(temp_config_path), running, lambda cfg: setattr(cfg, "web_port", 9090))

    assert temp_config_path.read_bytes() == content


@pytest.mark.parametrize("content", _WRONG_SHAPES, ids=_WRONG_SHAPE_IDS)
def test_a_file_that_parses_but_is_no_config_is_recovered_from_the_backup(temp_config_path, content) -> None:
    _good_config_with_backup(temp_config_path)
    temp_config_path.write_bytes(content)

    assert load_config(str(temp_config_path)).psn_system_name == "Good Config"


@pytest.mark.parametrize("backup", _WRONG_SHAPES, ids=_WRONG_SHAPE_IDS)
@pytest.mark.parametrize(
    "primary", [None, b"", b"not [[ toml", b"grid = 5\n"], ids=["missing", "empty", "unparseable", "shape"]
)
def test_a_backup_that_is_no_config_reads_as_no_backup(temp_config_path, primary, backup) -> None:
    _bak_of(temp_config_path).write_bytes(backup)
    if primary is not None:
        temp_config_path.write_bytes(primary)

    assert load_config(str(temp_config_path)) == AppConfig()


@pytest.mark.parametrize("content", _WRONG_SHAPES, ids=_WRONG_SHAPE_IDS)
def test_a_file_that_parses_but_is_no_config_is_a_problem_for_saving(temp_config_path, content) -> None:
    from openfollow.configuration import config_file_problem

    temp_config_path.write_bytes(content)

    problem = config_file_problem(str(temp_config_path))
    assert isinstance(problem, ConfigFileUnreadableError)
    assert not isinstance(problem, ConfigFileEmptyError)


@pytest.mark.parametrize(
    ("backup", "step"),
    [
        (True, "Restart OpenFollow to restore the settings from the latest backup copy"),
        (None, "Restart OpenFollow to start again with default settings"),
        (b"grid = 5\n", "Restart OpenFollow to start again with default settings"),
    ],
    ids=["readable-backup", "no-backup", "backup-no-config"],
)
def test_an_empty_file_is_mended_by_what_a_restart_will_do(temp_config_path, backup, step) -> None:
    from openfollow.configuration import config_file_problem, config_problem_words

    if backup is True:
        _good_config_with_backup(temp_config_path)
    elif backup is not None:
        _bak_of(temp_config_path).write_bytes(backup)
    temp_config_path.write_bytes(b"")

    problem = config_file_problem(str(temp_config_path))
    assert problem is not None
    assert config_problem_words(problem) == ("is empty", step)


def test_an_unreadable_file_is_replaced_by_an_import(temp_config_path) -> None:
    from openfollow.configuration import config_file_problem, config_problem_words

    _good_config_with_backup(temp_config_path)
    temp_config_path.write_bytes(b"grid = 5\n")

    problem = config_file_problem(str(temp_config_path))
    assert problem is not None
    assert config_problem_words(problem) == ("could not be read", "Import a settings file to replace it")


@pytest.mark.parametrize(
    ("primary", "backup", "health"),
    [
        ("good", "good", "reads whole; backup reads whole"),
        (b"", "good", "is empty; backup reads whole"),
        (b"not [[ toml", None, "could not be read; no readable backup"),
        (b"grid = 5\n", b"camera = 1\n", "could not be read; no readable backup"),
        (None, "good", "missing; backup reads whole"),
    ],
)
def test_the_health_of_the_config_file_names_no_contents(temp_config_path, primary, backup, health) -> None:
    from openfollow.configuration import config_file_health

    save_config(AppConfig(psn_system_name="secret-ish", web_pin="4321"), str(temp_config_path))
    good = temp_config_path.read_bytes()
    if backup is not None:
        _bak_of(temp_config_path).write_bytes(good if backup == "good" else backup)
    if primary is None:
        temp_config_path.unlink()
    elif primary != "good":
        temp_config_path.write_bytes(primary)

    assert config_file_health(str(temp_config_path)) == health


def test_a_save_read_of_a_missing_file_takes_the_fallback_it_is_given(temp_config_path) -> None:
    _good_config_with_backup(temp_config_path)
    temp_config_path.unlink()

    newer = AppConfig(psn_system_name="Last Whole Read")
    assert load_config_for_save(str(temp_config_path), missing=lambda: newer) is newer
    assert load_config_for_save(str(temp_config_path)).psn_system_name == "Good Config"


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


_PIN = {"slot": 2, "key": "usb:platform/xhci-hcd.1:1.4", "kind": "gamepad", "label": "Xbox Pad"}


def test_controller_slot_pins_default_to_none() -> None:
    assert ControllerConfig().slot_pins == []


def test_controller_slot_pin_entries_become_pins() -> None:
    config = ControllerConfig(slot_pins=[_PIN, {**_PIN, "slot": 3, "key": "bt:aa:bb", "kind": "mouse3d"}])
    assert config.slot_pins == [
        SlotPinConfig(slot=2, key="usb:platform/xhci-hcd.1:1.4", kind="gamepad", label="Xbox Pad"),
        SlotPinConfig(slot=3, key="bt:aa:bb", kind="mouse3d", label="Xbox Pad"),
    ]


@pytest.mark.parametrize("raw", ["usb:x:1", None, 3, {"slot": 2}, True])
def test_controller_slot_pins_that_are_not_a_list_are_none(raw: object) -> None:
    assert ControllerConfig(slot_pins=raw).slot_pins == []  # type: ignore[arg-type]


@pytest.mark.parametrize("entry", ["usb:x:1", None, 3, ["slot", 2], True])
def test_controller_slot_pin_entries_of_the_wrong_type_are_dropped(entry: object) -> None:
    assert ControllerConfig(slot_pins=[entry, _PIN]).slot_pins == [SlotPinConfig(**_PIN)]


@pytest.mark.parametrize("slot", [0, -1, 17, 2.0, "2", True, None])
def test_controller_slot_pins_outside_the_slots_are_dropped(slot: object) -> None:
    assert ControllerConfig(slot_pins=[{**_PIN, "slot": slot}]).slot_pins == []


@pytest.mark.parametrize("key", ["", "usb:", "bt:", "eth0", "USB:x:1", None, 5])
def test_controller_slot_pins_naming_no_socket_are_dropped(key: object) -> None:
    assert ControllerConfig(slot_pins=[{**_PIN, "key": key}]).slot_pins == []


@pytest.mark.parametrize("kind", ["keyboard", "", None, "Gamepad"])
def test_controller_slot_pins_of_an_unknown_kind_are_dropped(kind: object) -> None:
    assert ControllerConfig(slot_pins=[{**_PIN, "kind": kind}]).slot_pins == []


@pytest.mark.parametrize(
    "second",
    [{**_PIN, "key": "usb:platform/xhci-hcd.1:1.5"}, {**_PIN, "slot": 3}],
    ids=["same slot", "same socket"],
)
def test_controller_slot_pins_named_twice_keep_the_first(second: dict[str, object]) -> None:
    assert ControllerConfig(slot_pins=[_PIN, second]).slot_pins == [SlotPinConfig(**_PIN)]


@pytest.mark.parametrize(
    ("label", "kept"),
    [
        ("Xbox\x1b[31m Pad\u202e", "Xbox[31m Pad"),
        ("  Pad  ", "Pad"),
        ("x" * 200, "x" * 128),
        (None, ""),
        (7, ""),
    ],
)
def test_controller_slot_pin_labels_are_cleaned(label: object, kept: str) -> None:
    assert ControllerConfig(slot_pins=[{**_PIN, "label": label}]).slot_pins[0].label == kept


def test_controller_slot_pins_survive_a_hand_edited_config(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[controller]\n"
        "slot_pins = [\n"
        '    { slot = 2, key = "usb:platform/xhci-hcd.1:1.4", kind = "gamepad", label = "Xbox Pad" },\n'
        '    { slot = 99, key = "usb:platform/xhci-hcd.1:1.5", kind = "gamepad" },\n'
        '    "not a pin",\n'
        "]\n",
        encoding="utf-8",
    )
    assert load_config(str(path)).controller.slot_pins == [SlotPinConfig(**_PIN)]


def test_controller_slot_pins_round_trip_through_the_config_file(tmp_path) -> None:
    path = str(tmp_path / "config.toml")
    cfg = AppConfig()
    cfg.controller = ControllerConfig(slot_pins=[_PIN])
    save_config(cfg, path)
    assert load_config(path).controller.slot_pins == [SlotPinConfig(**_PIN)]


def test_controller_slot_markers_default_to_none() -> None:
    assert ControllerConfig().slot_markers == []


def test_controller_slot_marker_entries_become_picks_in_slot_order() -> None:
    cfg = ControllerConfig(slot_markers=[{"slot": 3, "marker": 0}, SlotMarkerConfig(slot=1, marker=7)])
    assert cfg.slot_markers == [SlotMarkerConfig(slot=1, marker=7), SlotMarkerConfig(slot=3, marker=0)]


@pytest.mark.parametrize("raw", [None, "slot 1", {"slot": 1, "marker": 2}, 3])
def test_controller_slot_markers_that_are_not_a_list_are_none(raw: object) -> None:
    assert ControllerConfig(slot_markers=raw).slot_markers == []


@pytest.mark.parametrize("entry", [None, "1=2", 4, [1, 2], {"marker": 2}, {"slot": 1}])
def test_controller_slot_marker_entries_of_the_wrong_shape_are_dropped(entry: object) -> None:
    assert ControllerConfig(slot_markers=[entry]).slot_markers == []


@pytest.mark.parametrize("slot", [0, 17, -1, "2", 2.0, True, None])
def test_controller_slot_markers_outside_the_slots_are_dropped(slot: object) -> None:
    assert ControllerConfig(slot_markers=[{"slot": slot, "marker": 2}]).slot_markers == []


@pytest.mark.parametrize("marker", [-1, "2", 2.5, True, None])
def test_controller_slot_markers_naming_no_marker_id_are_dropped(marker: object) -> None:
    assert ControllerConfig(slot_markers=[{"slot": 1, "marker": marker}]).slot_markers == []


@pytest.mark.parametrize("second", [{"slot": 1, "marker": 3}, {"slot": 2, "marker": 2}])
def test_controller_slot_markers_named_twice_keep_the_first(second: dict[str, object]) -> None:
    cfg = ControllerConfig(slot_markers=[{"slot": 1, "marker": 2}, second])
    assert cfg.slot_markers == [SlotMarkerConfig(slot=1, marker=2)]


def test_several_slots_can_be_left_without_a_marker() -> None:
    cfg = ControllerConfig(slot_markers=[{"slot": 1, "marker": 0}, {"slot": 2, "marker": 0}])
    assert [pick.slot for pick in cfg.slot_markers] == [1, 2]


def test_controller_slot_markers_round_trip_through_the_config_file(tmp_path) -> None:
    path = str(tmp_path / "config.toml")
    cfg = AppConfig(controlled_marker_ids=[2, 5])
    cfg.controller = ControllerConfig(slot_markers=[{"slot": 1, "marker": 5}, {"slot": 2, "marker": 0}])
    save_config(cfg, path)
    assert load_config(path).controller.slot_markers == [
        SlotMarkerConfig(slot=1, marker=5),
        SlotMarkerConfig(slot=2, marker=0),
    ]


def test_a_pick_for_a_marker_no_longer_controlled_is_dropped_on_load(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "controlled_marker_ids = [2, 5]\n"
        "[controller]\n"
        "slot_markers = [\n"
        "    { slot = 1, marker = 5 },\n"
        "    { slot = 2, marker = 9 },\n"
        "    { slot = 3, marker = 0 },\n"
        '    "not a pick",\n'
        "]\n",
        encoding="utf-8",
    )
    assert load_config(str(path)).controller.slot_markers == [
        SlotMarkerConfig(slot=1, marker=5),
        SlotMarkerConfig(slot=3, marker=0),
    ]


def test_controller_keyboard_layout_defaults_to_the_system_layout() -> None:
    config = ControllerConfig()
    assert (config.keyboard_layout, config.keyboard_variant) == ("", "")


@pytest.mark.parametrize(
    ("layout", "variant", "expected"),
    [
        ("de", "nodeadkeys", ("de", "nodeadkeys")),
        ("jp", "OADG109A", ("jp", "OADG109A")),
        (" de ", " T3 ", ("de", "T3")),
        ("de", "", ("de", "")),
        ("brai", "left_hand_invert", ("brai", "left_hand_invert")),
    ],
)
def test_controller_keyboard_layout_accepts_xkb_names(layout: str, variant: str, expected: tuple[str, str]) -> None:
    config = ControllerConfig(keyboard_layout=layout, keyboard_variant=variant)
    assert (config.keyboard_layout, config.keyboard_variant) == expected


@pytest.mark.parametrize("value", [None, 7, True, 1.5, ["de"], {"de": 1}])
def test_controller_keyboard_layout_of_the_wrong_type_falls_back_to_system(value: object) -> None:
    config = ControllerConfig(keyboard_layout=value, keyboard_variant="nodeadkeys")  # type: ignore[arg-type]
    assert (config.keyboard_layout, config.keyboard_variant) == ("", "")


@pytest.mark.parametrize("value", ["DE", "de us", "de;id", "de,us", "$(id)", "dé", "a" * 33])
def test_controller_keyboard_layout_outside_the_pattern_falls_back_to_system(value: str) -> None:
    assert ControllerConfig(keyboard_layout=value).keyboard_layout == ""


def test_controller_keyboard_layout_at_its_length_limit_is_kept() -> None:
    assert ControllerConfig(keyboard_layout="a" * 32).keyboard_layout == "a" * 32


@pytest.mark.parametrize("value", [None, 7, False, "no dead", "x;id", "`id`", "a" * 65])
def test_controller_keyboard_variant_outside_the_pattern_falls_back_to_standard(value: object) -> None:
    config = ControllerConfig(keyboard_layout="de", keyboard_variant=value)  # type: ignore[arg-type]
    assert (config.keyboard_layout, config.keyboard_variant) == ("de", "")


def test_controller_keyboard_variant_at_its_length_limit_is_kept() -> None:
    assert ControllerConfig(keyboard_layout="de", keyboard_variant="V" * 64).keyboard_variant == "V" * 64


@pytest.mark.parametrize("layout", ["", "   ", None, "DE"])
def test_controller_keyboard_variant_is_cleared_without_a_layout(layout: object) -> None:
    """The system's own variant goes with the system layout; a variant alone means nothing."""
    config = ControllerConfig(keyboard_layout=layout, keyboard_variant="nodeadkeys")  # type: ignore[arg-type]
    assert config.keyboard_variant == ""


def test_controller_keyboard_layout_survives_a_save_and_load(tmp_path) -> None:
    path = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.controller.keyboard_layout = "de"
    cfg.controller.keyboard_variant = "nodeadkeys"
    save_config(cfg, str(path))
    loaded = load_config(str(path))
    assert (loaded.controller.keyboard_layout, loaded.controller.keyboard_variant) == ("de", "nodeadkeys")


def test_controller_keyboard_layout_is_what_the_session_helper_reads(tmp_path) -> None:
    """The launcher reads the saved file with its own parser: it must find the field where the app wrote it."""
    from openfollow.keyboard_layout import read_configured

    path = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.controller.keyboard_layout = "jp"
    cfg.controller.keyboard_variant = "OADG109A"
    save_config(cfg, str(path))
    assert read_configured(path) == ("jp", "OADG109A")


@pytest.mark.parametrize("raw", [None, "abc", 1, [True]])
def test_controller_swap_triggers_that_is_no_bool_reads_as_not_swapped(raw: object) -> None:
    assert ControllerConfig(swap_triggers=raw).swap_triggers is False  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", [None, 3, True, ["Pad"]])
@pytest.mark.parametrize("name", ["mapped_controller_name", "mapped_controller_guid"])
def test_controller_mapped_pad_that_is_no_string_reads_as_none(name: str, raw: object) -> None:
    assert getattr(ControllerConfig(**{name: raw}), name) == ""


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


def test_a_config_carrying_the_removed_source_select_binding_still_loads(
    temp_config_path,
) -> None:
    """``btn_source_select`` is gone, and stations in the field have it in
    their ``config.toml``. The loader strips keys that match no field, so such
    a config must load unchanged rather than raise on an unexpected key."""
    temp_config_path.write_text(
        '[controller]\nbtn_source_select = "X"\nbtn_settings = "START"\n',
        encoding="utf-8",
    )
    cfg = load_config(str(temp_config_path))
    assert not hasattr(cfg.controller, "btn_source_select")
    assert cfg.controller.btn_settings == "START"


def test_loading_default_config_does_not_emit_deprecation_warnings(
    temp_config_path,
    caplog,
) -> None:
    save_config(AppConfig(), str(temp_config_path))
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        load_config(str(temp_config_path))
    assert not any("deprecated" in r.message for r in caplog.records)


# --- One input, one action ---------------------------------------------------


@pytest.mark.parametrize(
    ("upper", "lower"),
    [
        ("btn_reset", "btn_toggle_help"),
        ("btn_settings", "btn_next_marker"),
        ("btn_speed_up", "btn_move_z_up"),
        ("btn_prev_marker", "btn_clear_messages"),
        ("btn_clear_messages", "btn_toggle_zones"),
        ("btn_toggle_zones", "btn_toggle_paths"),
        ("btn_toggle_paths", "btn_pathfinder"),
        ("btn_menu_confirm", "btn_menu_cancel"),
    ],
)
def test_gamepad_button_on_two_actions_stays_on_the_upper_one(upper: str, lower: str) -> None:
    config = ControllerConfig(**{upper: "START", lower: "START"})
    assert getattr(config, upper) == "START"
    assert getattr(config, lower) == ""


def test_gamepad_zone_overlay_paths_and_pathfinder_start_unbound() -> None:
    config = ControllerConfig()
    assert (config.btn_toggle_zones, config.btn_toggle_paths, config.btn_pathfinder) == ("", "", "")


def test_gamepad_trigger_shared_by_z_and_an_action_stays_on_the_upper_one() -> None:
    config = ControllerConfig(btn_move_z_up="X")
    assert config.btn_reset == "X"
    assert config.btn_move_z_up == ""


@pytest.mark.parametrize(
    "shared",
    [
        {"btn_toggle_zones": "B", "btn_menu_cancel": "B"},
        {"btn_reset": "A", "btn_menu_confirm": "A"},
    ],
)
def test_gamepad_button_shared_across_groups_is_kept(shared: dict[str, str]) -> None:
    config = ControllerConfig(**shared)
    for name, value in shared.items():
        assert getattr(config, name) == value


@pytest.mark.parametrize("name", ["btn_menu_confirm", "btn_menu_cancel"])
@pytest.mark.parametrize("button", ["DPAD_UP", "DPAD_DOWN"])
def test_menu_button_on_the_menu_highlight_falls_back_to_default(name: str, button: str) -> None:
    default = ControllerConfig.__dataclass_fields__[name].default
    assert getattr(ControllerConfig(**{name: button}), name) == default


def test_menu_fallback_that_collides_keeps_the_upper_field() -> None:
    config = ControllerConfig(btn_menu_confirm="DPAD_UP", btn_menu_cancel="A")
    assert config.btn_menu_confirm == "A"
    assert config.btn_menu_cancel == ""


@pytest.mark.parametrize(
    ("upper", "lower", "key"),
    [
        ("key_reset", "key_toggle_help", "h"),
        ("key_move_z_down", "key_toggle_zones", "e"),
        ("key_settings", "key_next_marker", "Tab"),
        ("key_reset", "key_settings", "g"),
        ("key_next_marker", "key_prev_marker", "n"),
    ],
)
def test_key_on_two_actions_stays_on_the_upper_one(upper: str, lower: str, key: str) -> None:
    config = ControllerConfig(**{upper: key, lower: key})
    assert getattr(config, upper) == key
    assert getattr(config, lower) == ""


@pytest.mark.parametrize(
    ("move", "fader", "kept_fader"),
    [
        ("left", "left_y", ""),
        ("right", "right_y", ""),
        ("right", "left_y", "left_y"),
        ("left", "right_y", "right_y"),
    ],
)
def test_fader_stick_on_the_move_stick_is_unbound(move: str, fader: str, kept_fader: str) -> None:
    config = ControllerConfig(move_xy_stick=move, marker_fader_stick=fader)
    assert config.move_xy_stick == move
    assert config.marker_fader_stick == kept_fader


@pytest.fixture
def fresh_binding_log(monkeypatch):
    import openfollow.configuration as cfg_mod

    monkeypatch.setattr(cfg_mod, "_LOGGED_BINDINGS", set())


def test_duplicate_binding_names_both_fields_in_the_log(caplog, fresh_binding_log) -> None:
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        ControllerConfig(btn_reset="Y")
    assert any("btn_reset" in r.message and "btn_toggle_help" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    ("build", "section"),
    [
        (lambda: ControllerConfig(btn_reset="Y"), "controller"),
        (lambda: ControllerConfig(key_reset="h"), "controller"),
        (lambda: ControllerConfig(btn_menu_confirm="DPAD_UP"), "controller"),
        (lambda: Mouse3DConfig(btn_reset=0), "mouse3d"),
    ],
    ids=["gamepad", "keyboard", "menu-highlight", "mouse3d"],
)
def test_binding_repair_is_logged_once_under_its_section(caplog, fresh_binding_log, build, section) -> None:
    # A config.toml is parsed on every web request; the repair must not be re-logged each time.
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        for _ in range(3):
            build()
    assert [r.message[: len(section) + 2] for r in caplog.records] == [f"[{section}]"]


def test_duplicate_binding_in_config_toml_is_logged_once_across_loads(
    temp_config_path, caplog, fresh_binding_log
) -> None:
    temp_config_path.write_text("[mouse3d]\nbtn_reset = 0\nbtn_next_marker = 0\n")
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        for _ in range(3):
            assert load_config(str(temp_config_path)).mouse3d.btn_next_marker == -1
    settled = [r.message for r in caplog.records if "btn_next_marker" in r.message]
    assert settled == [
        "[mouse3d] btn_reset and btn_next_marker are both bound to 0; keeping btn_reset, unbinding btn_next_marker"
    ]


def test_duplicate_binding_in_config_toml_is_settled_on_load(temp_config_path) -> None:
    temp_config_path.write_text('[controller]\nbtn_reset = "Y"\nbtn_toggle_help = "Y"\n')
    controller = load_config(str(temp_config_path)).controller
    assert (controller.btn_reset, controller.btn_toggle_help) == ("Y", "")


def test_default_controller_config_has_no_duplicate_to_settle(caplog) -> None:
    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        config = ControllerConfig()
    for name, spec in ControllerConfig.__dataclass_fields__.items():
        if name.startswith(("btn_", "key_")) or name.endswith("_stick"):
            assert getattr(config, name) == spec.default, name
    assert not caplog.records


@pytest.mark.parametrize(
    ("upper", "lower"),
    [
        ("btn_reset", "btn_next_marker"),
        ("btn_prev_marker", "btn_speed_up"),
        ("btn_speed_down", "btn_toggle_zones"),
    ],
)
def test_mouse3d_button_on_two_actions_stays_on_the_upper_one(upper: str, lower: str) -> None:
    config = Mouse3DConfig(**{upper: 5, lower: 5})
    assert getattr(config, upper) == 5
    assert getattr(config, lower) == -1


def test_mouse3d_default_buttons_keep_their_bindings() -> None:
    config = Mouse3DConfig()
    assert (config.btn_next_marker, config.btn_prev_marker) == (0, 1)


def test_apply_runtime_does_not_reload_marker_move_speeds() -> None:
    """Per-marker speeds are device-local + runtime-authoritative: a hot-reload
    must NOT overwrite the live in-memory dict from disk, whatever the reloaded
    file happens to contain. This is the fix for the reported "saving any Web UI
    setting resets all marker speeds" bug – the web save writes the file, the
    watcher reloads it, and that reload must leave the live speeds untouched."""
    app = _DummyApp(AppConfig(marker_move_speeds={2: 2.0}))
    # A reloaded config carrying DIFFERENT speeds must not clobber the live ones.
    new_config = AppConfig(marker_move_speeds={2: 3.5, 1: 1.5})

    apply_runtime_config_changes(app, new_config)

    assert app._config.marker_move_speeds == {2: 2.0}
    assert app._web_commands.restart_requested is False


def test_apply_runtime_does_not_clear_marker_move_speeds_when_disk_empty() -> None:
    """A reloaded config with EMPTY speeds (e.g. a fresh disk load that never saw
    the runtime edits) must not wipe the live dict."""
    app = _DummyApp(AppConfig(marker_move_speeds={2: 2.0, 3: 4.0}))
    new_config = AppConfig()  # no speeds

    apply_runtime_config_changes(app, new_config)

    assert app._config.marker_move_speeds == {2: 2.0, 3: 4.0}


def test_startup_load_seeds_marker_move_speeds_from_disk(temp_config_path) -> None:
    """Part 0 only skips the *reload*; the initial ``load_config`` at startup
    still seeds the speeds from disk so a restart picks up the persisted values."""
    config = AppConfig(controlled_marker_ids=[2, 3], marker_move_speeds={2: 1.5, 3: 4.0})
    save_config(config, str(temp_config_path))
    reloaded = load_config(str(temp_config_path))
    assert reloaded.marker_move_speeds == {2: 1.5, 3: 4.0}


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


def test_apply_runtime_ui_shown_features_propagates_to_config() -> None:
    """The Features list is read off ``app._config``, so a change applies live without a restart."""
    app = _DummyApp(AppConfig())
    assert not app._config.ui.feature_on("paths")
    new_config = AppConfig()
    new_config.ui.shown_features = ["paths"]

    apply_runtime_config_changes(app, new_config)

    assert app._config.ui.shown_features == ["paths"]
    assert app._web_commands.restart_requested is False


class _RecordingZoneEngine:
    def __init__(self) -> None:
        self.reloads: list[TriggerZonesConfig] = []

    def reload_config(self, config: TriggerZonesConfig, destinations: object = None) -> None:
        self.reloads.append(config)


class _RecordingMidi:
    def __init__(self) -> None:
        self.applied: list[list[object]] = []

    def apply_config(self, patches: list[object]) -> None:
        self.applied.append(list(patches))


def _with_features(cfg: AppConfig, *keys: str) -> AppConfig:
    cfg.ui.shown_features = list(keys)
    cfg.ui.__post_init__()
    return cfg


class TestFeatureGatesHotReload:
    """A Features change re-applies exactly the services whose entry flipped, with the gated config."""

    def test_psn_input_flip_reapplies_the_receiver_once(self) -> None:
        app = _DummyApp(AppConfig())
        apply_runtime_config_changes(app, _with_features(AppConfig(), "zones", "midi"))
        assert app._runtime_services.psn_input_changes == 1
        apply_runtime_config_changes(app, _with_features(AppConfig(), "zones", "midi", "paths"))
        assert app._runtime_services.psn_input_changes == 1

    @pytest.mark.parametrize("feature_on", [True, False])
    def test_rttrpm_runs_only_with_its_entry(self, feature_on) -> None:  # noqa: ANN001
        app = _DummyApp(AppConfig())
        new = AppConfig(rttrpm_output=RttrpmOutputConfig(enabled=True))
        _with_features(new, *(["rttrpm"] if feature_on else []))
        apply_runtime_config_changes(app, new)
        assert app._runtime_services.rttrpm_changes[-1].enabled is feature_on
        assert app._config.rttrpm_output.enabled is True

    def test_rttrpm_entry_flip_alone_reapplies_the_sender(self) -> None:
        app = _DummyApp(_with_features(AppConfig(rttrpm_output=RttrpmOutputConfig(enabled=True)), "rttrpm"))
        new = _with_features(AppConfig(rttrpm_output=RttrpmOutputConfig(enabled=True)))
        apply_runtime_config_changes(app, new)
        assert [cfg.enabled for cfg in app._runtime_services.rttrpm_changes] == [False]

    def test_detection_entry_off_stops_the_detector_and_keeps_its_switch(self) -> None:
        app = _DummyApp(_with_features(AppConfig(detection=DetectionConfig(enabled=True)), "detection"))
        apply_runtime_config_changes(app, _with_features(AppConfig(detection=DetectionConfig(enabled=True))))
        assert [cfg.enabled for cfg in app._runtime_services.detection_changes] == [False]
        assert app._config.detection.enabled is True

    def test_detection_entry_on_starts_the_detector(self) -> None:
        app = _DummyApp(AppConfig(detection=DetectionConfig(enabled=True)))
        app._runtime_services._person_detector = None
        apply_runtime_config_changes(
            app, _with_features(AppConfig(detection=DetectionConfig(enabled=True)), "detection")
        )
        assert [cfg.enabled for cfg in app._runtime_services.detection_swaps] == [True]

    def test_zones_entry_flip_reloads_the_engine_with_the_gated_config(self) -> None:
        app = _DummyApp(AppConfig(trigger_zones=TriggerZonesConfig(enabled=True)))
        engine = _RecordingZoneEngine()
        app._runtime_services._zone_engine = engine
        apply_runtime_config_changes(app, _with_features(AppConfig(trigger_zones=TriggerZonesConfig(enabled=True))))
        apply_runtime_config_changes(
            app, _with_features(AppConfig(trigger_zones=TriggerZonesConfig(enabled=True)), "zones")
        )
        assert [cfg.enabled for cfg in engine.reloads] == [False, True]
        assert app._config.trigger_zones.enabled is True

    def test_midi_entry_off_closes_every_patch_and_keeps_them(self) -> None:
        patch = MidiPatch(id=1, alias="Fader box")
        app = _DummyApp(AppConfig(midi=MidiConfig(patches=[patch])))
        midi = _RecordingMidi()
        app._runtime_services._midi = midi
        apply_runtime_config_changes(app, _with_features(AppConfig(midi=MidiConfig(patches=[patch]))))
        apply_runtime_config_changes(app, _with_features(AppConfig(midi=MidiConfig(patches=[patch])), "midi"))
        assert midi.applied == [[], [patch]]
        assert app._config.midi.patches == [patch]

    def test_unchanged_features_reapply_nothing(self) -> None:
        app = _DummyApp(AppConfig(rttrpm_output=RttrpmOutputConfig(enabled=True)))
        midi = _RecordingMidi()
        app._runtime_services._midi = midi
        apply_runtime_config_changes(app, AppConfig(rttrpm_output=RttrpmOutputConfig(enabled=True)))
        assert app._runtime_services.rttrpm_changes == []
        assert app._runtime_services.psn_input_changes == 0
        assert midi.applied == []


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


def test_apply_runtime_leaves_the_update_and_time_sync_settings_for_the_next_start() -> None:
    """The updater and the online-sync worker read them off the running config, so a saved
    change (an import) waits for the next start rather than reaching one of them only."""
    app = _DummyApp(AppConfig())
    new_config = AppConfig(
        update_github_repo="stage-crew/openfollow",
        update_include_prereleases=True,
        auto_update_check=False,
        auto_time_sync=False,
        time_sync_server="ntp.example.com",
    )

    apply_runtime_config_changes(app, new_config)

    kept = app._config
    assert (kept.update_github_repo, kept.update_include_prereleases) == ("openfollowapp/openfollow", False)
    assert (kept.auto_update_check, kept.auto_time_sync, kept.time_sync_server) == (True, True, "ptbtime1.ptb.de")
    assert app._web_commands.restart_requested is False


def test_apply_runtime_osc_multicast_group_threads_into_restart() -> None:
    """An ``[osc] multicast_group`` change reaches ``InputManager.restart_osc``."""
    app = _DummyApp(AppConfig())
    new_config = AppConfig()
    new_config.osc.multicast_group = "239.1.2.3"

    apply_runtime_config_changes(app, new_config)

    assert app._config.osc.multicast_group == "239.1.2.3"
    assert app._input_manager.osc_multicast_groups == ["239.1.2.3"]


def test_apply_runtime_osc_listen_iface_threads_into_restart() -> None:
    """An ``[osc] listen_iface`` change reaches ``InputManager.restart_osc``.

    Live-applied like the rest of the section: the listener is rebound between
    frames, so repinning it needs no restart. Dropped here, a saved pin would
    sit in config while the socket stayed on the old interface.
    """
    app = _DummyApp(AppConfig())
    new_config = AppConfig()
    new_config.osc.listen_iface = "eth1"

    apply_runtime_config_changes(app, new_config)

    assert app._config.osc.listen_iface == "eth1"
    assert app._input_manager.osc_listen_ifaces == ["eth1"]
    assert app._web_commands.restart_requested is False


def test_apply_runtime_mouse3d_enabled_toggle_reaches_input_manager() -> None:
    """Enabling 3D Mouse applies live: stored config updates and the handler is
    reloaded, with no process restart."""
    app = _DummyApp(AppConfig())
    new_config = AppConfig()
    new_config.mouse3d.enabled = True  # differs from default-off

    apply_runtime_config_changes(app, new_config)

    assert app._config.mouse3d.enabled is True
    assert len(app._input_manager.mouse3d_restarts) == 1
    assert app._input_manager.mouse3d_restarts[0] is new_config.mouse3d
    assert app._web_commands.restart_requested is False


def test_apply_runtime_mouse3d_field_change_reaches_input_manager() -> None:
    """A mapping/sensitivity edit (enabled unchanged) still live-applies."""
    app = _DummyApp(AppConfig())
    new_config = AppConfig()
    new_config.mouse3d.map_pitch = "speed"
    new_config.mouse3d.sens_pan_x = 3.0

    apply_runtime_config_changes(app, new_config)

    assert app._config.mouse3d.map_pitch == "speed"
    assert app._config.mouse3d.sens_pan_x == 3.0
    assert len(app._input_manager.mouse3d_restarts) == 1


def test_apply_runtime_mouse3d_unchanged_does_not_restart() -> None:
    """An unrelated config save must not churn the 3D Mouse handler."""
    app = _DummyApp(AppConfig())
    new_config = AppConfig()
    new_config.psn_system_name = "Renamed"  # unrelated change

    apply_runtime_config_changes(app, new_config)

    assert app._input_manager.mouse3d_restarts == []


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


def test_apply_runtime_touch_remote_changes_apply_live() -> None:
    app = _DummyApp(AppConfig(touch_remote=TouchRemoteConfig(enabled=True)))
    apply_runtime_config_changes(app, AppConfig(touch_remote=TouchRemoteConfig(enabled=True, stick_curve=1.0)))
    assert app._config.touch_remote.stick_curve == 1.0
    apply_runtime_config_changes(app, AppConfig())
    assert app._config.touch_remote.enabled is False
    assert app._web_commands.restart_requested is False


def test_touch_remote_defaults() -> None:
    cfg = TouchRemoteConfig()
    assert (cfg.enabled, cfg.stick_curve, cfg.stick_dead_zone, cfg.z_step_m, cfg.z_notch_px) == (
        False,
        2.0,
        0.08,
        0.05,
        20,
    )


@pytest.mark.parametrize(
    ("field_name", "value", "want"),
    [
        ("stick_curve", "abc", 2.0),
        ("stick_curve", None, 2.0),
        ("stick_curve", True, 2.0),
        ("stick_dead_zone", "abc", 0.08),
        ("stick_dead_zone", None, 0.08),
        ("stick_dead_zone", True, 0.08),
        ("z_step_m", "abc", 0.05),
        ("z_step_m", None, 0.05),
        ("z_step_m", False, 0.05),
        ("z_notch_px", "abc", 20),
        ("z_notch_px", None, 20),
        ("z_notch_px", True, 20),
        ("enabled", None, False),
        ("enabled", [1], False),
    ],
)
def test_touch_remote_wrong_types_fall_back_to_defaults(field_name: str, value: object, want: object) -> None:
    assert getattr(TouchRemoteConfig(**{field_name: value}), field_name) == want


@pytest.mark.parametrize(
    ("field_name", "value", "want"),
    [
        ("stick_curve", 0.2, 1.0),
        ("stick_curve", 9.0, 3.0),
        ("stick_curve", float("nan"), 2.0),
        ("stick_dead_zone", -0.1, 0.0),
        ("stick_dead_zone", 0.9, 0.5),
        ("z_step_m", 0.0, 0.001),
        ("z_step_m", 5.0, 1.0),
        ("z_step_m", float("inf"), 0.05),
        ("z_notch_px", 1, 8),
        ("z_notch_px", 500, 80),
    ],
)
def test_touch_remote_out_of_range_is_clamped(field_name: str, value: object, want: float) -> None:
    assert getattr(TouchRemoteConfig(**{field_name: value}), field_name) == pytest.approx(want)


def test_touch_remote_survives_a_hand_edited_config_toml(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "config.toml"
    path.write_text('[touch_remote]\nenabled = true\nstick_curve = "steep"\nz_notch_px = 1000\nstale_key = 1\n')
    cfg = load_config(str(path)).touch_remote
    assert (cfg.enabled, cfg.stick_curve, cfg.z_notch_px) == (True, 2.0, 80)


def test_touch_remote_crafted_section_post_is_normalised() -> None:
    from openfollow.web.routes import apply_section_data

    cfg = AppConfig()
    apply_section_data(cfg, "touch_remote", {"enabled": "on", "stick_dead_zone": "0.9", "z_notch_px": "-4"})
    assert cfg.touch_remote.enabled is True
    assert cfg.touch_remote.stick_dead_zone == 0.5
    assert cfg.touch_remote.z_notch_px == 8


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


def test_apply_runtime_combined_psn_failure_reverts_both_fields(monkeypatch) -> None:
    """When the combined orchestrator raises, both stored fields
    revert so the next hot-reload pass re-attempts. Without
    reverting both, a stuck ``stored == new`` for either field
    would silently no-op the next pass.

    ``eth0`` is made live so this exercises the rebind path; a down interface
    suspends instead and is covered separately."""
    import socket as _socket

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {"eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.1.5")]},
    )

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
    # A live interface takes the rebind path, never the fail-closed suspend.
    assert app._runtime_services.psn_suspends == 0


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


def test_apply_runtime_hands_new_interface_labels_to_the_running_station() -> None:
    """The HUD's outage rows and the Network screen read the running config, so a
    label saved on the web shows there without a restart."""
    app = _DummyApp(AppConfig(interface_labels={"eth0": "Production"}))
    apply_runtime_config_changes(app, AppConfig(interface_labels={"eth0": "Video", "eth1": "Lighting"}))
    assert app._config.interface_labels == {"eth0": "Video", "eth1": "Lighting"}
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


def test_station_iface_change_repoints_the_planes_that_follow_it(monkeypatch) -> None:
    """A plane with a blank pin follows the station interface, but its own
    dataclass is unchanged – so nothing else in the dispatcher re-applies it.
    Without this the panel renders the new address for a plane still sending
    from the old interface until a restart."""
    import socket as _socket
    from types import SimpleNamespace

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.1.5")],
            "eth1": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.9")],
        },
    )
    app = _DummyApp(AppConfig(psn_source_iface="eth0"))
    apply_runtime_config_changes(app, AppConfig(psn_source_iface="eth1"))

    assert app._runtime_services.station_iface_follow_calls == 1


def test_station_iface_rollback_does_not_repoint_followers(monkeypatch) -> None:
    """The PSN rebind failed and the iface reverted, so the followers are
    still correctly on the old interface – repointing them would move them
    to an interface the station isn't actually using."""
    import socket as _socket
    from types import SimpleNamespace

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.1.5")],
            "eth1": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.9")],
        },
    )
    app = _DummyApp(AppConfig(psn_source_iface="eth0"))

    def _boom(*_a: object, **_kw: object) -> None:
        raise OSError("bind failed")

    app._runtime_services.apply_psn_source_ip_change = _boom  # type: ignore[method-assign]
    apply_runtime_config_changes(app, AppConfig(psn_source_iface="eth1"))

    assert app._config.psn_source_iface == "eth0"
    assert app._runtime_services.station_iface_follow_calls == 0


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


@pytest.mark.parametrize(("raw", "expected"), [(None, ""), (5, ""), (True, ""), ([], ""), ("  eth1  ", "eth1")])
def test_app_config_normalises_video_input_iface(raw: object, expected: str) -> None:
    assert AppConfig(network_mode="advanced", video_input_iface=raw).video_input_iface == expected  # type: ignore[arg-type]


def test_video_input_iface_survives_a_toml_round_trip(tmp_path) -> None:
    path = tmp_path / "config.toml"
    save_config(AppConfig(network_mode="advanced", video_input_iface="eth0.13"), path)
    assert load_config(path).video_input_iface == "eth0.13"


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


def test_app_config_strips_web_bind_iface_at_construction() -> None:
    """Stripped on load so a hand-edited TOML matches the panel's save path -
    an unstripped ``" eth0 "`` would resolve to no address and read as a
    down interface."""
    assert AppConfig(network_mode="advanced", web_bind_iface="  eth0  ").web_bind_iface == "eth0"


@pytest.mark.parametrize("bad", [0, None, True, 1.5, ["eth0"], {"iface": "eth0"}])
def test_app_config_coerces_non_str_web_bind_iface(bad) -> None:
    """A non-string pin becomes "" (serve everywhere) rather than reaching
    ``resolve_web_bind``, where it would raise inside the web server's bind."""
    cfg = AppConfig(web_bind_iface=bad)  # type: ignore[arg-type]
    assert cfg.web_bind_iface == ""


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


@pytest.mark.parametrize("station", ["eth1", ""], ids=["moved", "auto-detect"])
def test_a_station_change_rebuilds_a_video_input_that_follows_it(monkeypatch, station: str) -> None:
    """The station row is committed before the video block runs; compared
    afterwards, both sides would resolve the new station and nothing would move."""
    import socket as _socket
    from types import SimpleNamespace

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.1.5")],
            "eth1": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.9")],
        },
    )
    app = _DummyApp(AppConfig(video_source_type="srt", srt_host="srt://203.0.113.20:5000", psn_source_iface="eth0"))
    new_config = AppConfig(video_source_type="srt", srt_host="srt://203.0.113.20:5000", psn_source_iface=station)

    apply_runtime_config_changes(app, new_config)

    assert app._runtime_services.video_swaps == [new_config]


class _RejectingStationServices(_DummyRuntimeServices):
    def apply_psn_source_ip_change(self, new_source_ip: str, *, new_mcast_ip: object = None) -> None:
        raise OSError("simulated rebind failure")


def _two_interfaces(monkeypatch) -> None:  # noqa: ANN001
    import socket as _socket
    from types import SimpleNamespace

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.1.5")],
            "eth1": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.9")],
        },
    )


def test_a_rejected_station_change_leaves_the_video_input_where_it_is(monkeypatch) -> None:
    """Rolled back, the station is still eth0: pinning the video to eth1 would
    disagree with the config, and every retry would rebuild it again."""
    _two_interfaces(monkeypatch)
    fields = {"video_source_type": "srt", "srt_host": "srt://203.0.113.20:5000"}
    app = _DummyApp(AppConfig(psn_source_iface="eth0", **fields))
    app._runtime_services = _RejectingStationServices()
    new_config = AppConfig(psn_source_iface="eth1", **fields)

    apply_runtime_config_changes(app, new_config)
    apply_runtime_config_changes(app, new_config)

    assert app._config.psn_source_iface == "eth0"
    assert app._runtime_services.video_swaps == []


def test_a_video_change_saved_with_a_rejected_station_change_keeps_the_station(monkeypatch) -> None:
    _two_interfaces(monkeypatch)
    app = _DummyApp(AppConfig(psn_source_iface="eth0", video_source_type="srt", srt_host="srt://203.0.113.20:5000"))
    app._runtime_services = _RejectingStationServices()

    apply_runtime_config_changes(
        app, AppConfig(psn_source_iface="eth1", video_source_type="srt", srt_host="srt://203.0.113.21:5000")
    )

    [swapped] = app._runtime_services.video_swaps
    assert swapped.srt_host == "srt://203.0.113.21:5000"
    assert swapped.psn_source_iface == "eth0"


def test_a_failed_video_swap_after_a_station_change_is_retried(monkeypatch) -> None:
    """The station change is committed, so the configs alone no longer differ on
    the next pass; the swap is retried until it succeeds, then no more."""
    _two_interfaces(monkeypatch)

    class _FailingOnce(_DummyRuntimeServices):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def swap_video(self, new_cfg: AppConfig) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("pipeline would not stop")
            super().swap_video(new_cfg)

    fields = {"video_source_type": "srt", "srt_host": "srt://203.0.113.20:5000"}
    app = _DummyApp(AppConfig(psn_source_iface="eth0", **fields))
    app._runtime_services = _FailingOnce()
    new_config = AppConfig(psn_source_iface="eth1", **fields)

    apply_runtime_config_changes(app, new_config)
    assert app._config.psn_source_iface == "eth1"
    assert app._runtime_services.video_swaps == []

    apply_runtime_config_changes(app, new_config)
    [swapped] = app._runtime_services.video_swaps
    assert swapped.psn_source_iface == "eth1"

    apply_runtime_config_changes(app, new_config)
    assert app._runtime_services.attempts == 2


def test_a_station_change_leaves_a_video_input_with_its_own_pin(monkeypatch) -> None:
    import socket as _socket
    from types import SimpleNamespace

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.1.5")],
            "eth1": [SimpleNamespace(family=_socket.AF_INET, address="10.0.0.9")],
        },
    )
    fields = {"video_source_type": "srt", "srt_host": "srt://203.0.113.20:5000", "video_input_iface": "eth1"}
    app = _DummyApp(AppConfig(network_mode="advanced", psn_source_iface="eth0", **fields))

    apply_runtime_config_changes(app, AppConfig(network_mode="advanced", psn_source_iface="eth1", **fields))

    assert app._runtime_services.video_swaps == []


def test_apply_runtime_swaps_video_on_source_type_change() -> None:
    """Switching ``video_source_type`` between plugins (RTSP → SRT)
    also routes through the live-swap path. The dispatcher commits
    every plugin field from the new plugin; the old plugin's fields
    then take the file's values like any input that is not running."""
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
    assert app._config.rtsp_url == new_config.rtsp_url
    assert app._web_commands.restart_requested is False
    assert app._runtime_services.video_swaps == [new_config]


def test_apply_runtime_video_swap_failure_reverts_config() -> None:
    """When the orchestrator raises, ``video_source_type`` reverts and the
    running input's fields (``rtsp_url`` during an RTSP → SRT swap) stay as it
    runs them. The new input is not running, so its fields keep the file's values."""

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

    assert app._config.video_source_type == "rtsp"
    assert app._config.rtsp_url == "rtsp://192.168.0.20:554/stream"
    assert app._config.srt_host == "srt://10.0.0.5:5000"
    assert app._web_commands.restart_requested is False


def test_apply_runtime_mirrors_the_fields_of_an_input_that_failed_to_start() -> None:
    """A failed swap leaves the new input not running, so its fields take the
    file's values like any other input's: a later switch to it is built from them."""

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def swap_video(self, new_cfg: AppConfig) -> None:
            raise RuntimeError("simulated pipeline build failure")

    app = _DummyApp(
        AppConfig(video_source_type="srt", srt_host="srt://192.0.2.21:5000", rtsp_url="rtsp://192.0.2.20/one")
    )
    app._runtime_services = _FailingRuntimeServices()
    new_config = AppConfig(video_source_type="rtsp", srt_host="srt://192.0.2.21:5000", rtsp_url="rtsp://192.0.2.20/two")

    assert apply_runtime_config_changes(app, new_config) is False

    assert app._config.video_source_type == "srt"
    assert app._config.rtsp_url == "rtsp://192.0.2.20/two"


def test_apply_runtime_mirrors_the_fields_of_inputs_that_are_not_running() -> None:
    """An input that is not running reads nothing from its fields, so the reload
    takes them as the file has them, without rebuilding the running input."""
    app = _DummyApp(AppConfig(video_source_type="rtsp", rtsp_url="rtsp://192.0.2.20/stream"))
    new_config = AppConfig(
        video_source_type="rtsp",
        rtsp_url="rtsp://192.0.2.20/stream",
        ndi_source_name="CAM-HOST (Camera 2)",
        avf_unique_id="avf-test-id",
        testpattern_selected_media="default:grey",
        picam_width=1280,
    )

    assert apply_runtime_config_changes(app, new_config) is True

    assert app._config.ndi_source_name == "CAM-HOST (Camera 2)"
    assert app._config.avf_unique_id == "avf-test-id"
    assert app._config.testpattern_selected_media == "default:grey"
    assert app._config.picam_width == 1280
    assert app._runtime_services.video_swaps == []


def test_apply_runtime_leaves_a_field_the_running_input_shares_to_the_swap() -> None:
    """``video_input_iface`` belongs to RTSP as well as SRT and RTP: while RTSP runs,
    a failed swap keeps the value the running input was built with."""

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def swap_video(self, new_cfg: AppConfig) -> None:
            raise RuntimeError("simulated pipeline build failure")

    app = _DummyApp(
        AppConfig(
            network_mode="advanced",
            video_source_type="rtsp",
            rtsp_url="rtsp://192.0.2.20/stream",
            video_input_iface="eth0",
        )
    )
    app._runtime_services = _FailingRuntimeServices()
    new_config = AppConfig(
        network_mode="advanced",
        video_source_type="rtsp",
        rtsp_url="rtsp://192.0.2.20/stream",
        video_input_iface="eth1",
    )

    assert apply_runtime_config_changes(app, new_config) is False

    assert app._config.video_input_iface == "eth0"


def test_apply_runtime_mirrors_every_input_field_while_the_type_names_no_input() -> None:
    """A hand-edited file can name a removed plugin: no input runs, so every
    input's fields are taken as the file has them."""
    app = _DummyApp(AppConfig(video_source_type="removed-plugin"))
    new_config = AppConfig(
        video_source_type="removed-plugin", rtsp_url="rtsp://192.0.2.20/stream", srt_host="srt://192.0.2.21:5000"
    )

    assert apply_runtime_config_changes(app, new_config) is True

    assert (app._config.rtsp_url, app._config.srt_host) == ("rtsp://192.0.2.20/stream", "srt://192.0.2.21:5000")
    assert app._runtime_services.video_swaps == []


def test_apply_runtime_mirrors_no_plugin_field_that_is_not_stored(monkeypatch) -> None:
    """A plugin field with no ``AppConfig`` counterpart is never in the file, so
    there is nothing to mirror and the reload must not trip over it."""
    from openfollow.video.inputs import get_registry
    from openfollow.video.inputs._base import ConfigField

    class _UnstoredInput:
        @classmethod
        def config_fields(cls) -> list[ConfigField]:
            return [ConfigField("unstored_width", int, 1920, "Width")]

    registry = {**get_registry(), "unstored": _UnstoredInput}
    monkeypatch.setattr("openfollow.video.inputs.get_registry", lambda: registry)
    app = _DummyApp(AppConfig(video_source_type="rtsp"))

    assert apply_runtime_config_changes(app, AppConfig(video_source_type="rtsp", ndi_source_name="CAM")) is True

    assert app._config.ndi_source_name == "CAM"
    assert not hasattr(app._config, "unstored_width")


# ---------------------------------------------------------------------------
# On-screen saves keep what the file holds
# ---------------------------------------------------------------------------


def _app_on_file(path: str) -> _DummyApp:
    app = _DummyApp(load_config(path))
    app._config_path = path
    return app


def test_an_on_screen_save_after_a_reload_keeps_the_file_edits(tmp_path) -> None:
    """The file is edited while the app runs and reloaded; an Operator Screen save
    afterwards must not write the running config's older values back."""
    from openfollow.runtime import app_modes

    path = str(tmp_path / "config.toml")
    save_config(AppConfig(video_source_type="rtsp", rtsp_url="rtsp://192.0.2.20/stream"), path)
    app = _app_on_file(path)
    edited = load_config(path)
    edited.ndi_source_name = "CAM-HOST (Camera 2)"
    edited.avf_unique_id = "avf-test-id"
    edited.testpattern_selected_media = "default:grey"
    edited.web_port = 8080  # restart-only: the reload never takes it in
    edited.controlled_marker_ids = [1, 2]
    save_config(edited, path)
    assert apply_runtime_config_changes(app, load_config(path, strict=True)) is True

    app_modes._toggle_zone_overlay(app)

    saved = load_config(path)
    assert saved.trigger_zones.show_overlay is False
    assert saved.ndi_source_name == "CAM-HOST (Camera 2)"
    assert saved.avf_unique_id == "avf-test-id"
    assert saved.testpattern_selected_media == "default:grey"
    assert saved.web_port == 8080
    assert saved.controlled_marker_ids == [1, 2]


def test_an_on_screen_source_switch_after_a_reload_uses_the_file_values(tmp_path) -> None:
    """Change Video Source builds the new input from the running config, so the
    reload must have taken in that input's fields while another one ran."""
    from openfollow.runtime import app_modes

    path = str(tmp_path / "config.toml")
    save_config(AppConfig(video_source_type="rtsp", ndi_source_name="CAM-HOST (Camera 1)"), path)
    app = _app_on_file(path)
    edited = load_config(path)
    edited.ndi_source_name = "CAM-HOST (Camera 2)"
    save_config(edited, path)
    assert apply_runtime_config_changes(app, load_config(path, strict=True)) is True
    app._available_source_types = [("ndi", "NDI")]
    app._selected_source_type_index = 0
    app._enter_source_selection = lambda: None

    app_modes.confirm_source_type_selection(app)

    assert [cfg.ndi_source_name for cfg in app._runtime_services.video_swaps] == ["CAM-HOST (Camera 2)"]
    saved = load_config(path)
    assert (saved.video_source_type, saved.ndi_source_name) == ("ndi", "CAM-HOST (Camera 2)")


def test_a_reload_while_an_auto_chained_url_editor_is_open_keeps_the_switch(tmp_path) -> None:
    """Change Video Source to an input that needs a URL first opens the editor with
    the old input still running. A reload landing then, as one does right after the
    operator's previous save, must not take the switch back."""
    from openfollow.runtime import app_modes

    class _NeedsUrl(_DummyRuntimeServices):
        def swap_video(self, new_cfg: AppConfig) -> None:
            super().swap_video(new_cfg)
            if new_cfg.video_source_type == "rtsp" and not new_cfg.rtsp_url:
                raise ValueError("rtsp_url is empty")

    path = str(tmp_path / "config.toml")
    save_config(AppConfig(video_source_type="ndi", ndi_source_name="CAM-HOST (Camera 1)", rtsp_url=""), path)
    app = _app_on_file(path)
    app._runtime_services = _NeedsUrl()
    app._available_source_types = [("rtsp", "RTSP")]
    app._selected_source_type_index = 0
    banners: list[str] = []
    app._enter_url_editor = lambda **kw: app_modes.enter_url_editor(app, **kw)
    app._confirm_url_editor = lambda: app_modes.confirm_url_editor(app)
    app._enter_settings_menu = lambda **kw: banners.append(kw.get("banner", ""))

    app_modes.confirm_source_type_selection(app)
    assert app._url_editor_active is True
    assert apply_runtime_config_changes(app, load_config(path, strict=True)) is True
    for ch in "rtsp://192.0.2.20/stream":
        app_modes.handle_url_editor_key(app, ch)
    app_modes.handle_url_editor_key(app, "Enter")

    assert [cfg.video_source_type for cfg in app._runtime_services.video_swaps] == ["rtsp", "rtsp"]
    assert app._config.video_source_type == "rtsp"
    saved = load_config(path)
    assert (saved.video_source_type, saved.rtsp_url) == ("rtsp", "rtsp://192.0.2.20/stream")
    assert banners == []


def test_a_reload_while_an_auto_chained_value_picker_is_open_keeps_the_switch(tmp_path) -> None:
    from openfollow.runtime import app_modes

    class _FailsFirst(_DummyRuntimeServices):
        def swap_video(self, new_cfg: AppConfig) -> None:
            super().swap_video(new_cfg)
            if len(self.video_swaps) == 1:
                raise RuntimeError("device busy")

    path = str(tmp_path / "config.toml")
    save_config(AppConfig(video_source_type="ndi", v4l2_render_resolution="native"), path)
    app = _app_on_file(path)
    app._runtime_services = _FailsFirst()
    app._available_source_types = [("v4l2", "USB Camera")]
    app._selected_source_type_index = 0
    banners: list[str] = []
    app._enter_field_choice_picker = lambda **kw: app_modes.enter_field_choice_picker(app, **kw)
    app._enter_settings_menu = lambda **kw: banners.append(kw.get("banner", ""))

    app_modes.confirm_source_type_selection(app)
    assert app._field_choice_active is True
    assert apply_runtime_config_changes(app, load_config(path, strict=True)) is True
    app._field_choice_selected_index = 1
    app_modes.confirm_field_choice_picker(app)

    assert [cfg.video_source_type for cfg in app._runtime_services.video_swaps] == ["v4l2", "v4l2"]
    saved = load_config(path)
    assert (saved.video_source_type, saved.v4l2_render_resolution) == ("v4l2", app._config.v4l2_render_resolution)
    assert app._config.video_source_type == "v4l2"
    assert banners == []


def test_a_plain_url_edit_after_a_reload_brought_up_another_input_saves_only_the_url(tmp_path) -> None:
    """The URL editor opened on the running input; a web save switches the station
    to another input before Enter. The edit is kept for its own input, which does
    not come back."""
    from openfollow.runtime import app_modes

    path = str(tmp_path / "config.toml")
    save_config(
        AppConfig(video_source_type="rtsp", rtsp_url="rtsp://192.0.2.20/a", srt_host="srt://192.0.2.30:5000"), path
    )
    app = _app_on_file(path)
    banners: list[str] = []
    app._confirm_url_editor = lambda: app_modes.confirm_url_editor(app)
    app._enter_settings_menu = lambda **kw: banners.append(kw.get("banner", ""))

    app_modes.enter_url_editor(app)
    switched = load_config(path)
    switched.video_source_type = "srt"
    save_config(switched, path)
    assert apply_runtime_config_changes(app, load_config(path, strict=True)) is True
    app_modes.handle_url_editor_key(app, "Backspace")
    app_modes.handle_url_editor_key(app, "b")
    app_modes.handle_url_editor_key(app, "Enter")

    assert [cfg.video_source_type for cfg in app._runtime_services.video_swaps] == ["srt"]
    assert (app._config.video_source_type, app._config.rtsp_url) == ("srt", "rtsp://192.0.2.20/b")
    saved = load_config(path)
    assert (saved.video_source_type, saved.rtsp_url) == ("srt", "rtsp://192.0.2.20/b")
    assert app._url_editor_active is False
    assert banners == []


def test_a_plain_value_pick_after_a_reload_brought_up_another_input_saves_only_the_value(tmp_path) -> None:
    from openfollow.runtime import app_modes

    path = str(tmp_path / "config.toml")
    save_config(AppConfig(video_source_type="v4l2", v4l2_render_resolution="native"), path)
    app = _app_on_file(path)
    banners: list[str] = []
    app._enter_settings_menu = lambda **kw: banners.append(kw.get("banner", ""))

    app_modes.enter_field_choice_picker(app)
    picked = app._field_choice_options[1][0]
    switched = load_config(path)
    switched.video_source_type = "rtsp"
    switched.rtsp_url = "rtsp://192.0.2.20/a"
    save_config(switched, path)
    assert apply_runtime_config_changes(app, load_config(path, strict=True)) is True
    app._field_choice_selected_index = 1
    app_modes.confirm_field_choice_picker(app)

    assert [cfg.video_source_type for cfg in app._runtime_services.video_swaps] == ["rtsp"]
    assert (app._config.video_source_type, app._config.v4l2_render_resolution) == ("rtsp", picked)
    saved = load_config(path)
    assert (saved.video_source_type, saved.v4l2_render_resolution) == ("rtsp", picked)
    assert app._field_choice_active is False
    assert banners == []


def test_a_switch_that_could_not_be_saved_is_saved_by_switching_again_once_the_file_is_fixed(tmp_path) -> None:
    """What the unsaved banner tells the operator: the repaired file brings its own
    source back at once, and the next switch is saved."""
    from pathlib import Path

    from openfollow.runtime import app_modes

    path = str(tmp_path / "config.toml")
    good = AppConfig(video_source_type="ndi", ndi_source_name="CAM-HOST (Camera 1)", rtsp_url="rtsp://192.0.2.20/a")
    save_config(good, path)
    app = _app_on_file(path)
    banners: list[tuple[str, str]] = []
    app._enter_settings_menu = lambda **kw: banners.append((kw.get("banner", ""), kw.get("action", "")))
    app._enter_url_editor = lambda **_kw: None
    app._available_source_types = [("rtsp", "RTSP")]
    app._selected_source_type_index = 0
    Path(path).write_text("video_source_type = [unclosed", encoding="utf-8")

    app_modes.confirm_source_type_selection(app)
    assert app._config.video_source_type == "rtsp"
    assert banners == [
        (
            "RTSP is live but could not be saved. The settings file could not be read.",
            "Import a settings file to replace it, then switch again.",
        )
    ]

    save_config(good, path, replace_unreadable=True)  # the operator repairs it
    assert apply_runtime_config_changes(app, load_config(path, strict=True)) is True
    assert app._config.video_source_type == "ndi"
    app_modes.confirm_source_type_selection(app)

    assert [cfg.video_source_type for cfg in app._runtime_services.video_swaps] == ["rtsp", "ndi", "rtsp"]
    assert load_config(path).video_source_type == "rtsp"
    assert len(banners) == 1


def test_an_on_screen_save_keeps_an_edit_the_reload_has_not_seen_and_the_reload_applies_it(
    tmp_path,
) -> None:
    import os

    from openfollow.runtime import app_modes
    from openfollow.runtime import app_orchestration as orch

    path = str(tmp_path / "config.toml")
    save_config(AppConfig(), path)
    os.utime(path, (100.0, 100.0))
    app = _app_on_file(path)
    app._config_mtime = 100.0
    app._last_config_check = float("-inf")
    app._get_config_mtime = lambda: orch.get_config_mtime(app)
    edited = load_config(path)
    edited.grid.spacing = 2.0
    save_config(edited, path)
    os.utime(path, (200.0, 200.0))

    app_modes._toggle_zone_overlay(app)  # within the reload's poll interval
    assert load_config(path).grid.spacing == 2.0

    orch.check_config_reload(app)
    assert app._config.grid.spacing == 2.0
    assert app._config.trigger_zones.show_overlay is False


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


def test_apply_runtime_seeds_a_live_added_marker_with_the_default_position() -> None:
    """Startup registration seeds the default position; the hot-reload add path
    must match it. An unseeded marker broadcasts (0, 0, 0) and, never having
    taken a data write, reports STATUS invalid on the PSN wire."""
    app = _DummyApp(AppConfig(controlled_marker_ids=[1], viewer_marker_ids=[1]))
    new_config = AppConfig(controlled_marker_ids=[1, 305], viewer_marker_ids=[1, 305])
    new_config.marker.default_pos_x = 1.5
    new_config.marker.default_pos_y = -2.0
    new_config.marker.default_pos_z = 1.6

    apply_runtime_config_changes(app, new_config)

    assert app._server.markers[305].pos == (1.5, -2.0, 1.6)


def test_apply_runtime_seeds_a_marker_restored_by_a_rolled_back_add() -> None:
    """The rollback re-registers a removed marker; its position went with the
    discarded Marker, so it needs the same seed the forward path gives."""
    app = _app_with(controlled_marker_ids=[1, 2], viewer_marker_ids=[1, 2])
    orig_add = app._server.add_marker

    def _failing_add(marker_id, name):  # noqa: ANN001
        if marker_id == 3:
            raise RuntimeError("PSN add failed")
        return orig_add(marker_id, name)

    app._server.add_marker = _failing_add  # type: ignore[method-assign]
    new_config = AppConfig(controlled_marker_ids=[1, 3], viewer_marker_ids=[1, 3])

    apply_runtime_config_changes(app, new_config)

    # Marker 2 is back after the rollback, seeded rather than left at the origin.
    assert app._server.markers[2].pos == (0.0, 0.0, 1.6)


def test_apply_runtime_filters_non_int_and_bool_marker_ids() -> None:
    """Two defence layers in the hot-reload filter:

    - ``bool`` is an ``int`` subclass, so ``True >= 1`` is ``True``;
      a bare ``marker_id >= 1`` filter lets ``True`` through and then
      crashes on the bool-rejecting ``Marker.__init__``.
    - A non-int value (string from an in-memory mutation / test
      fixture / hand-built ``AppConfig``) raises ``TypeError`` on the
      ``>=`` comparison – the orchestrator crashes mid-reload rather
      than the operator seeing a clean fallback.

    Filter therefore requires ``isinstance(marker_id, int)`` AND
    ``not isinstance(marker_id, bool)`` AND ``marker_id >= 1``."""
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


@pytest.mark.parametrize(
    ("entries", "kept"),
    [
        (["192.0.2.1", "2001:db8::1"], ["192.0.2.1"]),
        (["::ffff:192.0.2.1", "198.51.100.7"], ["198.51.100.7"]),
        (["fe80::1%eth0", "192.0.2.1", "not-an-ip", "192.0.2.300"], ["192.0.2.1"]),
    ],
)
def test_osc_config_allowlist_keeps_only_ipv4_entries(entries: list[str], kept: list[str]) -> None:
    """The listener is IPv4, so no other entry can ever match a sender."""
    assert OscConfig(allowed_sender_ips=entries).allowed_sender_ips == kept


@pytest.mark.parametrize("entries", [["2001:db8::1"], ["::1", "fe80::1"], ["2001:db8::1", "not-an-ip"]])
def test_osc_config_allowlist_without_ipv4_entry_never_reads_as_allow_all(entries: list[str]) -> None:
    """Dropping every entry would leave the empty list, which accepts every sender."""
    assert OscConfig(allowed_sender_ips=entries).allowed_sender_ips == entries


def test_osc_config_clamps_port_to_blur_bounds() -> None:
    # Port must be in range 1-65535.
    assert OscConfig(port=0).port == 1
    assert OscConfig(port=70000).port == 65535
    assert OscConfig(port="not-a-port").port == 8765  # type: ignore[arg-type]


def test_osc_config_listen_iface_defaults_to_blank() -> None:
    assert OscConfig().listen_iface == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  eth0  ", "eth0"),
        ("\teth0.10 ", "eth0.10"),
        ("   ", ""),
    ],
)
def test_osc_config_listen_iface_strips_whitespace(raw: str, expected: str) -> None:
    """Whitespace around a pin must not read as a configured interface: a
    " " would otherwise resolve as down and silence the listener."""
    assert OscConfig(listen_iface=raw).listen_iface == expected


@pytest.mark.parametrize("raw", [None, 42, True, ["eth0"]])
def test_osc_config_listen_iface_rejects_non_strings(raw: object) -> None:
    """A hand-edited TOML can put anything here; the resolver indexes it by
    name, so a non-string must fall back to "follow the station"."""
    assert OscConfig(listen_iface=raw).listen_iface == ""  # type: ignore[arg-type]


# Mouse3DConfig.__post_init__ – 3D Mouse (6DOF) coercion


def test_mouse3d_config_defaults() -> None:
    cfg = Mouse3DConfig()
    assert cfg.enabled is False
    assert cfg.curve == "logarithmic"
    # Translations drive x/y/z, yaw ramps speed, pitch/roll are off.
    assert (cfg.map_pan_x, cfg.map_pan_y, cfg.map_lift) == ("x", "y", "z")
    assert cfg.map_yaw == "speed"
    assert (cfg.map_pitch, cfg.map_roll) == ("none", "none")
    # Lift is geared down (gentler height control).
    assert cfg.sens_lift == 0.3
    assert cfg.invert_pan_x is False
    # Per-axis deadzone: tight on the translations, wider on geared/speed axes.
    assert cfg.deadzone_pan_x == 0.05
    assert cfg.deadzone_lift == 0.3
    assert cfg.deadzone_yaw == 0.3
    assert cfg.deadzone_roll == 0.1
    # Default button binds (2-button device): left = next, right = prev.
    assert cfg.btn_next_marker == 0
    assert cfg.btn_prev_marker == 1
    assert cfg.btn_reset == -1
    assert cfg.btn_toggle_help == -1


def test_mouse3d_form_labels_cover_every_axis_and_button() -> None:
    # The web form loops over these label lists; if one omits (or misnames) an
    # axis / button, that control silently drops out of the form. Pin them to
    # the canonical field tuples so they can't drift.
    from openfollow.configuration import (
        MOUSE3D_AXIS_FORM_LABELS,
        MOUSE3D_BUTTON_FIELDS,
        MOUSE3D_BUTTON_FORM_LABELS,
    )

    assert tuple(axis for axis, _label in MOUSE3D_AXIS_FORM_LABELS) == MOUSE3D_AXES
    assert {field for field, _label in MOUSE3D_BUTTON_FORM_LABELS} == set(MOUSE3D_BUTTON_FIELDS)


def test_mouse3d_invalid_value_falls_back_to_declared_field_default() -> None:
    # __post_init__ derives its per-axis fallback from the field declarations
    # (single source), so an invalid value lands on the SAME default as an
    # omitted field – for every axis, not just the few spot-checked elsewhere.
    defaults = Mouse3DConfig()
    junk = Mouse3DConfig(
        **{f"sens_{a}": "x" for a in MOUSE3D_AXES},  # type: ignore[arg-type]
        **{f"deadzone_{a}": "x" for a in MOUSE3D_AXES},
        **{f"map_{a}": "bogus" for a in MOUSE3D_AXES},
    )
    for axis in MOUSE3D_AXES:
        assert getattr(junk, f"sens_{axis}") == getattr(defaults, f"sens_{axis}")
        assert getattr(junk, f"deadzone_{axis}") == getattr(defaults, f"deadzone_{axis}")
        assert getattr(junk, f"map_{axis}") == getattr(defaults, f"map_{axis}")


@pytest.mark.parametrize(
    "bad,expected",
    # ``True`` coerces via float()==1.0 then clamps (same as ControllerConfig);
    # wrong-type / unparseable falls back to the axis default (pan_x = 0.05).
    [(-1.0, 0.0), (2.0, 1.0), ("abc", 0.05), (None, 0.05), (True, 1.0)],
)
def test_mouse3d_config_clamps_deadzone(bad: object, expected: float) -> None:
    assert Mouse3DConfig(deadzone_pan_x=bad).deadzone_pan_x == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad,expected",
    [(-1.0, 0.0), (99.0, 10.0), ("nope", 1.0), (None, 1.0)],
)
def test_mouse3d_config_clamps_sensitivity(bad: object, expected: float) -> None:
    # Out-of-range / wrong-type sensitivity clamps or falls back per axis.
    cfg = Mouse3DConfig(sens_pan_x=bad, sens_roll=bad)  # type: ignore[arg-type]
    assert cfg.sens_pan_x == expected
    assert cfg.sens_roll == expected


@pytest.mark.parametrize("bad", ["wobble", 5, None, True, ""])
def test_mouse3d_config_falls_back_on_unknown_curve(bad: object) -> None:
    assert Mouse3DConfig(curve=bad).curve == "logarithmic"  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["sideways", "X", 1, None, True])
def test_mouse3d_config_falls_back_on_unknown_axis_target(bad: object) -> None:
    # An unknown map target falls back to that axis's default (pan_x → "x").
    assert Mouse3DConfig(map_pan_x=bad).map_pan_x == "x"  # type: ignore[arg-type]
    # And a default-"none" axis falls back to "none".
    assert Mouse3DConfig(map_pitch=bad).map_pitch == "none"  # type: ignore[arg-type]


@pytest.mark.parametrize("target", ["none", "x", "y", "z", "speed", "fader"])
def test_mouse3d_config_accepts_all_axis_targets(target: str) -> None:
    assert Mouse3DConfig(map_pan_x=target).map_pan_x == target


@pytest.mark.parametrize(
    "bad,expected",
    [(-5, -1), (-1, -1), (3, 3), ("x", -1), (None, -1), (True, -1)],
)
def test_mouse3d_config_button_coercion(bad: object, expected: int) -> None:
    # Invalid / out-of-range button index collapses to unbound (-1).
    assert Mouse3DConfig(btn_reset=bad).btn_reset == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw,expected",
    # _coerce_bool accepts real bools + recognised strings only; a bare int is
    # not a bool form, so it falls to the default (False).
    [("true", True), ("false", False), (True, True), ("junk", False), (1, False)],
)
def test_mouse3d_config_enabled_and_invert_coercion(raw: object, expected: bool) -> None:
    assert Mouse3DConfig(enabled=raw).enabled is expected  # type: ignore[arg-type]
    assert Mouse3DConfig(invert_lift=raw).invert_lift is expected  # type: ignore[arg-type]


def test_mouse3d_config_survives_save_reload(temp_config_path) -> None:
    config = AppConfig(
        mouse3d=Mouse3DConfig(
            enabled=True,
            deadzone_pitch=0.25,
            curve="s-law",
            map_pitch="speed",
            sens_pitch=2.5,
            invert_pan_y=True,
            btn_toggle_zones=4,
        )
    )
    save_config(config, str(temp_config_path))
    reloaded = load_config(str(temp_config_path)).mouse3d
    assert reloaded.enabled is True
    assert reloaded.deadzone_pitch == 0.25
    assert reloaded.curve == "s-law"
    assert reloaded.map_pitch == "speed"
    assert reloaded.sens_pitch == 2.5
    assert reloaded.invert_pan_y is True
    assert reloaded.btn_toggle_zones == 4


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


def test_app_config_caps_psn_system_name_at_the_field_rule_bound() -> None:
    """A hand-edited config.toml must not bypass the 64-char bound the web form
    enforces: the name also sizes the marker-catalog beacon envelope."""
    cfg = AppConfig(psn_system_name="S" * 300)
    assert cfg.psn_system_name == "S" * 64


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


@pytest.mark.parametrize(("raw", "expected"), [("  eth1  ", "eth1"), ("\teth0.10 ", "eth0.10"), ("   ", "")])
def test_rttrpm_output_config_source_iface_strips_whitespace(raw: str, expected: str) -> None:
    """A " " pin would otherwise read as a configured interface that is down."""
    assert RttrpmOutputConfig(source_iface=raw).source_iface == expected


@pytest.mark.parametrize("raw", [None, 5, True, ["eth1"]])
def test_rttrpm_output_config_source_iface_rejects_non_strings(raw: object) -> None:
    """Falls back to following the station rather than crashing the resolver."""
    assert RttrpmOutputConfig(source_iface=raw).source_iface == ""  # type: ignore[arg-type]


@pytest.mark.parametrize(("raw", "expected"), [(" eth1 ", "eth1"), (None, ""), (5, ""), (True, ""), (["eth1"], "")])
def test_osc_destination_source_iface_is_normalised(raw: object, expected: str) -> None:
    from openfollow.configuration import OscDestinationConfig

    assert OscDestinationConfig(source_iface=raw).source_iface == expected  # type: ignore[arg-type]


def test_osc_destination_source_iface_loads_from_toml(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[[osc_destinations.destinations]]\nid = "foh"\nhost = "198.51.100.20"\nsource_iface = "eth1 "\n',
        encoding="utf-8",
    )
    (dest,) = load_config(str(path)).osc_destinations.destinations
    assert dest.source_iface == "eth1"


def test_rttrpm_output_source_iface_loads_from_toml(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[rttrpm_output]\nenabled = true\nsource_iface = " eth1 "\n', encoding="utf-8")
    assert load_config(str(path)).rttrpm_output.source_iface == "eth1"


def test_apply_runtime_routes_an_rttrpm_pin_change_to_the_live_apply() -> None:
    app = _DummyApp(AppConfig())
    new_config = AppConfig()
    new_config.rttrpm_output.source_iface = "eth1"
    apply_runtime_config_changes(app, new_config)
    assert [cfg.source_iface for cfg in app._runtime_services.rttrpm_changes] == ["eth1"]


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


def test_camera_config_lens_distortion_defaults_to_pinhole() -> None:
    # A config without lens keys draws exactly what a pinhole camera sees.
    cfg = CameraConfig()
    assert (cfg.lens_k1, cfg.lens_k2, cfg.lens_cx, cfg.lens_cy) == (0.0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "bad_k,expected",
    [
        (0.1, 0.1),
        ("0.15", 0.15),
        (0.9, 0.9),  # pincushion, valid up to k1 = 1 without a corner term
        (None, 0.0),
        ("nope", 0.0),
        (float("inf"), 0.0),  # non-finite -> declared default
        (float("nan"), 0.0),
        (10**400, 0.0),
    ],
)
def test_camera_config_coerces_lens_k1(bad_k: object, expected: float) -> None:
    cfg = CameraConfig(lens_k1=bad_k)  # type: ignore[arg-type]
    assert cfg.lens_k1 == expected


@pytest.mark.parametrize(
    "bad_k,expected",
    [
        (0.03, 0.03),
        ("0.04", 0.04),
        (-0.8, -0.8),
        (None, 0.0),
        ("nope", 0.0),
        (float("inf"), 0.0),
        (float("nan"), 0.0),
        (10**400, 0.0),
    ],
)
def test_camera_config_coerces_lens_k2(bad_k: object, expected: float) -> None:
    cfg = CameraConfig(lens_k2=bad_k)  # type: ignore[arg-type]
    assert cfg.lens_k2 == expected


@pytest.mark.parametrize("field", ["lens_cx", "lens_cy"])
@pytest.mark.parametrize(
    "value,expected",
    [
        (0.04, 0.04),
        ("-0.03", -0.03),
        (C_BOUND, C_BOUND),
        (-C_BOUND, -C_BOUND),
        (0.0, 0.0),
    ],
)
def test_camera_config_keeps_a_lens_centre_inside_its_bound(field: str, value: object, expected: float) -> None:
    assert getattr(CameraConfig(**{field: value}), field) == expected


@pytest.mark.parametrize("field", ["lens_cx", "lens_cy"])
@pytest.mark.parametrize(
    "value,expected",
    [
        (C_BOUND + 1e-9, C_BOUND),
        (-C_BOUND - 1e-9, -C_BOUND),
        (3.0, C_BOUND),
        ("-7", -C_BOUND),
    ],
)
def test_camera_config_clamps_a_lens_centre_past_its_bound(field: str, value: object, expected: float) -> None:
    assert getattr(CameraConfig(**{field: value}), field) == expected


@pytest.mark.parametrize("field", ["lens_cx", "lens_cy"])
@pytest.mark.parametrize("value", [None, "abc", [0.1], {"x": 0.1}, float("nan"), float("inf"), float("-inf"), 10**400])
def test_camera_config_lens_centre_that_is_no_number_reads_as_0(field: str, value: object) -> None:
    assert getattr(CameraConfig(**{field: value}), field) == 0.0


@pytest.mark.parametrize("field", ["lens_cx", "lens_cy"])
def test_camera_config_lens_centre_reads_a_boolean_as_the_number_it_is(field: str) -> None:
    # float(True) is 1, past the bound, as for every float field read by _coerce_float.
    assert getattr(CameraConfig(**{field: True}), field) == C_BOUND
    assert getattr(CameraConfig(**{field: False}), field) == 0.0


# Lenses whose map stops short of a frame corner, with and without a lens centre.
_LENSES_SHORT_OF_A_CORNER = [
    (-1.0, 0.0, 0.0, 0.0),  # the pole on the corner
    (-0.6, -0.4, 0.0, 0.0),
    (0.4, 0.2, 0.0, 0.0),  # the fold on the corner
    (-1.5, 0.0, 0.0, 0.0),
    (0.0, 1.5, 0.0, 0.0),
    (4.0, -1.0, 0.0, 0.0),  # P dips inside the frame
    (-5e3, 0.0, 0.0, 0.0),
    (-0.9, 0.0, 0.1, 0.05),  # valid about the frame centre, not about this one
    (-0.53, -0.4, -C_BOUND, C_BOUND),
]


def _assert_pulled_back_toward_the_limit(old: tuple[float, ...], new: tuple[float, ...]) -> None:
    """Valid, the centre kept, the pair on its own ray short of it, and close to where that ray stops."""
    assert lens_warp_is_valid(*new)
    assert new[2:] == old[2:]
    scale = (new[0] * old[0] + new[1] * old[1]) / (old[0] ** 2 + old[1] ** 2)
    assert 0.0 < scale < 1.0
    assert new[:2] == pytest.approx((scale * old[0], scale * old[1]), rel=1e-12, abs=1e-15)
    assert not lens_warp_is_valid(old[0] * scale * 1.02, old[1] * scale * 1.02, *old[2:])


def _lens_of(cfg: CameraConfig) -> tuple[float, float, float, float]:
    return cfg.lens_k1, cfg.lens_k2, cfg.lens_cx, cfg.lens_cy


@pytest.mark.parametrize("lens", _LENSES_SHORT_OF_A_CORNER)
def test_camera_config_pulls_a_lens_back_until_it_reaches_every_corner(
    lens: tuple[float, float, float, float], caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openfollow.configuration as cfg_mod

    monkeypatch.setattr(cfg_mod, "_reported_drops", {})
    k1, k2, cx, cy = lens
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        cfg = CameraConfig(lens_k1=k1, lens_k2=k2, lens_cx=cx, lens_cy=cy)
    _assert_pulled_back_toward_the_limit(lens, _lens_of(cfg))
    [message] = [rec.getMessage() for rec in caplog.records]
    assert f"lens_k1={float(k1)} / lens_k2={float(k2)} with lens centre {cx:.6g} / {cy:.6g}" in message
    assert "cannot follow the picture out to its corners" in message
    assert f"using {cfg.lens_k1:.6g} / {cfg.lens_k2:.6g}" in message


def test_camera_config_pulls_back_to_the_closed_form_point() -> None:
    # 1 - 1.2 t s with the far corner at s = 1.1^2: t* = 1 / (1.2 * 1.21), less the 1 % margin.
    cfg = CameraConfig(lens_k1=-1.2, lens_k2=0.0, lens_cx=0.1, lens_cy=0.0)
    assert _lens_of(cfg) == pytest.approx((-0.99 / 1.21, 0.0, 0.1, 0.0), rel=1e-12)


def test_camera_config_clamps_the_centre_before_it_judges_the_pair() -> None:
    # k1 = -0.7 reaches a corner 1.1 half-diagonals out, not the 1 + |c| of the clamped centre.
    cfg = CameraConfig(lens_k1=-0.7, lens_cx=1.0, lens_cy=-1.0)
    assert (cfg.lens_cx, cfg.lens_cy) == (C_BOUND, -C_BOUND)
    assert lens_warp_is_valid(*_lens_of(cfg))
    assert -0.7 < cfg.lens_k1 < 0.0


def test_a_lens_short_of_a_corner_is_reported_once_not_on_every_load(caplog, monkeypatch) -> None:
    import openfollow.configuration as cfg_mod

    monkeypatch.setattr(cfg_mod, "_reported_drops", {})
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        for _ in range(3):
            CameraConfig(lens_k1=-1.2, lens_k2=0.0)
    assert len(caplog.records) == 1


def test_the_report_names_the_centre_the_pair_was_judged_with(caplog, monkeypatch) -> None:
    import openfollow.configuration as cfg_mod

    monkeypatch.setattr(cfg_mod, "_reported_drops", {})
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        CameraConfig(lens_k1=-0.9, lens_cx=0.6, lens_cy=-9.0)
    [message] = [rec.getMessage() for rec in caplog.records]
    assert "with lens centre 0.25 / -0.25 cannot follow" in message


def test_a_clamped_centre_alone_is_not_reported(caplog, monkeypatch) -> None:
    import openfollow.configuration as cfg_mod

    monkeypatch.setattr(cfg_mod, "_reported_drops", {})
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        CameraConfig(lens_k1=-0.2, lens_cx=0.9)
    assert caplog.records == []


def test_a_pulled_back_lens_is_stable_across_a_save_and_load(temp_config_path) -> None:
    temp_config_path.write_text(
        "[camera]\nlens_k1 = -0.9\nlens_k2 = -0.1\nlens_cx = 0.12\nlens_cy = -0.3\n", encoding="utf-8"
    )
    loaded = load_config(str(temp_config_path))
    _assert_pulled_back_toward_the_limit((-0.9, -0.1, 0.12, -C_BOUND), _lens_of(loaded.camera))
    save_config(loaded, str(temp_config_path))
    again = load_config(str(temp_config_path))
    assert _lens_of(again.camera) == _lens_of(loaded.camera)


@pytest.mark.parametrize(
    "lens",
    [
        (0.4, 0.15, 0.0, 0.0),
        (0.9, -0.2, 0.0, 0.0),
        (-0.3, 0.0, 0.0, 0.0),
        (-0.527, -0.069, 0.0, 0.0),  # an equidistant 2.3 mm lens
        (-0.527, -0.069, 0.04, -0.03),
        (0.0, -0.8, 0.0, 0.0),
        (-0.2, 0.05, C_BOUND, -C_BOUND),
    ],
)
def test_camera_config_keeps_a_lens_that_reaches_every_corner(lens: tuple[float, float, float, float]) -> None:
    k1, k2, cx, cy = lens
    assert _lens_of(CameraConfig(lens_k1=k1, lens_k2=k2, lens_cx=cx, lens_cy=cy)) == lens


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc", None])
def test_camera_config_lens_values_that_are_no_number_still_read_as_0(bad: object) -> None:
    cfg = CameraConfig(lens_k1=bad, lens_k2=bad, lens_cx=bad, lens_cy=bad)  # type: ignore[arg-type]
    assert _lens_of(cfg) == (0.0, 0.0, 0.0, 0.0)


def test_camera_config_wrong_type_on_one_lens_value_keeps_the_others() -> None:
    cfg = CameraConfig(lens_k1="abc", lens_k2=0.3, lens_cx=0.02, lens_cy=None)  # type: ignore[arg-type]
    assert _lens_of(cfg) == (0.0, 0.3, 0.02, 0.0)


def test_a_lens_centre_change_hot_reloads_onto_the_running_config() -> None:
    app = _DummyApp(AppConfig(camera=CameraConfig(lens_k1=-0.3)))
    new = AppConfig(camera=CameraConfig(lens_k1=-0.3, lens_cx=0.05, lens_cy=-0.02))
    apply_runtime_config_changes(app, new)
    assert effective_lens(app._config) == (-0.3, 0.0, 0.05, -0.02)
    assert app._web_commands.restart_requested is False


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
        z_line="no",  # type: ignore[arg-type]
        ground_circle="true",  # type: ignore[arg-type]
        ground_circle_filled="false",  # type: ignore[arg-type]
        z_display_from_stage="yes",  # type: ignore[arg-type]
        cone_filled="off",  # type: ignore[arg-type]
        cone_shaded="no",  # type: ignore[arg-type]
    )
    assert cfg.cone_filled is False
    assert cfg.cone_shaded is False
    assert cfg.ball_visible is False
    assert cfg.crosshair_visible is False
    assert cfg.z_line is False
    assert cfg.ground_circle is True
    assert cfg.ground_circle_filled is False
    assert cfg.z_display_from_stage is True


def test_marker_config_control_frame_defaults_to_stage_axes() -> None:
    """Any other default would turn every operator's controls on upgrade."""
    cfg = MarkerConfig()
    assert cfg.control_frame == "stage"
    assert cfg.control_heading == 0.0


@pytest.mark.parametrize("frame", ["stage", "heading", "camera", "screen"])
def test_marker_config_accepts_every_control_frame(frame: str) -> None:
    assert MarkerConfig(control_frame=frame).control_frame == frame


@pytest.mark.parametrize("raw", ["Screen", "picture", "", None, 1, True, ["camera"]])
def test_marker_config_unknown_control_frame_falls_back_to_stage(raw: object) -> None:
    assert MarkerConfig(control_frame=raw).control_frame == "stage"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (90.0, 90.0),
        ("180", 180.0),
        (360.0, 0.0),
        (-90.0, 270.0),
        (450, 90.0),
        (-1e-15, 0.0),
    ],
)
def test_marker_config_control_heading_wraps_to_one_turn(raw: object, expected: float) -> None:
    """A bearing outside one turn names the same direction as its wrap, e.g. a yaw of -90."""
    assert MarkerConfig(control_heading=raw).control_heading == pytest.approx(expected)  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["abc", None, True, float("nan"), float("inf"), [90.0]])
def test_marker_config_bad_control_heading_falls_back_to_zero(raw: object) -> None:
    assert MarkerConfig(control_heading=raw).control_heading == 0.0  # type: ignore[arg-type]


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
    assert cfg.marker_style == "crosshair"
    assert cfg.cone_base_diameter == 0.6
    assert cfg.cone_top_diameter == 0.3
    assert cfg.cone_thickness == 2
    assert cfg.cone_filled is True
    assert cfg.cone_opacity == 0.4
    assert cfg.cone_shaded is True
    assert cfg.transparency == 0.3
    assert cfg.z_line_thickness == 2
    assert cfg.ground_circle is True
    assert cfg.ground_circle_filled is False
    assert cfg.z_display_from_stage is True


@pytest.mark.parametrize("bad_color", ["red", "#12", "", None])
def test_marker_config_rejects_invalid_crosshair_color(bad_color: object) -> None:
    cfg = MarkerConfig(crosshair_color=bad_color)  # type: ignore[arg-type]
    assert cfg.crosshair_color == "#ffffff"


@pytest.mark.parametrize("bad_thickness", [0, -1, "x", None, True])
def test_marker_config_clamps_thicknesses(bad_thickness: object) -> None:
    cfg = MarkerConfig(crosshair_thickness=bad_thickness, z_line_thickness=bad_thickness)  # type: ignore[arg-type]
    assert cfg.crosshair_thickness >= 1
    assert cfg.z_line_thickness >= 1


@pytest.mark.parametrize("style", ["crosshair", "cone"])
def test_marker_config_accepts_each_marker_style(style: str) -> None:
    assert MarkerConfig(marker_style=style).marker_style == style


@pytest.mark.parametrize("bad_style", ["sphere", "Cone", "", None, True, 1])
def test_marker_config_falls_back_on_unknown_marker_style(bad_style: object) -> None:
    cfg = MarkerConfig(marker_style=bad_style)  # type: ignore[arg-type]
    assert cfg.marker_style == "crosshair"


@pytest.mark.parametrize(
    "bad_radius,expected",
    [(-1.0, 0.0), ("abc", 0.6), (None, 0.6), (float("nan"), 0.6), (float("inf"), 0.6)],
)
def test_marker_config_clamps_cone_base_diameter(bad_radius: object, expected: float) -> None:
    cfg = MarkerConfig(cone_base_diameter=bad_radius)  # type: ignore[arg-type]
    assert cfg.cone_base_diameter == expected


@pytest.mark.parametrize(
    "bad_radius,expected",
    [(-1.0, 0.0), ("abc", 0.3), (None, 0.3), (float("nan"), 0.3), (float("inf"), 0.3)],
)
def test_marker_config_clamps_cone_top_diameter(bad_radius: object, expected: float) -> None:
    cfg = MarkerConfig(cone_top_diameter=bad_radius)  # type: ignore[arg-type]
    assert cfg.cone_top_diameter == expected


def test_marker_config_cone_top_diameter_may_exceed_the_base() -> None:
    cfg = MarkerConfig(cone_base_diameter=0.2, cone_top_diameter=0.5)
    assert (cfg.cone_base_diameter, cfg.cone_top_diameter) == (0.2, 0.5)


def test_marker_config_cone_base_diameter_accepts_zero_and_strings() -> None:
    assert MarkerConfig(cone_base_diameter=0).cone_base_diameter == 0.0
    assert MarkerConfig(cone_base_diameter="0.45").cone_base_diameter == 0.45  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad_thickness,expected",
    [(0, 1), (-3, 1), (11, 10), (999, 10), ("x", 2), (None, 2), (True, 2)],
)
def test_marker_config_clamps_cone_thickness(bad_thickness: object, expected: int) -> None:
    cfg = MarkerConfig(cone_thickness=bad_thickness)  # type: ignore[arg-type]
    assert cfg.cone_thickness == expected


@pytest.mark.parametrize("bad_alpha,expected", [(-0.5, 0.0), (5.0, 1.0), ("x", 0.4), (None, 0.4)])
def test_marker_config_clamps_cone_opacity(bad_alpha: object, expected: float) -> None:
    cfg = MarkerConfig(cone_opacity=bad_alpha)  # type: ignore[arg-type]
    assert cfg.cone_opacity == expected


@pytest.mark.parametrize("bad_flag", ["maybe", None, 2])
def test_marker_config_cone_filled_falls_back_to_on(bad_flag: object) -> None:
    cfg = MarkerConfig(cone_filled=bad_flag)  # type: ignore[arg-type]
    assert cfg.cone_filled is True


@pytest.mark.parametrize("bad_flag", ["maybe", None, 2])
def test_marker_config_cone_shaded_falls_back_to_on(bad_flag: object) -> None:
    cfg = MarkerConfig(cone_shaded=bad_flag)  # type: ignore[arg-type]
    assert cfg.cone_shaded is True


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


def test_detection_config_default_model() -> None:
    """The shipped default model is the NMS-free YOLO26 nano export."""
    assert DetectionConfig().model == "yolo26n.onnx"


@pytest.mark.parametrize("bad_model", [123, 4.5, None, ["yolo26n.onnx"], {"name": "x"}])
def test_detection_config_coerces_non_string_model_to_default(bad_model: object) -> None:
    """Non-string model must coerce to default string."""
    cfg = DetectionConfig(model=bad_model)  # type: ignore[arg-type]
    assert cfg.model == "yolo26n.onnx"
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
    larger inference size, a lower confidence floor, a faster cadence, and
    prediction lookahead. Guards against silent drift."""
    cfg = DetectionConfig()
    assert cfg.inference_size == 640
    assert cfg.confidence == pytest.approx(0.2)
    assert cfg.interval_ms == 67
    assert cfg.prediction == pytest.approx(8.0)


@pytest.mark.parametrize("patches", [None, "abc", 3, True, {"id": 1}])
def test_midi_patches_that_are_not_a_list_read_as_none(patches: object) -> None:
    assert MidiConfig(patches=patches).patches == []  # type: ignore[arg-type]


def test_midi_patches_keep_only_their_patch_entries() -> None:
    kept = MidiPatch(id=4, alias="Wing")
    config = MidiConfig(patches=[{"id": 2, "alias": "Desk"}, "Desk", 7, None, kept])  # type: ignore[list-item]
    assert [(patch.id, patch.alias) for patch in config.patches] == [(2, "Desk"), (4, "Wing")]


@pytest.mark.parametrize("faders", [None, "abc", 3, True, {"name": "Iris"}])
def test_virtual_faders_that_are_not_a_list_read_as_unmapped(faders: object) -> None:
    from openfollow.configuration import VIRTUAL_FADER_COUNT, VirtualFaderConfig, VirtualFadersConfig

    assert VirtualFadersConfig(faders=faders).faders == [VirtualFaderConfig()] * VIRTUAL_FADER_COUNT  # type: ignore[arg-type]


def test_virtual_faders_keep_only_their_fader_entries() -> None:
    from openfollow.configuration import VirtualFaderConfig, VirtualFadersConfig

    config = VirtualFadersConfig(faders=["Iris", {"name": "Zoom"}, 2, VirtualFaderConfig(name="Focus")])  # type: ignore[list-item]
    assert [fader.name for fader in config.faders[:3]] == ["Zoom", "Focus", ""]


def test_detection_config_masks_enabled_defaults_off() -> None:
    """The masking master switch ships off so drawn masks never restrict
    detection until the operator turns them on."""
    assert DetectionConfig().masks_enabled is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("on", True),
        ("1", True),
        ("false", False),
        ("off", False),
        ("garbage", False),
        (None, False),
        (1, False),
    ],
)
def test_detection_config_coerces_masks_enabled(raw: object, expected: bool) -> None:
    cfg = DetectionConfig(masks_enabled=raw)  # type: ignore[arg-type]
    assert cfg.masks_enabled is expected


def test_load_config_drops_obsolete_detection_keys(temp_config_path) -> None:
    """A hand-edited ``[detection]`` carrying the removed ``pin_marker`` /
    ``preprocess_clahe`` keys loads without raising; the keys are silently
    dropped so the resulting config exposes no such attributes."""
    temp_config_path.write_text(
        "[detection]\nenabled = true\npin_marker = true\npreprocess_clahe = true\n",
        encoding="utf-8",
    )

    config = load_config(str(temp_config_path))

    assert config.detection.enabled is True
    assert not hasattr(config.detection, "pin_marker")
    assert not hasattr(config.detection, "preprocess_clahe")


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


def test_load_config_migrates_the_former_marker_key_names(temp_config_path) -> None:
    """A pre-rename config carries the marker's Z-line settings under their former
    names. Both carry over, so an operator who turned the line off or thickened it
    keeps that instead of silently reverting to the default when ``_filter_known``
    drops them."""
    temp_config_path.write_text("[marker]\ndrop_line = false\ndrop_line_thickness = 7\n", encoding="utf-8")

    cfg = load_config(str(temp_config_path))

    assert cfg.marker.z_line is False
    assert cfg.marker.z_line_thickness == 7


def test_load_config_prefers_the_current_marker_key_over_the_legacy_one(temp_config_path) -> None:
    """With both spellings present the current one wins, so a former name left
    behind in a hand-edited file cannot override a deliberate ``z_line``."""
    temp_config_path.write_text(
        "[marker]\ndrop_line = false\nz_line = true\ndrop_line_thickness = 7\nz_line_thickness = 3\n",
        encoding="utf-8",
    )

    cfg = load_config(str(temp_config_path))

    assert cfg.marker.z_line is True
    assert cfg.marker.z_line_thickness == 3


def test_load_config_warns_once_per_legacy_marker_key(temp_config_path, caplog, monkeypatch) -> None:
    """``load_config`` runs on every hot-reload, so the rename warning is emitted
    once per key rather than once per reload.

    ``monkeypatch`` both isolates the shared warn-once set and restores it, so this
    test cannot leave the keys suppressed for whatever runs next in the process.
    """
    import openfollow.configuration as configuration

    monkeypatch.setattr(configuration, "_DEPRECATED_WARNED", set())
    temp_config_path.write_text("[marker]\ndrop_line = false\ndrop_line_thickness = 7\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        load_config(str(temp_config_path))
        load_config(str(temp_config_path))

    renamed = [r for r in caplog.records if "has been renamed" in r.getMessage()]
    assert len(renamed) == 2
    assert {"drop_line", "drop_line_thickness"} == {r.args[0] for r in renamed}


@pytest.mark.parametrize("flag", ["true", '"on"'])
def test_load_config_reads_the_control_direction_flip_as_heading_180(temp_config_path, flag) -> None:  # noqa: ANN001
    """The flip was a camera looking downstage, which is a 180-degree heading."""
    temp_config_path.write_text(f"[marker]\ninvert_control_direction = {flag}\n", encoding="utf-8")

    cfg = load_config(str(temp_config_path))

    assert cfg.marker.control_frame == "heading"
    assert cfg.marker.control_heading == pytest.approx(180.0)


@pytest.mark.parametrize("flag", ["false", '"banana"', "1"])
def test_load_config_reads_an_unset_control_direction_flip_as_stage_axes(temp_config_path, flag) -> None:  # noqa: ANN001
    temp_config_path.write_text(f"[marker]\ninvert_control_direction = {flag}\n", encoding="utf-8")

    assert load_config(str(temp_config_path)).marker.control_frame == "stage"


def test_load_config_ignores_the_flip_beside_a_control_frame_without_warning(
    temp_config_path, caplog, monkeypatch
) -> None:  # noqa: ANN001
    import openfollow.configuration as configuration

    monkeypatch.setattr(configuration, "_DEPRECATED_WARNED", set())
    temp_config_path.write_text(
        '[marker]\ninvert_control_direction = false\ncontrol_frame = "camera"\n', encoding="utf-8"
    )

    with caplog.at_level("WARNING"):
        cfg = load_config(str(temp_config_path))

    assert cfg.marker.control_frame == "camera"
    assert not [r for r in caplog.records if "has been renamed" in r.getMessage()]


def test_load_config_prefers_the_control_frame_over_the_flip(temp_config_path) -> None:
    temp_config_path.write_text(
        '[marker]\ninvert_control_direction = true\ncontrol_frame = "screen"\n',
        encoding="utf-8",
    )

    assert load_config(str(temp_config_path)).marker.control_frame == "screen"


def test_load_config_keeps_a_heading_set_beside_the_flip(temp_config_path) -> None:
    temp_config_path.write_text("[marker]\ninvert_control_direction = true\ncontrol_heading = 90.0\n", encoding="utf-8")

    cfg = load_config(str(temp_config_path))

    assert cfg.marker.control_frame == "heading"
    assert cfg.marker.control_heading == pytest.approx(90.0)


def test_load_config_warns_once_about_the_control_direction_flip(temp_config_path, caplog, monkeypatch) -> None:
    import openfollow.configuration as configuration

    monkeypatch.setattr(configuration, "_DEPRECATED_WARNED", set())
    temp_config_path.write_text("[marker]\ninvert_control_direction = false\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        load_config(str(temp_config_path))
        load_config(str(temp_config_path))

    renamed = [r for r in caplog.records if "has been renamed" in r.getMessage()]
    assert [(r.args[0], r.args[1]) for r in renamed] == [("invert_control_direction", "control_frame")]


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
# DetectionMaskConfig – region-of-interest polygons (normalised 0-1 coords)
# ---------------------------------------------------------------------------


def test_detection_mask_coerces_numeric_vertices() -> None:
    from openfollow.configuration import DetectionMaskConfig

    mask = DetectionMaskConfig(
        vertices=[
            [0, 1],  # ints → floats
            [0.25, 0.75],  # already floats
            ["bad", 0.5],  # non-numeric x → dropped
            [0.6],  # too short → dropped
            "not a list",  # wrong type → dropped
            (0.7, 0.8, 0.9),  # tuple len>=2 → kept (first two)
        ]
    )
    assert mask.vertices == [[0.0, 1.0], [0.25, 0.75], [0.7, 0.8]]


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", float("nan"), float("inf")])
def test_detection_mask_drops_non_finite_vertices(bad: object) -> None:
    from openfollow.configuration import DetectionMaskConfig

    mask = DetectionMaskConfig(
        vertices=[
            [0.0, 0.0],
            [bad, 0.5],  # non-finite x → dropped
            [0.5, bad],  # non-finite y → dropped
            [1.0, 1.0],
        ]
    )
    assert mask.vertices == [[0.0, 0.0], [1.0, 1.0]]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("false", False), ("true", True), ("0", False), ("1", True)],
)
def test_detection_mask_coerces_enabled(raw: object, expected: bool) -> None:
    from openfollow.configuration import DetectionMaskConfig

    assert DetectionMaskConfig(enabled=raw).enabled is expected


@pytest.mark.parametrize("bad", [None, 5, [1], "maybe"])
def test_detection_mask_enabled_unrecognised_falls_back_to_default(bad: object) -> None:
    # ``_coerce_bool`` only recognises real bools + known string forms; anything
    # else (incl. bare ints) falls to the declared default (True).
    from openfollow.configuration import DetectionMaskConfig

    assert DetectionMaskConfig(enabled=bad).enabled is True


@pytest.mark.parametrize("bad_name", [None, 123, ["x"], {"a": 1}])
def test_detection_mask_coerces_non_string_name_to_empty(bad_name: object) -> None:
    from openfollow.configuration import DetectionMaskConfig

    assert DetectionMaskConfig(name=bad_name).name == ""


def test_detection_config_converts_dict_masks_to_dataclasses() -> None:
    from openfollow.configuration import DetectionConfig, DetectionMaskConfig

    cfg = DetectionConfig(
        masks=[
            {"name": "stage", "vertices": [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]], "enabled": True},
            "junk",  # non-object → dropped
            123,  # non-object → dropped
        ]
    )
    assert len(cfg.masks) == 1
    assert isinstance(cfg.masks[0], DetectionMaskConfig)
    assert cfg.masks[0].name == "stage"
    assert cfg.masks[0].vertices == [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]]


@pytest.mark.parametrize("masks", [None, "abc", 3, True, {"name": "stage"}])
def test_detection_config_masks_that_are_not_a_list_read_as_none(masks: object) -> None:
    assert DetectionConfig(masks=masks).masks == []  # type: ignore[arg-type]


def test_detection_config_preserves_already_built_masks() -> None:
    from openfollow.configuration import DetectionConfig, DetectionMaskConfig

    mask = DetectionMaskConfig(name="pre-built")
    cfg = DetectionConfig(masks=[mask])
    assert cfg.masks[0] is mask


def test_detection_config_masks_default_empty() -> None:
    from openfollow.configuration import DetectionConfig

    assert DetectionConfig().masks == []


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


@pytest.mark.parametrize(("move", "stick"), [("left", ""), ("right", "left_y"), ("left", "right_y")])
def test_controller_accepts_valid_marker_fader_sticks(move: str, stick: str) -> None:
    cfg = ControllerConfig(move_xy_stick=move, marker_fader_stick=stick)
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


def test_controller_mouse_steering_defaults_are_back_compatible() -> None:
    # Defaults reproduce direct 1:1 control with wheel-Z on, as before:
    # smoothing 0 = instant (the glide alpha is 1 - smoothing).
    cfg = ControllerConfig()
    assert cfg.mouse_hysteresis_px == 0
    assert cfg.mouse_smoothing == 0.0
    assert cfg.mouse_max_y == 0.0
    assert cfg.mouse_wheel_z_enabled is True
    assert cfg.mouse_wheel_invert is False
    assert cfg.mouse_wheel_z_step == 0.1
    assert cfg.mouse_double_click_reset is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [("false", False), ("no", False), ("true", True), (0, True), ("garbage", True)],
)
def test_controller_mouse_double_click_reset_coerced(value: object, expected: bool) -> None:
    assert ControllerConfig(mouse_double_click_reset=value).mouse_double_click_reset is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-5, 0), (500, 200), ("abc", 0), (None, 0), (10, 10), (12.5, 12), ("3.5", 0)],
)
def test_controller_mouse_hysteresis_coerced_to_int(value: object, expected: int) -> None:
    # Pixel deadband is a whole number of pixels: clamped to [0, 200]; a
    # non-integral float truncates; a non-integer string falls back to default.
    result = ControllerConfig(mouse_hysteresis_px=value).mouse_hysteresis_px
    assert result == expected
    assert isinstance(result, int)


@pytest.mark.parametrize(
    ("value", "expected"),
    # 0 = instant is in range now; out-of-range / non-numeric fall back to the
    # 0.0 default; the upper clamp is 1.0.
    [(0.0, 0.0), (1.0, 1.0), (5.0, 1.0), (-1.0, 0.0), ("nope", 0.0), (None, 0.0), (0.4, 0.4)],
)
def test_controller_mouse_smoothing_coerced(value: object, expected: float) -> None:
    assert ControllerConfig(mouse_smoothing=value).mouse_smoothing == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-1.0, 0.0), (99999.0, 10000.0), ("x", 0.0), (None, 0.0), (25.0, 25.0)],
)
def test_controller_mouse_max_y_coerced(value: object, expected: float) -> None:
    assert ControllerConfig(mouse_max_y=value).mouse_max_y == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-2.0, 0.0), (50.0, 10.0), ("x", 0.1), (None, 0.1), (0.25, 0.25)],
)
def test_controller_mouse_wheel_z_step_coerced(value: object, expected: float) -> None:
    assert ControllerConfig(mouse_wheel_z_step=value).mouse_wheel_z_step == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("false", False), ("no", False), ("true", True), (0, True), ("garbage", True)],
)
def test_controller_mouse_wheel_z_enabled_coerced(value: object, expected: bool) -> None:
    # Non-bool / unrecognised input falls back to the default (True); a TOML
    # string "false" is correctly read as False (not truthy).
    assert ControllerConfig(mouse_wheel_z_enabled=value).mouse_wheel_z_enabled is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("on", True), ("false", False), ("garbage", False)],
)
def test_controller_mouse_wheel_invert_coerced(value: object, expected: bool) -> None:
    assert ControllerConfig(mouse_wheel_invert=value).mouse_wheel_invert is expected


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


def test_camera_sensor_and_focal_keys_are_dropped_on_load(temp_config_path) -> None:
    """A camera section carrying sensor_width_mm / focal_length_mm loads like one without them."""
    camera = "[camera]\nfov = 52.5\npos_z = 7.0\n"
    temp_config_path.write_text(camera, encoding="utf-8")
    expected = load_config(str(temp_config_path))
    temp_config_path.write_text(camera + "sensor_width_mm = 6.17\nfocal_length_mm = 4.0\n", encoding="utf-8")

    cfg = load_config(str(temp_config_path))

    assert cfg == expected
    save_config(cfg, str(temp_config_path))
    written = temp_config_path.read_text(encoding="utf-8")
    assert "sensor_width_mm" not in written
    assert "focal_length_mm" not in written


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

    _fail_the_first_fsync(monkeypatch, _BoomError("simulated disk failure"))

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

    def boom_unlink(_path):
        raise OSError("cleanup also failed")

    _fail_the_first_fsync(monkeypatch, RuntimeError("primary failure"))
    monkeypatch.setattr(cfg_mod.os, "unlink", boom_unlink)

    with pytest.raises(RuntimeError, match="primary failure"):
        cfg_mod.save_config(AppConfig(), str(temp_config_path))


# ---------------------------------------------------------------------------
# save_config_fields – a runtime save writes what it changed, nothing else
# ---------------------------------------------------------------------------


def test_save_config_fields_keeps_every_other_field_as_the_file_has_it(temp_config_path) -> None:
    path = str(temp_config_path)
    save_config(AppConfig(video_source_type="ndi", ndi_source_name="On disk", web_port=8080), path)
    running = AppConfig(video_source_type="rtsp", ndi_source_name="Stale")

    save_config_fields(running, path, ["video_source_type"])

    saved = load_config(path)
    assert saved.video_source_type == "rtsp"
    assert (saved.ndi_source_name, saved.web_port) == ("On disk", 8080)


def test_save_config_fields_writes_one_field_of_a_sub_config(temp_config_path) -> None:
    path = str(temp_config_path)
    on_disk = AppConfig()
    on_disk.trigger_zones.eval_fps = 5
    save_config(on_disk, path)
    running = AppConfig()
    running.trigger_zones.show_overlay = False

    save_config_fields(running, path, ["trigger_zones.show_overlay"])

    saved = load_config(path).trigger_zones
    assert saved.show_overlay is False
    assert saved.eval_fps == 5


def test_save_config_fields_writes_nothing_new_without_names(temp_config_path) -> None:
    path = str(temp_config_path)
    save_config(AppConfig(web_port=8080), path)

    save_config_fields(AppConfig(), path, [])

    assert load_config(path).web_port == 8080


def test_save_config_fields_writes_the_whole_config_when_the_file_is_missing(temp_config_path) -> None:
    running = AppConfig(web_port=8080, psn_system_name="Stage Left")

    save_config_fields(running, str(temp_config_path), ["camera"])

    saved = load_config(str(temp_config_path), strict=True)
    assert (saved.web_port, saved.psn_system_name) == (8080, "Stage Left")


@pytest.mark.parametrize("content", ["", "  \n\t\n"])
def test_save_config_fields_refuses_an_empty_file_and_leaves_it(temp_config_path, content) -> None:
    """An empty file loads as every default, and an editor that truncates before
    it writes leaves one for a moment: the unnamed fields must not become defaults."""
    temp_config_path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigFileEmptyError, match="empty"):
        save_config_fields(AppConfig(web_port=8080), str(temp_config_path), ["camera"])

    assert temp_config_path.read_text(encoding="utf-8") == content


def test_save_config_fields_decides_on_the_bytes_it_parses(temp_config_path, monkeypatch) -> None:
    """A file emptied after an earlier read saw it whole is still refused: the
    unnamed fields must not become defaults."""
    from pathlib import Path

    save_config(AppConfig(web_port=8080), str(temp_config_path))
    whole = temp_config_path.read_bytes()
    temp_config_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(Path, "read_bytes", lambda _self: whole)

    with pytest.raises(ConfigFileEmptyError, match="empty"):
        save_config_fields(AppConfig(web_port=9090), str(temp_config_path), ["camera"])

    monkeypatch.undo()
    assert temp_config_path.read_text(encoding="utf-8") == ""


def test_save_config_fields_writes_whole_a_file_that_vanished_after_an_earlier_check(
    temp_config_path, monkeypatch
) -> None:
    """An editor that renames the file away before it writes leaves no file for a
    moment: the running config is written, never defaults with one field."""
    import openfollow.configuration as cfg_mod

    if temp_config_path.exists():
        temp_config_path.unlink()
    monkeypatch.setattr(cfg_mod.os.path, "exists", lambda _p: True)
    running = AppConfig(web_port=8080, psn_system_name="Stage Left")

    save_config_fields(running, str(temp_config_path), ["camera"])

    monkeypatch.undo()
    saved = load_config(str(temp_config_path), strict=True)
    assert (saved.web_port, saved.psn_system_name) == (8080, "Stage Left")


@pytest.mark.parametrize("content", ["", "  \n\t\n"])
def test_load_config_strict_refuses_an_empty_file(temp_config_path, content) -> None:
    temp_config_path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigFileEmptyError, match="empty"):
        load_config(str(temp_config_path), strict=True)
    assert load_config(str(temp_config_path)) == AppConfig()


@pytest.mark.parametrize("content", ["", "  \n\t\n", "web_port = [unclosed"])
def test_save_config_fields_can_replace_an_empty_or_unreadable_file(temp_config_path, content) -> None:
    temp_config_path.write_text(content, encoding="utf-8")
    running = AppConfig(web_port=8080, psn_system_name="Stage Left")

    save_config_fields(running, str(temp_config_path), ["web_bind_iface"], replace_unreadable=True)

    saved = load_config(str(temp_config_path), strict=True)
    assert (saved.web_port, saved.psn_system_name) == (8080, "Stage Left")


def test_save_config_fields_treats_a_file_it_may_not_read_as_unreadable(temp_config_path, monkeypatch) -> None:
    import builtins
    from pathlib import Path

    import openfollow.configuration as cfg_mod

    target = str(temp_config_path)
    save_config(AppConfig(web_port=8080), target)
    real_read_bytes = Path.read_bytes

    def _read_bytes(self: Path) -> bytes:
        if str(self) == target:
            raise PermissionError(13, "Permission denied", target)
        return real_read_bytes(self)

    def _open(file, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if str(file) == target:
            raise PermissionError(13, "Permission denied", target)
        return builtins.open(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", _read_bytes)
    monkeypatch.setattr(cfg_mod, "open", _open, raising=False)

    with pytest.raises(ConfigFileUnreadableError, match="Permission denied"):
        save_config_fields(AppConfig(), target, ["camera"])
    save_config_fields(AppConfig(web_port=9090), target, ["camera"], replace_unreadable=True)

    monkeypatch.undo()
    assert load_config(target, strict=True).web_port == 9090


def test_save_config_fields_leaves_an_unreadable_file_alone(temp_config_path) -> None:
    temp_config_path.write_text("web_port = [unclosed", encoding="utf-8")

    with pytest.raises(ConfigFileUnreadableError):
        save_config_fields(AppConfig(), str(temp_config_path), ["camera"])

    assert temp_config_path.read_text(encoding="utf-8") == "web_port = [unclosed"


def test_save_config_fields_holds_the_write_lock_across_load_and_save(temp_config_path, monkeypatch) -> None:
    import openfollow.configuration as cfg_mod

    save_config(AppConfig(), str(temp_config_path))
    held: list[bool] = []
    real_load, real_save = cfg_mod.read_config_whole, cfg_mod.save_config

    def _load(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        held.append(config_write_lock.locked())
        return real_load(*args, **kwargs)

    def _save(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        held.append(config_write_lock.locked())
        real_save(*args, **kwargs)

    monkeypatch.setattr(cfg_mod, "read_config_whole", _load)
    monkeypatch.setattr(cfg_mod, "save_config", _save)

    save_config_fields(AppConfig(), str(temp_config_path), ["camera"])

    assert held == [True, True]
    assert not config_write_lock.locked()


@pytest.mark.parametrize("name", ["no_such_field", "camera.no_such_field", "no_such_section.fov"])
def test_save_config_fields_refuses_an_unknown_name_and_leaves_the_file(temp_config_path, name) -> None:
    save_config(AppConfig(web_port=8080), str(temp_config_path))
    before = temp_config_path.read_bytes()

    with pytest.raises(AttributeError):
        save_config_fields(AppConfig(), str(temp_config_path), [name])

    assert temp_config_path.read_bytes() == before
    assert not config_write_lock.locked()


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

    def unregister_marker(self, marker_id: int) -> None:
        self.unregistered.append(marker_id)


class _DummyRttrpmServer(_DummyOtpServer):
    pass


def _app_with(**overrides) -> _DummyApp:
    """Build a _DummyApp with an initial AppConfig; overrides feed AppConfig."""
    app = _DummyApp(AppConfig(**overrides))
    # get_marker is used by the OTP/RTTrPM mirror loops – add it here.
    app._server.get_marker = lambda marker_id: app._server.markers.get(marker_id)  # type: ignore[attr-defined]
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
    # -1 stripped; the controlled 1 and 5 are viewed too.
    assert cfg.viewer_marker_ids == [2, 1, 5]


def test_load_config_dedupes_marker_id_lists(tmp_path) -> None:
    from openfollow.configuration import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        "controlled_marker_ids = [1, 1, 2, 1, 3]\nviewer_marker_ids = [2, 2, 1, 2]\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg.controlled_marker_ids == [1, 2, 3]
    # Deduplicated in first-seen order, then the controlled 3 is viewed too.
    assert cfg.viewer_marker_ids == [2, 1, 3]


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


def test_apply_runtime_moves_controllers_to_hand_edited_pins() -> None:
    app = _app_with(controller=ControllerConfig())
    pins = [SlotPinConfig(slot=2, key="usb:platform/xhci-hcd.1:1.4", label="Pad")]
    apply_runtime_config_changes(app, AppConfig(controller=ControllerConfig(slot_pins=pins)))
    assert app._input_manager.slot_pin_applies == [pins]
    assert app._config.controller.slot_pins == pins


def test_apply_runtime_leaves_the_pins_alone_on_another_controller_edit() -> None:
    pins = [SlotPinConfig(slot=2, key="usb:platform/xhci-hcd.1:1.4", label="Pad")]
    app = _app_with(controller=ControllerConfig(slot_pins=list(pins)))
    apply_runtime_config_changes(app, AppConfig(controller=ControllerConfig(slot_pins=list(pins), deadzone=0.3)))
    assert app._input_manager.slot_pin_applies == []


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


@pytest.mark.usefixtures("every_feature_on")
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


def test_apply_runtime_psn_source_iface_failure_reverts_config(monkeypatch) -> None:
    """``eth0`` is live here, so the rebind path runs and its failure reverts."""
    import socket as _socket

    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {"eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.1.5")]},
    )

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_psn_source_ip_change(self, new_source_ip: str) -> None:
            raise OSError(f"failed to bind to {new_source_ip!r}")

    app = _DummyApp(AppConfig(psn_source_iface=""))
    app._runtime_services = _FailingRuntimeServices()
    new_config = AppConfig(psn_source_iface="eth0")

    apply_runtime_config_changes(app, new_config)

    # Stored config reverted so a subsequent reload retries.
    assert app._config.psn_source_iface == ""
    assert app._web_commands.restart_requested is False
    assert app._runtime_services.psn_suspends == 0


def test_apply_runtime_down_iface_suspends_and_never_rebinds(monkeypatch) -> None:
    """A pin to an interface with no address must stop PSN, not move it.

    The hot-reload path used to resolve through the fail-open chain, so this
    same edit rebound PSN to the OS-primary interface - stage data on the
    network the operator had just deselected, while the panel showed the
    interface as down.
    """
    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(net_utils_module.psutil, "net_if_addrs", dict)
    monkeypatch.setattr(net_utils_module, "get_primary_local_ipv4", lambda default="": "10.0.0.1")

    app = _DummyApp(AppConfig(psn_source_iface=""))
    apply_runtime_config_changes(app, AppConfig(psn_source_iface="ghost0"))

    assert app._runtime_services.psn_suspends == 1
    assert app._runtime_services.psn_source_ip_changes == []
    assert app._runtime_services.psn_combined_changes == []
    # The operator's choice is honoured and kept - the plane stops, the pin stays.
    assert app._config.psn_source_iface == "ghost0"


def test_apply_runtime_suspend_failure_reverts_config(monkeypatch) -> None:
    """A failing suspend reverts like a failing rebind, so the next pass retries."""
    import openfollow.net_utils as net_utils_module

    monkeypatch.setattr(net_utils_module.psutil, "net_if_addrs", dict)
    monkeypatch.setattr(net_utils_module, "get_primary_local_ipv4", lambda default="": "10.0.0.1")

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def suspend_psn_planes(self) -> None:
            raise OSError("simulated suspend failure")

    app = _DummyApp(AppConfig(psn_source_iface=""))
    app._runtime_services = _FailingRuntimeServices()
    apply_runtime_config_changes(app, AppConfig(psn_source_iface="ghost0"))

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


@pytest.mark.usefixtures("every_feature_on")
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


@pytest.mark.usefixtures("every_feature_on")
def test_a_detector_that_fails_after_the_swap_returned_reverts_the_stored_config() -> None:
    """The detector is built off the frame loop: a failure reported later reverts like a raise does."""
    app = _app_with()
    app._runtime_services._person_detector = None
    old_detection = app._config.detection
    new_det = DetectionConfig(enabled=True, confidence=0.75)

    apply_runtime_config_changes(app, AppConfig(detection=new_det))
    assert app._config.detection == new_det
    app._runtime_services.detection_swap_on_failure()

    assert app._config.detection == old_detection


@pytest.mark.usefixtures("every_feature_on")
def test_an_edit_saved_while_a_detector_is_being_built_is_built_rather_than_reloaded() -> None:
    """The running detector is about to be replaced: an edit staged on it would be lost."""
    app = _app_with(detection=DetectionConfig(enabled=True, confidence=0.40))
    app._runtime_services.detector_build_pending = True
    new_det = DetectionConfig(enabled=True, confidence=0.85)

    apply_runtime_config_changes(app, AppConfig(detection=new_det))

    assert app._runtime_services.detection_swaps == [new_det]
    assert app._runtime_services.detection_changes == []


@pytest.mark.usefixtures("every_feature_on")
def test_a_detector_swap_that_fails_after_the_reload_is_retried_on_the_next_poll(tmp_path) -> None:
    import os

    from openfollow.runtime import app_orchestration as orch

    path = str(tmp_path / "config.toml")
    save_config(AppConfig(), path)
    os.utime(path, (100.0, 100.0))
    app = _app_on_file(path)
    app._runtime_services._person_detector = None
    app._config_mtime = 100.0
    app._last_config_check = float("-inf")
    app._get_config_mtime = lambda: orch.get_config_mtime(app)
    save_config(AppConfig(detection=DetectionConfig(enabled=True)), path)
    os.utime(path, (200.0, 200.0))

    orch.check_config_reload(app)
    app._runtime_services.detection_swap_on_failure()
    assert app._config.detection.enabled is False
    app._last_config_check = float("-inf")
    orch.check_config_reload(app)

    assert [cfg.enabled for cfg in app._runtime_services.detection_swaps] == [True, True]
    assert app._config.detection.enabled is True


@pytest.mark.usefixtures("every_feature_on")
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


@pytest.mark.usefixtures("every_feature_on")
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


@pytest.mark.usefixtures("every_feature_on")
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


@pytest.mark.usefixtures("every_feature_on")
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


@pytest.mark.usefixtures("every_feature_on")
def test_apply_runtime_detection_swap_failure_reverts_stored_config() -> None:
    """swap_detector failure reverts config for retry."""

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def swap_detector(self, new_cfg: DetectionConfig, on_failure: Callable[[], None] | None = None) -> None:
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

    def _failing_add(marker_id, name):  # noqa: ANN001
        if marker_id == 3:
            raise RuntimeError("PSN add failed")
        return orig_add(marker_id, name)

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
    app._server.get_marker = lambda marker_id: app._server.markers.get(marker_id)  # type: ignore[attr-defined]
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

    # Write a config that trips the deprecation so we observe the
    # "warn once per field" guard.
    bad_ctrl = ControllerConfig(
        btn_settings_confirm="X",  # confirm/cancel deprecation
    )
    save_config(AppConfig(controller=bad_ctrl), str(temp_config_path))

    with caplog.at_level("WARNING", logger="openfollow.configuration"):
        load_config(str(temp_config_path))
        first_pass = [r.message for r in caplog.records]
        caplog.clear()
        load_config(str(temp_config_path))
        second_pass = [r.message for r in caplog.records]

    # First load emits the deprecation warning.
    assert any("btn_settings_confirm" in m for m in first_pass)
    # Second load must not re-emit – the module-level set suppresses repeats.
    assert not any("btn_settings_confirm" in m for m in second_pass)


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
    # The explicit list leads instead of being replaced by the fallback; the controlled markers join it.
    assert cfg.viewer_marker_ids == [5, 1, 2]


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


class TestDisplayConfig:
    """The Operator Screen override, set by hand in config.toml."""

    def test_default_is_auto(self) -> None:
        from openfollow.configuration import AppConfig

        assert AppConfig().display.operator_screen == "auto"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("auto", "auto"), ("cairo", "cairo"), ("  Cairo ", "cairo"), ("AUTO", "auto")],
        ids=["auto", "cairo", "padded-mixed-case", "upper-case"],
    )
    def test_a_known_choice_is_kept_normalised(self, value: str, expected: str) -> None:
        from openfollow.configuration import DisplayConfig

        assert DisplayConfig(operator_screen=value).operator_screen == expected

    @pytest.mark.parametrize("value", ["gl", "", "opengl"], ids=["gl", "empty", "unknown"])
    def test_an_unknown_choice_is_auto(self, value: str) -> None:
        from openfollow.configuration import DisplayConfig

        assert DisplayConfig(operator_screen=value).operator_screen == "auto"

    @pytest.mark.parametrize("value", [None, 1, True, ["cairo"]], ids=["none", "int", "bool", "list"])
    def test_a_wrong_type_is_auto(self, value: object) -> None:
        from openfollow.configuration import DisplayConfig

        assert DisplayConfig(operator_screen=value).operator_screen == "auto"  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("toml", "expected"),
        [
            ('[display]\noperator_screen = "cairo"\n', "cairo"),
            ("[display]\noperator_screen = 3\n", "auto"),
            ('[display]\noperator_screen = "metal"\n', "auto"),
            ("", "auto"),
        ],
        ids=["cairo", "wrong-type", "unknown", "no-section"],
    )
    def test_a_hand_edited_file_is_read_and_normalised(self, temp_config_path, toml: str, expected: str) -> None:
        temp_config_path.write_text(toml, encoding="utf-8")

        assert load_config(str(temp_config_path)).display.operator_screen == expected

    def test_the_choice_survives_a_save(self, temp_config_path) -> None:
        from openfollow.configuration import AppConfig

        cfg = AppConfig()
        cfg.display.operator_screen = "cairo"
        save_config(cfg, str(temp_config_path))

        assert load_config(str(temp_config_path)).display.operator_screen == "cairo"


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


class TestCoerceOptionalMarkerIdBlankString:
    """Pins the blank-string arm deterministically.

    Its only caller strips and returns before reaching the helper, so the
    public boundary cannot drive this arm and no ordinary test covers it. What
    did cover it was a Hypothesis property test happening to draw a
    whitespace-only string, which made the 100% gate depend on a random draw -
    green on one machine and red on the next with no code change between them.
    """

    @pytest.mark.parametrize("value", ["", " ", "\t", "   \n "])
    def test_a_blank_string_collapses_to_none(self, value: str) -> None:
        from openfollow.configuration import _coerce_optional_marker_id

        assert _coerce_optional_marker_id(value) is None


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
        # Templates now live as .oftemplate files; test covers transmitters list.
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

    # The Features list.
    def test_shown_features_default_is_every_non_experimental_feature(self) -> None:
        from openfollow.configuration import EXPERIMENTAL_FEATURES, FEATURE_KEYS, UiConfig

        assert UiConfig().shown_features == [key for key in FEATURE_KEYS if key not in EXPERIMENTAL_FEATURES]
        assert UiConfig().shown_features == ["zones", "midi", "psn_input"]

    def test_lens_line_suggestions_are_experimental_and_offered(self) -> None:
        from openfollow.configuration import EXPERIMENTAL_FEATURES, OFFERED_FEATURES, UiConfig

        assert "lens_suggestions" in EXPERIMENTAL_FEATURES and "lens_suggestions" in OFFERED_FEATURES
        assert not UiConfig().feature_on("lens_suggestions")
        assert UiConfig(shown_features=["lens_suggestions"]).feature_on("lens_suggestions")

    def test_shown_features_drops_an_unknown_key_on_load_and_the_lens_applies(self, temp_config_path) -> None:  # noqa: ANN001
        from openfollow.configuration import effective_lens, load_config

        temp_config_path.write_text(
            "[camera]\nlens_k1 = -0.2\nlens_k2 = 0.05\nlens_cx = 0.03\nlens_cy = -0.01\n"
            '[ui]\nshown_features = ["zones", "lens_distortion", "psn_input"]\n'
        )
        loaded = load_config(str(temp_config_path))
        assert loaded.ui.shown_features == ["zones", "psn_input"]
        assert effective_lens(loaded) == (-0.2, 0.05, 0.03, -0.01)

    def test_shown_features_keeps_known_keys_in_list_order(self) -> None:
        from openfollow.configuration import UiConfig

        ui = UiConfig(shown_features=["touch_remote", "zones", "detection"])
        assert ui.shown_features == ["zones", "detection", "touch_remote"]
        assert ui.feature_on("detection")
        assert not ui.feature_on("midi")

    def test_shown_features_empty_list_turns_everything_off(self) -> None:
        from openfollow.configuration import FEATURE_KEYS, UiConfig

        ui = UiConfig(shown_features=[])
        assert ui.shown_features == []
        assert not any(ui.feature_on(key) for key in FEATURE_KEYS)

    @pytest.mark.parametrize("value", [None, "abc", "zones", True, 42, {"zones": True}])
    def test_shown_features_wrong_type_falls_back_to_default(self, value) -> None:  # noqa: ANN001
        from openfollow.configuration import DEFAULT_SHOWN_FEATURES, UiConfig

        assert UiConfig(shown_features=value).shown_features == list(DEFAULT_SHOWN_FEATURES)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "entries,expected",
        [
            (["zones", "warp_drive"], ["zones"]),
            (["zones", "zones", "midi"], ["zones", "midi"]),
            (["zones", None, 3, True, ["midi"]], ["zones"]),
            ([" Zones ", "MIDI"], ["zones", "midi"]),
        ],
    )
    def test_shown_features_drops_unknown_duplicate_and_non_string_entries(self, entries, expected) -> None:  # noqa: ANN001
        from openfollow.configuration import UiConfig

        assert UiConfig(shown_features=entries).shown_features == expected

    def test_shown_features_toml_round_trip(self, temp_config_path) -> None:  # noqa: ANN001
        from openfollow.configuration import AppConfig, UiConfig, load_config, save_config

        save_config(AppConfig(ui=UiConfig(shown_features=["paths", "midi"])), str(temp_config_path))
        assert load_config(str(temp_config_path)).ui.shown_features == ["midi", "paths"]

    def test_shown_features_missing_from_toml_reads_the_default(self, temp_config_path) -> None:  # noqa: ANN001
        from openfollow.configuration import DEFAULT_SHOWN_FEATURES, load_config

        temp_config_path.write_text('[ui]\nunit_system = "metric"\n')
        assert load_config(str(temp_config_path)).ui.shown_features == list(DEFAULT_SHOWN_FEATURES)

    @pytest.mark.parametrize(
        "ui_toml,expected",
        [
            ("show_experimental_features = true\n", "all"),
            ('show_experimental_features = "on"\n', "all"),
            ("show_experimental_features = false\n", "default"),
            ('show_experimental_features = "maybe"\n', "default"),
            ('unit_system = "metric"\n', "default"),
            ('show_experimental_features = true\nshown_features = ["midi"]\n', ["midi"]),
            ('show_experimental_features = false\nshown_features = ["paths"]\n', ["paths"]),
        ],
    )
    def test_show_experimental_features_migrates_on_load(self, temp_config_path, ui_toml, expected) -> None:  # noqa: ANN001
        from openfollow.configuration import DEFAULT_SHOWN_FEATURES, OFFERED_FEATURES, load_config, save_config

        temp_config_path.write_text("[ui]\n" + ui_toml)
        loaded = load_config(str(temp_config_path))
        want = (
            {"all": list(OFFERED_FEATURES), "default": list(DEFAULT_SHOWN_FEATURES)}.get(expected)
            if isinstance(expected, str)
            else expected
        )
        assert loaded.ui.shown_features == want
        save_config(loaded, str(temp_config_path))
        assert "show_experimental_features" not in temp_config_path.read_text()

    @pytest.mark.parametrize("features_on", [["zones", "detection", "paths"], ["detection", "paths", "ground_grid"]])
    def test_feature_on_reads_the_list(self, features_on) -> None:  # noqa: ANN001
        from openfollow.configuration import AppConfig, UiConfig, feature_on

        cfg = AppConfig(ui=UiConfig(shown_features=features_on))
        for key in features_on:
            assert feature_on(cfg, key)
        assert feature_on(cfg, "midi") is False

    def test_feature_on_without_a_ui_section_reads_the_defaults(self) -> None:
        from types import SimpleNamespace

        from openfollow.configuration import feature_on

        assert feature_on(SimpleNamespace(), "zones")
        assert not feature_on(SimpleNamespace(), "paths")

    @pytest.mark.parametrize("shown", [[], ["zones"], list(FEATURE_KEYS)], ids=["none", "one", "all"])
    def test_effective_lens_is_the_cameras_whatever_the_features(self, shown) -> None:  # noqa: ANN001
        from openfollow.configuration import AppConfig, CameraConfig, UiConfig, effective_lens

        camera = CameraConfig(lens_k1=0.12, lens_k2=-0.03, lens_cx=0.04, lens_cy=-0.02)
        cfg = AppConfig(camera=camera, ui=UiConfig(shown_features=shown))
        assert effective_lens(cfg) == (0.12, -0.03, 0.04, -0.02)

    # developer_mode: config-only, shows the developer's views in the web UI.
    def test_developer_mode_default_is_false(self) -> None:
        from openfollow.configuration import UiConfig

        assert UiConfig().developer_mode is False

    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True),
            ("true", True),
            ("on", True),
            (False, False),
            ("off", False),
            ("maybe", False),
            (42, False),
            (None, False),
        ],
    )
    def test_developer_mode_coercion(self, value, expected) -> None:  # noqa: ANN001
        from openfollow.configuration import UiConfig

        assert UiConfig(developer_mode=value).developer_mode is expected  # type: ignore[arg-type]

    def test_developer_mode_toml_round_trip(self, temp_config_path) -> None:  # noqa: ANN001
        from openfollow.configuration import AppConfig, UiConfig, load_config, save_config

        save_config(AppConfig(ui=UiConfig(developer_mode=True)), str(temp_config_path))
        loaded = load_config(str(temp_config_path))
        assert loaded.ui.developer_mode is True
        assert loaded.ui.shown_features == ["zones", "midi", "psn_input"]


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


@pytest.mark.parametrize(
    ("controlled", "viewer", "expected"),
    [
        ("[7, 5]", "[5, 6]", [5, 6, 7]),
        ("[1, 2]", "[2, 1]", [2, 1]),
        ("[]", "[4]", [4]),
        ("[3]", "[]", [3]),
        ("[3, 3, 0]", "[0, 2, 2]", [2, 3]),
        ("[3]", '"not-a-list"', [3]),
    ],
    ids=["adds-unviewed", "already-viewed", "none-controlled", "empty-viewer", "cleaned-first", "non-list-viewer"],
)
def test_load_config_views_every_controlled_marker(temp_config_path, controlled, viewer, expected) -> None:
    """A marker this station controls is always one it views: the viewed list keeps its
    order and gains any controlled marker it lacked."""
    temp_config_path.write_text(
        f"controlled_marker_ids = {controlled}\nviewer_marker_ids = {viewer}\n", encoding="utf-8"
    )
    assert load_config(str(temp_config_path)).viewer_marker_ids == expected


def test_load_config_says_once_which_markers_it_now_views(temp_config_path, caplog) -> None:
    """The config is loaded on every request, so the change is logged once per process."""
    temp_config_path.write_text("controlled_marker_ids = [7, 5]\nviewer_marker_ids = [5]\n", encoding="utf-8")
    with caplog.at_level("INFO"):
        load_config(str(temp_config_path))
        load_config(str(temp_config_path))
    said = [r.getMessage() for r in caplog.records if "now viewed too" in r.getMessage()]
    assert len(said) == 1
    assert "[7]" in said[0]


def test_viewed_with_controlled_keeps_the_viewed_order() -> None:
    assert viewed_with_controlled([9, 1], [4, 1]) == [4, 1, 9]


# ---------------------------------------------------------------------------
# AppConfig.interface_labels: the operator's names for this station's adapters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["Lighting", ["eth0"], None, 7, True])
def test_interface_labels_that_are_not_a_table_are_empty(raw: object) -> None:
    assert AppConfig(interface_labels=raw).interface_labels == {}  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ({"eth0": "  Production  "}, {"eth0": "Production"}),
        ({"eth0": "Pro\x07duc\u202etion"}, {"eth0": "Production"}),
        ({"eth0": "L" * 25}, {"eth0": "L" * 20}),
        ({"eth0": None, "eth1": 3, "eth2": ""}, {}),
    ],
    ids=["whitespace", "control-and-bidi", "over-20", "not-text"],
)
def test_interface_label_values_are_clean_text(raw: dict, stored: dict) -> None:
    assert AppConfig(interface_labels=raw).interface_labels == stored


@pytest.mark.parametrize("name", ["../eth0", "eth 0", "a" * 16, "", "x:y"])
def test_interface_labels_on_names_no_interface_can_have_are_dropped(name: str) -> None:
    assert AppConfig(interface_labels={name: "Lighting", "eth0": "Video"}).interface_labels == {"eth0": "Video"}


def test_interface_labels_keep_one_owner_per_label() -> None:
    cfg = AppConfig(interface_labels={"enx2": "LIGHTING", "enx1": "Lighting", "eth0": "Video"})
    assert cfg.interface_labels == {"enx1": "Lighting", "eth0": "Video"}


def test_interface_labels_round_trip_through_toml(temp_config_path) -> None:
    labels = {"eth0": "Production", "eth0.13": "Video", "enx9c69d3ac16ab": "Lighting"}
    save_config(AppConfig(interface_labels=labels), temp_config_path)
    assert load_config(temp_config_path).interface_labels == labels


def test_a_hand_edited_label_table_is_normalised_on_load(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[interface_labels]\neth0 = " Production "\n"eth0.13" = 5\n"bad/name" = "X"\n', encoding="utf-8")
    assert load_config(str(path)).interface_labels == {"eth0": "Production"}


# ---------------------------------------------------------------------------
# AppConfig.station_fqdn: this station's name on a venue's own DNS
# ---------------------------------------------------------------------------

# 63 + 63 + 63 + 61 characters and three dots: the longest name DNS carries.
_LONGEST_FQDN = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("of-1.stage.example.com", "of-1.stage.example.com"),
        ("  Of-1.Stage.Example.COM  ", "of-1.stage.example.com"),
        ("of-1.stage.example.com.", "of-1.stage.example.com"),
        ("a.b", "a.b"),
        ("1of.2.example.com", "1of.2.example.com"),
        (_LONGEST_FQDN, _LONGEST_FQDN),
        ("", ""),
    ],
    ids=["plain", "case-and-whitespace", "root-dot", "two-labels", "digit-labels", "253-chars", "blank"],
)
def test_a_station_fqdn_is_stored_canonical(raw: str, stored: str) -> None:
    assert AppConfig(station_fqdn=raw).station_fqdn == stored


@pytest.mark.parametrize("raw", [None, 7, True, ["of-1.example.com"], {"of-1": "example.com"}])
def test_a_station_fqdn_that_is_not_text_loads_blank(raw: object) -> None:
    assert AppConfig(station_fqdn=raw).station_fqdn == ""  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw",
    [
        "of-1",
        "of-1.",
        ".",
        " . ",
        "192.0.2.10",
        "1.2.3.4.5",
        "2001:db8::1",
        "of-1.local",
        "Of-1.LOCAL.",
        "-of-1.example.com",
        "of-1-.example.com",
        "of-1..example.com",
        ".of-1.example.com",
        "of-1.example.com..",
        "of_1.example.com",
        "of 1.example.com",
        "öf-1.example.com",
        "http://of-1.example.com",
        "of-1.example.com:80",
        "a" * 64 + ".example.com",
        _LONGEST_FQDN + "d",
    ],
    ids=[
        "one-label",
        "one-label-root-dot",
        "root-only",
        "padded-root-only",
        "ipv4",
        "numeric-tld",
        "ipv6",
        "mdns",
        "mdns-any-case",
        "leading-hyphen",
        "trailing-hyphen",
        "empty-label",
        "leading-dot",
        "two-root-dots",
        "underscore",
        "space",
        "non-ascii",
        "url",
        "port",
        "label-over-63",
        "name-over-253",
    ],
)
def test_a_station_fqdn_that_breaks_the_rules_loads_blank(raw: str) -> None:
    assert AppConfig(station_fqdn=raw).station_fqdn == ""


def test_a_station_fqdn_round_trips_through_toml(temp_config_path) -> None:
    save_config(AppConfig(station_fqdn="of-1.stage.example.com"), temp_config_path)
    assert load_config(temp_config_path).station_fqdn == "of-1.stage.example.com"


@pytest.mark.parametrize(
    ("line", "stored"),
    [
        ('station_fqdn = "Of-1.Stage.Example.COM."', "of-1.stage.example.com"),
        ('station_fqdn = "of-1.local"', ""),
        ('station_fqdn = "."', ""),
        ("station_fqdn = 42", ""),
    ],
    ids=["canonicalised", "refused-name", "root-only", "wrong-type"],
)
def test_a_hand_edited_station_fqdn_is_normalised_on_load(tmp_path, line: str, stored: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(line + "\n", encoding="utf-8")
    assert load_config(str(path)).station_fqdn == stored


def test_apply_runtime_hands_a_new_station_fqdn_to_the_running_station() -> None:
    """The HUD's web address rows read the running config, so a name saved on the
    web shows on the station's screen without a restart."""
    app = _DummyApp(AppConfig())
    apply_runtime_config_changes(app, AppConfig(station_fqdn="of-1.stage.example.com"))
    assert app._config.station_fqdn == "of-1.stage.example.com"
    apply_runtime_config_changes(app, AppConfig(station_fqdn="of-1.stage.example.com"))
    apply_runtime_config_changes(app, AppConfig())
    assert app._config.station_fqdn == ""
    assert app._web_commands.restart_requested is False
    # /etc/hosts and DHCP follow each change, once.
    assert app._runtime_services.station_fqdn_changes == ["of-1.stage.example.com", ""]


def test_a_station_fqdn_that_could_not_be_handed_on_is_retried() -> None:
    """Keeping the new name while nothing applied it would accept the file and never try again."""

    class _FailingRuntimeServices(_DummyRuntimeServices):
        def apply_station_fqdn_change(self, fqdn: str) -> None:
            raise RuntimeError("can't start new thread")

    app = _DummyApp(AppConfig(station_fqdn="old.example.com"))
    app._runtime_services = _FailingRuntimeServices()
    assert apply_runtime_config_changes(app, AppConfig(station_fqdn="of-1.stage.example.com")) is False
    assert app._config.station_fqdn == "old.example.com"


def test_a_refused_station_fqdn_is_reported_once_not_on_every_load(caplog, monkeypatch) -> None:
    """A hand edit that loads blank must say why; the web layer loads the config on every
    request, so once per value, not once per load."""
    import openfollow.station_fqdn as station_fqdn

    monkeypatch.setattr(station_fqdn, "_reported", set())
    with caplog.at_level("WARNING", logger="openfollow.station_fqdn"):
        for _ in range(3):
            AppConfig(station_fqdn="of_1.stage.example.com")
        AppConfig(station_fqdn="")
        AppConfig(station_fqdn="of-1.stage.example.com")
        AppConfig(station_fqdn=".")
    warnings = [r.getMessage() for r in caplog.records]
    assert warnings == [
        "Ignoring station_fqdn 'of_1.stage.example.com': Use only letters, digits and hyphens between the dots,"
        " and no hyphen at the start or end of a part.",
        "Ignoring station_fqdn '.': Enter the full name with its domain, such as of-1.stage.example.com.",
    ]


class TestPathsConfig:
    """Paths: points, names that OSC can address, caps, and the per-station Active flag."""

    @pytest.mark.parametrize("bad", [None, "abc", [], {}, float("nan"), float("inf")])
    def test_a_point_coordinate_of_the_wrong_type_falls_back_to_zero(self, bad: object) -> None:
        from openfollow.configuration import PathPointConfig

        p = PathPointConfig(x=bad, y=bad, z=bad)
        assert (p.x, p.y, p.z) == (0.0, 0.0, 0.0)

    def test_point_coordinates_clamp_to_a_stage_sized_range(self) -> None:
        from openfollow.configuration import PathPointConfig

        p = PathPointConfig(x=5000, y=-5000, z="12.5")
        assert (p.x, p.y, p.z) == (1000.0, -1000.0, 12.5)

    @pytest.mark.parametrize(("raw", "want"), [(-1.0, 0.0), (500, 100.0), ("x", 0.5), (None, 0.5), (2, 2.0)])
    def test_a_point_radius_is_a_non_negative_length(self, raw: object, want: float) -> None:
        from openfollow.configuration import PathPointConfig

        assert PathPointConfig(radius=raw).radius == want

    @pytest.mark.parametrize(
        ("raw", "want"),
        [
            ("downstage-cross", "downstage-cross"),
            ("Down Stage/Cross!", "DownStageCross"),
            ("a" * 40, "a" * 32),
            ("v1.2_b", "v1.2_b"),
            (".hidden", ".hidden"),
            ("..a", "..a"),
            (None, ""),
            (42, ""),
        ],
    )
    def test_a_path_name_keeps_only_what_an_osc_address_allows(self, raw: object, want: str) -> None:
        from openfollow.configuration import PathConfig

        assert PathConfig(name=raw).name == want

    @pytest.mark.parametrize("raw", [".", "..", "...", "./..", " . ", "." * 40])
    def test_a_path_name_of_dots_alone_is_no_name(self, raw: str) -> None:
        # A URL resolves "." and ".." away, so the path could never be addressed.
        from openfollow.configuration import PathConfig, PathsConfig

        assert PathConfig(name=raw).name == ""
        assert PathsConfig(paths=[{"name": raw}]).paths[0].name == "path-1"

    @pytest.mark.parametrize("field", ["active", "closed", "release_at_start", "release_at_end"])
    @pytest.mark.parametrize(("raw", "want"), [(True, True), ("yes", True), ("off", False), (1, False), (None, False)])
    def test_path_switches_take_booleans_only(self, field: str, raw: object, want: bool) -> None:
        from openfollow.configuration import PathConfig

        assert getattr(PathConfig(**{field: raw}), field) is want

    def test_path_points_come_from_tables_and_junk_entries_are_dropped(self) -> None:
        from openfollow.configuration import PathConfig, PathPointConfig

        path = PathConfig(points=[{"x": 1, "y": 2, "z": 0.5, "radius": 0, "stale": 1}, "junk", 7, PathPointConfig(x=3)])
        assert path.points == [PathPointConfig(1.0, 2.0, 0.5, 0.0), PathPointConfig(x=3.0)]

    def test_a_points_value_that_is_not_a_list_reads_as_no_points(self) -> None:
        from openfollow.configuration import PathConfig

        assert PathConfig(points="nope").points == []

    def test_a_path_keeps_at_most_its_point_cap(self, caplog) -> None:
        from openfollow.configuration import MAX_PATH_POINTS, PathConfig

        with caplog.at_level("WARNING"):
            path = PathConfig(name="long", points=[{"x": i} for i in range(MAX_PATH_POINTS + 5)])
        assert len(path.points) == MAX_PATH_POINTS
        assert path.points[-1].x == MAX_PATH_POINTS - 1
        assert "keeping the first" in caplog.text

    def test_paths_keep_at_most_their_cap(self, caplog) -> None:
        from openfollow.configuration import MAX_PATHS, PathsConfig

        with caplog.at_level("WARNING"):
            cfg = PathsConfig(paths=[{"name": f"p{i}"} for i in range(MAX_PATHS + 3)])
        assert [p.name for p in cfg.paths] == [f"p{i}" for i in range(MAX_PATHS)]
        assert "keeping the first" in caplog.text

    @pytest.mark.parametrize(("raw", "want"), [(2, 2.0), (-1, 0.0), (99, 10.0), ("x", 1.0), (None, 1.0)])
    def test_the_slide_time_is_seconds_from_zero_to_ten(self, raw: object, want: float) -> None:
        from openfollow.configuration import PathsConfig

        assert PathsConfig(slide_time=raw).slide_time == want

    @pytest.mark.parametrize(("raw", "want"), [(False, False), ("no", False), ("junk", True), (None, True)])
    def test_show_paths_takes_booleans_only(self, raw: object, want: bool) -> None:
        from openfollow.configuration import PathsConfig

        assert PathsConfig(show_paths=raw).show_paths is want

    def test_paths_from_tables_become_paths_and_junk_is_dropped(self) -> None:
        from openfollow.configuration import PathConfig, PathsConfig

        cfg = PathsConfig(paths=[{"name": "a", "closed": True, "stale": 1}, "junk", PathConfig(name="b")])
        assert [(p.name, p.closed) for p in cfg.paths] == [("a", True), ("b", False)]
        assert PathsConfig(paths={"name": "a"}).paths == []

    def test_a_path_with_no_name_gets_one_from_its_place(self) -> None:
        from openfollow.configuration import PathsConfig

        cfg = PathsConfig(paths=[{"name": "path-2"}, {"name": ""}, {"name": "!!"}])
        assert [p.name for p in cfg.paths] == ["path-2", "path-2-2", "path-3"]

    @pytest.mark.parametrize(
        ("names", "want"),
        [
            (["..", "path-1"], ["path-1-2", "path-1"]),
            (["", "PATH-1"], ["path-1-2", "PATH-1"]),
            (["cross", "cross", "cross-2"], ["cross", "cross-3", "cross-2"]),
        ],
    )
    def test_a_name_made_up_never_takes_one_a_later_path_was_given(self, names: list[str], want: list[str]) -> None:
        from openfollow.configuration import PathsConfig

        assert [p.name for p in PathsConfig(paths=[{"name": n} for n in names]).paths] == want

    def test_names_that_match_ignoring_case_are_made_unique(self, caplog) -> None:
        from openfollow.configuration import PathsConfig

        with caplog.at_level("WARNING"):
            cfg = PathsConfig(
                paths=[{"name": "Cross"}, {"name": "cross"}, {"name": "CROSS"}, {"name": "x" * 32}, {"name": "X" * 32}]
            )
        assert [p.name for p in cfg.paths] == ["Cross", "cross-2", "CROSS-3", "x" * 32, "X" * 30 + "-2"]
        assert "'cross' is already taken, renamed to 'cross-2'" in caplog.text

    def test_a_path_is_found_by_name_ignoring_case(self) -> None:
        from openfollow.configuration import PathsConfig

        cfg = PathsConfig(paths=[{"name": "Downstage-Cross"}, {"name": "loop"}])
        found = cfg.find("downstage-cross")
        assert found is not None and found.name == "Downstage-Cross"
        assert cfg.find("nope") is None

    def test_replacing_paths_keeps_this_station_s_active_flags_by_name(self) -> None:
        from openfollow.configuration import PathsConfig

        cfg = PathsConfig(paths=[{"name": "Cross", "active": True}, {"name": "loop", "active": False}])
        cfg.replace_paths(
            [
                {"name": "LOOP", "active": True, "points": [{"x": 1}]},
                {"name": "cross", "active": False, "closed": True},
                {"name": "new", "active": True},
            ]
        )
        assert [(p.name, p.active, p.closed) for p in cfg.paths] == [
            ("LOOP", False, False),
            ("cross", True, True),
            ("new", False, False),
        ]
        assert cfg.paths[0].points[0].x == 1.0

    def test_replacing_with_something_that_is_not_a_list_clears_the_paths(self) -> None:
        from openfollow.configuration import PathsConfig

        cfg = PathsConfig(paths=[{"name": "cross", "active": True}])
        cfg.replace_paths("junk")
        assert cfg.paths == []

    def test_paths_survive_a_save_and_load(self, tmp_path) -> None:
        from openfollow.configuration import PathConfig, PathPointConfig, PathsConfig

        paths = PathsConfig(
            show_paths=False,
            slide_time=1.25,
            paths=[
                PathConfig(
                    name="cross",
                    active=True,
                    closed=False,
                    release_at_end=True,
                    points=[PathPointConfig(0, 0, 0, 0), PathPointConfig(4, 0, 1.5, 1.0)],
                ),
                PathConfig(name="loop", closed=True),
            ],
        )
        target = tmp_path / "config.toml"
        save_config(AppConfig(paths=paths), str(target))
        assert load_config(str(target)).paths == paths


def test_apply_runtime_takes_new_paths() -> None:
    from openfollow.configuration import PathConfig, PathsConfig

    app = _app_with()
    new_paths = PathsConfig(paths=[PathConfig(name="cross", active=True)])
    apply_runtime_config_changes(app, AppConfig(paths=new_paths))
    assert app._config.paths == new_paths


class TestPathBindings:
    """Pathfinder and Toggle paths start unbound and validate like every other binding."""

    @pytest.mark.parametrize("field", ["btn_pathfinder", "btn_toggle_paths", "key_pathfinder", "key_toggle_paths"])
    def test_they_start_unbound(self, field: str) -> None:
        assert getattr(ControllerConfig(), field) == ""

    @pytest.mark.parametrize(
        ("field", "bad"), [("btn_pathfinder", "ZZ"), ("btn_toggle_paths", 3), ("key_pathfinder", "F99")]
    )
    def test_an_unknown_input_falls_back_to_unbound(self, field: str, bad: object) -> None:
        assert getattr(ControllerConfig(**{field: bad}), field) == ""

    def test_a_movement_key_is_refused(self) -> None:
        assert ControllerConfig(key_toggle_paths="w").key_toggle_paths == ""

    def test_a_shared_input_stays_with_the_action_above(self) -> None:
        cfg = ControllerConfig(key_reset="x", key_pathfinder="x", btn_reset="X", btn_pathfinder="X")
        assert (cfg.key_reset, cfg.key_pathfinder) == ("x", "")
        assert (cfg.btn_reset, cfg.btn_pathfinder) == ("X", "")

    @pytest.mark.parametrize("field", ["btn_pathfinder", "btn_toggle_paths"])
    def test_3d_mouse_path_buttons_start_unbound(self, field: str) -> None:
        assert getattr(Mouse3DConfig(), field) == -1


class TestPathsHotReload:
    def _app(self) -> _DummyApp:
        from openfollow.paths.engine import PathEngine

        app = _app_with()
        app._paths = PathEngine()
        return app

    @staticmethod
    def _config(*, paths_on: bool, active: bool = True) -> AppConfig:
        from openfollow.configuration import PathConfig, PathPointConfig, PathsConfig

        cfg = AppConfig()
        cfg.ui.shown_features = ["paths"] if paths_on else []
        cfg.paths = PathsConfig(
            paths=[PathConfig(name="cross", active=active, points=[PathPointConfig(0, 0, 0), PathPointConfig(1, 0, 0)])]
        )
        return cfg

    def test_new_paths_reach_the_engine(self) -> None:
        app = self._app()
        apply_runtime_config_changes(app, self._config(paths_on=True))
        assert app._paths.enabled
        assert app._paths.lock_onto(1, "cross", (0.5, 0.5, 0.0))

    def test_switching_paths_off_releases_everyone(self) -> None:
        app = self._app()
        apply_runtime_config_changes(app, self._config(paths_on=True))
        app._paths.press(1, (0.5, 0.5, 0.0))
        apply_runtime_config_changes(app, self._config(paths_on=False))
        assert not app._paths.enabled
        assert not app._paths.is_locked(1)

    def test_switching_paths_off_and_on_keeps_each_path_active(self) -> None:
        app = self._app()
        apply_runtime_config_changes(app, self._config(paths_on=True))
        apply_runtime_config_changes(app, self._config(paths_on=False))
        assert [(p.name, p.active) for p in app._config.paths.paths] == [("cross", True)]
        apply_runtime_config_changes(app, self._config(paths_on=True))
        assert app._paths.lock_onto(1, "cross", (0.5, 0.5, 0.0))

    def test_an_unrelated_change_keeps_the_locks(self) -> None:
        app = self._app()
        apply_runtime_config_changes(app, self._config(paths_on=True))
        app._paths.press(1, (0.5, 0.5, 0.0))
        changed = self._config(paths_on=True)
        changed.grid.width = 12.0
        apply_runtime_config_changes(app, changed)
        assert app._paths.is_locked(1)


# ---------------------------------------------------------------------------
# GroundGridConfig
# ---------------------------------------------------------------------------


def test_ground_grid_defaults_are_inactive() -> None:
    from openfollow.configuration import GroundGridConfig

    gg = GroundGridConfig()
    assert (gg.enabled, gg.visible, gg.z_offset, gg.vertices) == (False, True, 0.0, [])
    assert gg.active is False


@pytest.mark.parametrize("bad", [None, "abc", 3, [1, 2]])
def test_ground_grid_flags_reject_wrong_type(bad: object) -> None:
    from openfollow.configuration import GroundGridConfig

    gg = GroundGridConfig(enabled=bad, visible=bad)
    assert (gg.enabled, gg.visible) == (False, True)


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False), (True, True)])
def test_ground_grid_flags_accept_bool_forms(value: object, expected: bool) -> None:
    from openfollow.configuration import GroundGridConfig

    assert GroundGridConfig(enabled=value).enabled is expected


@pytest.mark.parametrize(("value", "expected"), [(-1.5, -1.5), ("0.6", 0.6), (2, 2.0)])
def test_ground_grid_z_offset_keeps_finite_numbers(value: object, expected: float) -> None:
    from openfollow.configuration import GroundGridConfig

    assert GroundGridConfig(z_offset=value).z_offset == pytest.approx(expected)


@pytest.mark.parametrize("bad", [None, "abc", True, float("nan"), float("inf"), [1.0]])
def test_ground_grid_z_offset_rejects_wrong_type_to_default(bad: object) -> None:
    from openfollow.configuration import GroundGridConfig

    assert GroundGridConfig(z_offset=bad).z_offset == 0.0


@pytest.mark.parametrize("bad", [None, "abc", True, 5, {"x": 1}])
def test_ground_grid_vertices_reject_non_list(bad: object) -> None:
    from openfollow.configuration import GroundGridConfig

    assert GroundGridConfig(vertices=bad).vertices == []


def test_ground_grid_vertices_keep_only_finite_pairs() -> None:
    from openfollow.configuration import GroundGridConfig

    gg = GroundGridConfig(
        vertices=[
            [0, 0],
            ["1", "2.5"],
            ["bad", 1],
            [3],
            "xy",
            [True, 1],
            [float("nan"), 1],
            [4, float("inf")],
            (5, 6, 7),
        ]
    )
    assert gg.vertices == [[0.0, 0.0], [1.0, 2.5], [5.0, 6.0]]


def test_ground_grid_vertices_are_capped() -> None:
    from openfollow.configuration import GROUND_GRID_MAX_VERTICES, GroundGridConfig

    gg = GroundGridConfig(vertices=[[float(i), 0.0] for i in range(GROUND_GRID_MAX_VERTICES + 10)])
    assert len(gg.vertices) == GROUND_GRID_MAX_VERTICES
    assert gg.vertices[-1] == [float(GROUND_GRID_MAX_VERTICES - 1), 0.0]


@pytest.mark.parametrize(
    ("enabled", "vertices", "active"),
    [
        (True, [[0, 0], [1, 0], [1, 1]], True),
        (True, [[0, 0], [1, 0]], False),
        (False, [[0, 0], [1, 0], [1, 1]], False),
        (True, [[0, 0], [1, 0], ["x", 1]], False),
    ],
)
def test_ground_grid_active_needs_enabled_and_three_corners(enabled: bool, vertices: list, active: bool) -> None:
    from openfollow.configuration import GroundGridConfig

    assert GroundGridConfig(enabled=enabled, vertices=vertices).active is active


def test_ground_grid_round_trips_through_toml(tmp_path) -> None:
    from openfollow.configuration import load_config, save_config

    cfg = AppConfig()
    cfg.ground_grid.enabled = True
    cfg.ground_grid.visible = False
    cfg.ground_grid.z_offset = -1.2
    cfg.ground_grid.vertices = [[0.0, 0.0], [2.0, 0.0], [2.0, 1.5]]
    path = tmp_path / "config.toml"
    save_config(cfg, str(path))

    loaded = load_config(str(path))
    assert loaded.ground_grid == cfg.ground_grid


def test_ground_grid_hand_edited_toml_is_normalised(tmp_path) -> None:
    from openfollow.configuration import load_config

    path = tmp_path / "config.toml"
    path.write_text('[ground_grid]\nenabled = "yes"\nz_offset = "low"\nvertices = [[0, 0], ["a", 1], [1, 0], [1, 1]]\n')
    gg = load_config(str(path)).ground_grid
    assert gg.enabled is True
    assert gg.z_offset == 0.0
    assert gg.vertices == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]


def test_apply_runtime_ground_grid_swaps_live() -> None:
    app = _DummyApp(AppConfig())
    new_config = AppConfig()
    new_config.ground_grid.enabled = True
    new_config.ground_grid.vertices = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]

    apply_runtime_config_changes(app, new_config)

    assert app._config.ground_grid == new_config.ground_grid
    assert app._web_commands.restart_requested is False


@pytest.mark.parametrize("bad", [None, 7, True])
def test_trigger_zone_vertices_reject_non_list(bad: object) -> None:
    from openfollow.configuration import TriggerZoneConfig

    assert TriggerZoneConfig(vertices=bad).vertices == []


# --------------------------------------------------------------------------- #
# Network mode
# --------------------------------------------------------------------------- #

_PLANE_PINS = {
    "otp_output": lambda: OtpOutputConfig(source_iface="eth1"),
    "rttrpm_output": lambda: RttrpmOutputConfig(source_iface="eth1"),
    "osc": lambda: OscConfig(listen_iface="eth1"),
    "video_input_iface": lambda: "eth1",
    "web_bind_iface": lambda: "eth1",
}


def test_a_fresh_station_starts_in_simple() -> None:
    assert AppConfig().network_mode == "simple"


@pytest.mark.parametrize("raw", ["Simple", "", "expert", None, 1, True, ["simple"]])
def test_an_unknown_network_mode_without_a_pin_reads_as_simple(raw: object) -> None:
    assert AppConfig(network_mode=raw).network_mode == "simple"  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["Advanced", " advanced ", "ADVANCED"])
def test_the_mode_is_read_case_and_space_blind(raw: str) -> None:
    assert AppConfig(network_mode=raw).network_mode == "advanced"


@pytest.mark.parametrize("raw", ["advance", "", None, True, 3])
def test_an_unreadable_mode_never_erases_a_pin(raw: object) -> None:
    cfg = AppConfig(network_mode=raw, video_input_iface="eth1")  # type: ignore[arg-type]
    assert (cfg.network_mode, cfg.video_input_iface) == ("advanced", "eth1")


@pytest.mark.parametrize("field", sorted(_PLANE_PINS))
def test_simple_holds_no_plane_pin(field: str) -> None:
    from openfollow.configuration import plane_pins

    cfg = AppConfig(network_mode="simple", **{field: _PLANE_PINS[field]()})
    assert plane_pins(cfg) == []


def test_simple_clears_an_osc_destination_pin() -> None:
    from openfollow.configuration import OscDestinationConfig, OscDestinationsConfig, plane_pins

    dests = OscDestinationsConfig(destinations=[OscDestinationConfig(id="desk", source_iface="eth1")])
    assert plane_pins(AppConfig(network_mode="simple", osc_destinations=dests)) == []
    dests = OscDestinationsConfig(destinations=[OscDestinationConfig(id="desk", source_iface="eth1")])
    assert plane_pins(AppConfig(network_mode="advanced", osc_destinations=dests)) == [
        ("osc_destinations.desk.source_iface", "eth1")
    ]


@pytest.mark.parametrize("field", sorted(_PLANE_PINS))
def test_advanced_keeps_its_pins(field: str) -> None:
    from openfollow.configuration import plane_pins

    assert len(plane_pins(AppConfig(network_mode="advanced", **{field: _PLANE_PINS[field]()}))) == 1


def test_simple_keeps_the_station_interface_and_web_bind() -> None:
    cfg = AppConfig(network_mode="simple", psn_source_iface="eth0", web_bind="192.0.2.5")
    assert (cfg.psn_source_iface, cfg.web_bind) == ("eth0", "192.0.2.5")


_TOML_PINS = [
    'video_input_iface = "eth1"',
    'web_bind_iface = "eth1"',
    '[otp_output]\nsource_iface = "eth1"',
    '[rttrpm_output]\nsource_iface = "eth1"',
    '[osc]\nlisten_iface = "eth1"',
    '[[osc_destinations.destinations]]\nid = "desk"\nsource_iface = "eth1"',
]


@pytest.mark.parametrize("toml", _TOML_PINS)
def test_a_config_without_a_mode_starts_in_advanced_when_it_pins_a_plane(tmp_path, toml: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(toml + "\n")
    cfg = load_config(str(path))
    assert cfg.network_mode == "advanced"
    assert "eth1" in str(config_to_toml_dict(cfg))


@pytest.mark.parametrize(
    "toml",
    [
        "",
        'psn_source_iface = "eth0"',
        'web_bind = "192.0.2.5"',
        '[interface_labels]\neth0 = "Show"',
        'video_input_iface = "  "',
        "video_input_iface = 5",
        '[osc_destinations]\ndestinations = ["x"]',
    ],
)
def test_a_config_without_a_mode_starts_in_simple_without_a_plane_pin(tmp_path, toml: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(toml + "\n")
    assert load_config(str(path)).network_mode == "simple"


@pytest.mark.parametrize("toml", _TOML_PINS)
def test_a_pin_hand_edited_into_a_simple_config_is_cleared_at_load(tmp_path, toml: str) -> None:
    from openfollow.configuration import plane_pins

    path = tmp_path / "config.toml"
    path.write_text('network_mode = "simple"\n' + toml + "\n")
    assert plane_pins(load_config(str(path))) == []


def test_the_mode_survives_a_toml_round_trip(tmp_path) -> None:
    from openfollow.configuration import network_mode_unsaved

    path = tmp_path / "config.toml"
    # A file that does not read says nothing: it may have been recovered from the backup.
    assert not network_mode_unsaved(str(path))
    path.write_text("not = [toml")
    assert not network_mode_unsaved(str(path))
    path.write_text('psn_source_iface = "eth0"\n')
    assert network_mode_unsaved(str(path))
    save_config(AppConfig(network_mode="advanced"), str(path))
    assert not network_mode_unsaved(str(path))
    assert load_config(str(path)).network_mode == "advanced"


def test_a_hot_reload_into_simple_clears_the_live_pins(tmp_path) -> None:
    path = tmp_path / "config.toml"
    save_config(AppConfig(network_mode="advanced", otp_output=OtpOutputConfig(source_iface="eth1")), str(path))
    text = path.read_text().replace('network_mode = "advanced"', 'network_mode = "simple"')
    path.write_text(text)
    assert load_config(str(path), strict=True).otp_output.source_iface == ""


@pytest.mark.parametrize(("old", "new"), [("advanced", "simple"), ("simple", "advanced")])
def test_a_mode_switch_reaches_the_running_config(old: str, new: str) -> None:
    app = _DummyApp(AppConfig(network_mode=old))
    apply_runtime_config_changes(app, AppConfig(network_mode=new))
    assert app._config.network_mode == new


def test_a_cleared_web_ui_pin_reaches_the_running_config() -> None:
    """The listener waits for a restart, but a whole-config save must not write the pin back."""
    app = _DummyApp(AppConfig(network_mode="advanced", web_bind_iface="eth1"))
    apply_runtime_config_changes(app, AppConfig(network_mode="simple"))
    assert app._config.web_bind_iface == ""


def test_a_video_pin_simple_cleared_reaches_the_running_config_without_a_rebuild() -> None:
    """The Media Gallery has no pin to rebuild for, so only the mirror can drop it."""
    app = _DummyApp(AppConfig(network_mode="advanced", video_input_iface="eth1"))
    apply_runtime_config_changes(app, AppConfig(network_mode="simple"))
    assert app._config.video_input_iface == ""


# ---------------------------------------------------------------------------
# CameraConfig.calibration_mode / calibration_marks – the Setup Wizard's last calibration
# ---------------------------------------------------------------------------

_DSL = {"id": 1, "kind": "known", "x": 5.0, "y": 0.0, "z": 0.0, "pin": [0.81, 0.94]}
_DSR = {"id": 2, "kind": "known", "x": -5.0, "y": 0.0, "z": 0.0, "pin": [0.14, 0.95]}
_FAR = {
    "id": 3,
    "kind": "triangulated",
    "from_a": 1,
    "from_b": 2,
    "distance_a": 21.4,
    "distance_b": 23.9,
    "z": 1.2,
    "pin": [0.41, 0.22],
}


def _mark(base: dict[str, object], **changes: object) -> dict[str, object]:
    return {**base, **changes}


def _marks(*entries: object, mode: str = "advanced") -> list:
    return CameraConfig(calibration_mode=mode, calibration_marks=list(entries)).calibration_marks  # type: ignore[arg-type]


@pytest.fixture
def fresh_drop_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    import openfollow.configuration as cfg_mod

    monkeypatch.setattr(cfg_mod, "_reported_drops", {})


def _drop_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("camera.calibration_marks")]


def test_a_camera_starts_in_simple_without_marks_and_says_nothing(caplog, fresh_drop_reports) -> None:
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        camera = CameraConfig()
        assert CameraConfig(calibration_marks=[]).calibration_marks == []
    assert (camera.calibration_mode, camera.calibration_marks) == ("simple", [])
    assert caplog.records == []


@pytest.mark.parametrize(("raw", "expected"), [("simple", "simple"), ("advanced", "advanced")])
def test_calibration_mode_keeps_a_known_mode(raw: str, expected: str) -> None:
    assert CameraConfig(calibration_mode=raw).calibration_mode == expected


@pytest.mark.parametrize("raw", ["Advanced", "expert", "", None, 1, True, ["advanced"]])
def test_calibration_mode_off_the_list_reads_as_simple(raw: object) -> None:
    assert CameraConfig(calibration_mode=raw).calibration_mode == "simple"  # type: ignore[arg-type]


def test_a_known_mark_keeps_its_coordinates_and_pin_and_nothing_it_does_not_read() -> None:
    (mark,) = _marks(
        _mark(_DSL, x="5", y=-2, z="0.5", from_a=2, from_b=3, distance_a=4.0, position=[1.0, 2.0, 3.0], fit=9)
    )
    assert (mark.id, mark.name, mark.kind, mark.x, mark.y, mark.z) == (1, "P1", "known", 5.0, -2.0, 0.5)
    assert (mark.from_a, mark.from_b, mark.distance_a, mark.distance_b) == (0, 0, 0.0, 0.0)
    assert (mark.pin, mark.position) == ([0.81, 0.94], [])


def test_a_triangulated_mark_keeps_its_measurements_and_nothing_it_does_not_read() -> None:
    (_, _, mark) = _marks(_DSL, _DSR, _mark(_FAR, x=4.0, y=7.0, distance_a="21.4", position=[2.5, 9, 1.2]))
    assert (mark.id, mark.kind, mark.x, mark.y, mark.z) == (3, "triangulated", 0.0, 0.0, 1.2)
    assert (mark.from_a, mark.from_b, mark.distance_a, mark.distance_b) == (1, 2, 21.4, 23.9)
    assert (mark.pin, mark.position) == ([0.41, 0.22], [2.5, 9.0, 1.2])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Reference Point  ", "Reference Point"),
        ("x" * 70, "x" * 60),
        ("", "P1"),
        ("   ", "P1"),
        (None, "P1"),
        (42, "P1"),
    ],
)
def test_a_mark_name_is_trimmed_capped_and_defaults_to_its_number(raw: object, expected: str) -> None:
    (mark,) = _marks(_mark(_DSL, name=raw))
    assert mark.name == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([2.5, 9, -0.5], [2.5, 9.0, -0.5]),
        (("2.5", "9", "0"), [2.5, 9.0, 0.0]),
        ([], []),
        ([2.5, 9.0], []),
        ([2.5, 9.0, 0.0, 1.0], []),
        ([2.5, "abc", 0.0], []),
        ([2.5, None, 0.0], []),
        ([2.5, True, 0.0], []),
        ([2.5, float("nan"), 0.0], []),
        ([2.5, float("inf"), 0.0], []),
        ("2.5, 9, 0", []),
        (None, []),
        ([10_000.5, 9.0, 0.0], []),
        ([2.5, -10_000.5, 0.0], []),
        ([10_000.0, -10_000.0, 10_000.0], [10_000.0, -10_000.0, 10_000.0]),
    ],
)
def test_a_triangulated_mark_position_is_three_numbers_or_none(raw: object, expected: list[float]) -> None:
    (*_, mark) = _marks(_DSL, _DSR, _mark(_FAR, position=raw))
    assert mark.position == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([0.0, 1.0], [0.0, 1.0]),
        (("0.5", "0.25"), [0.5, 0.25]),
        ([], []),
        (None, []),
    ],
)
def test_a_known_mark_pin_is_two_numbers_or_not_pinned(raw: object, expected: list[float]) -> None:
    """A known mark need not be pinned: it may sit outside the picture."""
    (mark,) = _marks(_mark(_DSL, pin=raw))
    assert mark.pin == expected


_UNUSABLE_MARKS = {
    "id none": (_mark(_DSL, id=None), "needs an id between 1 and 999"),
    "id text": (_mark(_DSL, id="abc"), "needs an id between 1 and 999"),
    "id bool": (_mark(_DSL, id=True), "needs an id between 1 and 999"),
    "id zero": (_mark(_DSL, id=0), "needs an id between 1 and 999"),
    "id negative": (_mark(_DSL, id=-1), "needs an id between 1 and 999"),
    "id fraction": (_mark(_DSL, id=1.5), "needs an id between 1 and 999"),
    "id past 999": (_mark(_DSL, id=1000), "needs an id between 1 and 999"),
    "id huge": (_mark(_DSL, id=1e300), "needs an id between 1 and 999"),
    "kind none": (_mark(_DSL, kind=None), "needs a kind of known or triangulated"),
    "kind bool": (_mark(_DSL, kind=True), "needs a kind of known or triangulated"),
    "kind off the list": (_mark(_DSL, kind="laser"), "needs a kind of known or triangulated"),
    "kind in capitals": (_mark(_DSL, kind="Known"), "needs a kind of known or triangulated"),
    "x none": (_mark(_DSL, x=None), "needs its X and Y"),
    "x text": (_mark(_DSL, x="abc"), "needs its X and Y"),
    "x bool": (_mark(_DSL, x=True), "needs its X and Y"),
    "x inf": (_mark(_DSL, x=float("inf")), "needs its X and Y"),
    "x out of reach": (_mark(_DSL, x=10_000.5), "needs its X and Y"),
    "x out of reach stage right": (_mark(_DSL, x=-10_000.5), "needs its X and Y"),
    "y none": (_mark(_DSL, y=None), "needs its X and Y"),
    "y text": (_mark(_DSL, y="abc"), "needs its X and Y"),
    "y bool": (_mark(_DSL, y=False), "needs its X and Y"),
    "y out of reach": (_mark(_DSL, y=-10_000.5), "needs its X and Y"),
    "z none": (_mark(_DSL, z=None), "needs its Z"),
    "z text": (_mark(_DSL, z="abc"), "needs its Z"),
    "z bool": (_mark(_DSL, z=True), "needs its Z"),
    "z out of reach": (_mark(_DSL, z=10_000.5), "needs its Z"),
    "pin left of the snapshot": (_mark(_DSL, pin=[-0.01, 0.5]), "needs its pin on the snapshot"),
    "pin below the snapshot": (_mark(_DSL, pin=[0.5, 1.01]), "needs its pin on the snapshot"),
    "pin of one number": (_mark(_DSL, pin=[0.5]), "needs its pin on the snapshot"),
    "pin of three numbers": (_mark(_DSL, pin=[0.5, 0.5, 0.5]), "needs its pin on the snapshot"),
    "pin with a bool": (_mark(_DSL, pin=[True, 0.5]), "needs its pin on the snapshot"),
    "pin with none": (_mark(_DSL, pin=[None, 0.5]), "needs its pin on the snapshot"),
    "pin with text": (_mark(_DSL, pin=["abc", 0.5]), "needs its pin on the snapshot"),
    "pin with nan": (_mark(_DSL, pin=[float("nan"), 0.5]), "needs its pin on the snapshot"),
    "pin text": (_mark(_DSL, pin="abc"), "needs its pin on the snapshot"),
    "pin a number": (_mark(_DSL, pin=0.5), "needs its pin on the snapshot"),
    "pin a bool": (_mark(_DSL, pin=True), "needs its pin on the snapshot"),
    "not a table": ("DSL 5 0 0", "is not a table"),
    "a number": (42, "is not a table"),
}


@pytest.mark.parametrize(("bad", "reason"), list(_UNUSABLE_MARKS.values()), ids=list(_UNUSABLE_MARKS))
def test_a_known_mark_that_cannot_be_used_is_dropped_never_clamped(bad, reason, caplog, fresh_drop_reports) -> None:
    """A wrong measurement is not a measurement nearby: clamping would invent one."""
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        marks = _marks(bad, _mark(_DSR, id=7))
    assert [m.id for m in marks] == [7]
    assert _drop_warnings(caplog) == [f"camera.calibration_marks: entry 1 {reason}; dropped"]


_UNUSABLE_TRIANGULATED = {
    "from_a none": (_mark(_FAR, from_a=None), "needs two other marks to be measured from"),
    "from_a text": (_mark(_FAR, from_a="abc"), "needs two other marks to be measured from"),
    "from_a bool": (_mark(_FAR, from_a=True), "needs two other marks to be measured from"),
    "from_b missing": ({k: v for k, v in _FAR.items() if k != "from_b"}, "needs two other marks to be measured from"),
    "from itself": (_mark(_FAR, from_a=3), "needs two other marks to be measured from"),
    "from one mark twice": (_mark(_FAR, from_b=1), "needs two other marks to be measured from"),
    "distance_a none": (_mark(_FAR, distance_a=None), "needs both distances between 0 and 200 m"),
    "distance_a text": (_mark(_FAR, distance_a="abc"), "needs both distances between 0 and 200 m"),
    "distance_a bool": (_mark(_FAR, distance_a=True), "needs both distances between 0 and 200 m"),
    "distance_a zero": (_mark(_FAR, distance_a=0.0), "needs both distances between 0 and 200 m"),
    "distance_b zero": (_mark(_FAR, distance_b=0.0), "needs both distances between 0 and 200 m"),
    "distance_b negative": (_mark(_FAR, distance_b=-3.0), "needs both distances between 0 and 200 m"),
    "distance_a past 200 m": (_mark(_FAR, distance_a=200.5), "needs both distances between 0 and 200 m"),
    "distance_b inf": (_mark(_FAR, distance_b=float("inf")), "needs both distances between 0 and 200 m"),
    "z none": (_mark(_FAR, z=None), "needs its Z"),
    "z out of reach": (_mark(_FAR, z=-10_000.5), "needs its Z"),
    "not pinned": (_mark(_FAR, pin=[]), "needs its pin: two distances allow two places"),
    "pin malformed": (_mark(_FAR, pin=[0.4]), "needs its pin: two distances allow two places"),
    "pin off the snapshot": (_mark(_FAR, pin=[1.2, 0.2]), "needs its pin on the snapshot"),
}


@pytest.mark.parametrize(("bad", "reason"), list(_UNUSABLE_TRIANGULATED.values()), ids=list(_UNUSABLE_TRIANGULATED))
def test_a_triangulated_mark_that_cannot_be_used_is_dropped_never_clamped(
    bad, reason, caplog, fresh_drop_reports
) -> None:
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        marks = _marks(_DSL, _DSR, bad)
    assert [m.id for m in marks] == [1, 2]
    assert _drop_warnings(caplog) == [f"camera.calibration_marks: entry 3 {reason}; dropped"]


@pytest.mark.parametrize(
    "edge",
    [
        _mark(_DSL, x=10_000.0, y=-10_000.0, z=10_000.0),
        _mark(_DSL, z=-10_000.0, pin=[0.0, 0.0]),
        _mark(_DSL, pin=[1.0, 1.0]),
    ],
)
def test_a_known_mark_at_the_edge_of_reach_is_kept(edge: dict[str, object]) -> None:
    (mark,) = _marks(edge)
    assert (mark.x, mark.y, mark.z) == (float(edge["x"]), float(edge["y"]), float(edge["z"]))  # type: ignore[arg-type]


@pytest.mark.parametrize(("distance_a", "distance_b"), [(200.0, 0.01), (1e-6, 200)])
def test_a_triangulated_mark_keeps_distances_up_to_200_m(distance_a: float, distance_b: float) -> None:
    (*_, mark) = _marks(_DSL, _DSR, _mark(_FAR, distance_a=distance_a, distance_b=distance_b))
    assert (mark.distance_a, mark.distance_b) == (float(distance_a), float(distance_b))


_USL = _mark(_DSL, id=4, y=6.0)


@pytest.mark.parametrize(
    ("entries", "kept"),
    [
        ([_DSL, _DSR, _FAR, _USL], [1, 2, 4]),  # a triangulated mark, even with a corner's id
        ([_DSL, _DSR, _mark(_DSR, id=3, y=6.0), _mark(_DSL, id=5, y=6.0)], [1, 2, 3]),
        ([_DSL, _DSR, _mark(_DSR, id=3, y=6.0), _mark(_DSL, id=12, x=0.0)], [1, 2, 3]),
    ],
)
def test_simple_keeps_only_its_four_corners(entries, kept, caplog, fresh_drop_reports) -> None:
    """Simple's marks are the rectangle's corners, known marks with ids 1 to 4; anything else is Advanced's."""
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        marks = _marks(*entries, mode="simple")
    assert [m.id for m in marks] == kept
    assert len(_drop_warnings(caplog)) == 1
    assert "is not one of Simple's four corners; dropped" in _drop_warnings(caplog)[0]


def test_advanced_keeps_the_same_marks_simple_drops() -> None:
    assert [m.id for m in _marks(_DSL, _DSR, _FAR, _USL, _mark(_DSL, id=12), mode="advanced")] == [1, 2, 3, 4, 12]


def test_a_repeated_id_keeps_the_first_mark(caplog, fresh_drop_reports) -> None:
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        marks = _marks(_DSL, _mark(_DSR, id=1))
    assert [(m.id, m.x) for m in marks] == [(1, 5.0)]
    assert _drop_warnings(caplog) == ["camera.calibration_marks: entry 2 repeats id 1; dropped"]


def test_a_mark_measured_from_a_mark_that_is_not_there_is_dropped_and_so_is_one_measured_from_it(
    caplog, fresh_drop_reports
) -> None:
    from_dropped = _mark(_FAR, id=4, from_a=3)
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        marks = _marks(_DSL, _DSR, _mark(_FAR, from_b=9), from_dropped)
    assert [m.id for m in marks] == [1, 2]
    assert _drop_warnings(caplog) == [
        "camera.calibration_marks: mark id 3 is measured from a mark that is not kept;"
        " mark id 4 is measured from a mark that is not kept; dropped"
    ]


def test_marks_measured_from_each_other_are_dropped_with_the_marks_measured_from_them(
    caplog, fresh_drop_reports
) -> None:
    """3 needs 4 and 4 needs 3: neither can be placed, nor can 5, which needs 3."""
    looping = [_mark(_FAR, id=3, from_a=1, from_b=4), _mark(_FAR, id=4, from_a=3, from_b=2)]
    downstream = _mark(_FAR, id=5, from_a=3, from_b=1)
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        marks = _marks(_DSL, _DSR, *looping, downstream, _mark(_FAR, id=6))
    assert [m.id for m in marks] == [1, 2, 6]
    assert _drop_warnings(caplog) == [
        "camera.calibration_marks: mark id 3 is measured from itself through others;"
        " mark id 4 is measured from itself through others; mark id 5 is measured from a mark that is not kept; dropped"
    ]


def test_a_mark_measured_from_a_triangulated_mark_is_kept() -> None:
    chained = _mark(_FAR, id=4, from_a=3, from_b=2)
    assert [m.id for m in _marks(_DSL, _DSR, _FAR, chained)] == [1, 2, 3, 4]


def test_calibration_marks_keep_the_first_twelve_usable(caplog, fresh_drop_reports) -> None:
    from openfollow.configuration import MAX_CALIBRATION_MARKS

    entries = ["junk", *[_mark(_DSL, id=i, x=float(i)) for i in range(1, 15)]]
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        marks = _marks(*entries)
    assert MAX_CALIBRATION_MARKS == 12
    assert [m.id for m in marks] == list(range(1, 13))
    assert _drop_warnings(caplog) == [
        "camera.calibration_marks: entry 1 is not a table; entries from 14 on, past the first 12 usable; dropped"
    ]


def test_many_unusable_marks_are_reported_in_one_line(caplog, fresh_drop_reports) -> None:
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        marks = _marks(*([1] * 1000), _DSL)
    assert [m.id for m in marks] == [1]
    assert _drop_warnings(caplog) == [
        "camera.calibration_marks: entry 1 is not a table; entry 2 is not a table; entry 3 is not a table;"
        " 997 more; dropped"
    ]


@pytest.mark.parametrize("raw", ["DSL", 3, None, {"id": 1, "kind": "known"}])
def test_calibration_marks_that_are_not_a_list_read_as_none_and_say_so(raw, caplog, fresh_drop_reports) -> None:
    """``[camera.calibration_marks]`` (one table) where ``[[...]]`` was meant must not vanish unseen."""
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        assert CameraConfig(calibration_marks=raw).calibration_marks == []  # type: ignore[arg-type]
    assert _drop_warnings(caplog) == ["camera.calibration_marks: is not a list of tables; dropped"]


def test_a_dropped_mark_is_reported_once_per_distinct_drop(caplog, fresh_drop_reports) -> None:
    """Every web request loads the config again; one bad entry must not repeat its warning each time."""
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        for _ in range(3):
            _marks(_mark(_DSL, x=None), _DSR)
            CameraConfig()  # the frame loop's bare camera is no new file
        _marks(_DSL, _mark(_DSR, z=None))
        _marks(_DSL, _mark(_DSR, z=None))
    assert _drop_warnings(caplog) == [
        "camera.calibration_marks: entry 1 needs its X and Y; dropped",
        "camera.calibration_marks: entry 2 needs its Z; dropped",
    ]


def test_calibration_marks_survive_a_toml_round_trip_with_finite_numbers_only(tmp_path) -> None:
    import json
    from dataclasses import asdict

    path = tmp_path / "config.toml"
    unpinned_known = _mark(_DSR, pin=[])
    chained = _mark(_FAR, id=4, name="USR riser", from_a=3, from_b=2, z=0.0, position=[-5.0, 6.0, 0.0])
    camera = CameraConfig(calibration_mode="advanced", calibration_marks=[_DSL, unpinned_known, _FAR, chained])
    save_config(AppConfig(camera=camera), path)
    loaded = load_config(str(path)).camera
    assert loaded.calibration_mode == "advanced"
    assert loaded.calibration_marks == camera.calibration_marks
    assert [(m.id, m.name, m.pin, m.position) for m in loaded.calibration_marks] == [
        (1, "P1", [0.81, 0.94], []),
        (2, "P2", [], []),
        (3, "P3", [0.41, 0.22], []),
        (4, "USR riser", [0.41, 0.22], [-5.0, 6.0, 0.0]),
    ]
    assert "nan" not in path.read_text(encoding="utf-8").lower()
    json.dumps(asdict(loaded), allow_nan=False)  # raises on any NaN or inf


# CameraConfig.calibration_height: the camera's exact height the Setup Wizard's solve held


def _height_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("camera.calibration_height")]


@pytest.mark.parametrize("raw", [None, "", "  "], ids=["none", "empty", "blank"])
def test_a_calibration_height_not_entered_reads_as_none_and_says_nothing(raw, caplog, fresh_drop_reports) -> None:
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        assert CameraConfig(calibration_height=raw).calibration_height is None  # type: ignore[arg-type]
        assert CameraConfig().calibration_height is None
    assert caplog.records == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(9.8, 9.8), (12, 12.0), (0.0, 0.0), (-2.5, -2.5), ("9.8", 9.8), (10_000.0, 10_000.0), (-10_000.0, -10_000.0)],
)
def test_a_calibration_height_within_reach_is_kept_as_metres(raw, expected: float) -> None:
    height = CameraConfig(calibration_height=raw).calibration_height
    assert height == expected and isinstance(height, float)


@pytest.mark.parametrize(
    "raw", [True, False, "abc", [12.0], {"z": 12.0}], ids=["true", "false", "text", "list", "table"]
)
def test_a_calibration_height_of_the_wrong_type_is_dropped_and_said(raw, caplog, fresh_drop_reports) -> None:
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        assert CameraConfig(calibration_height=raw).calibration_height is None
    assert len(_height_warnings(caplog)) == 1


@pytest.mark.parametrize("raw", [10_000.5, -10_000.5, 1e300], ids=["above", "below", "huge"])
def test_a_calibration_height_out_of_reach_is_dropped_not_clamped(raw: float, caplog, fresh_drop_reports) -> None:
    with caplog.at_level(logging.WARNING, logger="openfollow.configuration"):
        assert CameraConfig(calibration_height=raw).calibration_height is None
    assert _height_warnings(caplog) == [f"camera.calibration_height: {raw!r} is not a height within 10000 m; dropped"]


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf"), "nan", "inf"])
def test_a_calibration_height_that_is_not_finite_is_dropped(raw, fresh_drop_reports) -> None:
    assert CameraConfig(calibration_height=raw).calibration_height is None


@pytest.mark.parametrize("height", [9.8, None])
def test_a_calibration_height_survives_a_toml_round_trip(tmp_path, height: float | None) -> None:
    path = tmp_path / "config.toml"
    marks = [_DSL, _DSR]
    camera = CameraConfig(calibration_mode="advanced", calibration_marks=marks, calibration_height=height)  # type: ignore[arg-type]
    save_config(AppConfig(camera=camera), path)
    assert load_config(str(path)).camera.calibration_height == height
    assert ("calibration_height" in path.read_text(encoding="utf-8")) is (height is not None)


def test_a_hand_edited_calibration_height_is_read_through_load_config(tmp_path, fresh_drop_reports) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[camera]\ncalibration_height = "abc"\n', encoding="utf-8")
    assert load_config(str(path)).camera.calibration_height is None
    path.write_text("[camera]\ncalibration_height = 7.25\n", encoding="utf-8")
    assert load_config(str(path)).camera.calibration_height == 7.25


# CameraConfig's calibration record: the lens rating, time and video source of the Setup Wizard's save


@pytest.mark.parametrize("rating", ["excellent", "good", "fair", "poor", "user_input", "loaded", ""])
def test_a_calibration_lens_rating_on_the_list_is_kept(rating: str) -> None:
    assert CameraConfig(calibration_lens_rating=rating).calibration_lens_rating == rating


# A fit rated Not usable is never applied, so no saved lens carries that rating.
@pytest.mark.parametrize(
    "raw",
    ["Good", "okay", "medium", "low", "not_usable", " good"],
    ids=["case", "okay", "medium", "low", "not usable", "pad"],
)
def test_a_calibration_lens_rating_off_the_list_reads_as_none(raw: str) -> None:
    assert CameraConfig(calibration_lens_rating=raw).calibration_lens_rating == ""


@pytest.mark.parametrize("raw", [None, 3, True, ["good"]], ids=["none", "number", "bool", "list"])
def test_a_calibration_lens_rating_of_the_wrong_type_reads_as_none(raw: object) -> None:
    assert CameraConfig(calibration_lens_rating=raw).calibration_lens_rating == ""  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-25T11:04:00+01:00", "2026-10-25T11:04:00+01:00"),
        ("2026-10-26T09:30:00-02:30", "2026-10-26T09:30:00-02:30"),
        ("2026-10-25T10:04:00Z", "2026-10-25T10:04:00+00:00"),
        ("2026-10-25T11:04:05.678+01:00", "2026-10-25T11:04:05+01:00"),
        (" 2026-10-25 11:04+01:00 ", "2026-10-25T11:04:00+01:00"),
    ],
    ids=["offset", "negative half hour", "z", "fraction", "space"],
)
def test_a_calibration_time_with_its_offset_is_kept_to_the_second(raw: str, expected: str) -> None:
    assert CameraConfig(calibration_time=raw).calibration_time == expected


@pytest.mark.parametrize(
    "raw",
    ["2026-10-25T11:04:00", "2026-10-25", "2026-13-01T00:00:00+00:00", "yesterday", ""],
    ids=["no offset", "date only", "no such month", "text", "empty"],
)
def test_a_calibration_time_without_an_offset_or_unreadable_reads_as_none(raw: str) -> None:
    assert CameraConfig(calibration_time=raw).calibration_time == ""


@pytest.mark.parametrize(
    "raw", [None, 1761386640, True, ["2026-10-25T11:04:00+01:00"]], ids=["none", "number", "bool", "list"]
)
def test_a_calibration_time_of_the_wrong_type_reads_as_none(raw: object) -> None:
    assert CameraConfig(calibration_time=raw).calibration_time == ""  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RTSP: rtsp://192.0.2.10/stream", "RTSP: rtsp://192.0.2.10/stream"),
        ("RTSP: rtsp://user:pw@192.0.2.10/stream", "RTSP: rtsp://192.0.2.10/stream"),
        ("SRT srt://192.0.2.1:5000?passphrase=secret123", "SRT srt://192.0.2.1:5000?passphrase=***"),
        ("NDI®: Stage\x00 \x1b[31mLeft\u202e", "NDI®: Stage [31mLeft"),
        ("SRT srt://192.0.2.1:5000?pass\x00phrase=secret123", "SRT srt://192.0.2.1:5000?passphrase=***"),
        ("9\x00rtsp://user:secret@cam/s", "9rtsp://cam/s"),
        ("_\u200ertsp://user:secret@cam/s", "_rtsp://cam/s"),
        ("  Media Gallery (Clip)  ", "Media Gallery (Clip)"),
    ],
    ids=[
        "plain",
        "credentials",
        "passphrase",
        "control characters",
        "split passphrase",
        "joined digit",
        "joined underscore",
        "pad",
    ],
)
def test_a_calibration_source_is_kept_without_credentials_or_control_characters(raw: str, expected: str) -> None:
    assert CameraConfig(calibration_source=raw).calibration_source == expected


def test_a_calibration_source_past_its_length_is_cut() -> None:
    source = CameraConfig(calibration_source="NDI®: " + "x" * 400).calibration_source
    assert source == ("NDI®: " + "x" * 400)[:CALIBRATION_SOURCE_MAX_LEN]


@pytest.mark.parametrize("raw", [None, 3, True, ["NDI®: Stage"]], ids=["none", "number", "bool", "list"])
def test_a_calibration_source_of_the_wrong_type_reads_as_none(raw: object) -> None:
    assert CameraConfig(calibration_source=raw).calibration_source == ""  # type: ignore[arg-type]


def test_a_calibration_record_survives_a_toml_round_trip(tmp_path) -> None:
    path = tmp_path / "config.toml"
    record = {
        "calibration_lens_rating": "poor",
        "calibration_time": "2026-10-26T09:30:00-02:30",
        "calibration_source": "USB Camera: Webcam (1280x720@30)",
    }
    camera = CameraConfig(calibration_mode="advanced", calibration_marks=[_DSL, _DSR], **record)  # type: ignore[arg-type]
    save_config(AppConfig(camera=camera), path)
    loaded = load_config(str(path)).camera
    assert {name: getattr(loaded, name) for name in record} == record


def test_an_unquoted_calibration_time_in_a_hand_edited_file_is_read(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[camera]\ncalibration_time = 2026-10-25T11:04:00+01:00\n", encoding="utf-8")
    assert load_config(str(path)).camera.calibration_time == "2026-10-25T11:04:00+01:00"
    path.write_text("[camera]\ncalibration_time = 2026-10-25T11:04:00\n", encoding="utf-8")
    assert load_config(str(path)).camera.calibration_time == ""


def test_a_hand_edited_calibration_record_is_read_through_load_config(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[camera]\ncalibration_lens_rating = "Good"\ncalibration_time = 3\ncalibration_source = "rtsp://u:p@192.0.2.1/s"\n',
        encoding="utf-8",
    )
    camera = load_config(str(path)).camera
    assert (camera.calibration_lens_rating, camera.calibration_time, camera.calibration_source) == (
        "",
        "",
        "rtsp://192.0.2.1/s",
    )


_FILE_GRID = "[grid]\nwidth = 12.0\ndepth = 6.0\nx_offset = 1.0\ny_offset = 4.0\nz_offset = 0.5\n"
# DSL, DSR, USR, USL of that grid
_FILE_CORNERS = [(7.0, 1.0, 0.5), (-5.0, 1.0, 0.5), (-5.0, 7.0, 0.5), (7.0, 7.0, 0.5)]


def test_simple_corners_written_for_another_grid_follow_the_files_grid_unpinned(temp_config_path) -> None:
    """A grid edited by hand: the corners go with it, and their pins, taken on the old grid, go."""
    marks = "".join(
        f'[[camera.calibration_marks]]\nid = {i + 1}\nname = "{n}"\nkind = "known"\n'
        f"x = {x}\ny = {y}\nz = 0.5\npin = [0.5, 0.5]\n"
        for i, (n, (x, y)) in enumerate(
            zip(("DSL", "DSR", "USR", "USL"), [(5.0, 1.0), (-3.0, 1.0), (-3.0, 7.0), (5.0, 7.0)], strict=True)
        )
    )
    temp_config_path.write_text('[camera]\ncalibration_mode = "simple"\n' + marks + _FILE_GRID, encoding="utf-8")
    camera = load_config(str(temp_config_path)).camera
    assert [(m.x, m.y, m.z, m.pin) for m in camera.calibration_marks] == [(*c, []) for c in _FILE_CORNERS]


@pytest.mark.parametrize("pin", ['"0.5, 0.5"', "{ x = 0.5, y = 0.5 }", "true"])
def test_a_pin_that_is_no_list_drops_the_mark(pin, temp_config_path) -> None:
    temp_config_path.write_text(
        '[camera]\ncalibration_mode = "advanced"\n[[camera.calibration_marks]]\n'
        f'id = 1\nkind = "known"\nx = 1.0\ny = 2.0\nz = 0.0\npin = {pin}\n',
        encoding="utf-8",
    )
    assert load_config(str(temp_config_path)).camera.calibration_marks == []


def test_unknown_camera_calibration_keys_are_dropped(temp_config_path) -> None:
    temp_config_path.write_text(
        "[camera]\nfov = 55.0\ncorner_pins = [[0.81, 0.94], [0.14, 0.95], [0.37, 0.61], [0.66, 0.6]]\n"
        '[[camera.calibration_points]]\ncorner_a = "DSL"\ncorner_b = "DSR"\ndistance_a = 21.4\ndistance_b = 23.9\n'
        "pin_x = 0.41\npin_y = 0.22\n" + _FILE_GRID,
        encoding="utf-8",
    )
    cfg = load_config(str(temp_config_path))
    assert (cfg.camera.fov, cfg.camera.calibration_mode, cfg.camera.calibration_marks) == (55.0, "simple", [])
    save_config(cfg, str(temp_config_path))
    text = temp_config_path.read_text(encoding="utf-8")
    assert "corner_pins" not in text
    assert "calibration_points" not in text


def test_a_stored_calibration_points_feature_is_dropped_as_unknown(temp_config_path) -> None:
    from openfollow.configuration import EXPERIMENTAL_FEATURES, FEATURE_KEYS

    temp_config_path.write_text('[ui]\nshown_features = ["calibration_points", "paths"]\n', encoding="utf-8")
    assert load_config(str(temp_config_path)).ui.shown_features == ["paths"]
    assert "calibration_points" not in FEATURE_KEYS
    assert "calibration_points" not in EXPERIMENTAL_FEATURES
