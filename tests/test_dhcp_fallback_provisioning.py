# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Provisioning contract for the no-DHCP link-local fallback.

A station on a show LAN with no DHCP server must still self-assign an address,
or it is unreachable from the web UI and from SSH alike. Nothing in the running
app arms this - it is provisioning only, so every install route has to write the
same NetworkManager connection default: the ``.deb`` (which the image installs)
and the Ansible playbook.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import openfollow

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(openfollow.__file__).resolve().parent.parent
_DROPIN_NAME = "10-openfollow-dhcp-fallback.conf"

_SOURCES = {
    "ansible playbook": _REPO_ROOT / "scripts" / "ansible" / "install-raspberry-pi.yml",
    "deb drop-in": _REPO_ROOT / "packaging" / "debian" / "nm-dhcp-fallback.conf",
    "deb build script": _REPO_ROOT / "packaging" / "build-deb.sh",
}

# NMSettingIP4LinkLocal 4 = fallback; 3 ("enabled") would put a 169.254 address
# on healthy interfaces too. 2147483647 is NM's "infinity" for a DHCP timeout.
_NM_INFINITY = "2147483647"
_REQUIRED = (
    f"ipv4.dhcp-timeout={_NM_INFINITY}",
    "ipv4.link-local=4",
)

# NetworkManager accepts only a subset of properties as [connection] defaults.
# One it rejects, logged on every config read as "unknown key".
_REJECTED_BY_NETWORKMANAGER = ("ipv4.may-fail",)

# Routes carrying the property block itself; the build script only installs it.
_BLOCK_SOURCES = ("ansible playbook", "deb drop-in")

# Routes that name the installed file. The deb's drop-in *is* the content, so
# it is the build script that has to reference the destination path.
_INSTALLING_SOURCES = ("ansible playbook", "deb build script")


def _read(name: str) -> str:
    path = _SOURCES[name]
    if not path.is_file():
        pytest.skip(f"no checkout {path.name} (wheel install)")
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(_INSTALLING_SOURCES))
def test_every_install_route_ships_the_dropin(name: str) -> None:
    """A route that skips it leaves that install method dark on a DHCP-less
    LAN while the others are fine - the hardest kind of gap to notice."""
    assert _DROPIN_NAME in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
@pytest.mark.parametrize("prop", _REQUIRED)
def test_routes_agree_on_the_properties(name: str, prop: str) -> None:
    """All three must write identical settings, or the wait before the
    fallback appears would depend on which path provisioned the unit."""
    assert prop in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
def test_fallback_is_not_unconditionally_enabled(name: str) -> None:
    assert "ipv4.link-local=3" not in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
@pytest.mark.parametrize("prop", _REJECTED_BY_NETWORKMANAGER)
def test_no_property_networkmanager_rejects_as_a_connection_default(name: str, prop: str) -> None:
    """A key NM does not accept here makes it log "unknown key" every time it
    reads its config, and leaves a provisioning file whose comments describe a
    setting that never applied - the reader has no way to tell which of the
    remaining ones are real."""
    assert prop not in _read(name)


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
def test_dhcp_timeout_is_not_finite(name: str) -> None:
    """A finite timeout takes the fallback address away again. On expiry
    NetworkManager fails the activation with ``ip-config-unavailable`` and
    removes the link-local along with it, so the interface is dark until the
    next retry succeeds - the outage this drop-in exists to prevent. Measured
    on a DHCP-less NIC: the address appeared at 0.5 s and was gone at 45.6 s."""
    text = _read(name)
    finite = re.findall(r"ipv4\.dhcp-timeout=(\S+)", text)
    assert finite, "route writes no ipv4.dhcp-timeout at all"
    assert all(value == _NM_INFINITY for value in finite), finite


