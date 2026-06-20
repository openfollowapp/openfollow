#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# OpenFollow Wayland kiosk session launcher – runs as the openfollow user.
#
# Resolves XDG_RUNTIME_DIR from the *current* uid rather than a systemd %U
# specifier: in a system unit %U expands to the service-manager uid (0), NOT the
# User= uid, so /run/user/%U would point at the nonexistent /run/user/0 and the
# session would never find its Wayland runtime dir. id -u (this process already
# runs as openfollow) gives the real, install-time-assigned uid.
#
# enable-linger (postinst) makes /run/user/<uid> exist at boot without a login;
# wait for it before starting Cage. This wait lives in ExecStart (not a
# start-pre) so it is not bounded by TimeoutStartSec.
set -u
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
until [ -d "$XDG_RUNTIME_DIR" ]; do sleep 0.5; done
# kanshi runs *inside* the Cage session (it needs the Wayland display) to apply
# the multi-output layout (mirror all HDMI outputs). -c points at the
# package-managed config (the openfollow user has no ~/.config/kanshi/config,
# unlike the Ansible deploy).
#
# Run the app in the FOREGROUND (do NOT `exec` it) and kill kanshi once it
# returns: the web-UI restart calls sys.exit, relying on Cage exiting so systemd
# (Restart=always) respawns a fresh session. A backgrounded kanshi keeps the
# Cage session non-empty, so with the old `exec python` Cage stayed alive after
# the app died (zombie app, black screen) and systemd never respawned. Killing
# kanshi empties the session so Cage exits cleanly.
exec /usr/bin/cage -- /bin/sh -c \
  'kanshi -c /usr/share/openfollow/kanshi.config & /opt/openfollow/venv/bin/python -m openfollow.main; rc=$?; kill "$!" 2>/dev/null; exit $rc'
