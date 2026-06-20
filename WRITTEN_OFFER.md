# Written Offer for Source Code

This is OpenFollow's written offer to provide the **complete corresponding
source code** for the free-software components it distributes in binary form, as
required by the **GNU General Public License** (GPL-2.0 §3, GPL-3.0 §6) and the
**GNU Lesser General Public License**. It is an informational compliance
statement, **not legal advice**.

## What this covers

OpenFollow is distributed as a ready-to-flash **Raspberry Pi appliance image**.
That image conveys, in binary form, a complete operating system – the Linux
kernel, the GNU userland, GStreamer, GTK, NetworkManager, and many other GPL-
and LGPL-licensed packages (catalogued in the **Third-Party Notices**, §5). This
offer applies to every GPL- and LGPL-covered component in any binary (image)
distribution of OpenFollow.

OpenFollow's own code is licensed **AGPL-3.0-or-later**; its complete
corresponding source is this project's repository, and per the Affero clause
(AGPL §13) the running web UI links to it directly from the About page. The
written offer below is primarily about the **operating-system components**
bundled into the image.

## The offer

For **at least three (3) years** from the date OpenFollow distributed a given
binary image, OpenFollow will give any third party who possesses that image, on
request:

> a complete machine-readable copy of the corresponding source code for the
> GPL- and LGPL-covered software contained in that image, for the exact versions
> distributed, under the terms of the respective licenses – delivered by
> download at no charge, or on physical media for a charge no greater than our
> cost of performing the physical distribution.

To make a request, contact
**[hello@openfollow.app](mailto:hello@openfollow.app)** and identify the image
version (shown on the device's About screen and in the web UI footer).

## Obtaining the source directly (usually faster)

The corresponding source for the bundled OS components is published by their
upstreams and can be downloaded without contacting us:

- **Debian packages** – each binary package's exact source version is available
  from the Debian archive, including historical versions at
  [snapshot.debian.org](https://snapshot.debian.org/).
- **Raspberry Pi Linux kernel** –
  [github.com/raspberrypi/linux](https://github.com/raspberrypi/linux), at the
  kernel version shipped.
- **Raspberry Pi firmware / bootloader** –
  [github.com/raspberrypi/firmware](https://github.com/raspberrypi/firmware) and
  [github.com/raspberrypi/rpi-eeprom](https://github.com/raspberrypi/rpi-eeprom).
- **OpenFollow** – [openfollow.app](https://openfollow.app) (source link on the
  About page).

A per-release **SPDX Software Bill of Materials** (syft) identifying the exact
versions baked into an image accompanies that image's release; the package URLs
(purls) it records pin each component to its source.

## Modifying and reinstalling (GPLv3 / AGPLv3 §6 Installation Information)

The image runs on the operator's **own, unlocked Raspberry Pi** – no secure boot
and no signed-image enforcement. A user may build modified versions of the GPL-
and LGPL-covered binaries, install them on the device (via `apt` / `dpkg`, over
SSH, or by re-flashing) and run them. No signing keys or authorization are
required.

> If a future OpenFollow image ever restricts which binaries the device will run
> (secure boot / signed images), this document must be updated to ship the
> necessary Installation Information – the signing keys or an authorized install
> method – or the GPLv3- / AGPLv3-covered components must be removed.

## Excluded components

The person-detection stack (`onnxruntime`, OpenCV) and the NDI SDK / plugin are
**not** included in the appliance image, so this offer does not extend to them.
NDI is proprietary; see the **Third-Party Notices**.

## No warranty

OpenFollow and the components it distributes come with **NO WARRANTY**, to the
extent permitted by law. See the
[GNU GPL](https://www.gnu.org/licenses/gpl-3.0.html) /
[GNU AGPL](https://www.gnu.org/licenses/agpl-3.0.html) for the full terms.

---

*Contact: [hello@openfollow.app](mailto:hello@openfollow.app) ·
[openfollow.app](https://openfollow.app)*
