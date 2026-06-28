#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Build the offline OpenFollow Debian package.
#
# Bundles the runtime AND all Python deps into a private virtualenv shipped
# inside the .deb (/opt/openfollow/venv), so install/run needs NO internet.
# Build time is online (resolves PyPI wheels + the pypsn git dep, compiles
# sdists). Run NATIVELY on the target arch/OS (a Raspberry Pi runner): the venv
# embeds this host's Python ABI, and the python3/arch/libasound control-file
# pins are derived from THIS host, so the package only runs on a matching Pi OS.
#
# Usage:  packaging/build-deb.sh [output-dir]
#   OF_DEB_VERSION=1.2.3   override the version (e.g. from a release tag)
#
# Build prerequisites (run scripts/install-system-deps.sh first, plus the -dev set):
#   python3-venv python3-dev build-essential pkg-config dpkg-dev
#   libgirepository-2.0-dev libcairo2-dev libasound2-dev   (compile PyGObject/rtmidi)
#   librsvg2-bin                                            (render the splash)
set -euo pipefail
umask 022  # 755 dirs / 644 files in the staged tree (clean package perms)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
DEBIAN_DIR="$HERE/debian"
OUT_DIR="${1:-$REPO_ROOT/dist}"
INSTALL_PREFIX="/opt/openfollow"

log() { printf '[build-deb] %s\n' "$*"; }
die() { printf '[build-deb] ERROR: %s\n' "$*" >&2; exit 1; }

# --- version: pyproject [project] version, PEP 440 -> Debian (rc -> ~rc) -------
raw_version="${OF_DEB_VERSION:-}"
raw_version="${raw_version#v}"  # strip leading 'v' from git tags (e.g. v0.2.4-rc9-citest -> 0.2.4-rc9-citest)
if [ -z "$raw_version" ]; then
  raw_version="$(sed -n 's/^version *= *"\(.*\)"/\1/p' "$REPO_ROOT/pyproject.toml" | head -1)"
fi
[ -n "$raw_version" ] || die "could not determine version"
# A pre-release tilde sorts BEFORE the final release in dpkg version ordering,
# so 0.2.4~rc9 < 0.2.4. The optional leading '-' is consumed so that git tags
# like v0.2.4-rc9-citest produce 0.2.4~rc9-citest, not 0.2.4-~rc9-citest. A
# .post release is the opposite – it must sort AFTER the base version – so it
# keeps a dotted (non-tilde) form instead: 0.2.4.post1 > 0.2.4.
deb_version="$(printf '%s' "$raw_version" | sed -E 's/-?(a|b|rc|\.?dev)([0-9]+)/~\1\2/g; s/~\./~/g; s/-?\.?(post[0-9]+)/.\1/g')"

# PEP 440 form, injected into pyproject.toml below so the installed package's
# importlib.metadata version (openfollow.__version__) matches the release/tag.
# Drop the hyphen before a pre-release marker (0.2.4-rc12 -> 0.2.4rc12); a
# remaining trailing segment (e.g. -citest) becomes a local version (+citest).
# If alnum text is glued directly after the numeric marker (0.2.4-rc12f1),
# split it out as -f1 so the local-version step below can produce +f1.
pep440_version="$(printf '%s' "$raw_version" | sed -E 's/-(a|b|c|rc|alpha|beta|pre|post|dev)([0-9]+)([[:alpha:]][[:alnum:]]*)/\1\2-\3/g; s/-(a|b|c|rc|alpha|beta|pre|post|dev)([0-9]+)/\1\2/g')"
case "$pep440_version" in
  *-*) pep440_version="$(printf '%s' "$pep440_version" | sed -E 's/-/+/; s/-/./g')" ;;
esac

ARCH="$(dpkg --print-architecture)"
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYNEXT="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor+1}")')"
PYTHON_DEP="python3 (>= ${PYVER}), python3 (<< ${PYNEXT})"
# time_t64 transition renamed libasound2 -> libasound2t64 (trixie / noble). Pin
# to whichever the build host actually provides.
if dpkg-query -W -f='${Status}' libasound2t64 2>/dev/null | grep -q "install ok installed"; then
  ALSA_DEP="libasound2t64"
