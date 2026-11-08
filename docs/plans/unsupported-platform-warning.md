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

Supported hardware with a 32-bit OS or a desktop (a display manager is enabled) is unsupported too: "Raspberry Pi 5 Model B with a desktop", "… with a 32-bit OS". A Pi below generation 5 is unsupported whatever OS it runs; the OS rule applies
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
5. **Diagnostics bundle:** a `support:` line in the header after `platform:`,
   with the title and the step line, so whoever receives a bundle sees it first.
6. **Help drawer:** `diagnostics.md` explains the supported platforms and why
   the warning shows. No inline help.

Not in scope: the Station Network peer list, the `.deb` install (Python 3.13
gate stays as is), refusing to start.

## Implementation

- [x] `openfollow/platform_support.py`: `PlatformSupport(supported, name)`,
      `detect(platform, model_paths, os_release_paths)`, `current()` (cached
      once per process), `warning_shown(config)`, `stats_entry(config)`, and
      the wording (`title`, `STEP`, `SHORT_STEP`).
- [x] Startup: one WARNING line from `AppRuntimeServices.__init__`.
- [x] HUD corner: a `platform` error row after every live fault's row.
- [x] HUD Settings menu: `OverlayState.settings_platform_warning`, an ERROR box
      under the live fault's; the bottom-left panel stays as it is.
- [x] Web: `partials/platform_warning.tpl`, in `base.tpl` under the header, or
      under the page's own navigation (index, About, Setup Wizard rebase with
      `platform_warning_inline=True`); fed by `_page_host_context` and the
      refused page.
- [x] `/api/stats`: `system.platform` (`supported`, `name`, `warning`).
- [x] `web/live_alerts.py`: the alert first in This Station, in `spoken()` and `key()`.
- [x] `web/diagnostics.py`: the header's `support:` line.
- [x] Developer mode through `warning_shown`; the field's comment and
      `docs/architecture/web.md`.
- [x] Help: `diagnostics.md`.
- [x] Docs: `docs/STATUS_LANGUAGE.md`, `docs/architecture/diagnostics.md`,
      the CLAUDE.md guide table.
- [x] Mockup Artifact of the web box, the HUD corner row and the Settings box.

## Tests

- [x] `tests/test_platform_support.py`: every Pi model string (NUL-terminated,
      CM, 400, 500, Zero, Pi 1, a hypothetical Pi 6), an older Pi on Debian 13,
      `os-release` for Debian 12/13/14/sid, Ubuntu, Mint, Fedora, Arch, a bad
      version, no `ID`, an empty and a missing file, the fallback path, an
      unreadable model, `darwin`, `win32`, another OS, developer mode.
- [x] HUD state (`test_services_marker_visuals_remaining.py`): the row last of
      the errors and before a caution, the Settings title, developer mode, a
      supported platform, a closed menu.
- [x] HUD draw (`test_overlay_draw_hud.py`): the box's text and place, after a
      live fault, none without a title, the panel not red.
- [x] Web (`test_web_server.py`): between the navigation and the panels on
      `/`, `/about` and `/wizard`; under the header on `/login` and the refused
      page; hidden by developer mode on the next request; none when supported.
- [x] Live alerts: the box, the spoken text, its order, the key, hidden.
- [x] Services → stats → alert (`test_services_runtime_stats.py`), both modes;
      the startup log (`test_services_startup.py`).
- [x] Bundle header for every verdict, developer mode and a failing stats provider.
- [x] `tests/conftest.py` pins a supported platform for every other test.

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
