# Plan: warn on unsupported hardware and operating systems

OpenFollow is supported on a Raspberry Pi 5, Compute Module 5 or Pi 500 only.
It also starts on macOS, on older Pis and possibly on Windows, and nothing tells
the operator that such a station is outside what the project supports. This plan
adds a loud, permanent warning on the Operator Screen, on every web page and in
the diagnostics, saying the platform is unsupported and that no support is
provided for it.

## What counts as unsupported

| Platform | Detected by | Verdict |
|---|---|---|
| Raspberry Pi 5, CM5, Pi 500 (and any later generation) | `/proc/device-tree/model` generation >= 5 | Supported |
| Raspberry Pi 4, CM4, Pi 400, Pi 3, Pi 2, Pi 1, Zero, Zero 2 W | `/proc/device-tree/model` generation < 5, or a Zero | Unsupported: "Raspberry Pi 4" etc. |
| macOS | `sys.platform == "darwin"` | Unsupported: "macOS" |
| Windows | `sys.platform == "win32"` | Unsupported: "Windows" |
| Debian 13 (or later) or a Debian derivative, on hardware that is not a Pi | no Raspberry Pi model string; `/etc/os-release` `ID=debian` with `VERSION_ID` >= 13, or `debian` in `ID_LIKE` | Supported |
| Any other Linux on hardware that is not a Pi (Fedora, Arch, Debian 12, …) | no Raspberry Pi model string, `/etc/os-release` as above does not match | Unsupported: the `PRETTY_NAME`, e.g. "Fedora Linux 42" |

A Pi below generation 5 is unsupported whatever OS it runs; the OS rule applies
to hardware that is not a Pi. Derivatives are taken by `ID_LIKE` alone (Ubuntu,
Linux Mint, Raspberry Pi OS), with no version check, since their own version
numbers don't map onto Debian's. An unreadable `/etc/os-release` counts as
unsupported, named "Linux".

The generation is parsed from the model string: `Raspberry Pi 5 Model B`,
`Raspberry Pi Compute Module 5`, `Raspberry Pi 500` (hundreds → generation 5),
`Raspberry Pi Zero 2 W` (always unsupported). A model string that names a Pi but
can't be parsed counts as unsupported, naming the string as read.

The verdict is computed **once at startup** (hardware and OS don't change at
runtime) and held on the app; every surface reads that one value.

## Surfaces

**Level: error.** By the status language, an error means "it does not work,
and will not until someone acts". Here the station runs, but it is on its own
with no support, and the warning is meant to be loud. Developer mode hides
every surface but the bundle (see Decisions).

Wording, on every surface: the observed fact, then the consequence.

- Main line: "Unsupported platform: <name>" (e.g. "Raspberry Pi 4", "macOS",
  "Windows", "Fedora Linux 42").
- Step line: "OpenFollow is not developed to work on this platform and errors are expected. No support is provided. Never use this setup in a show!"

1. **Operator Screen corner:** a permanent top-right error status row, the
   same component as the under-voltage row. The row holds two lines, so it
   carries the short form: "Unsupported platform: macOS. Errors are expected.
   Never use this setup in a show!" It cannot be dismissed. It sits last in the
   row stack, so live faults (video, power) stay on top.
2. **Operator Screen Settings menu:** an ERROR box below the station card,
   main line and step line in full. If the video-failure ERROR box also shows,
   both stack, and the video failure comes first.
3. **Web UI, every page:** an error box (`.notice.error`) between the
   navigation (header and tab bar) and the panels, on every page (main UI,
   Setup Wizard, About, login), main line and step line in full. Not
   dismissible, no role (static, rendered once per page), so it isn't
   re-announced. On pages without a tab bar (login, About, Setup Wizard) it
   sits under the header.
4. **Status & Diagnostics page:** the same error in the alert boxes
   (`web/live_alerts.py`), spoken once by the existing announcer.
5. **Diagnostics bundle:** a `Platform support:` line at the top of Section E
   (environment), with the verdict and the model string or OS as read, and the
   step line in the bundle's header,
   so whoever receives a bundle sees it first.
