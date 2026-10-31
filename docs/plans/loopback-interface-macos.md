# Plan: loopback as a network interface on macOS

On a Mac, a lighting desk or media server often runs on the same machine as
OpenFollow (grandMA3 onPC, for example). Today every interface list drops
loopback, so PSN and OTP cannot reach a receiver listening on `lo0`, and with
no network cable there is nothing to send from at all. This plan offers `lo0`
as an interface on macOS, the way MA3 offers it.

## Decisions

- **macOS only.** Linux `lo` carries no multicast by default; enabling it would
  need a privileged helper. A Pi never offers loopback.
- **Every interface picker offers it on a Mac:** the station-wide interface in
  Simple mode, and each plane's pin in Advanced mode. MA3 onPC was verified to
  receive PSN sent this way.
- **Station-wide loopback moves everything that follows the station
  interface:** PSN output and input, OTP, RTTrPM, OSC destinations and input,
  station discovery, marker catalog sync and the video input. The station then
  works alone on this Mac: it finds no stations on the LAN, and a camera on the
  LAN is refused by the video preflight. That is the setting's meaning.
- **Automatic never picks loopback.** `get_primary_local_ipv4` and
  `get_iface_for_ip` keep ignoring it.

## What changes

- [x] **One rule** in `net_utils`: `LOOPBACK_OFFERED` is true on macOS and
      `offered_loopback(name)` checks it. With
      it, `get_iface_ipv4("lo0")` resolves `127.0.0.1` (never a driver alias
      such as `127.51.68.120`), and `list_iface_ipv4` lists `lo0`. Every picker,
      resolver and the network observer go through these two.
- [x] **Startup:** `wait_for_source_ip` reports a pinned loopback as resolved,
      not as its timeout fallback, so the "Starting without a usable network
      address" warning does not fire for a station deliberately on `lo0`.
- [x] **Simple mode station picker** (web `_station_picker`, Operator Screen
      `_station_picker_rows`): offer `lo0` on a Mac. The network backend's
      reading drops loopback, so the row is added from `list_iface_ipv4`.
- [x] **Labels:** the interface shows as `Loopback (lo0)` in every picker
      (`display_name`); a plane on it shows `127.0.0.1` as its address.
- [x] **Fail closed for unicast:** an RTTrPM or OSC destination off this Mac
      cannot be reached through loopback. When its plane resolves to `lo0` and
      the destination is not a loopback address, it is not sent; the panel and
      the skip reason say "Loopback reaches only this Mac". (PSN, OTP, discovery
      and sync are multicast and simply stay on this Mac.)
- [x] **Help:** the network and interface-assignment help drawers explain that
      loopback reaches only programs on this Mac, and what follows a
      station-wide choice.
- [x] **Docs:** `docs/architecture/network.md` describes the rule.

## Tests

- [ ] The rule on and off (tests pin it off, as Linux); `get_iface_ipv4` and
      `list_iface_ipv4` with an injected interface table holding `lo0` with
      `127.0.0.1` and a driver alias, on both platforms.
- [ ] Automatic resolution never picks loopback on a Mac.
- [ ] `wait_for_source_ip` with a `lo0` pin returns at once, and startup logs
      no warning.
- [ ] The Simple mode pickers (web and Operator Screen) offer `lo0` on a Mac
      only; the Advanced pickers list it.
- [ ] A plane pinned to `lo0` resolves `127.0.0.1`; PSN and OTP open on `lo0`.
- [ ] RTTrPM and an OSC destination on `lo0` with a LAN host are not sent and
      report the reason; with a loopback host they are sent.
- [ ] Existing tests that assert loopback is excluded keep doing so off macOS.

## Verification

- [ ] On a Mac: a local listener on `lo0` receives PSN and OTP multicast and
      RTTrPM and OSC unicast from OpenFollow pinned to loopback.
- [ ] MA3 onPC with its PSN interface set to `lo0 (127.0.0.1)` shows
      OpenFollow's trackers moving.
- [ ] `make ci-remote` green.