else
  ALSA_DEP="libasound2"
fi

log "version: $raw_version -> $deb_version  arch: $ARCH  python: $PYVER"

# --- staging tree -------------------------------------------------------------
STAGE="$(mktemp -d)"
PYPROJECT="$REPO_ROOT/pyproject.toml"
PYPROJECT_BAK="$STAGE/pyproject.toml.orig"
# Restore pyproject.toml (we rewrite its version below) before removing the
# staging dir, so a local build never leaves the working tree dirty.
cleanup() {
  [ -f "$PYPROJECT_BAK" ] && cp -f "$PYPROJECT_BAK" "$PYPROJECT"
  rm -rf "$STAGE"
}
trap cleanup EXIT
chmod 755 "$STAGE"  # mktemp makes 0700; the packaged root dir must be 0755
VENV="$STAGE$INSTALL_PREFIX/venv"
SHARE="$STAGE/usr/share/openfollow"
UNIT_DIR="$STAGE/usr/lib/systemd/system"
DOC_DIR="$STAGE/usr/share/doc/openfollow"
mkdir -p "$STAGE$INSTALL_PREFIX" "$SHARE" "$UNIT_DIR" "$DOC_DIR" \
         "$STAGE/var/lib/openfollow" "$STAGE/DEBIAN"

# --- venv: all Python deps + the openfollow package, built AT the final path --
# Building at $STAGE/opt/openfollow/venv keeps sys.prefix valid after unpack to
# /opt/openfollow/venv (python derives prefix from the binary location).
#
# Created WITHOUT --system-site-packages so pip bundles the FULL dependency
# closure: --system-site-packages would let pip skip any dep already on the build
# host's system site-packages (e.g. python3-packaging), leaving it absent from
# the venv -> ModuleNotFoundError on a slim target. The OS GI stack is exposed
# instead by flipping the pyvenv.cfg flag AFTER install, so only the GI bindings
# come from the OS, never the pure-Python deps.
log "creating virtualenv ..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel >/dev/null

# Stamp the release version into pyproject.toml so the built wheel's metadata
# (and thus openfollow.__version__) reports the release/tag version. Backed up
# and restored by the EXIT trap.
log "stamping package version: $pep440_version"
cp -f "$PYPROJECT" "$PYPROJECT_BAK"
sed -i -E "0,/^version = \".*\"/s//version = \"$pep440_version\"/" "$PYPROJECT"

log "installing openfollow + dependencies (base, no detection extra) ..."
# PEP 517 build via poetry-core; pulls runtime deps from pyproject. Compiles
# PyGObject / python-rtmidi from sdist when no wheel matches this Python.
"$VENV/bin/pip" install "$REPO_ROOT"

# PyGObject / pycairo MUST come from the OS (python3-gi, python3-gi-cairo) so
# they match the distro's gobject-introspection typelibs + libgirepository.
# pip resolves PyGObject 3.56 (the girepository-2.0 ABI), which Debian Trixie
# (python3-gi 3.50 on libgirepository-1.0-1) cannot load:
#   ImportError: libgirepository-2.0.so.0: cannot open shared object file
# the system-site-packages flip below exposes the OS bindings; drop the pip-built
# copies here so the OS ones win on sys.path.
log "removing pip-built GI bindings (use the OS python3-gi / python3-gi-cairo) ..."
"$VENV/bin/pip" uninstall -y PyGObject pycairo >/dev/null 2>&1 || true

# Now expose the OS GObject-Introspection stack (python3-gi, python3-gi-cairo,
# the GStreamer typelibs) at RUNTIME by enabling system site-packages on the
# already-populated venv. Done here, not at venv creation, so it does not affect
# pip's resolution: every pure-Python dep is bundled in the venv above; only the
# GI bindings fall through to the OS. The bundled venv site-packages still take
# precedence on sys.path, so bundled deps win over any same-named system package.
log "enabling system site-packages for the OS GI stack (runtime only) ..."
sed -i 's/^include-system-site-packages = false/include-system-site-packages = true/' "$VENV/pyvenv.cfg"
grep -q '^include-system-site-packages = true' "$VENV/pyvenv.cfg" \
  || die "failed to enable include-system-site-packages in pyvenv.cfg"

