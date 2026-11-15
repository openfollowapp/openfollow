# Written Offer for Source Code (macOS app)

This is OpenFollow's written offer to provide the **complete corresponding
source code** for the free-software components the macOS app (the
`OpenFollow-<version>-arm64.dmg` disk image) distributes in binary form, as
required by the **GNU General Public License** (GPL-2.0 §3, GPL-3.0 §6) and the
**GNU Lesser General Public License** (LGPL-2.1 §6, LGPL-3.0 §4). It is an
informational compliance statement, **not legal advice**.

## What this covers

The macOS app bundles, in binary form, GStreamer and its plugins (FFmpeg among
them, through `libgstlibav`), GTK and its dependencies (GLib, Cairo, Pango,
HarfBuzz and others), a Python runtime and the Python packages the app imports.
Every one of them is listed, with its version and license, in this app's
**Third-Party Notices**, which are generated from the app bundle when it is
built. This offer applies to every GPL- and LGPL-covered component listed there.

OpenFollow's own code is licensed **AGPL-3.0-or-later**; its complete
corresponding source is this project's repository, and per the Affero clause
(AGPL §13) the running web UI links to it directly from the About page.

## The offer

For **at least three (3) years** from the date OpenFollow distributed a given
disk image, OpenFollow will give any third party who possesses that image, on
request:

> a complete machine-readable copy of the corresponding source code for the
> GPL- and LGPL-covered software contained in that image, for the exact versions
> distributed, under the terms of the respective licenses – delivered by
> download at no charge, or on physical media for a charge no greater than our
> cost of performing the physical distribution.

To make a request, contact
**[hello@openfollow.app](mailto:hello@openfollow.app)** and identify the app
version (shown on the About page and in the web UI footer).

## Obtaining the source directly (usually faster)

- **Homebrew formulae** (GStreamer, GTK, FFmpeg and their dependencies) – each
  formula names its source archive and checksum; the Third-Party Notices give
  the formula and version, and `brew info <formula>` or
  [formulae.brew.sh](https://formulae.brew.sh/) leads to the upstream source.
- **Python packages** – the exact versions listed in the Third-Party Notices are
  on [pypi.org](https://pypi.org/) as source distributions or from each
  project's homepage.
- **OpenFollow** – [openfollow.app](https://openfollow.app) (source link on the
  About page).

## Modifying and relinking (LGPL)

The LGPL-covered libraries are separate dynamic libraries inside the app bundle
(`OpenFollow.app/Contents/Frameworks`), loaded at run time. A user may replace
them with modified builds of the same interface. The app is only ad-hoc signed,
so no signing key is needed to run it afterwards; re-sign it ad hoc
(`codesign --force --deep --sign - OpenFollow.app`) after changing it.

## Excluded components

The NDI SDK and its GStreamer plugin are **not** included in the macOS app, so
this offer does not extend to them. NDI is proprietary and is installed
separately by the operator (NDI Tools); see the **Third-Party Notices**.

## No warranty

OpenFollow and the components it distributes come with **NO WARRANTY**, to the
extent permitted by law. See the
[GNU GPL](https://www.gnu.org/licenses/gpl-3.0.html) /
[GNU LGPL](https://www.gnu.org/licenses/lgpl-3.0.html) /
[GNU AGPL](https://www.gnu.org/licenses/agpl-3.0.html) for the full terms.

---

*Contact: [hello@openfollow.app](mailto:hello@openfollow.app) ·
[openfollow.app](https://openfollow.app)*
