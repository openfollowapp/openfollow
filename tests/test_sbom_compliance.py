"""Tests for ``packaging/image/check_sbom_compliance.py``."""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

pytestmark = pytest.mark.unit

_MOD_PATH = pathlib.Path(__file__).resolve().parents[1] / "packaging" / "image" / "check_sbom_compliance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_sbom_compliance", _MOD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _pkg(name: str, *, kind: str = "deb", concluded: str = "NOASSERTION", declared: str = "NOASSERTION") -> dict:
    """An SPDX package. ``kind`` is 'deb' or 'pypi' (drives the purl => venv test)."""
    locator = f"pkg:pypi/{name}@1.0" if kind == "pypi" else f"pkg:deb/debian/{name}@1.0?arch=arm64"
    return {
        "name": name,
        "licenseConcluded": concluded,
        "licenseDeclared": declared,
        "externalRefs": [{"referenceType": "purl", "referenceLocator": locator}],
    }


def _write_sbom(tmp_path: pathlib.Path, packages: list[dict], *, spdx_version: str = "SPDX-2.3") -> str:
    doc = {"spdxVersion": spdx_version, "packages": packages}
    path = tmp_path / "image.spdx.json"
    path.write_text(json.dumps(doc))
    return str(path)


# A realistic-ish clean image: AGPLv3-compatible OS libs, a clean venv, plus the
# two real-world packages that must NOT trip the gate – a transitive
# permissively-licensed libonnxruntime (deb) and the accepted Pi firmware blob.
_CLEAN_PACKAGES = [
    _pkg("python3-gi", declared="LGPL-2.1-or-later"),
    _pkg("gstreamer1.0-plugins-bad", declared="LGPL-2.1-or-later"),
    _pkg("libonnxruntime1.21", declared="Apache-2.0 AND BSD-2-Clause AND MPL-2.0"),
    _pkg("raspi-firmware", declared="GPL-2.0-only AND GPL-2.0-or-later AND LicenseRef-Proprietary-1"),
    _pkg("indicator-application", declared="GPL-3.0-or-later"),
    _pkg("libindicator7", declared="GPL-3.0-or-later"),
    _pkg("numpy", kind="pypi", declared="BSD-3-Clause"),
    _pkg("bottle", kind="pypi", declared="MIT"),
    _pkg("openfollow", kind="pypi", declared="AGPL-3.0-or-later"),
]


def test_clean_sbom_passes(tmp_path: pathlib.Path) -> None:
    path = _write_sbom(tmp_path, _CLEAN_PACKAGES)
    assert mod.find_violations(mod.load_sbom(path)) == []
    assert mod.main([path]) == 0


def test_transitive_dpkg_libonnxruntime_passes(tmp_path: pathlib.Path) -> None:
    # Regression: a permissively-licensed onnxruntime pulled by the OS media
    # stack is a deb (not venv), so the detection-extra check must not fire.
    path = _write_sbom(tmp_path, [_pkg("libonnxruntime1.21", declared="Apache-2.0 AND MPL-2.0")])
    assert mod.find_violations(mod.load_sbom(path)) == []
    assert mod.main([path]) == 0


def test_accepted_proprietary_firmware_passes(tmp_path: pathlib.Path) -> None:
    # Regression: the redistributable Pi firmware blob is proprietary but accepted.
    # There is no license gate – only NDI / detection-extra names trip the build.
    path = _write_sbom(tmp_path, [_pkg("raspi-firmware", declared="GPL-2.0-only AND LicenseRef-Proprietary-1")])
    assert mod.find_violations(mod.load_sbom(path)) == []
    assert mod.main([path]) == 0


@pytest.mark.parametrize("kind", ["deb", "pypi"])
@pytest.mark.parametrize("name", ["gst-plugin-ndi", "gstreamer1.0-ndi", "libndi0", "libndi-dev", "ndi-sdk"])
def test_ndi_name_fails_anywhere(tmp_path: pathlib.Path, kind: str, name: str) -> None:
    path = _write_sbom(tmp_path, [*_CLEAN_PACKAGES, _pkg(name, kind=kind, declared="MIT")])
    violations = mod.find_violations(mod.load_sbom(path))
    assert any(line.startswith("name:") and name in line for line in violations), violations
    assert mod.main([path]) == 1


@pytest.mark.parametrize("name", ["ultralytics", "onnxruntime", "opencv-python", "opencv-python-headless"])
def test_detection_extra_in_venv_fails(tmp_path: pathlib.Path, name: str) -> None:
    # Permissive license – proves the *name* policy fires on venv packages alone.
    path = _write_sbom(tmp_path, [*_CLEAN_PACKAGES, _pkg(name, kind="pypi", declared="MIT")])
    violations = mod.find_violations(mod.load_sbom(path))
    assert any(line.startswith("venv:") and name in line for line in violations), violations
    assert mod.main([path]) == 1