6. **Help drawer:** `diagnostics.md` and `general-station.md` explain the
   supported hardware and why the warning shows. No inline help.

Not in scope: the Station Network peer list, the `.deb` install (Python 3.13
gate stays as is), refusing to start.

## Implementation

- [ ] `openfollow/platform_support.py`: `PlatformSupport(supported, name)` and
      `detect_platform_support(model_paths=..., platform=sys.platform)`. Reads the
      model paths `services._is_raspberry_pi` already reads (NUL-stripped), parses
      the generation. `_is_raspberry_pi` uses it.
- [ ] Startup: detect once in `AppRuntimeServices`, log one WARNING line,
      expose it to the renderer, the web server and the diagnostics providers.
- [ ] HUD corner: `flags_dict["platform"]` in `services_marker_visuals.py`,
      last in insertion order.
- [ ] HUD Settings menu: the ERROR box below the station card, stacked after
      the video failure's.
- [ ] Web: the box in `base.tpl` between the navigation and the panels, fed by the server's template defaults (login
      renders without `config`, so it can't depend on `config`).
- [ ] `web/live_alerts.py`: a platform alert in `diagnostics_alerts`, and in
      `spoken()` and `key()`.
- [ ] `web/diagnostics.py`: the Section E line and the header sentence.
- [ ] Developer mode: every surface asks one helper,
      `platform_warning_shown(verdict, cfg)` (unsupported and not
      `cfg.ui.developer_mode`); update the field's comment in `configuration.py`
      and its entry in `docs/architecture/web.md`.
- [ ] Help: `diagnostics.md`, `general-station.md`.
- [ ] Docs: `docs/STATUS_LANGUAGE.md` (error examples: "an unsupported
      platform"; HUD row), `docs/architecture/diagnostics.md`, README system
      requirements cross-link.
- [ ] Mockup Artifact of the web box, the HUD corner row and the Settings box for approval before
      the templates are written.

## Tests

- [ ] `tests/test_platform_support.py`, one `parametrize` per class: every Pi
      model string above (including NUL-terminated and CM/400/500/Zero forms), a
      hypothetical "Raspberry Pi 6", a garbled Pi string, no model file on Linux,
      `darwin`, `win32`; model file unreadable (`OSError`); `os-release` for
      Debian 13, Debian 14, Debian 12, Ubuntu, Raspberry Pi OS, Fedora, a missing
      file and a file with no `ID`.
- [ ] HUD: the corner row and the Settings ERROR box are present on an
      unsupported verdict and absent on a supported one, the row ordered last
      and the box after the video failure's.
- [ ] Web: the box renders between the navigation and the panels on `/`, `/wizard`, `/about` and `/login` when
      unsupported, and on none of them when supported (`tests/test_web_server.py`).
- [ ] Live alerts: alert, spoken text and key change with the verdict.
- [ ] Bundle: the Section E line and the header sentence for both verdicts,
      and the "warning hidden: developer mode" suffix.
- [ ] Developer mode: with `ui.developer_mode` on, no web box, HUD row,
      Settings box or live alert on an unsupported verdict; switching it via
      `apply_runtime_config_changes` shows and hides them live.
- [ ] `tests/test_status_language.py` stays green (no literal colours).
- [ ] Every test injects the verdict or the model path; none reads the host.

## Verification

- [ ] `make ci-remote` green.
- [ ] On the Mac: the box on every page, the HUD corner row and the Settings box, with a screenshot.
- [ ] On pi66 (Pi 5): neither shows; the bundle says "supported".
- [ ] A Pi 4, if one is at hand: box, HUD row and bundle line.

## Decisions

- **Level: error** (red), on every surface.
- **Developer mode hides the warning.** With `ui.developer_mode` on
  (config-only, see the [web guide](../architecture/web.md)) the web box, the
  HUD corner row, the Settings ERROR box and the Status & Diagnostics alert
  don't show. The setting is read live, so editing `config.toml` and
  saving hides or shows them without a restart. The diagnostics bundle still
  states the verdict, followed by "warning hidden: developer mode", so a
  bundle from such a station never hides what it runs on.