# Rewrite pip/console-script shebangs from the staging path to the final install
# path. The venv is built at $STAGE/opt/openfollow/venv; pip bakes that full
# staging prefix into every script shebang. After dpkg unpacks to /opt/openfollow
# the shebangs are broken and `pip` / entry-point scripts fail with "not found".
log "fixing venv script shebangs (staging path -> /opt/openfollow/venv) ..."
find "$VENV/bin" -maxdepth 1 -type f | while read -r script; do
  head -c 2 "$script" | grep -q '#!' || continue
  sed -i "1s|#!$STAGE/opt/openfollow/venv|#!/opt/openfollow/venv|" "$script"
done

# Sanity: the package and its heavy deps must import from the bundled venv, and
# GStreamer must load via the OS PyGObject (the exact path that broke before).
"$VENV/bin/python" - <<'PY'
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # OS PyGObject via system site-packages
Gst.init(None)
import openfollow, numpy, pygame, bottle, mido  # noqa: F401,E401
import packaging
from packaging.version import Version  # noqa: F401

# Pure-Python deps MUST be bundled in the venv, not resolved from the build
# host's system site-packages (which a slim target image won't have). `packaging`
# regressed exactly this way once: --system-site-packages let pip skip it because
# the runner had python3-packaging. Assert it loads from inside the venv prefix.
import os, sys
prefix = os.path.realpath(sys.prefix)
mod = os.path.realpath(packaging.__file__)
assert mod.startswith(prefix + os.sep), f"packaging not bundled in venv: {mod}"
print("[build-deb] venv import smoke OK:", openfollow.__version__, "| Gst", Gst.version_string())
PY

# --- splash png (pre-rendered so install needs no rsvg / DRM) -----------------
log "rendering boot splash ..."
"$DEBIAN_DIR/render-splash.sh" "$REPO_ROOT/openfollow/web/static/openfollow.svg" \
  "$SHARE/splash.png" 1920 1080

# --- sudoers drop-in (privilege broker) ---------------------------------------
# The app's privilege broker runs specific commands via `sudo -n` (network
# Apply via nmcli/dhcpcd, service restart, journal reads, device repair). The
# openfollow user has no password and no sudo by default, so without this the
# broker probes all resolve UNAVAILABLE and those web-UI features silently fail.
# The Ansible deploy self-installs this at runtime; the .deb ships it directly.
# Generated from openfollow.privilege.capabilities so it can't drift from the
# broker, then syntax-checked with visudo before it can break system-wide sudo.
log "generating sudoers drop-in from the capability registry ..."
SUDOERS_DIR="$STAGE/etc/sudoers.d"
mkdir -p "$SUDOERS_DIR"
"$VENV/bin/python" -c \
  "from openfollow.privilege.drop_in import render_drop_in; print(render_drop_in('openfollow'), end='')" \
  > "$SUDOERS_DIR/openfollow-privileged"
chmod 0440 "$SUDOERS_DIR/openfollow-privileged"
# visudo lives in /usr/sbin, off a non-root PATH; resolve it explicitly so a
# non-root build (operator building locally) validates like the root CI build.
visudo_bin="$(command -v visudo || true)"
if [ -z "$visudo_bin" ]; then
  for d in /usr/sbin /sbin; do
    if [ -x "$d/visudo" ]; then visudo_bin="$d/visudo"; break; fi
  done
fi
[ -n "$visudo_bin" ] || die "visudo not found (install the 'sudo' package)."
"$visudo_bin" -cf "$SUDOERS_DIR/openfollow-privileged" >/dev/null || die "rendered sudoers drop-in failed visudo validation"

# --- static payload -----------------------------------------------------------
install -m 0644 "$REPO_ROOT/openfollow/web/static/openfollow.svg" "$SHARE/openfollow.svg"
# udev rule granting the service user (plugdev) access to a 3D Mouse hidraw
# node. The postinst reloads + triggers udev so it applies without a replug.
install -d -m 0755 "$STAGE/lib/udev/rules.d"
install -m 0644 "$REPO_ROOT/packaging/udev/99-openfollow-3dmouse.rules" \
  "$STAGE/lib/udev/rules.d/99-openfollow-3dmouse.rules"