@pytest.mark.parametrize("name", ["onnxruntime", "libopencv-core4.5", "python3-opencv", "onnxruntime-gpu"])
def test_detection_extra_as_dpkg_passes(tmp_path: pathlib.Path, name: str) -> None:
    # Same names as deb (system) packages must not trip – that is the libonnxruntime case.
    path = _write_sbom(tmp_path, [*_CLEAN_PACKAGES, _pkg(name, kind="deb", declared="Apache-2.0")])
    assert mod.find_violations(mod.load_sbom(path)) == []
    assert mod.main([path]) == 0


@pytest.mark.parametrize("kind", ["deb", "pypi"])
@pytest.mark.parametrize("field", ["licenseConcluded", "licenseDeclared"])
@pytest.mark.parametrize(
    "expression",
    [
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "AGPL-3.0",
        "MIT AND AGPL-3.0-only",
        "(AGPL-3.0-or-later OR LGPL-2.1)",
        # Free-text forms Syft emits from raw package metadata, not SPDX ids.
        "GNU Affero General Public License v3",
        "GNU Affero General Public License v3 or later",
    ],
)
def test_agpl_license_passes_anywhere(tmp_path: pathlib.Path, kind: str, field: str, expression: str) -> None:
    # OpenFollow is itself AGPL-3.0-or-later, so AGPL is no longer a forbidden
    # license: a third-party AGPL component (SPDX id or Syft free-text, in either
    # field, deb or venv) is license-compatible and must not trip the gate. NDI
    # and the detection extras are still caught by name.
    pkg = _pkg("some-agpl-pkg", kind=kind)
    pkg[field] = expression
    path = _write_sbom(tmp_path, [*_CLEAN_PACKAGES, pkg])
    assert mod.find_violations(mod.load_sbom(path)) == []
    assert mod.main([path]) == 0


def test_ultralytics_in_venv_fails_by_name(tmp_path: pathlib.Path) -> None:
    # Real shape: ultralytics ships AGPL into the venv. With no license gate it is
    # caught purely by the venv name denylist – which is what keeps it out.
    path = _write_sbom(tmp_path, [_pkg("ultralytics", kind="pypi", declared="AGPL-3.0-or-later")])
    violations = mod.find_violations(mod.load_sbom(path))
    assert any(line.startswith("venv:") for line in violations), violations
    assert not any(line.startswith("license:") for line in violations), violations
    assert mod.main([path]) == 1


@pytest.mark.parametrize(
    "license_id",
    [
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "LGPL-2.1-or-later",
        "GPL-3.0-or-later",
        "LicenseRef-Proprietary-1",  # proprietary -> allowed (no license gate)
        "NOASSERTION",
    ],
)
def test_non_agpl_licenses_pass(tmp_path: pathlib.Path, license_id: str) -> None:
    path = _write_sbom(tmp_path, [_pkg("harmless", declared=license_id)])
    assert mod.find_violations(mod.load_sbom(path)) == []
    assert mod.main([path]) == 0


def test_non_dict_package_entry_is_skipped(tmp_path: pathlib.Path) -> None:
    doc = {"spdxVersion": "SPDX-2.3", "packages": ["not-a-mapping", _pkg("openfollow", kind="pypi")]}
    path = tmp_path / "s.json"
    path.write_text(json.dumps(doc))
    assert mod.find_violations(mod.load_sbom(str(path))) == []


def test_package_without_purl_is_not_treated_as_venv(tmp_path: pathlib.Path) -> None:
    # No purl => can't prove it's a venv package => detection-extra check is skipped.
    doc = {"spdxVersion": "SPDX-2.3", "packages": [{"name": "onnxruntime", "licenseDeclared": "MIT"}]}
    path = tmp_path / "s.json"
    path.write_text(json.dumps(doc))
    assert mod.find_violations(mod.load_sbom(str(path))) == []


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all {{{",
        json.dumps([1, 2, 3]),  # JSON, but not an object
        json.dumps({"spdxVersion": "SPDX-2.3"}),  # object, but no packages list
        json.dumps({"spdxVersion": "SPDX-2.3", "packages": {}}),  # packages not a list
        json.dumps({"spdxVersion": "SPDX-2.3", "packages": []}),  # valid doc, but a zero-package (broken) scan
        json.dumps({"packages": []}),  # not an SPDX document
        json.dumps({"spdxVersion": "CycloneDX-1.5", "packages": []}),  # wrong doc type
    ],
)
def test_malformed_sbom_returns_two(tmp_path: pathlib.Path, payload: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(payload)
    assert mod.main([str(path)]) == 2


def test_missing_file_returns_two(tmp_path: pathlib.Path) -> None:
    assert mod.main([str(tmp_path / "does-not-exist.json")]) == 2


def test_bad_arguments_return_usage_error(tmp_path: pathlib.Path) -> None:
    assert mod.main([]) == 2
    assert mod.main(["a", "b"]) == 2
