# Plan: start the app sooner once the network is online

After `network-online.target` the `.deb`'s `openfollow.service` waits a fixed 5 s before the session starts:
`sleep 3` before it stops the boot splash, and `sleep 2` for USB devices to finish enumerating. Both run on every
start of the unit, restarts included. `nm-wait-online.sh` polls about once a second and counts its grace and cap in
polls. This plan removes the fixed waits and polls the network faster.

## Decisions

- **Splash:** the splash is stopped once it has been up 1.5 s, counted from the start of `openfollow-splash.service`
  (its `ActiveEnterTimestampMonotonic` against the system's monotonic clock). On a boot the network takes longer
  than that, so the step waits 0 s. A restart of the unit starts the splash again (the unit wants it), so a restart
  shows it for 1.5 s instead of today's 3 s plus 2 s of black. Whether the logo can also fill the black gap while
  Cage and the app start, and the 1.5 s go, is decided on the station measurements.
- **USB:** `sleep 2` goes. The unit is already ordered after `systemd-udev-settle.service`, gamepads are picked up
  on hotplug and by the retry while none is open, and 3D mice are enumerated again every 1.5 s. If a gamepad or a 3D
  mouse present at boot does not reach its saved controller slot without a replug, a bounded wait comes back.
- **Network wait:** `nm-wait-online.sh` checks every 0.25 s (`nm-online --timeout=0`). The grace stays 3 s and the
  cap 20 s, both measured in seconds on a monotonic clock instead of counted in polls.
- **Kept as is:** the 3 s grace that lets a DHCP lease beat the link-local fallback, and waiting for every device
  NetworkManager is still activating.
- No What's new entry: nothing here asks anything of the operator.

## Steps

- [x] `packaging/debian/stop-splash.sh`: wait out the rest of the splash's 1.5 s, then stop it. Tested with a fake
      `systemctl` and a fake clock: a splash up long ago, one just started, one not running, unreadable output.
- [x] `openfollow.service`: both sleeps replaced by the one root step; a test pins that the unit sleeps nowhere.
- [x] `nm-wait-online.sh`: 0.25 s polls, grace and cap in seconds. `tests/test_dhcp_fallback_provisioning.py`
      covers a lease within the grace, the fallback accepted after 3 s, the 20 s cap, and a device that drops out
      of a poll starting its grace over.
- [x] `build-deb.sh` installs the new script; `docs/PACKAGING.md` describes the start steps and the polling.
- [x] Before, from a `.deb` of main on pi66: critical chain, `systemctl is-system-running`, time to
      `Network ready for startup`, with and without DHCP (+15.5 s and +18.1 s from the start of the network wait).
- [ ] After, from a `.deb` of the branch on pi66: the same measurements; the splash on a cold boot; a restart
      showing the splash 1.5 s; screenshots every 0.25 s of how long the screen stays black; the 8BitDo pad and the
      SpaceNavigator in their slots without a replug; `nm-online --timeout=0` while the network is not ready yet.
- [ ] Mutation check of the new tests, three review rounds, `make ci-remote`, draft PR.