install -m 0755 "$DEBIAN_DIR/splash.sh"                           "$SHARE/splash.sh"
install -m 0755 "$DEBIAN_DIR/session.sh"                          "$SHARE/session.sh"
install -m 0755 "$DEBIAN_DIR/apply-update.sh"                     "$SHARE/apply-update.sh"
# NDI is not bundled (closed-source SDK); this helper builds + installs it
# post-install. See https://openfollow.app/docs/ndi-install.html.
install -m 0755 "$REPO_ROOT/scripts/install-ndi.sh"               "$SHARE/install-ndi.sh"
# Detection deps are not bundled (torch is large + ultralytics is AGPL-3.0); this
# helper installs them into the app venv on demand. See
# https://openfollow.app/docs/detection-install.html.
install -m 0755 "$REPO_ROOT/scripts/install-detection.sh"         "$SHARE/install-detection.sh"
# Model export script: exported ONNX models land in the detection storage
# directory (live on built images after detection install, or from source).
install -d -m 0755 "$SHARE/scripts"
install -m 0755 "$REPO_ROOT/scripts/export_onnx.py"               "$SHARE/scripts/export_onnx.py"
install -m 0644 "$DEBIAN_DIR/kanshi.config"                       "$SHARE/kanshi.config"
install -m 0644 "$REPO_ROOT/config.example.toml"                 "$SHARE/config.example.toml"
# First-boot seed: bootstrap_config_if_missing() looks for the example next to
# config.toml – i.e. the service's WorkingDirectory (/var/lib/openfollow) – so a
# fresh device copies the curated example instead of falling back to bare
# dataclass defaults. The $SHARE copy above is reference-only (docs/PACKAGING.md).
install -m 0644 "$REPO_ROOT/config.example.toml"                 "$STAGE/var/lib/openfollow/config.example.toml"
# Legal docs served offline by the /about pages.
install -m 0644 "$REPO_ROOT/LICENSE"                             "$SHARE/LICENSE"
install -m 0644 "$REPO_ROOT/THIRD_PARTY_NOTICES.md"              "$SHARE/THIRD_PARTY_NOTICES.md"
install -m 0644 "$REPO_ROOT/WRITTEN_OFFER.md"                    "$SHARE/WRITTEN_OFFER.md"
install -m 0644 "$DEBIAN_DIR/openfollow.service"                 "$UNIT_DIR/openfollow.service"
install -m 0644 "$DEBIAN_DIR/openfollow-splash.service"          "$UNIT_DIR/openfollow-splash.service"
# Vendor drop-in bounding NetworkManager-wait-online so a slow/network-less boot
# doesn't hold the kiosk GUI behind the splash for ~60s (see the .conf header).
install -d -m 0755 "$UNIT_DIR/NetworkManager-wait-online.service.d"
install -m 0644 "$DEBIAN_DIR/nm-wait-online-timeout.conf" \
  "$UNIT_DIR/NetworkManager-wait-online.service.d/10-openfollow-timeout.conf"
install -m 0644 "$DEBIAN_DIR/copyright"                          "$DOC_DIR/copyright"

# --- control + maintainer scripts --------------------------------------------
sed -e "s|@VERSION@|$deb_version|g" \
    -e "s|@ARCH@|$ARCH|g" \
    -e "s|@PYTHON_DEP@|$PYTHON_DEP|g" \
    -e "s|@ALSA_DEP@|$ALSA_DEP|g" \
    "$DEBIAN_DIR/control.in" > "$STAGE/DEBIAN/control"
for s in postinst prerm postrm; do
  install -m 0755 "$DEBIAN_DIR/$s" "$STAGE/DEBIAN/$s"
done

# --- build --------------------------------------------------------------------
mkdir -p "$OUT_DIR"
DEB="$OUT_DIR/openfollow_${deb_version}_${ARCH}.deb"
log "packaging $DEB ..."
dpkg-deb --root-owner-group --build "$STAGE" "$DEB" >/dev/null

log "done:"
dpkg-deb -I "$DEB" | sed 's/^/    /'
printf '%s\n' "$DEB"