@pytest.mark.parametrize("name", sorted(_BLOCK_SOURCES))
def test_dhcp_timeout_does_not_spell_infinity_as_a_word(name: str) -> None:
    """``nmcli`` takes ``infinity`` on a profile and stores it as the integer,
    but as a ``[connection]`` default NetworkManager silently ignores the word
    and keeps its built-in 45 s. That reinstates the teardown with nothing in
    the file to show for it, so the sentinel has to stay numeric."""
    assert "dhcp-timeout=infinity" not in _read(name)


def test_ansible_reloads_networkmanager_after_writing_it() -> None:
    """NetworkManager parses conf.d only at startup, so a drop-in written to an
    already-running Pi is inert until it re-reads. Without the notify the
    operator believes the fallback is armed and it is not."""
    text = _read("ansible playbook")
    assert "reload networkmanager" in text
    assert "nmcli general reload" in text


_POSTINST = _REPO_ROOT / "packaging" / "debian" / "postinst"
# Every external command postinst runs, stubbed so the test touches no real user,
# unit or directory. ``systemctl`` exits 0, so the script sees a running system.
_POSTINST_COMMANDS = ("getent", "adduser", "udevadm", "install", "chown", "loginctl", "systemctl")


def _run_postinst(tmp_path: Path, *, nmcli_exit: int | None) -> tuple[list[str], str]:
    """Run ``postinst configure`` with only the stubs on PATH; ``None`` leaves nmcli out."""
    if not _POSTINST.is_file():
        pytest.skip("no packaging/debian/postinst in this tree")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    stubs = dict.fromkeys(_POSTINST_COMMANDS, 0)
    if nmcli_exit is not None:
        stubs["nmcli"] = nmcli_exit
    for name, code in stubs.items():
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\nexit {code}\n', encoding="utf-8")
        stub.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(_POSTINST), "configure"],
        env={"PATH": str(bin_dir)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return calls.read_text(encoding="utf-8").splitlines(), result.stderr


def test_deb_reloads_networkmanager_on_a_running_system(tmp_path: Path) -> None:
    """The .deb twin of the Ansible notify: an install or upgrade on a running
    station must arm the fallback without waiting for the next reboot."""
    calls, _ = _run_postinst(tmp_path, nmcli_exit=0)
    assert "nmcli general reload conf" in calls


def test_deb_install_survives_networkmanager_not_running(tmp_path: Path) -> None:
    calls, _ = _run_postinst(tmp_path, nmcli_exit=1)
    assert "systemctl restart openfollow.service" in calls


def test_deb_install_survives_a_host_without_networkmanager(tmp_path: Path) -> None:
    """A dhcpcd host has no nmcli; the install must neither fail nor print a
    'not found' into the operator's apt output."""
    calls, stderr = _run_postinst(tmp_path, nmcli_exit=None)
    assert "systemctl restart openfollow.service" in calls
    assert "nmcli" not in stderr


def test_deb_declares_the_dropin_as_a_conffile() -> None:
    """It is the one file this package ships under /etc. An operator who tuned
    the DHCP timeout must keep that value across an upgrade."""
    text = _read("deb build script")
    assert "DEBIAN/conffiles" in text
    assert f"/etc/NetworkManager/conf.d/{_DROPIN_NAME}" in text


def test_nothing_arms_the_fallback_at_runtime() -> None:
    """The app must never rewrite a NIC's persistent NetworkManager profile:
    an operator's network config is not ours to change while a show is running.
    The fallback is provisioning, and only provisioning."""
    for module in ("network/nm_adapter.py", "network/adapter.py", "network/dhcpcd_adapter.py", "services.py"):
        text = (_REPO_ROOT / "openfollow" / module).read_text(encoding="utf-8")
        assert "ensure_dhcp_fallback" not in text
        assert "ipv4.link-local" not in text
        assert "ipv4.dhcp-timeout" not in text


def test_every_install_route_ships_an_mdns_responder() -> None:
    """The HUD advertises <hostname>.local as the recovery route that survives
    an address change. A station without a responder answers nothing there, so
    the advice sends the operator to a dead name - worse than showing none."""
    assert "avahi-daemon" in (_REPO_ROOT / "packaging" / "debian" / "control.in").read_text(encoding="utf-8")
    assert "avahi-daemon" in _read("ansible playbook")
    image = _REPO_ROOT / "packaging" / "image" / "layer" / "openfollow.yaml"
    assert "avahi-daemon" in image.read_text(encoding="utf-8")


def test_help_does_not_tie_the_fallback_to_the_dhcp_timeout() -> None:
    """``ipv4.link-local=4`` is not gated on ``ipv4.dhcp-timeout``: the 169.254
    address lands within seconds of the first failed lease attempt, not after
    the retry window. Quoting the timeout as the wait sends an operator away
    from a station that is already reachable."""
    help_text = (_REPO_ROOT / "openfollow" / "web" / "help" / "general-network-interface.md").read_text(
        encoding="utf-8"
    )
    assert "within a few seconds" in help_text
    assert "45 seconds" not in help_text


def test_help_does_not_promise_the_station_name_resolves() -> None:
    """The name that answers mDNS is the machine's hostname, which is only
    usually the station name - the HUD shows the real one for that reason, and
    the help must not contradict it."""
    help_text = (_REPO_ROOT / "openfollow" / "web" / "help" / "general-network-interface.md").read_text(
        encoding="utf-8"
    )
    assert "<station-name>.local" not in help_text


# --- network-online.target on a station whose DHCP may go unanswered ----------

_WAIT_SCRIPT = _REPO_ROOT / "packaging" / "debian" / "nm-wait-online.sh"
_WAIT_DROPIN = _REPO_ROOT / "packaging" / "debian" / "nm-wait-online-timeout.conf"
_WAIT_INSTALLED = "/usr/share/openfollow/nm-wait-online.sh"
_IP_CONFIG = "connecting (getting IP configuration)"
_FALLBACK = "169.254.7.9/16"
_LEASE = "192.0.2.10/24"
# Polls (a second each) a device must hold only its fallback before it counts.
_GRACE = 3

# One poll's view: is NetworkManager's startup complete, and each device's
# (type, state, IPv4 addresses). ``None`` for devices: NetworkManager not running.
_Poll = tuple[bool, dict[str, tuple[str, str, list[str]]] | None]


def _run_wait(tmp_path: Path, polls: list[_Poll], cap: int = 10, shell: str = "/bin/sh") -> tuple[int, int]:
    """Run the packaged wait with ``nm-online``, ``nmcli`` and ``ip`` stubbed.

    Poll *n* sees ``polls[n]``, the last one repeating. Returns the exit code
    and how many times it polled.
    """
    if not _WAIT_SCRIPT.is_file():
        pytest.skip("no packaging/debian/nm-wait-online.sh in this tree")
    for n, (started, devices) in enumerate(polls):
        poll = tmp_path / f"p{n}"
        poll.mkdir()
        (poll / "startup").write_text("0" if started else "1", encoding="utf-8")
        if devices is None:
            continue
        rows = [f"{name}:{kind}:{state}" for name, (kind, state, _) in devices.items()]
        (poll / "devices").write_text("".join(f"{row}\n" for row in rows), encoding="utf-8")
        for name, (_, _, addresses) in devices.items():
            lines = [f"2: {name}    inet {a} scope global {name}\\       valid_lft forever" for a in addresses]
            (poll / f"addr_{name}").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = f'cur="$(cat "{tmp_path}/cur")"; dir="{tmp_path}/p$cur"'
    stubs = {
        # Answers only the startup question (-s); any other use is an error.
        "nm-online": (
            'case " $* " in *" -s "*) ;; *) exit 2 ;; esac\n'
            f'n="$(cat "{tmp_path}/count" 2>/dev/null || echo 0)"\n'
            f'echo "$((n + 1))" > "{tmp_path}/count"\n'
            f'[ "$n" -lt {len(polls)} ] && cur="$n" || cur={len(polls) - 1}\n'
            f'echo "$cur" > "{tmp_path}/cur"\n'
            f'exit "$(cat "{tmp_path}/p$cur/startup")"\n'
        ),
        "nmcli": f'{state}\n[ -f "$dir/devices" ] || exit 8\ncat "$dir/devices"\n',
        # ip -4 -o addr show dev <name>
        "ip": f'{state}\neval "dev=\\${{$#}}"\ncat "$dir/addr_$dev" 2>/dev/null\nexit 0\n',
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        stub.chmod(0o755)
    result = subprocess.run(
        [shell, str(_WAIT_SCRIPT), str(cap)],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return result.returncode, int((tmp_path / "count").read_text(encoding="utf-8"))


def test_wait_online_dropin_runs_the_station_wait() -> None:
    """``nm-online -s`` alone waits for NetworkManager's startup to complete,
    which a DHCP activation with an infinite timeout never reaches: every boot
    on a LAN without DHCP would run out the cap and leave the system degraded."""
    if not _WAIT_DROPIN.is_file():
        pytest.skip("no packaging/debian/nm-wait-online-timeout.conf in this tree")
    exec_lines = re.findall(r"^ExecStart=(.*)$", _WAIT_DROPIN.read_text(encoding="utf-8"), re.MULTILINE)
    assert exec_lines == ["", f"{_WAIT_INSTALLED} 20"]


def test_deb_installs_the_station_wait_executable() -> None:
    text = _read("deb build script")
    assert re.search(rf'install -m 0755 "\$DEBIAN_DIR/nm-wait-online\.sh"\s+"\$SHARE/{_WAIT_SCRIPT.name}"', text)


@pytest.fixture(params=["sh", "dash"])
def shell(request: pytest.FixtureRequest) -> str:
    """The station's ``/bin/sh`` is dash; run under the host's ``sh`` too."""
    path = "/bin/sh" if request.param == "sh" else shutil.which("dash")
    if path is None:
        pytest.skip("no dash on this host")
    return path


def _eth0(state: str, *addresses: str) -> dict[str, tuple[str, str, list[str]]]:
    return {"eth0": ("ethernet", state, list(addresses))}


_ON_FALLBACK = (False, _eth0(_IP_CONFIG, _FALLBACK))
_ADDRESSLESS = (False, _eth0(_IP_CONFIG))


@pytest.mark.parametrize(
    ("polls", "expected_polls"),
    [
        pytest.param([(True, _eth0("connected", _LEASE))], 1, id="startup-complete"),
        pytest.param([(False, _eth0(_IP_CONFIG, _LEASE))], 1, id="lease-while-activating-counts-at-once"),
        pytest.param([(False, _eth0(_IP_CONFIG, _FALLBACK, _LEASE))], 1, id="lease-beside-the-fallback-counts-at-once"),
        pytest.param([_ON_FALLBACK], 1 + _GRACE, id="only-nic-holds-its-fallback-for-the-grace"),
        pytest.param(
            [
                (
                    False,
                    {
                        "eth0": ("ethernet", "connected", [_LEASE]),
                        "eth0.20": ("vlan", _IP_CONFIG, [_FALLBACK]),
                    },
                )
            ],
            1 + _GRACE,
            id="lease-on-one-fallback-on-the-vlan",
        ),
        pytest.param(
            [
                (
                    False,
                    {
                        "lo": ("loopback", "connecting (externally)", []),
                        "eth0": ("ethernet", _IP_CONFIG, [_FALLBACK]),
                    },
                )
            ],
            1 + _GRACE,
            id="loopback-is-not-waited-for",
        ),
        pytest.param(
            [(False, _eth0("unavailable")), _ADDRESSLESS, _ON_FALLBACK],
            3 + _GRACE,
            id="fallback-arrives-on-the-third-poll",
        ),
        # Fallback first seen on poll 1; the lease ends the wait before the grace would.
        pytest.param(
            [_ON_FALLBACK, _ON_FALLBACK, (True, _eth0("connected", _LEASE))],
            3,
            id="lease-within-the-grace-completes-startup",
        ),
        pytest.param(
            [_ON_FALLBACK, _ON_FALLBACK, (False, _eth0(_IP_CONFIG, _LEASE))],
            3,
            id="lease-within-the-grace-while-activating",
        ),
        # Held on polls 1-2, gone on 3, back on 4: the grace runs again from poll 4.
        pytest.param(
            [_ON_FALLBACK, _ON_FALLBACK, _ADDRESSLESS, _ON_FALLBACK], 4 + _GRACE, id="fallback-lost-restarts-the-grace"
        ),
        pytest.param(
            [_ON_FALLBACK, _ON_FALLBACK, (False, _eth0("unavailable")), _ON_FALLBACK],
            4 + _GRACE,
            id="carrier-lost-restarts-the-grace",
        ),
        # eth0 has held its fallback since poll 1 when eth1's lease lands on poll 4.
        pytest.param(
            [(False, {**_eth0(_IP_CONFIG, _FALLBACK), "eth1": ("ethernet", _IP_CONFIG, [])})] * 3
            + [(False, {**_eth0(_IP_CONFIG, _FALLBACK), "eth1": ("ethernet", _IP_CONFIG, [_LEASE])})],
            4,
            id="grace-counts-per-device",
        ),
        # eth1's fallback lands on poll 3, so its grace ends three polls after eth0's would.
        pytest.param(
            [(False, {**_eth0(_IP_CONFIG, _FALLBACK), "eth1": ("ethernet", _IP_CONFIG, [])})] * 2
            + [(False, {**_eth0(_IP_CONFIG, _FALLBACK), "eth1": ("ethernet", _IP_CONFIG, [_FALLBACK])})],
            3 + _GRACE,
            id="every-fallback-serves-its-own-grace",
        ),
    ],
)
def test_wait_online_succeeds_once_every_activating_device_has_an_address(
    tmp_path: Path, shell: str, polls: list[_Poll], expected_polls: int
) -> None:
    """A DHCP profile with no server stays activating beside its link-local
    fallback; the station is reachable there, so network-online is reached. A
    lease counts at once; the fallback only once held for the grace, so a lease
    that arrives just after it is the address the station starts on."""
    assert _run_wait(tmp_path, polls, shell=shell) == (0, expected_polls)


@pytest.mark.parametrize(
    "devices",
    [
        pytest.param({"eth0": ("ethernet", _IP_CONFIG, [])}, id="activating-with-no-address-yet"),
        pytest.param(
            {
                "eth0": ("ethernet", _IP_CONFIG, [_FALLBACK]),
                "eth1": ("ethernet", "connecting (configuring)", []),
            },
            id="one-addressed-one-not",
        ),
        # Before carrier, nothing is activating yet: passing here would race the lease.
        pytest.param({"eth0": ("ethernet", "unavailable", [])}, id="nothing-activating-yet"),
        pytest.param(
            {
                "lo": ("loopback", "connecting (externally)", ["127.0.0.1/8"]),
                "eth0": ("ethernet", "unavailable", []),
            },
            id="only-loopback-activating",
        ),
        pytest.param({"eth0": ("ethernet", _IP_CONFIG, ["127.0.0.2/8"])}, id="only-a-loopback-address"),
        # The cap ends before the fallback has been held for the grace.
        pytest.param({"eth0": ("ethernet", _IP_CONFIG, [_FALLBACK])}, id="fallback-held-short-of-the-grace"),
        pytest.param(None, id="networkmanager-not-running"),
    ],
)
def test_wait_online_fails_at_the_cap_while_a_device_has_no_address(
    tmp_path: Path, shell: str, devices: dict[str, tuple[str, str, list[str]]] | None
) -> None:
    assert _run_wait(tmp_path, [(False, devices)], cap=3, shell=shell) == (1, 3)
