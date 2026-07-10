# Third-Party Notices

OpenFollow is free software licensed under the **GNU Affero General Public
License, version 3 or (at your option) any later version** (see
[`LICENSE`](LICENSE)).

## License Information & Source Code

This system consists of two legally separate main components combined on one
storage medium (**Mere Aggregation**):

- **OpenFollow (Control Software & AI Models).** The application itself,
  including the integrated YOLO models (Ultralytics), is licensed under the
  **AGPL-3.0-or-later**. Because OpenFollow is operated over a network through
  its web UI, the Affero clause (AGPL §13) applies: every user interacting with
  it remotely is offered the complete corresponding source. That source is
  freely available at
  **[github.com/openfollowapp/openfollow](https://github.com/openfollowapp/openfollow)**,
  and the running web UI links to it directly from its **About** page.
- **Operating System (Debian GNU/Linux).** On the ready-to-flash Raspberry Pi
  appliance image, the underlying system image and its included packages are
  primarily licensed under the **GPL-2.0** (and compatible licenses). The source
  for these system components, and OpenFollow's own build scripts for the image,
  are provided on request and by reference to the upstream Debian / Raspberry Pi
  sources – see [`WRITTEN_OFFER.md`](WRITTEN_OFFER.md) and §5 below.

Aggregating an AGPL work (OpenFollow) and the GPL-2.0 operating system on a
single image is permitted: they are independent programs that are not combined
into a single work, so each keeps its own license and obligations.

This file catalogues the third-party components OpenFollow bundles, depends on,
links against, or – in the appliance image – conveys, together with their
licenses. It is an informational summary for operators and redistributors,
**not legal advice**; each component is governed by its own license, linked
below.

OpenFollow ships in two forms, and the obligations differ:

- **Source, Python wheel, or Debian package** (`pip install` / `dpkg -i`):
  OpenFollow ships only the components in §1–§3; the system libraries in §4 come
  from the operator's own operating system.
- **Ready-to-flash Raspberry Pi appliance image**: OpenFollow also conveys a
  complete operating system – see §5.

Unless noted otherwise, every component listed here carries a license that is
compatible with the GNU AGPL v3 (MIT, BSD, ISC, Apache-2.0, the Unlicense, and
the LGPL are all AAGPLv3-compatible; the FSF maintains the authoritative
[license list](https://www.gnu.org/licenses/license-list.html)).

The one component that warrants attention is the **NDI® SDK**, which is
proprietary – it is covered in detail below.

---

## 1. Bundled assets (shipped in the repository / wheel)

| Component | Version | License | Where |
|-----------|---------|---------|-------|
| [htmx](https://htmx.org/) | 1.9.10 | [BSD-2-Clause](https://github.com/bigskysoftware/htmx/blob/master/LICENSE) | `openfollow/web/static/htmx.min.js` |

The remaining functional assets under `openfollow/web/static/` and
`openfollow/video/inputs/assets/` – the default stage graphics
(`stage_default.jpg`, the photoreal default, and `stage_default.svg`, the
fallback used when the JPG is absent) and the color-picker script
(`js/color-picker.js`) – are **original works of the OpenFollow Project** and
are covered by OpenFollow's own AGPLv3-or-later license, not by any third party.

No fonts are bundled: the web UI uses a CSS system-font stack and the on-screen
Cairo HUD resolves system fonts at runtime via pycairo's `cr.select_font_face`
(no font files are shipped).

### OpenFollow name & logo – all rights reserved

The AGPL applies to the OpenFollow code only. The OpenFollow **name**, the
**logo/icon** brand assets (`openfollow/web/static/openfollow.svg` and
`icon.svg`), and all OpenFollow branding are © Michel Honold, Paul Hermann,
Vinzenz Schultz – **all rights reserved** and are **not** covered by the AGPL.
You may redistribute and modify the software under the AGPLv3-or-later, but the
OpenFollow name and logo may not be used to brand derivative works without
permission.

---

## 2. Required Python dependencies

Declared in `pyproject.toml` (`[project] dependencies`). All are
AGPLv3-compatible.

| Package | Constraint | License |
|---------|-----------|---------|
| [pypsn](https://github.com/open-stage/python-psn) | `git+https://github.com/open-stage/python-psn.git` | MIT |
| [numpy](https://numpy.org/) | ≥1.24 | BSD-3-Clause |
| [tomli](https://github.com/hukkin/tomli) | ≥1.2.0 (Python < 3.11 only) | MIT |
| [tomli-w](https://github.com/hukkin/tomli-w) | ≥1.0 | MIT |
| [bottle](https://bottlepy.org/) | ≥0.12 | MIT |
| [pygame](https://www.pygame.org/) | ≥2.5.0 | LGPL-2.1-or-later |
| [multicast-expert](https://github.com/multiplemonomials/multicast_expert) | ≥1.4 | MIT |
| [PyGObject](https://gitlab.gnome.org/GNOME/pygobject) | ≥3.42 | LGPL-2.1-or-later |
| [psutil](https://github.com/giampaolo/psutil) | ≥5.9 | BSD-3-Clause |
| [python-osc](https://github.com/attwad/python-osc) | ≥1.8 | Unlicense (public-domain dedication) |

`tomli` is only installed on Python < 3.11; on 3.11+ the standard-library
`tomllib` is used instead.

---

## 3. Optional Python dependencies (extras)

Installed only when the corresponding extra is requested
(`pip install openfollow[detection]`, etc.).

### `detection`

| Package | Constraint | License |
|---------|-----------|---------|
| [onnxruntime](https://onnxruntime.ai/) | ≥1.17 | MIT |
| [opencv-python](https://github.com/opencv/opencv-python) | ≥4.8 | Apache-2.0 |

> The prebuilt `opencv-python` wheels bundle additional libraries (e.g. FFmpeg)
> under their own licenses, including the LGPL. These are AGPLv3-compatible.
>
> The `detection` backend (onnxruntime + opencv) is **bundled** in the macOS app
> and the Raspberry Pi appliance image, so detection runs offline out of the box;
> both are permissively licensed. Only the `export` toolchain below stays out of
> the image.

### `export`

| Package | Constraint | License |
|---------|-----------|---------|
| [ultralytics](https://github.com/ultralytics/ultralytics) | ≥8.4.71 | AGPL-3.0-or-later |

> The `export` extra is the **model-export toolchain only** – it converts
> YOLO `.pt` weights to the ONNX models the detector runs (`scripts/export_onnx.py`
> and the web model-export action). It is **never required at runtime**: OpenFollow
> invokes it as a separate subprocess on a workstation, never links or imports it
> into the running program, and it is **not bundled in the appliance image** (the
> release-time SBOM check keeps ultralytics out of the bundled venv by name).
> Operators install it on demand on a dev machine; the
> show Pi never sees it. ultralytics is AGPL-3.0-or-later – the same license as
> OpenFollow itself – so it raises no additional licensing concern where used.

---

## 4. System libraries (dynamically linked at runtime)

OpenFollow uses these via [PyGObject](https://pygobject.gnome.org/)
(GObject-Introspection) and GStreamer. In a `pip` / Debian-package install they
are **not bundled** – they come from the operator's operating system and are
linked dynamically at runtime. **In the appliance image (§5) they are conveyed
as part of the OS.** All are LGPL (or LGPL + permissive), which is
AGPLv3-compatible.

| Library | License |
|---------|---------|
| GLib / GObject / GIO | LGPL-2.1-or-later |
| GTK | LGPL-2.1-or-later |
| GStreamer core + base/good plugins | LGPL-2.1-or-later |
| cairo | LGPL-2.1 / MPL-1.1 (dual) |
| Pango | LGPL-2.1-or-later |
| WebKitGTK | LGPL-2.1-or-later + BSD-2-Clause |

> **GStreamer plugin sets:** `gst-plugins-bad` and `gst-libav` (FFmpeg) are
> pulled in by the Debian package and are therefore present in the appliance
> image; `gst-plugins-ugly` is not. These wrap codecs (e.g. H.264 / H.265) that
> are LGPL/GPL in licensing but **patent-encumbered** in some jurisdictions –
> their source is available like any other GPL/LGPL component (§5), but patent
> licensing for codec use is the operator's responsibility. In a `pip` install
> the installed plugin set is whatever the operator's system provides.

---

## 5. Appliance image (Debian / Raspberry Pi OS)

OpenFollow is also distributed as a ready-to-flash **Raspberry Pi appliance
image** (Compute Module 5) – the **software image only**; the Raspberry Pi
hardware is supplied and flashed by the operator. Unlike the `pip` /
Debian-package install – where the operating system is the operator's own – the
image **conveys a complete operating system**: Debian GNU/Linux (Trixie, arm64), the Raspberry Pi Linux
kernel and boot firmware, and the base-system packages OpenFollow depends on
(the GStreamer/GTK stack of §4, NetworkManager, Avahi, OpenSSH, Cage/seatd, …).

Every component keeps its own license – predominantly **GPL-2.0**, **GPL-3.0**,
**LGPL-2.1**, and permissive **MIT/BSD**. Conveying them as part of the image
carries each license's own obligations:

- **Corresponding source (GPL/LGPL).** For every GPL- and LGPL-licensed
  component in the image, the complete corresponding source for the version
  shipped is available. The image is accompanied by a written offer (the
  **Written Offer** document) for that source, valid for three years; source is
  provided from the upstream distribution archives (Debian, Raspberry Pi) and on
  request via [hello@openfollow.app](mailto:hello@openfollow.app).
- **Linux kernel** – GPL-2.0; source at the shipped version from
  [github.com/raspberrypi/linux](https://github.com/raspberrypi/linux).
- **Raspberry Pi GPU boot firmware** – its license permits redistribution **for
  use on Raspberry Pi hardware**. OpenFollow distributes the image, not the
  hardware; the operator runs it on their own Raspberry Pi, which is exactly
  that use.
- **Installation Information (GPLv3 / AGPLv3 §6).** The device is the operator's own
  unlocked Raspberry Pi – no secure boot, no signed-image enforcement – so a
  user can build and install modified GPL/LGPL binaries (via `apt`/`dpkg`, over
  SSH, or by re-flashing) and run them on the device.

**OpenFollow's detection backend (`onnxruntime` + OpenCV) is bundled in the
appliance image so detection runs offline; the AGPL model-export toolchain
(`ultralytics` / `torch`) and the NDI components are not** – the NDI SDK is
proprietary (§6). A release-time SBOM check fails the build if the proprietary
NDI component appears anywhere in the image, or if `ultralytics` is bundled in
the venv.
The base GStreamer stack (`gstreamer1.0-plugins-bad`) also transitively includes a
permissively-licensed `libonnxruntime` system library; it carries no AGPL or
proprietary obligation and is separate from OpenFollow's own bundled backend.

OpenFollow is built on Debian GNU/Linux and Raspberry Pi OS components but is
**not affiliated with or endorsed by** the Debian Project, Raspberry Pi Ltd, or
Software in the Public Interest.

---

## 6. Proprietary / optional, dynamically-loaded – NDI®

The **NDI® SDK** (NewTek / Vizrt NDI AB) is **proprietary** and is **not part
of OpenFollow**: it is neither bundled in the repository nor declared as a
Python dependency.

The optional NDI video input works only when the operator has *separately
installed* the proprietary SDK and the corresponding GStreamer plugin, and
OpenFollow uses them only through runtime, optional, dynamic discovery:

- The NDI shared library (`libndi.so` / `libndi.dylib` /
  `Processing.NDI.Lib.x64.dll`) is located **at runtime** via
  `ctypes.util.find_library` and a directory probe
  (`openfollow/video/inputs/ndi.py`); it is loaded only if found.
- Video itself flows through the third-party **`ndisrc`** GStreamer element
  (the `gst-plugin-ndi` plugin), which OpenFollow likewise only uses if the
  operator has installed it; the input cleanly reports "NDI® GStreamer plugin
  not installed" otherwise.

Because the NDI SDK is an optional, separately-installed component that
OpenFollow merely loads dynamically at runtime if present – rather than a
library OpenFollow links against, bundles, or distributes – its proprietary
license does not impose terms on OpenFollow's own AGPLv3-or-later code. Operators
who enable NDI are responsible for complying with the NDI SDK license.

**NDI® is a registered trademark of Vizrt NDI AB.**

---

## 7. Build- and development-only tools

Tools used to build, test, or lint OpenFollow (e.g. `poetry-core` – MIT – as the
build backend, and the dev-group tools `ruff`, `pytest`, `mypy`, etc.) are
**not distributed as part of OpenFollow** and impose no terms on the shipped
program. They are listed in `pyproject.toml` for contributors.

---

*To refresh this file after a dependency change, re-check each package's
declared license (e.g. `python -m importlib.metadata` or the project's
repository) and update the tables above.*
